"""Dynamic worker scaling for team mode.

Port of ``src/team/scaling.ts`` — Phase 3a: scaleUp (mid-session worker
addition for a running team).

Sync, stdlib-only port per Phase 2 locked decisions. The TS module's
``async``/``await`` flow collapses to blocking calls; ``execFileSync`` /
``spawnSync`` map directly to :func:`subprocess.run`.

Preserves the existing Python-side heuristic helpers
(:func:`evaluate_scaling`, :func:`resolve_max_workers`) which can be
wired into a future auto-scaler that calls :func:`scale_up` /
``scale_down``.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from omx.agents.native_config import compose_role_instructions_for_role
from omx.team.mcp_comm import (
    DispatchOutcome,
    DispatchTransport,
    QueueInboxParams,
    TeamNotifierTarget,
    queue_inbox_instruction,
    wait_for_dispatch_receipt,
)
from omx.team.model_contract import (
    parse_team_worker_launch_args,
    resolve_agent_default_model,
    resolve_agent_reasoning_effort,
    resolve_team_worker_launch_args,
    ResolveTeamWorkerLaunchArgsOptions,
    TeamReasoningEffort,
)
from omx.team.role_router import load_role_prompt
from omx.team.state.types import DEFAULT_MAX_WORKERS, ABSOLUTE_MAX_WORKERS
from omx.team.state_root import team_dir
from omx.team.team_ops import (
    team_append_event,
    team_create_task,
    team_list_tasks,
    team_normalize_policy,
    team_read_config,
    team_read_manifest,
    team_save_config,
    team_with_scaling_lock,
    team_write_worker_identity,
)
from omx.team.tmux_session import (
    build_worker_startup_command,
    dismiss_trust_prompt_if_present,
    get_worker_pane_pid,
    is_tmux_available,
    resolve_team_worker_cli_plan,
    sanitize_team_name,
    send_to_worker,
    wait_for_worker_ready,
)
from omx.team.worker_bootstrap import (
    WorkerRootAgentsOptions,
    build_trigger_directive,
    generate_initial_inbox,
    remove_worker_worktree_root_agents_file,
    write_worker_role_instructions_file,
    write_worker_worktree_root_agents_file,
)
from omx.team.worktree import (
    EnsureWorktreeResult,
    WorktreeMode,
    WorktreePlanInput,
    ensure_worktree,
    plan_worktree_target,
)
from omx.utils.paths import codex_prompts_dir


__all__ = [
    # Existing Python-side heuristics (preserved).
    "ScalingDecision",
    "evaluate_scaling",
    "resolve_max_workers",
    # New TS-parity surface.
    "OMX_TEAM_SCALING_ENABLED_ENV",
    "is_scaling_enabled",
    "assert_scaling_enabled",
    "ScaleUpResult",
    "ScaleDownResult",
    "ScaleError",
    "scale_up",
]


# ---------------------------------------------------------------------------
# Existing Python-side heuristic helpers (preserved).
# ---------------------------------------------------------------------------


@dataclass
class ScalingDecision:
    """A decision to scale the team up or down."""

    action: str  # "scale_up", "scale_down", "no_change"
    target_count: int
    reason: str


def evaluate_scaling(
    current_workers: int,
    pending_tasks: int,
    idle_workers: int,
    dead_workers: int,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> ScalingDecision:
    """Evaluate whether the team should scale up or down.

    Args:
        current_workers: Current number of active workers.
        pending_tasks: Number of pending tasks.
        idle_workers: Number of idle workers.
        dead_workers: Number of dead workers.
        max_workers: Maximum allowed workers.

    Returns:
        Scaling decision with action and target count.
    """
    max_workers = min(max_workers, ABSOLUTE_MAX_WORKERS)

    # Replace dead workers
    if dead_workers > 0:
        target = current_workers  # maintain count (dead ones will be replaced)
        return ScalingDecision(
            action="scale_up",
            target_count=min(target, max_workers),
            reason=f"{dead_workers} dead worker(s) need replacement",
        )

    # Scale up if tasks waiting and no idle workers
    if pending_tasks > 0 and idle_workers == 0 and current_workers < max_workers:
        scale_by = min(
            pending_tasks, max_workers - current_workers, 3
        )  # max 3 at a time
        return ScalingDecision(
            action="scale_up",
            target_count=current_workers + scale_by,
            reason=f"{pending_tasks} pending tasks, no idle workers",
        )

    # Scale down if too many idle workers and no pending work
    if idle_workers > 1 and pending_tasks == 0 and current_workers > 2:
        target = max(2, current_workers - (idle_workers - 1))
        return ScalingDecision(
            action="scale_down",
            target_count=target,
            reason=f"{idle_workers} idle workers, no pending tasks",
        )

    return ScalingDecision(
        action="no_change",
        target_count=current_workers,
        reason="workload balanced",
    )


def resolve_max_workers() -> int:
    """Resolve the maximum worker count from environment or default."""
    env_val = os.environ.get("OMX_TEAM_MAX_WORKERS", "").strip()
    if env_val:
        try:
            val = int(env_val)
            return min(max(1, val), ABSOLUTE_MAX_WORKERS)
        except ValueError:
            pass
    return DEFAULT_MAX_WORKERS


# ---------------------------------------------------------------------------
# TS parity — environment gate.
# ---------------------------------------------------------------------------


OMX_TEAM_SCALING_ENABLED_ENV = "OMX_TEAM_SCALING_ENABLED"
WORKTREE_TRIGGER_STATE_ROOT = "$OMX_TEAM_STATE_ROOT"

_ENABLED_TRUTHY = frozenset({"1", "true", "yes", "on", "enabled"})


def is_scaling_enabled(env: dict[str, str] | None = None) -> bool:
    """Return True when ``OMX_TEAM_SCALING_ENABLED`` is set to a truthy value.

    Mirrors TS ``isScalingEnabled``. Accepts ``1``/``true``/``yes``/``on``/
    ``enabled`` (case-insensitive); anything else is treated as disabled.
    """
    eff_env = env if env is not None else os.environ
    raw = eff_env.get(OMX_TEAM_SCALING_ENABLED_ENV)
    if not raw:
        return False
    normalized = raw.strip().lower()
    return normalized in _ENABLED_TRUTHY


def assert_scaling_enabled(env: dict[str, str] | None = None) -> None:
    """Raise :class:`RuntimeError` when dynamic scaling is disabled.

    Mirrors TS ``assertScalingEnabled``.
    """
    if not is_scaling_enabled(env):
        raise RuntimeError(
            f"Dynamic scaling is disabled. "
            f"Set {OMX_TEAM_SCALING_ENABLED_ENV}=1 to enable."
        )


# ---------------------------------------------------------------------------
# TS parity — result types.
# ---------------------------------------------------------------------------


@dataclass
class ScaleUpResult:
    """Successful scale-up outcome (TS parity)."""

    added_workers: list[dict[str, Any]]
    new_worker_count: int
    next_worker_index: int
    ok: bool = True


@dataclass
class ScaleDownResult:
    """Successful scale-down outcome (TS parity)."""

    removed_workers: list[str]
    new_worker_count: int
    ok: bool = True


@dataclass
class ScaleError:
    """Error outcome shared by scale_up / scale_down (TS parity)."""

    error: str
    ok: bool = False


# ---------------------------------------------------------------------------
# Helpers — legacy worktree-mode resolution, dispatch fallback.
# ---------------------------------------------------------------------------


_WORKER_BRANCH_RE = re.compile(r"^(.*)/worker-\d+$")


def _resolve_instruction_state_root(worktree_path: str | None) -> str | None:
    """Mirror TS ``resolveInstructionStateRoot``."""
    if worktree_path:
        return WORKTREE_TRIGGER_STATE_ROOT
    return None


def _resolve_legacy_scaled_team_worktree_mode(config: dict[str, Any]) -> WorktreeMode:
    """Reconstruct the worktree mode for a team without ``worktree_mode`` set.

    Mirrors TS ``resolveLegacyScaledTeamWorktreeMode``. Raises with a
    well-known ``scale_up_missing_team_worktree_contract:<team>`` message
    when the workers don't carry the metadata needed to infer the prior
    mode.
    """
    if config.get("worktree_mode"):
        wm = config["worktree_mode"]
        return WorktreeMode(
            enabled=bool(wm.get("enabled")),
            detached=bool(wm.get("detached")),
            name=wm.get("name"),
        )
    if config.get("workspace_mode") != "worktree":
        return WorktreeMode(enabled=False)

    workers = config.get("workers") or []
    workers_with_metadata = [
        w
        for w in workers
        if isinstance(w, dict)
        and (
            w.get("worktree_path")
            or w.get("worktree_branch")
            or isinstance(w.get("worktree_detached"), bool)
        )
    ]
    if not workers_with_metadata:
        raise RuntimeError(
            f"scale_up_missing_team_worktree_contract:{config.get('name', '')}"
        )

    if any(w.get("worktree_detached") is True for w in workers_with_metadata):
        return WorktreeMode(enabled=True, detached=True, name=None)

    branch_prefixes: set[str] = set()
    for w in workers_with_metadata:
        branch = w.get("worktree_branch")
        if not isinstance(branch, str) or not branch.strip():
            continue
        match = _WORKER_BRANCH_RE.match(branch.strip())
        if match:
            prefix = match.group(1).strip()
            if prefix:
                branch_prefixes.add(prefix)

    if len(branch_prefixes) == 1:
        return WorktreeMode(
            enabled=True, detached=False, name=next(iter(branch_prefixes))
        )

    raise RuntimeError(
        f"scale_up_missing_team_worktree_contract:{config.get('name', '')}"
    )


def _resolve_scale_up_worktree_mode(config: dict[str, Any]) -> WorktreeMode:
    """Mirror TS ``resolveScaleUpWorktreeMode``."""
    if config.get("workspace_mode") != "worktree":
        return WorktreeMode(enabled=False)
    try:
        return _resolve_legacy_scaled_team_worktree_mode(config)
    except RuntimeError as err:
        marker = f"scale_up_missing_team_worktree_contract:{config.get('name', '')}"
        if str(err) == marker:
            return WorktreeMode(enabled=True, detached=True, name=None)
        raise


def _notify_worker_pane_outcome(
    session_name: str,
    worker_index: int,
    message: str,
    pane_id: str | None,
    worker_cli: str | None,
) -> DispatchOutcome:
    """Mirror TS ``notifyWorkerPaneOutcome``.

    Wraps :func:`send_to_worker` with the canonical
    ``tmux_send_keys`` outcome shape.
    """
    try:
        send_to_worker(session_name, worker_index, message, pane_id, worker_cli)
        return DispatchOutcome(
            ok=True,
            transport=DispatchTransport.TMUX_SEND_KEYS.value,
            reason="tmux_send_keys_sent",
        )
    except Exception as err:  # noqa: BLE001 - mirror TS catch-all
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.TMUX_SEND_KEYS.value,
            reason=f"tmux_send_keys_failed:{err}",
        )


# ---------------------------------------------------------------------------
# Env-knob helpers (mirror TS).
# ---------------------------------------------------------------------------


def _resolve_worker_ready_timeout_ms(env: dict[str, str]) -> int:
    raw = env.get("OMX_TEAM_READY_TIMEOUT_MS")
    try:
        parsed = int(str(raw or "").strip())
    except (TypeError, ValueError):
        return 45_000
    if parsed >= 5_000:
        return parsed
    return 45_000


def _resolve_worker_launch_args_for_scaling(
    env: dict[str, str],
    agent_type: str,
    preferred_reasoning: TeamReasoningEffort | None = None,
) -> list[str]:
    """Mirror TS ``resolveWorkerLaunchArgsForScaling``."""
    inherited_args: list[str] = []
    fallback_model = resolve_agent_default_model(agent_type, env.get("CODEX_HOME"))
    return resolve_team_worker_launch_args(
        ResolveTeamWorkerLaunchArgsOptions(
            existing_raw=env.get("OMX_TEAM_WORKER_LAUNCH_ARGS"),
            inherited_args=inherited_args,
            fallback_model=fallback_model,
            preferred_reasoning=preferred_reasoning,
        )
    )


# ---------------------------------------------------------------------------
# scale_up — public API.
# ---------------------------------------------------------------------------


def _resolve_canonical_team_state_root(leader_cwd: str) -> str:
    """Mirror TS ``resolveCanonicalTeamStateRoot`` (local copy).

    The canonical layout is ``<leader_cwd>/.omx/state``.
    """
    return str(Path(leader_cwd).resolve() / ".omx" / "state")


def _kill_pane(pane_id: str | None) -> None:
    """Best-effort ``tmux kill-pane -t <pane_id>``; never raises."""
    if not pane_id:
        return
    try:
        subprocess.run(
            ["tmux", "kill-pane", "-t", pane_id],
            capture_output=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        pass


def _delete_task_file(team_state_dir: Path, task_id: str) -> None:
    """Best-effort task-file deletion under
    ``.omx/team/<team>/tasks/task-<id>.json``.
    """
    candidate = team_state_dir / "tasks" / f"task-{task_id}.json"
    try:
        candidate.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def scale_up(
    team_name: str,
    count: int,
    agent_type: str,
    tasks: Sequence[dict[str, Any]],
    cwd: str,
    env: dict[str, str] | None = None,
) -> ScaleUpResult | ScaleError:
    """Add ``count`` workers to a running team mid-session.

    Sync port of TS ``scaleUp`` (``src/team/scaling.ts:194-620``).

    The flow mirrors the TS implementation step-for-step:

    1. Assert :func:`is_scaling_enabled`.
    2. Validate ``count`` and tmux availability.
    3. Acquire the team scaling lock.
    4. Read the team config + V2 manifest; verify
       ``current_count + count <= max_workers``.
    5. Resolve the effective worktree mode (legacy fallback when the
       config does not already pin one).
    6. Persist incoming tasks first (matches ``startTeam`` so worker
       roles can resolve from canonical task state).
    7. For each new worker: assign a monotonic ``next_worker_index``,
       ensure a worktree (when enabled), create the tmux pane via
       ``tmux split-window``, bootstrap the worker, then dispatch the
       initial trigger directive.
    8. On any failure: roll back panes, AGENTS.md installs, created
       tasks, ``next_worker_index``, and the config ``workers`` list.
    9. Append a ``team_leader_nudge`` event on success.

    Args:
        team_name: Caller-supplied team identifier (will be sanitized).
        count: Number of workers to add. Must be a positive integer.
        agent_type: Default agent role for newly added workers.
        tasks: Task records to add ahead of bootstrap. Each entry accepts
            ``subject``, ``description``, ``owner``, ``blocked_by``,
            ``role``.
        cwd: Leader cwd (canonical worktree root).
        env: Environment mapping (defaults to ``os.environ``).

    Returns:
        :class:`ScaleUpResult` on success or :class:`ScaleError` on
        failure. Disabled gate or invalid ``count`` raise
        :class:`RuntimeError`/return :class:`ScaleError` respectively.
    """
    eff_env: dict[str, str] = dict(env) if env is not None else dict(os.environ)

    assert_scaling_enabled(eff_env)

    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return ScaleError(error=f"count must be a positive integer (got {count})")

    if not is_tmux_available():
        return ScaleError(error="tmux is not available")

    sanitized = sanitize_team_name(team_name)
    leader_cwd = str(Path(cwd).resolve())

    with team_with_scaling_lock(sanitized, leader_cwd):
        return _scale_up_locked(
            sanitized,
            leader_cwd,
            count,
            agent_type,
            list(tasks),
            eff_env,
        )


def _scale_up_locked(
    sanitized: str,
    leader_cwd: str,
    count: int,
    agent_type: str,
    tasks: list[dict[str, Any]],
    env: dict[str, str],
) -> ScaleUpResult | ScaleError:
    """Locked body of :func:`scale_up`. Separate function so that the
    surrounding scaling-lock context manager remains tiny.
    """
    config = team_read_config(leader_cwd, sanitized)
    if not config:
        return ScaleError(error=f"Team {sanitized} not found")

    max_workers = int(config.get("max_workers") or DEFAULT_MAX_WORKERS)
    workers_list: list[dict[str, Any]] = list(config.get("workers") or [])
    current_count = len(workers_list)

    if current_count + count > max_workers:
        return ScaleError(
            error=(
                f"Cannot add {count} workers: would exceed max_workers "
                f"({current_count} + {count} > {max_workers})"
            )
        )

    team_state_root = config.get(
        "team_state_root"
    ) or _resolve_canonical_team_state_root(leader_cwd)
    config["team_state_root"] = team_state_root
    session_name = config.get("tmux_session") or ""

    manifest = team_read_manifest(sanitized, leader_cwd)
    raw_policy = None
    if manifest is not None:
        raw_policy = getattr(manifest, "policy", None)
        if hasattr(raw_policy, "to_dict"):
            raw_policy = raw_policy.to_dict()
    raw_display_mode = (
        raw_policy.get("display_mode") if isinstance(raw_policy, dict) else None
    )
    dispatch_policy = team_normalize_policy(
        raw_policy if isinstance(raw_policy, dict) else None,
        {
            "display_mode": "split_pane"
            if raw_display_mode == "split_pane"
            else "auto",
            "worker_launch_mode": config.get("worker_launch_mode", "interactive"),
        },
    )

    try:
        effective_worktree_mode = (
            _resolve_legacy_scaled_team_worktree_mode(config)
            if config.get("worktree_mode") is None
            else _resolve_scale_up_worktree_mode(config)
        )
    except RuntimeError as err:
        # Match TS: legacy resolver raises only when workers lack metadata.
        return ScaleError(error=str(err))

    if config.get("worktree_mode") is None and effective_worktree_mode.enabled:
        config["worktree_mode"] = {
            "enabled": effective_worktree_mode.enabled,
            "detached": effective_worktree_mode.detached,
            "name": effective_worktree_mode.name,
        }
        team_save_config(leader_cwd, config, sanitized)

    # Monotonic worker index. TS falls back to current_count+1 when the
    # config does not have a next_worker_index recorded.
    next_index = int(config.get("next_worker_index") or (current_count + 1))
    initial_next_index = next_index

    added_workers: list[dict[str, Any]] = []
    created_task_ids: list[str] = []
    team_state_dir = team_dir(sanitized, leader_cwd)

    def _rollback(
        error: str,
        *,
        pane_id: str | None = None,
        worker_name: str | None = None,
        worktree_path: str | None = None,
    ) -> ScaleError:
        # Strip workers we added; kill their panes; remove their worktree
        # AGENTS.md install.
        for w in added_workers:
            w_name = w.get("name")
            for i, existing in enumerate(workers_list):
                if existing.get("name") == w_name:
                    workers_list.pop(i)
                    break
            _kill_pane(w.get("pane_id"))
            wt_path = w.get("worktree_path")
            if wt_path:
                try:
                    remove_worker_worktree_root_agents_file(
                        sanitized, w_name, team_state_root, wt_path
                    )
                except Exception:  # noqa: BLE001 - best-effort
                    pass

        # The in-flight worker (mid-bootstrap) may not yet be in added_workers.
        if (
            worker_name
            and worktree_path
            and not any(w.get("name") == worker_name for w in added_workers)
        ):
            try:
                remove_worker_worktree_root_agents_file(
                    sanitized, worker_name, team_state_root, worktree_path
                )
            except Exception:  # noqa: BLE001
                pass

        _kill_pane(pane_id)

        for task_id in created_task_ids:
            _delete_task_file(team_state_dir, task_id)

        config["workers"] = workers_list
        config["worker_count"] = len(workers_list)
        config["next_worker_index"] = initial_next_index
        try:
            team_save_config(leader_cwd, config, sanitized)
        except Exception:  # noqa: BLE001
            pass

        return ScaleError(error=error)

    # Persist incoming tasks first so worker role resolution can read
    # canonical task state.
    for task_input in tasks:
        try:
            created_task = team_create_task(
                leader_cwd,
                sanitized,
                description=task_input.get(
                    "description", task_input.get("subject", "")
                ),
                role=task_input.get("role"),
                file_paths=task_input.get("file_paths"),
                depends_on=task_input.get("blocked_by"),
                status=task_input.get("status", "pending"),
                owner=task_input.get("owner"),
            )
        except Exception as err:  # noqa: BLE001
            return _rollback(f"scale_up_task_persist_failed:{err}")
        created_task_ids.append(created_task.task_id)

    persisted_tasks = team_list_tasks(leader_cwd, sanitized)

    shared_worker_launch_args = _resolve_worker_launch_args_for_scaling(env, agent_type)
    try:
        worker_cli_plan = resolve_team_worker_cli_plan(
            count, shared_worker_launch_args, env
        )
    except ValueError as err:
        return _rollback(f"scale_up_cli_plan_failed:{err}")

    for i in range(count):
        worker_index = next_index
        next_index += 1
        worker_name = f"worker-{worker_index}"

        # Ensure the worker state directory exists (mkdir -p).
        worker_dir = (
            Path(leader_cwd)
            / ".omx"
            / "state"
            / "team"
            / sanitized
            / "workers"
            / worker_name
        )
        try:
            worker_dir.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            return _rollback(f"scale_up_worker_dir_failed:{err}")

        # Per-worker role resolution (mirrors TS exactly).
        worker_task_roles: list[str] = [
            t.role for t in persisted_tasks if t.owner == worker_name and t.role
        ]
        unique_task_roles = set(worker_task_roles)
        worker_role = (
            worker_task_roles[0]
            if len(worker_task_roles) > 0 and len(unique_task_roles) == 1
            else agent_type
        )
        runtime_role = worker_role

        # Worktree provisioning.
        worker_workspace: dict[str, Any] | None = None
        worker_cwd_for_pane = leader_cwd
        if effective_worktree_mode.enabled:
            try:
                planned = plan_worktree_target(
                    WorktreePlanInput(
                        cwd=leader_cwd,
                        scope="team",
                        mode=effective_worktree_mode,
                        team_name=sanitized,
                        worker_name=worker_name,
                    )
                )
                ensured = ensure_worktree(planned)
            except Exception as err:  # noqa: BLE001
                return _rollback(
                    f"scale_up_worktree_failed:{worker_name}:{err}",
                    worker_name=worker_name,
                )
            if isinstance(ensured, EnsureWorktreeResult) and ensured.enabled:
                worker_workspace = {
                    "repo_root": ensured.repo_root,
                    "worktree_path": ensured.worktree_path,
                    "branch_name": ensured.branch_name,
                    "detached": ensured.detached,
                    "created": ensured.created,
                }
                worker_cwd_for_pane = ensured.worktree_path

        # Load role prompt; project-local prompts win, fall back to user dir.
        project_prompts_dir = Path(leader_cwd) / ".codex" / "prompts"
        raw_role_prompt = load_role_prompt(runtime_role, project_prompts_dir)
        if raw_role_prompt is None:
            raw_role_prompt = load_role_prompt(runtime_role, codex_prompts_dir())

        preferred_reasoning = resolve_agent_reasoning_effort(
            runtime_role
        ) or resolve_agent_reasoning_effort(agent_type)
        worker_launch_args = _resolve_worker_launch_args_for_scaling(
            env, runtime_role, preferred_reasoning
        )
        resolved_worker_model = parse_team_worker_launch_args(
            worker_launch_args
        ).model_override

        role_prompt_content = (
            compose_role_instructions_for_role(
                runtime_role, raw_role_prompt, resolved_worker_model
            )
            if raw_role_prompt
            else None
        )

        team_instructions_path = str(
            Path(leader_cwd)
            / ".omx"
            / "state"
            / "team"
            / sanitized
            / "worker-agents.md"
        )

        instructions_file_path: str
        if worker_workspace:
            try:
                instructions_file_path = write_worker_worktree_root_agents_file(
                    WorkerRootAgentsOptions(
                        team_name=sanitized,
                        worker_name=worker_name,
                        worker_role=runtime_role,
                        role_prompt_content=role_prompt_content or "",
                        team_state_root=team_state_root,
                        leader_cwd=leader_cwd,
                        worktree_path=worker_workspace["worktree_path"],
                    )
                )
            except Exception as err:  # noqa: BLE001
                return _rollback(
                    f"scale_up_agents_md_failed:{worker_name}:{err}",
                    worker_name=worker_name,
                    worktree_path=worker_workspace.get("worktree_path"),
                )
        elif role_prompt_content:
            try:
                instructions_file_path = write_worker_role_instructions_file(
                    sanitized,
                    worker_name,
                    leader_cwd,
                    team_instructions_path,
                    runtime_role,
                    role_prompt_content,
                )
            except Exception as err:  # noqa: BLE001
                return _rollback(
                    f"scale_up_instructions_failed:{worker_name}:{err}",
                    worker_name=worker_name,
                )
        else:
            instructions_file_path = team_instructions_path

        extra_env: dict[str, str] = {
            "OMX_TEAM_STATE_ROOT": team_state_root,
            "OMX_TEAM_LEADER_CWD": leader_cwd,
            "OMX_MODEL_INSTRUCTIONS_FILE": instructions_file_path,
        }
        if worker_workspace:
            extra_env["OMX_TEAM_WORKTREE_PATH"] = worker_workspace["worktree_path"]
            if worker_workspace.get("branch_name"):
                extra_env["OMX_TEAM_WORKTREE_BRANCH"] = worker_workspace["branch_name"]
            extra_env["OMX_TEAM_WORKTREE_DETACHED"] = (
                "1" if worker_workspace.get("detached") else "0"
            )

        cmd = build_worker_startup_command(
            sanitized,
            worker_index,
            worker_launch_args,
            worker_cwd_for_pane,
            extra_env,
            worker_cli_plan[i],
            None,
            runtime_role,
        )

        # Choose split target — right-most worker for vertical splits,
        # leader for the initial horizontal split.
        leader_pane_id = config.get("leader_pane_id") or ""
        if workers_list:
            split_target = workers_list[-1].get("pane_id") or leader_pane_id
        else:
            split_target = leader_pane_id
        split_direction = "-h" if split_target == leader_pane_id else "-v"

        try:
            split = subprocess.run(
                [
                    "tmux",
                    "split-window",
                    split_direction,
                    "-t",
                    split_target,
                    "-d",
                    "-P",
                    "-F",
                    "#{pane_id}",
                    "-c",
                    worker_cwd_for_pane,
                    cmd,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, OSError) as err:
            return _rollback(
                f"scale_up_split_window_exception:{worker_name}:{err}",
                worker_name=worker_name,
                worktree_path=(
                    worker_workspace.get("worktree_path") if worker_workspace else None
                ),
            )

        if split.returncode != 0:
            stderr = (split.stderr or "").strip()
            return _rollback(
                f"Failed to create tmux pane for {worker_name}: {stderr}",
                worker_name=worker_name,
                worktree_path=(
                    worker_workspace.get("worktree_path") if worker_workspace else None
                ),
            )

        pane_id_raw = (split.stdout or "").strip().split("\n")[0].strip()
        if not pane_id_raw or not pane_id_raw.startswith("%"):
            return _rollback(
                f"Failed to capture pane ID for {worker_name}",
                pane_id=pane_id_raw or None,
                worker_name=worker_name,
                worktree_path=(
                    worker_workspace.get("worktree_path") if worker_workspace else None
                ),
            )

        pane_id = pane_id_raw

        try:
            pane_pid = get_worker_pane_pid(session_name, worker_index, pane_id)
        except Exception:  # noqa: BLE001
            pane_pid = None

        worker_info: dict[str, Any] = {
            "name": worker_name,
            "index": worker_index,
            "role": worker_role,
            "worker_cli": worker_cli_plan[i],
            "assigned_tasks": [],
            "pid": pane_pid,
            "pane_id": pane_id,
            "working_dir": worker_cwd_for_pane,
            "worktree_repo_root": (
                worker_workspace["repo_root"] if worker_workspace else None
            ),
            "worktree_path": (
                worker_workspace["worktree_path"] if worker_workspace else None
            ),
            "worktree_branch": (
                worker_workspace.get("branch_name") if worker_workspace else None
            ),
            "worktree_detached": (
                worker_workspace.get("detached") if worker_workspace else None
            ),
            "worktree_created": (
                worker_workspace.get("created") if worker_workspace else None
            ),
            "team_state_root": team_state_root,
        }

        try:
            team_write_worker_identity(leader_cwd, sanitized, worker_name, worker_info)
        except Exception as err:  # noqa: BLE001
            return _rollback(
                f"scale_up_write_identity_failed:{worker_name}:{err}",
                pane_id=pane_id,
                worker_name=worker_name,
                worktree_path=(
                    worker_workspace.get("worktree_path") if worker_workspace else None
                ),
            )

        ready_timeout_ms = _resolve_worker_ready_timeout_ms(env)
        skip_ready_wait = env.get("OMX_TEAM_SKIP_READY_WAIT") == "1"
        if not skip_ready_wait:
            try:
                wait_for_worker_ready(
                    session_name, worker_index, ready_timeout_ms, pane_id
                )
            except Exception:  # noqa: BLE001
                pass

        worker_tasks = [t for t in persisted_tasks if t.owner == worker_name]
        inbox = generate_initial_inbox(
            worker_name,
            sanitized,
            agent_type,
            worker_tasks,
            team_state_root=team_state_root,
            leader_cwd=leader_cwd,
            worker_role=runtime_role,
            role_prompt_content=raw_role_prompt,
            worktree_root_agents_canonical=bool(worker_workspace),
        )

        trigger_directive = build_trigger_directive(
            worker_name,
            sanitized,
            _resolve_instruction_state_root(worker_info.get("worktree_path"))
            or ".omx/state",
        )

        dispatch_mode = (
            dispatch_policy.dispatch_mode.value
            if hasattr(dispatch_policy.dispatch_mode, "value")
            else str(dispatch_policy.dispatch_mode)
        )

        def _notify(
            _target: TeamNotifierTarget,
            message: str,
            _context: dict[str, Any],
        ) -> DispatchOutcome:
            if dispatch_mode == "hook_preferred_with_fallback":
                return DispatchOutcome(
                    ok=True,
                    transport=DispatchTransport.HOOK.value,
                    reason="queued_for_hook_dispatch",
                )
            return _notify_worker_pane_outcome(
                session_name,
                worker_index,
                message,
                pane_id,
                worker_cli_plan[i],
            )

        outcome = queue_inbox_instruction(
            QueueInboxParams(
                team_name=sanitized,
                worker_name=worker_name,
                worker_index=worker_index,
                inbox=inbox,
                trigger_message=trigger_directive.text,
                cwd=leader_cwd,
                notify=_notify,
                pane_id=pane_id,
                intent=trigger_directive.intent,
                transport_preference=dispatch_mode,
                fallback_allowed=True,
                inbox_correlation_key=f"scale_up:{worker_name}",
            )
        )

        if dispatch_mode == "hook_preferred_with_fallback" and outcome.request_id:
            receipt = wait_for_dispatch_receipt(
                sanitized,
                outcome.request_id,
                leader_cwd,
                timeout_ms=dispatch_policy.dispatch_ack_timeout_ms,
                poll_ms=50,
            )
            if receipt and receipt.status in ("notified", "delivered"):
                outcome = DispatchOutcome(
                    ok=True,
                    transport=DispatchTransport.HOOK.value,
                    reason=f"hook_receipt_{receipt.status}",
                    request_id=outcome.request_id,
                )
            else:
                fallback = _notify_worker_pane_outcome(
                    session_name,
                    worker_index,
                    trigger_directive.text,
                    pane_id,
                    worker_cli_plan[i],
                )
                if fallback.ok:
                    outcome = DispatchOutcome(
                        ok=True,
                        transport=fallback.transport,
                        reason=f"hook_timeout_fallback_confirmed:{fallback.reason}",
                        request_id=outcome.request_id,
                    )
                else:
                    outcome = DispatchOutcome(
                        ok=False,
                        transport=fallback.transport,
                        reason=f"fallback_attempted_but_unconfirmed:{fallback.reason}",
                        request_id=outcome.request_id,
                    )

        # Retry once if a trust prompt is blocking the worker pane.
        if not outcome.ok and dismiss_trust_prompt_if_present(
            session_name, worker_index, pane_id
        ):
            try:
                wait_for_worker_ready(
                    session_name, worker_index, ready_timeout_ms, pane_id
                )
            except Exception:  # noqa: BLE001
                pass
            retry = _notify_worker_pane_outcome(
                session_name,
                worker_index,
                trigger_directive.text,
                pane_id,
                worker_cli_plan[i],
            )
            if retry.ok:
                outcome = retry

        if not outcome.ok:
            return _rollback(
                f"scale_up_dispatch_failed:{worker_name}:{outcome.reason}",
                pane_id=pane_id,
                worker_name=worker_name,
                worktree_path=(
                    worker_workspace.get("worktree_path") if worker_workspace else None
                ),
            )

        added_workers.append(worker_info)
        workers_list.append(worker_info)
        config["workers"] = workers_list
        config["worker_count"] = len(workers_list)
        config["next_worker_index"] = next_index
        try:
            team_save_config(leader_cwd, config, sanitized)
        except Exception as err:  # noqa: BLE001
            return _rollback(
                f"scale_up_save_config_failed:{worker_name}:{err}",
                worker_name=worker_name,
                worktree_path=(
                    worker_workspace.get("worktree_path") if worker_workspace else None
                ),
            )

    # Append the leader-nudge event on success (best effort).
    try:
        from omx.team.contracts import TeamEvent
        from datetime import datetime, timezone

        team_append_event(
            leader_cwd,
            TeamEvent(
                event_type="team_leader_nudge",
                timestamp=datetime.now(timezone.utc).isoformat(),
                worker_id="leader-fixed",
                detail={
                    "reason": (
                        f"scale_up: added {count} worker(s), "
                        f"new count={config.get('worker_count', 0)}"
                    )
                },
            ),
            sanitized,
        )
    except Exception:  # noqa: BLE001
        pass

    return ScaleUpResult(
        added_workers=added_workers,
        new_worker_count=int(config.get("worker_count", len(workers_list))),
        next_worker_index=next_index,
    )
