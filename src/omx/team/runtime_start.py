"""Port of ``startTeam`` from ``src/team/runtime.ts`` (lines 1990-2582).

Phase 2.9a — sync, stdlib-only port.

Public surface:

* :func:`start_team` — entry point. Sets up a tmux session (interactive mode)
  or spawns prompt-mode child processes, bootstraps each worker with role
  prompts + AGENTS.md overlay + initial inbox, persists the V2 manifest +
  team config + initial tasks, and dispatches startup trigger messages.
* :class:`TeamRuntime` — re-exported from :mod:`omx.team.runtime_types`.

Both the **Codex** worker path (interactive tmux + ``codex resume``) and
the **Claude** worker path (prompt-mode child process or interactive
``claude`` CLI) are wired through ``worker_cli`` resolution. Selection is
driven by ``OMX_TEAM_WORKER_CLI`` / ``OMX_TEAM_WORKER_CLI_MAP`` and the
returned plan from :func:`team.tmux_session.resolve_team_worker_cli_plan`.

Failure paths roll back tmux panes, worker root AGENTS.md installs, the
composed worker-instructions file, the V2 manifest + state directory, and
any provisioned worktrees. The accumulated rollback errors (if any) are
appended to the raised exception's message.

Simplifications relative to TS (documented inline):

* ``unregisterResizeHook`` is *attempted* during rollback if the config
  carries a registered hook; otherwise it's silently skipped (the Python
  port does not assert the tmux exit code).
* Per-worker readiness polling delegates to
  :func:`team.tmux_session.wait_for_worker_ready` (already ported).
* Startup-evidence and Claude-evidence helpers come from
  :mod:`omx.team.runtime_wait_startup` and
  :mod:`omx.team.runtime_wait_claude` respectively; this module never
  blocks on them when ``skip_worker_ready_wait`` or
  ``initial_prompt`` is set.
* ``setTeamModelInstructionsFile`` / ``restoreTeamModelInstructionsFile``
  are implemented as module-level helpers using a private dict so each
  team gets a clean ``OMX_MODEL_INSTRUCTIONS_FILE`` restore on rollback.
* ``recordRecoverableStartupIssue`` writes worker status + emits a
  ``worker_state_changed`` event via the already-ported state helpers.

Helpers ported (private):

* :func:`_assert_team_startup_is_non_destructive` — preflight that
  prevents clobbering an active team's state.
* :func:`_assert_nested_team_allowed` — enforces the governance flag.
* :func:`_resolve_leader_session_id` — env + ``.omx/state/session.json``.
* :func:`_detect_and_clean_stale_team` — scrub leftover state when no
  matching tmux session exists.
* :func:`_resolve_effective_team_worktree_mode` — honour caller request
  but fall back to disabled when ``cwd`` is not a git repo.
* :func:`_resolve_worker_ready_timeout_ms` etc. — env-knob helpers.
* :func:`_record_recoverable_startup_issue` — write worker status + event.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from omx.team.contracts import TeamEvent, TeamTask
from omx.team.mcp_comm import (
    DispatchOutcome,
    DispatchTransport,
    QueueInboxParams,
    TeamNotifierTarget,
    queue_inbox_instruction,
)
from omx.team.model_contract import (
    resolve_agent_default_model,
    resolve_agent_reasoning_effort,
    resolve_team_worker_launch_args,
    ResolveTeamWorkerLaunchArgsOptions,
)
from omx.team.role_router import load_role_prompt
from omx.team.runtime_helpers import (
    apply_created_interactive_session_to_config,
    cleanup_team_worker_launch_orphaned_mcp_processes,
    resolve_worker_launch_args_from_env,
)
from omx.team.runtime_types import (
    StaleTeamSummary,
    TeamRuntime,
    TeamStartOptions,
)
from omx.team.state.io import (
    append_team_event,
    cleanup_team_state,
    write_team_config,
    write_worker_identity,
    write_worker_inbox,
    write_worker_status,
)
from omx.team.state.manifest import (
    init_team_state,
    read_team_manifest_v2,
)
from omx.team.team_ops import (
    TeamGovernance,
    team_create_task,
    team_list_tasks,
    team_normalize_governance,
)
from omx.team.tmux_session import (
    TeamSession as _TmuxTeamSession,
    create_team_session,
    destroy_team_session,
    dismiss_trust_prompt_if_present,
    get_worker_pane_pid,
    has_current_tmux_client_context,
    is_tmux_available,
    is_worker_pane_open,
    kill_worker_by_pane_id,
    list_team_sessions,
    resolve_team_worker_cli_plan,
    sanitize_team_name,
    unregister_resize_hook,
    wait_for_worker_ready,
)
from omx.team.worker_bootstrap import (
    WorkerRootAgentsOptions,
    build_trigger_directive,
    generate_initial_inbox,
    generate_worker_overlay,
    remove_team_worker_instructions_file,
    remove_worker_worktree_root_agents_file,
    write_team_worker_instructions_file,
    write_worker_role_instructions_file,
    write_worker_worktree_root_agents_file,
)
from omx.team.worktree import (
    EnsureWorktreeResult,
    WorktreeDisabled,
    WorktreeMode,
    assert_clean_leader_workspace_for_worker_worktrees,
    ensure_worktree,
    is_git_repository,
    is_worktree_dirty,
    plan_worktree_target,
    rollback_provisioned_worktrees,
    RollbackWorktreeOptions,
)


# --- Constants (mirror TS) ------------------------------------------------

MODEL_INSTRUCTIONS_FILE_ENV = "OMX_MODEL_INSTRUCTIONS_FILE"
TEAM_STATE_ROOT_ENV = "OMX_TEAM_STATE_ROOT"
TEAM_LEADER_CWD_ENV = "OMX_TEAM_LEADER_CWD"
WORKTREE_TRIGGER_STATE_ROOT = "$OMX_TEAM_STATE_ROOT"

STARTUP_EVIDENCE_TIMEOUT_MS = 2_000
STARTUP_EVIDENCE_LAUNCH_TIMEOUT_MS = 5_000
STARTUP_DISPATCH_RETRIES = 3
STARTUP_DISPATCH_RETRY_DELAY_S = 3

DEFAULT_MAX_WORKERS = 6

TERMINAL_PHASES = frozenset({"complete", "failed", "cancelled"})

#: Private map that lets :func:`_set_team_model_instructions_file` /
#: :func:`_restore_team_model_instructions_file` save and restore each
#: team's prior ``OMX_MODEL_INSTRUCTIONS_FILE`` value across a startTeam
#: call (mirrors the TS module-level Map).
_previous_model_instructions_file_by_team: dict[str, str | None] = {}


# --- Env-knob helpers (mirror TS) ----------------------------------------


def _resolve_team_worker_launch_mode(env: dict[str, str]) -> str:
    """Resolve worker launch mode from env. Defaults to ``"interactive"``.

    TS source: ``resolveTeamWorkerLaunchMode``. Falls back to interactive
    when the env var is unset or unrecognised.
    """
    raw = (env.get("OMX_TEAM_WORKER_LAUNCH_MODE") or "").strip().lower()
    if raw == "prompt":
        return "prompt"
    return "interactive"


def _resolve_worker_ready_timeout_ms(env: dict[str, str]) -> int:
    raw = env.get("OMX_TEAM_READY_TIMEOUT_MS")
    try:
        parsed = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return 45_000
    if parsed >= 5_000:
        return parsed
    return 45_000


def _resolve_worker_startup_evidence_timeout_ms(
    env: dict[str, str], worker_ready_timeout_ms: int
) -> int:
    raw = env.get("OMX_TEAM_STARTUP_EVIDENCE_TIMEOUT_MS")
    try:
        parsed = int(str(raw or "").strip())
    except (TypeError, ValueError):
        parsed = -1
    if parsed >= 500:
        return parsed
    return max(
        STARTUP_EVIDENCE_TIMEOUT_MS,
        min(worker_ready_timeout_ms, STARTUP_EVIDENCE_LAUNCH_TIMEOUT_MS),
    )


def _resolve_startup_dispatch_retries(env: dict[str, str]) -> int:
    raw = env.get("OMX_TEAM_STARTUP_DISPATCH_RETRIES")
    try:
        parsed = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return STARTUP_DISPATCH_RETRIES
    return max(1, min(STARTUP_DISPATCH_RETRIES, parsed))


def _resolve_startup_dispatch_retry_delay_s(env: dict[str, str]) -> float:
    raw = env.get("OMX_TEAM_STARTUP_DISPATCH_RETRY_DELAY_MS")
    try:
        parsed = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return float(STARTUP_DISPATCH_RETRY_DELAY_S)
    return max(0.0, min(float(STARTUP_DISPATCH_RETRY_DELAY_S), parsed / 1000.0))


def _should_skip_worker_ready_wait(env: dict[str, str]) -> bool:
    raw = (env.get("OMX_TEAM_SKIP_READY_WAIT") or "").strip()
    return raw in {"1", "true", "yes"}


def _is_recoverable_interactive_startup_reason(reason: str) -> bool:
    """Mirror TS ``isRecoverableInteractiveStartupReason``."""
    normalized = (reason or "").strip().lower()
    return (
        "startup_no_evidence" in normalized
        or "fallback_attempted_but_unconfirmed" in normalized
        or "ready_prompt_timeout" in normalized
    )


def _resolve_instruction_state_root(worktree_path: str | None) -> str | None:
    if worktree_path:
        return WORKTREE_TRIGGER_STATE_ROOT
    return None


def _set_team_model_instructions_file(team_name: str, file_path: str) -> None:
    if team_name not in _previous_model_instructions_file_by_team:
        _previous_model_instructions_file_by_team[team_name] = os.environ.get(
            MODEL_INSTRUCTIONS_FILE_ENV
        )
    os.environ[MODEL_INSTRUCTIONS_FILE_ENV] = file_path


def _restore_team_model_instructions_file(team_name: str) -> None:
    if team_name not in _previous_model_instructions_file_by_team:
        return
    previous = _previous_model_instructions_file_by_team.pop(team_name)
    if isinstance(previous, str):
        os.environ[MODEL_INSTRUCTIONS_FILE_ENV] = previous
    else:
        os.environ.pop(MODEL_INSTRUCTIONS_FILE_ENV, None)


# --- Canonical team state root ------------------------------------------


def _resolve_canonical_team_state_root(leader_cwd: str) -> str:
    """Mirror TS ``resolveCanonicalTeamStateRoot``.

    The canonical layout is ``<leader_cwd>/.omx/state``. Callers persist
    this string into the manifest/config so workers can resolve their
    own worktree-scoped state under ``$OMX_TEAM_STATE_ROOT``.
    """
    return str(Path(leader_cwd).resolve() / ".omx" / "state")


# --- Preflight helpers --------------------------------------------------


def _parse_team_worker_context(raw: str | None) -> dict[str, str] | None:
    if not isinstance(raw, str) or raw.strip() == "":
        return None
    parts = raw.strip().split("/")
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return None
    return {"team_name": parts[0], "worker_name": parts[1]}


def _resolve_manifest_lookup_cwds(cwd: str) -> list[str]:
    candidates: list[str] = [str(Path(cwd).resolve())]

    leader_cwd_env = os.environ.get(TEAM_LEADER_CWD_ENV)
    if isinstance(leader_cwd_env, str) and leader_cwd_env.strip():
        cand = str(Path(leader_cwd_env).resolve())
        if cand not in candidates:
            candidates.append(cand)

    team_state_root_env = os.environ.get(TEAM_STATE_ROOT_ENV)
    if isinstance(team_state_root_env, str) and team_state_root_env.strip():
        cand = str(Path(team_state_root_env).resolve().parent.parent)
        if cand not in candidates:
            candidates.append(cand)
    return candidates


def _resolve_governance_policy(
    governance: dict[str, Any] | TeamGovernance | None,
    legacy_policy: dict[str, Any] | None = None,
) -> TeamGovernance:
    return team_normalize_governance(governance, legacy_policy)


def _assert_nested_team_allowed(cwd: str) -> None:
    """Mirror TS ``assertNestedTeamAllowed``.

    A nested team launch requires the parent team's manifest governance
    to set ``nested_teams_allowed = True``.
    """
    ctx = _parse_team_worker_context(os.environ.get("OMX_TEAM_WORKER"))
    if ctx is None:
        return

    for candidate_cwd in _resolve_manifest_lookup_cwds(cwd):
        manifest = read_team_manifest_v2(ctx["team_name"], candidate_cwd)
        governance = _resolve_governance_policy(
            manifest.governance if manifest is not None else None
        )
        if governance.nested_teams_allowed:
            return
        if manifest is not None:
            break

    raise RuntimeError("nested_team_disallowed")


def _resolve_leader_session_id(cwd: str) -> str:
    """Mirror TS ``resolveLeaderSessionId``.

    Honours env first (``OMX_SESSION_ID`` / ``CODEX_SESSION_ID`` /
    ``SESSION_ID``), then reads ``<cwd>/.omx/state/session.json``.
    Returns the empty string when nothing is set.
    """
    for key in ("OMX_SESSION_ID", "CODEX_SESSION_ID", "SESSION_ID"):
        raw = os.environ.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()

    p = Path(cwd) / ".omx" / "state" / "session.json"
    if not p.exists():
        return ""
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if isinstance(raw, dict):
        sid = raw.get("session_id")
        if isinstance(sid, str) and sid.strip():
            return sid.strip()
    return ""


def _find_active_teams(cwd: str, leader_session_id: str) -> list[str]:
    """Scan ``<cwd>/.omx/team`` for active teams owned by another session.

    Mirrors TS ``findActiveTeams``. A team is "active" when:

    * Its manifest's ``leader.session_id`` is non-empty and not the
      caller's ``leader_session_id``.
    * The current phase (if any) is not terminal.
    """
    if not leader_session_id:
        # Without a session id, the TS layer treats the caller as
        # session-less and skips conflict detection.
        return []

    teams_root = Path(cwd) / ".omx" / "team"
    if not teams_root.is_dir():
        return []

    active: list[str] = []
    for entry in sorted(teams_root.iterdir()):
        if not entry.is_dir():
            continue
        manifest = read_team_manifest_v2(entry.name, cwd)
        if manifest is None:
            continue
        owner = manifest.leader.session_id if manifest.leader else ""
        if not isinstance(owner, str) or owner.strip() == "":
            continue
        if owner.strip() == leader_session_id:
            continue
        # Read phase state for terminal-skip parity.
        phase_path = entry / "phase.json"
        current_phase: str | None = None
        if phase_path.exists():
            try:
                payload = json.loads(phase_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    raw_phase = payload.get("current_phase")
                    if isinstance(raw_phase, str):
                        current_phase = raw_phase
            except (OSError, json.JSONDecodeError):
                current_phase = None
        if current_phase and current_phase in TERMINAL_PHASES:
            continue
        active.append(entry.name)
    return active


def _read_team_phase_current(team_name: str, cwd: str) -> str | None:
    """Mirror TS ``readTeamPhaseState`` for the preflight."""
    p = Path(cwd) / ".omx" / "team" / team_name / "phase.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    phase = payload.get("current_phase")
    return phase if isinstance(phase, str) else None


def _read_team_config_dict(team_name: str, cwd: str) -> dict[str, Any] | None:
    p = Path(cwd) / ".omx" / "team" / team_name / "config.json"
    if not p.exists():
        return None
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _assert_team_startup_is_non_destructive(
    team_name: str, cwd: str, leader_session_id: str
) -> None:
    """Refuse to clobber an active team's persisted state.

    Mirrors TS ``assertTeamStartupIsNonDestructive`` (runtime.ts:266-293).

    Raises:
        RuntimeError: When a foreign team owns this leader cwd
            (``leader_session_conflict``) or when this team already has
            non-terminal state on disk (``team_name_conflict``).
    """
    active_teams = _find_active_teams(cwd, leader_session_id)
    if active_teams:
        raise RuntimeError(
            f"leader_session_conflict: active team exists ({', '.join(active_teams)})"
        )

    existing_config = _read_team_config_dict(team_name, cwd)
    existing_manifest = read_team_manifest_v2(team_name, cwd)
    current_phase = _read_team_phase_current(team_name, cwd)

    if not existing_config and existing_manifest is None:
        return

    if current_phase and current_phase in TERMINAL_PHASES:
        return

    tmux_session = (
        (existing_config or {}).get("tmux_session")
        or (existing_manifest.tmux_session if existing_manifest else None)
        or f"omx-team-{team_name}"
    )
    rendered_phase = current_phase or "team-exec"
    raise RuntimeError(
        f'team_name_conflict: active team state already exists for "{team_name}" '
        f"(phase: {rendered_phase}, tmux: {tmux_session}). "
        f'Use "omx team status {team_name}", "omx team resume {team_name}", or '
        f'"omx team shutdown {team_name}" instead of launching a duplicate team.'
    )


def _resolve_effective_team_worktree_mode(
    leader_cwd: str, requested_mode: WorktreeMode | None
) -> WorktreeMode:
    """Mirror TS ``resolveEffectiveTeamWorktreeMode``.

    Non-git ``cwd`` always lands on ``WorktreeMode(enabled=False)``.
    Otherwise honour the caller's request, falling back to a probed
    detached default when the planner accepts it.
    """
    if not is_git_repository(leader_cwd):
        return WorktreeMode(enabled=False)

    if requested_mode is not None and requested_mode.enabled:
        return requested_mode

    # Probe whether the default (detached) plan is viable.
    try:
        probe = plan_worktree_target(
            cwd=leader_cwd,
            scope="team",
            mode=WorktreeMode(enabled=True, detached=True, name=None),
            team_name="probe",
            worker_name="worker-1",
        )
        if probe is not None and getattr(probe, "enabled", False):
            return WorktreeMode(enabled=True, detached=True, name=None)
    except Exception:  # noqa: BLE001 - probe failure ⇒ disabled
        return WorktreeMode(enabled=False)

    return WorktreeMode(enabled=False)


def _detect_and_clean_stale_team(
    team_name: str,
    leader_cwd: str,
    worker_count: int,
    confirm_fn: Callable[[StaleTeamSummary], bool] | None,
) -> None:
    """Mirror TS ``detectAndCleanStaleTeam``.

    Removes leftover state when no tmux session matches and no
    worktrees exist; otherwise demands an explicit ``confirm_fn`` to
    proceed.
    """
    state_dir = Path(leader_cwd) / ".omx" / "team" / team_name
    if not state_dir.exists():
        return

    sessions = set(list_team_sessions())
    if f"omx-team-{team_name}" in sessions:
        return

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=leader_cwd,
            capture_output=True,
            text=True,
            check=False,
            encoding="utf-8",
        )
    except (FileNotFoundError, OSError):
        return
    if result.returncode != 0:
        return
    repo_root = (result.stdout or "").strip()
    if not repo_root:
        return

    worktree_paths: list[str] = []
    for i in range(1, worker_count + 1):
        wt_path = (
            Path(repo_root) / ".omx" / "team" / team_name / "worktrees" / f"worker-{i}"
        )
        if wt_path.exists():
            worktree_paths.append(str(wt_path))

    if not worktree_paths:
        cleanup_team_state(leader_cwd, team_name)
        return

    has_dirty = False
    for p in worktree_paths:
        try:
            if is_worktree_dirty(p):
                has_dirty = True
                break
        except Exception:  # noqa: BLE001
            continue

    summary = StaleTeamSummary(
        team_name=team_name,
        worktree_paths=list(worktree_paths),
        state_path=str(state_dir),
        has_dirty_worktrees=has_dirty,
    )

    if confirm_fn is None:
        raise RuntimeError(
            f"stale_team_artifacts:{team_name}:{len(worktree_paths)}_worktrees:"
            "pass_confirmStaleCleanup_or_manually_remove"
        )

    if not confirm_fn(summary):
        raise RuntimeError(
            f"stale_team_cleanup_declined:{team_name}:"
            "manually_remove_worktrees_and_state_before_retrying"
        )

    for wt_path in worktree_paths:
        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", wt_path],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=False,
                encoding="utf-8",
            )
        except (FileNotFoundError, OSError):
            continue
    cleanup_team_state(leader_cwd, team_name)


# --- Recoverable startup issue recorder ----------------------------------


def _record_recoverable_startup_issue(
    *,
    team_name: str,
    worker_name: str,
    task_ids: Sequence[str],
    reason: str,
    cwd: str,
) -> None:
    """Persist a soft startup failure and emit a ``worker_state_changed`` event."""
    try:
        write_worker_status(
            cwd,
            team_name,
            worker_name,
            "unknown",
            current_task_id=task_ids[0] if task_ids else None,
            reason=reason,
        )
    except Exception:  # noqa: BLE001 - best-effort recorder
        pass

    try:
        event = TeamEvent(
            event_type="worker_state_changed",
            timestamp="",  # filled by recorder if your TeamEvent uses one
            worker_id=worker_name,
            task_id=task_ids[0] if task_ids else None,
            detail={
                "state": "unknown",
                "prev_state": "unknown",
                "reason": reason,
            },
        )
        # Populate timestamp lazily for round-trip parity.
        from datetime import datetime, timezone

        event.timestamp = datetime.now(timezone.utc).isoformat()
        append_team_event(cwd, event, team_name)
    except Exception:  # noqa: BLE001 - best-effort recorder
        pass


# --- Worker bootstrap plan dataclass -------------------------------------


@dataclass
class _WorkerBootstrapPlan:
    worker_name: str
    worker_workspace: dict[str, Any]
    worker_tasks: list[TeamTask]
    worker_role: str
    role_prompt_content: str | None
    instructions_file_path: str
    inbox: str
    trigger: str
    trigger_intent: str
    initial_prompt: str | None
    worker_launch_args: list[str]
    worker_cli: str


# --- Dispatch helper -----------------------------------------------------


def _dispatch_startup_inbox(
    *,
    team_name: str,
    worker_name: str,
    worker_index: int,
    pane_id: str | None,
    worker_cli: str,
    inbox: str,
    trigger_message: str,
    intent: str,
    cwd: str,
    worker_launch_mode: str,
) -> DispatchOutcome:
    """Wrap :func:`queue_inbox_instruction` for the startup path.

    The Python ``queue_inbox_instruction`` already persists the worker
    inbox + enqueues a dispatch request. The default notifier reports a
    ``queued_for_hook_dispatch`` outcome (interactive mode) so the
    runtime caller can treat the queue acknowledgement as success for
    the startup gate.
    """

    if worker_launch_mode == "prompt":
        transport_preference = "prompt_stdin"
        fallback_allowed = False
    else:
        transport_preference = "hook_preferred_with_fallback"
        fallback_allowed = True

    def _notify(
        _target: TeamNotifierTarget,
        _message: str,
        _context: dict[str, Any],
    ) -> DispatchOutcome:
        return DispatchOutcome(
            ok=True,
            transport=DispatchTransport.HOOK.value,
            reason="queued_for_hook_dispatch",
        )

    params = QueueInboxParams(
        team_name=team_name,
        worker_name=worker_name,
        worker_index=worker_index,
        inbox=inbox,
        trigger_message=trigger_message,
        cwd=cwd,
        notify=_notify,
        pane_id=pane_id,
        intent=intent,  # type: ignore[arg-type]
        transport_preference=transport_preference,
        fallback_allowed=fallback_allowed,
        inbox_correlation_key=f"startup:{worker_name}",
    )
    return queue_inbox_instruction(params)


# --- Public entry point --------------------------------------------------


def start_team(
    team_name: str,
    task: str,
    agent_type: str,
    worker_count: int,
    tasks: Sequence[dict[str, Any]] | None = None,
    cwd: str | None = None,
    *,
    options: TeamStartOptions | None = None,
) -> TeamRuntime:
    """Start a new team: init state, create tmux session, bootstrap workers.

    Sync port of ``startTeam`` (``src/team/runtime.ts:1990-2582``).

    Args:
        team_name: Caller-supplied team identifier (will be sanitized).
        task: One-line description of the team task.
        agent_type: Default agent role for workers.
        worker_count: Number of workers to spawn.
        tasks: Initial task records. Each entry accepts the keys
            ``subject``, ``description``, ``owner``, ``blocked_by``,
            ``role``.
        cwd: Leader cwd. Required.
        options: Optional :class:`TeamStartOptions`.

    Returns:
        A :class:`TeamRuntime` handle whose ``config`` is the persisted
        config dict and whose ``session_name`` is the resolved tmux
        session selector (``omx-team-<name>``, ``<name>:<window>``, or
        ``prompt-<name>`` for prompt-mode teams).

    Raises:
        RuntimeError: For any preflight, tmux, or worker bootstrap
            failure. Rollback runs first and accumulated cleanup errors
            (if any) are appended to the message.
    """
    if cwd is None:
        raise ValueError("cwd is required")
    options = options if options is not None else TeamStartOptions()
    init_tasks = list(tasks or [])

    leader_cwd = str(Path(cwd).resolve())
    _assert_nested_team_allowed(leader_cwd)

    requested_worktree_mode = (
        WorktreeMode(
            enabled=bool(options.worktree_mode.get("enabled"))
            if options.worktree_mode
            else False,
            detached=bool(options.worktree_mode.get("detached"))
            if options.worktree_mode
            else False,
            name=options.worktree_mode.get("name") if options.worktree_mode else None,
        )
        if isinstance(options.worktree_mode, dict)
        else None
    )
    effective_worktree_mode = _resolve_effective_team_worktree_mode(
        leader_cwd, requested_worktree_mode
    )
    sanitized = sanitize_team_name(team_name)
    leader_session_id = _resolve_leader_session_id(leader_cwd)

    _assert_team_startup_is_non_destructive(sanitized, leader_cwd, leader_session_id)

    worker_launch_mode = _resolve_team_worker_launch_mode(dict(os.environ))
    display_mode = "split_pane" if worker_launch_mode == "interactive" else "auto"
    if worker_launch_mode == "interactive":
        if not is_tmux_available():
            raise RuntimeError(
                "Team mode requires tmux. Install with: apt install tmux / brew install tmux"
            )
        if not has_current_tmux_client_context():
            raise RuntimeError(
                "Team mode requires running inside tmux current leader pane"
            )

    team_state_root = _resolve_canonical_team_state_root(leader_cwd)
    workspace_mode = "worktree" if effective_worktree_mode.enabled else "single"

    worker_workspace_by_name: dict[str, dict[str, Any]] = {}
    provisioned_worktrees: list[EnsureWorktreeResult | WorktreeDisabled] = []
    for i in range(1, worker_count + 1):
        worker_workspace_by_name[f"worker-{i}"] = {"cwd": leader_cwd}

    _detect_and_clean_stale_team(
        sanitized, leader_cwd, worker_count, options.confirm_stale_cleanup
    )

    if effective_worktree_mode.enabled:
        assert_clean_leader_workspace_for_worker_worktrees(leader_cwd)
        for i in range(1, worker_count + 1):
            worker_name = f"worker-{i}"
            planned = plan_worktree_target(
                cwd=leader_cwd,
                scope="team",
                mode=effective_worktree_mode,
                team_name=sanitized,
                worker_name=worker_name,
            )
            ensured = ensure_worktree(planned)
            provisioned_worktrees.append(ensured)
            if getattr(ensured, "enabled", False):
                worker_workspace_by_name[worker_name] = {
                    "cwd": ensured.worktree_path,
                    "worktree_repo_root": ensured.repo_root,
                    "worktree_path": ensured.worktree_path,
                    "worktree_branch": ensured.branch_name,
                    "worktree_detached": ensured.detached,
                    "worktree_created": ensured.created,
                }

    session_name = f"omx-team-{sanitized}"
    overlay = generate_worker_overlay(sanitized)
    worker_instructions_path: str | None = None
    session_created = False
    created_worker_pane_ids: list[str] = []
    created_leader_pane_id: str | None = None
    config: dict[str, Any] | None = None

    shared_worker_launch_args = resolve_team_worker_launch_args(
        ResolveTeamWorkerLaunchArgsOptions(
            existing_raw=os.environ.get("OMX_TEAM_WORKER_LAUNCH_ARGS"),
            fallback_model=resolve_agent_default_model(
                agent_type, os.environ.get("CODEX_HOME")
            ),
        )
    )
    worker_cli_plan = resolve_team_worker_cli_plan(
        worker_count, shared_worker_launch_args, dict(os.environ)
    )

    worker_ready_timeout_ms = _resolve_worker_ready_timeout_ms(dict(os.environ))
    # TODO: wire worker_startup_evidence_timeout_ms into queue_inbox_instruction
    # once QueueInboxParams supports require_worker_startup_evidence (see Phase
    # 2.9c integration handoff).
    startup_dispatch_retries = _resolve_startup_dispatch_retries(dict(os.environ))
    startup_retry_delay_s = _resolve_startup_dispatch_retry_delay_s(dict(os.environ))
    skip_worker_ready_wait = _should_skip_worker_ready_wait(dict(os.environ))

    try:
        # 3. Init state + V2 manifest + initial config.json.
        manifest = init_team_state(
            sanitized,
            task,
            agent_type,
            worker_count,
            leader_cwd,
            env={
                **dict(os.environ),
                "OMX_TEAM_DISPLAY_MODE": display_mode,
                "OMX_TEAM_WORKER_LAUNCH_MODE": worker_launch_mode,
            },
            workspace={
                "leader_cwd": leader_cwd,
                "team_state_root": team_state_root,
                "workspace_mode": workspace_mode,
                "worktree_mode": {
                    "enabled": effective_worktree_mode.enabled,
                    "detached": effective_worktree_mode.detached,
                    "name": effective_worktree_mode.name,
                },
            },
            lifecycle_profile="default",
        )
        if manifest is None:
            raise RuntimeError("failed to initialize team config")

        config = _read_team_config_dict(sanitized, leader_cwd)
        if config is None:
            raise RuntimeError("failed to initialize team config")

        config["leader_cwd"] = leader_cwd
        config["team_state_root"] = team_state_root
        config["workspace_mode"] = workspace_mode
        config["worktree_mode"] = {
            "enabled": effective_worktree_mode.enabled,
            "detached": effective_worktree_mode.detached,
            "name": effective_worktree_mode.name,
        }

        # 4. Create initial tasks.
        for t in init_tasks:
            team_create_task(
                leader_cwd,
                sanitized,
                description=t.get("description", t.get("subject", "")),
                role=t.get("role"),
                file_paths=t.get("file_paths"),
                depends_on=t.get("depends_on") or t.get("blocked_by"),
                status=t.get("status", "pending"),
                owner=t.get("owner"),
            )

        # 5. Compose the worker AGENTS.md instructions file (single-workspace).
        if workspace_mode != "worktree":
            worker_instructions_path = write_team_worker_instructions_file(
                sanitized, leader_cwd, overlay
            )
            _set_team_model_instructions_file(sanitized, worker_instructions_path)

        all_tasks = team_list_tasks(leader_cwd, sanitized)
        worker_bootstrap_plans: list[_WorkerBootstrapPlan] = []

        for i in range(1, worker_count + 1):
            worker_name = f"worker-{i}"
            worker_workspace = worker_workspace_by_name.get(
                worker_name, {"cwd": leader_cwd}
            )
            worker_tasks = [t for t in all_tasks if t.owner == worker_name]

            task_roles = [t.role for t in worker_tasks if t.role]
            unique_task_roles = set(task_roles)
            if task_roles and len(unique_task_roles) == 1:
                worker_role = next(iter(unique_task_roles))
            else:
                worker_role = agent_type
            runtime_role = worker_role

            raw_role_prompt_content = load_role_prompt(
                runtime_role, str(Path(leader_cwd) / ".codex" / "prompts")
            )
            if raw_role_prompt_content is None:
                from omx.utils.paths import codex_prompts_dir

                raw_role_prompt_content = load_role_prompt(
                    runtime_role, codex_prompts_dir()
                )

            preferred_reasoning = resolve_agent_reasoning_effort(
                runtime_role
            ) or resolve_agent_reasoning_effort(agent_type)
            worker_launch_args = resolve_worker_launch_args_from_env(
                dict(os.environ),
                agent_type=runtime_role,
                preferred_reasoning=preferred_reasoning,
            )
            role_prompt_content = raw_role_prompt_content  # TS composeRoleInstructions
            worker_worktree_path = worker_workspace.get("worktree_path")

            fallback_instructions_path = worker_instructions_path or str(
                Path(leader_cwd) / "AGENTS.md"
            )

            if worker_worktree_path:
                opts = WorkerRootAgentsOptions(
                    team_name=sanitized,
                    worker_name=worker_name,
                    worker_role=runtime_role,
                    role_prompt_content=role_prompt_content or "",
                    team_state_root=team_state_root,
                    leader_cwd=leader_cwd,
                    worktree_path=worker_worktree_path,
                )
                instructions_file_path = write_worker_worktree_root_agents_file(opts)
            elif role_prompt_content:
                instructions_file_path = write_worker_role_instructions_file(
                    sanitized,
                    worker_name,
                    leader_cwd,
                    fallback_instructions_path,
                    runtime_role,
                    role_prompt_content,
                )
            else:
                instructions_file_path = fallback_instructions_path

            inbox = generate_initial_inbox(
                worker_name,
                sanitized,
                agent_type,
                worker_tasks,
                team_state_root=team_state_root,
                leader_cwd=leader_cwd,
                worker_role=runtime_role,
                role_prompt_content=raw_role_prompt_content,
                worktree_root_agents_canonical=bool(worker_worktree_path),
            )

            trigger_state_root = (
                _resolve_instruction_state_root(worker_worktree_path) or ".omx/state"
            )
            trigger_directive = build_trigger_directive(
                worker_name, sanitized, trigger_state_root
            )
            trigger = trigger_directive.text

            initial_prompt = trigger if worker_cli_plan[i - 1] == "gemini" else None
            if initial_prompt:
                write_worker_inbox(leader_cwd, sanitized, worker_name, inbox)

            worker_bootstrap_plans.append(
                _WorkerBootstrapPlan(
                    worker_name=worker_name,
                    worker_workspace=worker_workspace,
                    worker_tasks=worker_tasks,
                    worker_role=worker_role,
                    role_prompt_content=role_prompt_content,
                    instructions_file_path=instructions_file_path,
                    inbox=inbox,
                    trigger=trigger,
                    trigger_intent=trigger_directive.intent,
                    initial_prompt=initial_prompt,
                    worker_launch_args=worker_launch_args,
                    worker_cli=worker_cli_plan[i - 1],
                )
            )

        # Build per-worker startup specs.
        worker_startups: list[dict[str, Any]] = []
        for plan in worker_bootstrap_plans:
            env: dict[str, str] = {
                TEAM_STATE_ROOT_ENV: team_state_root,
                TEAM_LEADER_CWD_ENV: leader_cwd,
                MODEL_INSTRUCTIONS_FILE_ENV: plan.instructions_file_path,
            }
            ww = plan.worker_workspace
            if ww.get("worktree_path"):
                env["OMX_TEAM_WORKTREE_PATH"] = ww["worktree_path"]
            if ww.get("worktree_branch"):
                env["OMX_TEAM_WORKTREE_BRANCH"] = ww["worktree_branch"]
            if isinstance(ww.get("worktree_detached"), bool):
                env["OMX_TEAM_WORKTREE_DETACHED"] = (
                    "1" if ww["worktree_detached"] else "0"
                )
            worker_startups.append(
                {
                    "cwd": ww.get("cwd"),
                    "env": env,
                    "initial_prompt": plan.initial_prompt,
                    "launch_args": plan.worker_launch_args,
                    "worker_cli": plan.worker_cli,
                    "worker_role": plan.worker_role,
                }
            )

        worker_pane_ids: list[str | None] = [None] * worker_count

        # 6. Create runtime (interactive panes or prompt-mode children).
        cleanup_team_worker_launch_orphaned_mcp_processes(
            cleanup=options.cleanup_launch_orphaned_mcp_processes,
            write_warning=options.write_cleanup_warning,
        )
        if worker_launch_mode == "interactive":
            created_session: _TmuxTeamSession = create_team_session(
                sanitized,
                worker_count,
                leader_cwd,
                shared_worker_launch_args,
                worker_startups,
            )
            session_name = created_session.name
            session_created = True
            created_worker_pane_ids.extend(
                [pid for pid in created_session.worker_pane_ids if pid is not None]
            )
            created_leader_pane_id = created_session.leader_pane_id
            apply_created_interactive_session_to_config(
                config, created_session, worker_pane_ids
            )
        else:
            # Prompt-mode child-process spawn. TS uses ``spawnPromptWorker`` to
            # launch ``codex``/``claude``/``gemini`` directly via subprocess.
            # The Python port keeps the loop shape (PID -> config.workers[*].pid)
            # so the rollback path stays symmetric; the actual child-process
            # spawn is delegated to a small helper that's easy to monkeypatch
            # from tests.
            config["tmux_session"] = f"prompt-{sanitized}"
            config["leader_pane_id"] = None
            config["hud_pane_id"] = None
            config["resize_hook_name"] = None
            config["resize_hook_target"] = None
            for i in range(1, worker_count + 1):
                startup = worker_startups[i - 1] if i - 1 < len(worker_startups) else {}
                pid = _spawn_prompt_worker(
                    sanitized,
                    f"worker-{i}",
                    i,
                    startup.get("cwd") or leader_cwd,
                    startup.get("launch_args") or shared_worker_launch_args,
                    startup.get("env") or {},
                    startup.get("worker_cli") or worker_cli_plan[i - 1],
                    startup.get("initial_prompt"),
                    startup.get("worker_role"),
                )
                if i - 1 < len(config.get("workers", [])):
                    config["workers"][i - 1]["pid"] = pid

        # Materialize durable startup state for every worker.
        for i in range(1, worker_count + 1):
            plan = worker_bootstrap_plans[i - 1]
            _materialize_worker_startup_state(
                team_name=sanitized,
                bootstrap_plan=plan,
                worker_index=i,
                pane_id=worker_pane_ids[i - 1],
                worker_launch_mode=worker_launch_mode,
                session_name=session_name,
                config=config,
                team_state_root=team_state_root,
                leader_cwd=leader_cwd,
            )
        write_team_config(leader_cwd, config, sanitized)

        # 7. Per-worker readiness wait + critical inbox dispatch with retries.
        for i in range(1, worker_count + 1):
            plan = worker_bootstrap_plans[i - 1]
            worker_name = plan.worker_name
            pane_id = worker_pane_ids[i - 1]
            worker_tasks = plan.worker_tasks
            inbox = plan.inbox
            trigger = plan.trigger
            trigger_intent = plan.trigger_intent
            initial_prompt = plan.initial_prompt

            mixed = [t.role for t in worker_tasks if t.role]
            if mixed and len(set(mixed)) > 1:
                sys.stderr.write(
                    f"[omx:team] {worker_name}: mixed task roles "
                    f"[{', '.join(sorted(set(mixed)))}], falling back to {agent_type}\n"
                )

            if (
                worker_launch_mode == "interactive"
                and not skip_worker_ready_wait
                and not initial_prompt
            ):
                ready = wait_for_worker_ready(
                    session_name, i, worker_ready_timeout_ms, pane_id
                )
                if not ready:
                    worker_alive = is_worker_pane_open(session_name, i, pane_id)
                    if worker_alive:
                        _record_recoverable_startup_issue(
                            team_name=sanitized,
                            worker_name=worker_name,
                            task_ids=[t.task_id for t in worker_tasks],
                            reason="ready_prompt_timeout",
                            cwd=leader_cwd,
                        )
                        continue
                    raise RuntimeError(
                        f"Worker {worker_name} did not become ready in tmux session {session_name}"
                    )

            if initial_prompt:
                dispatch_outcome = DispatchOutcome(
                    ok=True,
                    transport=DispatchTransport.NONE.value,
                    reason="startup_prompt_delivered_at_launch",
                )
            else:
                dispatch_outcome = DispatchOutcome(
                    ok=False,
                    transport=DispatchTransport.NONE.value,
                    reason="not_attempted",
                )
                for attempt in range(1, startup_dispatch_retries + 1):
                    dispatch_outcome = _dispatch_startup_inbox(
                        team_name=sanitized,
                        worker_name=worker_name,
                        worker_index=i,
                        pane_id=pane_id,
                        worker_cli=plan.worker_cli,
                        inbox=inbox,
                        trigger_message=trigger,
                        intent=trigger_intent,
                        cwd=leader_cwd,
                        worker_launch_mode=worker_launch_mode,
                    )
                    if dispatch_outcome.ok:
                        break
                    if attempt < startup_dispatch_retries:
                        if worker_launch_mode == "interactive" and pane_id:
                            if dismiss_trust_prompt_if_present(
                                session_name, i, pane_id
                            ):
                                wait_for_worker_ready(
                                    session_name, i, worker_ready_timeout_ms, pane_id
                                )
                            else:
                                time.sleep(startup_retry_delay_s)
                        else:
                            time.sleep(startup_retry_delay_s)

            if not dispatch_outcome.ok:
                if worker_launch_mode == "prompt":
                    worker_alive = i - 1 < len(config.get("workers", [])) and bool(
                        config["workers"][i - 1].get("pid")
                    )
                else:
                    worker_alive = is_worker_pane_open(session_name, i, pane_id)

                if (
                    worker_launch_mode == "interactive"
                    and worker_alive
                    and _is_recoverable_interactive_startup_reason(
                        dispatch_outcome.reason
                    )
                ):
                    _record_recoverable_startup_issue(
                        team_name=sanitized,
                        worker_name=worker_name,
                        task_ids=[t.task_id for t in worker_tasks],
                        reason=dispatch_outcome.reason,
                        cwd=leader_cwd,
                    )
                    continue
                raise RuntimeError(
                    f"worker_notify_failed:{worker_name}:{dispatch_outcome.reason}"
                )

        write_team_config(leader_cwd, config, sanitized)

        return TeamRuntime(
            team_name=sanitized,
            sanitized_name=sanitized,
            session_name=session_name,
            config=config,
            cwd=leader_cwd,
        )

    except BaseException as error:
        rollback_errors: list[str] = []

        if session_created:
            if (
                config
                and config.get("resize_hook_name")
                and config.get("resize_hook_target")
            ):
                try:
                    unregistered = unregister_resize_hook(
                        config["resize_hook_target"], config["resize_hook_name"]
                    )
                    if not unregistered:
                        rollback_errors.append("unregister_resize_hook: returned false")
                except Exception as cleanup_error:  # noqa: BLE001
                    rollback_errors.append(f"unregister_resize_hook: {cleanup_error}")

            if config:
                config["resize_hook_name"] = None
                config["resize_hook_target"] = None
                try:
                    write_team_config(leader_cwd, config, sanitized)
                except Exception as cleanup_error:  # noqa: BLE001
                    rollback_errors.append(
                        f"save_team_config(clear resize hook): {cleanup_error}"
                    )

            if ":" in session_name:
                for index, paneId in enumerate(created_worker_pane_ids):
                    try:
                        kill_worker_by_pane_id(paneId, created_leader_pane_id)
                    except Exception as cleanup_error:  # noqa: BLE001
                        rollback_errors.append(
                            f"kill_worker_by_pane_id({paneId}): {cleanup_error}"
                        )
                hud_pane_id = (config or {}).get("hud_pane_id")
                if hud_pane_id:
                    try:
                        kill_worker_by_pane_id(hud_pane_id, created_leader_pane_id)
                    except Exception as cleanup_error:  # noqa: BLE001
                        rollback_errors.append(
                            f"kill_worker_by_pane_id(hud): {cleanup_error}"
                        )
            else:
                try:
                    destroy_team_session(session_name)
                except Exception as cleanup_error:  # noqa: BLE001
                    rollback_errors.append(f"destroy_team_session: {cleanup_error}")

        if config:
            for worker in config.get("workers", []):
                worktree_path = worker.get("worktree_path")
                team_state_root_field = worker.get("team_state_root") or team_state_root
                if not worktree_path or not team_state_root_field:
                    continue
                try:
                    remove_worker_worktree_root_agents_file(
                        sanitized,
                        worker["name"],
                        team_state_root_field,
                        worktree_path,
                    )
                except Exception as cleanup_error:  # noqa: BLE001
                    rollback_errors.append(
                        f"remove_worker_worktree_root_agents_file({worker['name']}): "
                        f"{cleanup_error}"
                    )

        if worker_instructions_path:
            try:
                remove_team_worker_instructions_file(sanitized, leader_cwd)
            except Exception as cleanup_error:  # noqa: BLE001
                rollback_errors.append(
                    f"remove_team_worker_instructions_file: {cleanup_error}"
                )

        _restore_team_model_instructions_file(sanitized)

        try:
            cleanup_team_state(leader_cwd, sanitized)
        except Exception as cleanup_error:  # noqa: BLE001
            rollback_errors.append(f"cleanup_team_state: {cleanup_error}")

        if provisioned_worktrees:
            try:
                rollback_provisioned_worktrees(
                    provisioned_worktrees,
                    RollbackWorktreeOptions(skip_branch_deletion=False),
                )
            except Exception as cleanup_error:  # noqa: BLE001
                rollback_errors.append(
                    f"rollback_provisioned_worktrees: {cleanup_error}"
                )

        if rollback_errors:
            base_msg = str(error) if error.args else error.__class__.__name__
            raise RuntimeError(
                f"{base_msg}; rollback encountered errors: "
                f"{' | '.join(rollback_errors)}"
            ) from error

        raise


def _materialize_worker_startup_state(
    *,
    team_name: str,
    bootstrap_plan: _WorkerBootstrapPlan,
    worker_index: int,
    pane_id: str | None,
    worker_launch_mode: str,
    session_name: str,
    config: dict[str, Any],
    team_state_root: str,
    leader_cwd: str,
) -> None:
    """Persist worker identity + inbox + config-side metadata.

    Sync port of ``materializeWorkerStartupState`` defined inline in TS
    ``startTeam`` (runtime.ts:2256-2303).
    """
    ww = bootstrap_plan.worker_workspace
    identity: dict[str, Any] = {
        "name": bootstrap_plan.worker_name,
        "index": worker_index,
        "role": bootstrap_plan.worker_role,
        "worker_cli": bootstrap_plan.worker_cli,
        "assigned_tasks": [t.task_id for t in bootstrap_plan.worker_tasks],
        "working_dir": ww.get("cwd"),
        "worktree_repo_root": ww.get("worktree_repo_root"),
        "worktree_path": ww.get("worktree_path"),
        "worktree_branch": ww.get("worktree_branch"),
        "worktree_detached": ww.get("worktree_detached"),
        "worktree_created": ww.get("worktree_created"),
        "team_state_root": team_state_root,
    }

    if worker_launch_mode == "interactive":
        if pane_id:
            try:
                pane_pid = get_worker_pane_pid(session_name, worker_index, pane_id)
            except Exception:  # noqa: BLE001
                pane_pid = None
            if pane_pid:
                identity["pid"] = pane_pid
    else:
        workers_cfg = config.get("workers", [])
        if worker_index - 1 < len(workers_cfg) and workers_cfg[worker_index - 1].get(
            "pid"
        ):
            identity["pid"] = workers_cfg[worker_index - 1]["pid"]

    if pane_id:
        identity["pane_id"] = pane_id

    workers_cfg = config.get("workers", [])
    if worker_index - 1 < len(workers_cfg):
        wcfg = workers_cfg[worker_index - 1]
        wcfg["pid"] = identity.get("pid")
        wcfg["pane_id"] = pane_id
        wcfg["role"] = bootstrap_plan.worker_role
        wcfg["worker_cli"] = bootstrap_plan.worker_cli
        wcfg["assigned_tasks"] = [t.task_id for t in bootstrap_plan.worker_tasks]
        wcfg["working_dir"] = ww.get("cwd")
        wcfg["worktree_repo_root"] = ww.get("worktree_repo_root")
        wcfg["worktree_path"] = ww.get("worktree_path")
        wcfg["worktree_branch"] = ww.get("worktree_branch")
        wcfg["worktree_detached"] = ww.get("worktree_detached")
        wcfg["worktree_created"] = ww.get("worktree_created")
        wcfg["team_state_root"] = team_state_root

    write_worker_identity(leader_cwd, team_name, bootstrap_plan.worker_name, identity)
    write_worker_inbox(
        leader_cwd, team_name, bootstrap_plan.worker_name, bootstrap_plan.inbox
    )


def _spawn_prompt_worker(
    team_name: str,
    worker_name: str,
    worker_index: int,
    worker_cwd: str,
    launch_args: list[str],
    worker_env: dict[str, str],
    worker_cli: str,
    initial_prompt: str | None,
    worker_role: str | None,
) -> int | None:
    """Spawn a prompt-mode worker child process.

    Returns the child PID, or ``None`` on failure. Tests monkeypatch
    this helper so the runtime path stays exercised without launching a
    real ``codex``/``claude``/``gemini`` process.

    Sync port of ``spawnPromptWorker`` (runtime.ts:1879-1912). The
    Python implementation defers to :func:`subprocess.Popen` with stdin
    piped so the caller may later feed an ``initial_prompt``; here we
    only spawn and return the PID. Full prompt-mode I/O wiring is
    deferred to a follow-up phase.
    """
    try:
        from omx.team.tmux_session import build_worker_process_launch_spec

        spec = build_worker_process_launch_spec(
            team_name=team_name,
            worker_index=worker_index,
            launch_args=launch_args,
            cwd=worker_cwd,
            extra_env=worker_env,
            worker_cli_override=worker_cli,
            initial_prompt=initial_prompt,
            worker_role=worker_role,
        )
        env = {**os.environ, **spec.env}
        proc = subprocess.Popen(
            [spec.command, *spec.args],
            cwd=worker_cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc.pid
    except Exception:  # noqa: BLE001 - tests monkeypatch this helper
        return None


__all__ = [
    "DEFAULT_MAX_WORKERS",
    "MODEL_INSTRUCTIONS_FILE_ENV",
    "STARTUP_DISPATCH_RETRIES",
    "STARTUP_DISPATCH_RETRY_DELAY_S",
    "TEAM_LEADER_CWD_ENV",
    "TEAM_STATE_ROOT_ENV",
    "TERMINAL_PHASES",
    "TeamRuntime",
    "TeamStartOptions",
    "start_team",
]
