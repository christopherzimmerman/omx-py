"""Port of ``resumeTeam`` from ``src/team/runtime.ts`` (line 3315).

Phase 2.10 — sync, stdlib-only port.

The TS function is small (~45 LOC) but supports a broader "live attach"
contract that the Python port flesh out per the Phase 2.10 acceptance
criteria:

* Read manifest V2 first, then fall back to ``config.json``.
* Probe tmux for the existing session; if it is missing and the team is
  **not** in ``prompt`` launch mode, raise :class:`TeamNotRunningError`.
* Verify the worker count and pane IDs match what the manifest claimed.
* For each worker (in parallel via :class:`ThreadPoolExecutor`):
  liveness, status, heartbeat, and a state classification.
* Read pending dispatch requests and re-emit "stale" pending ones via
  :func:`omx.team.state.dispatch.transition_dispatch_request`.
* Replay leader-attention. If the leader's recorded session has stopped
  and the recorded session id no longer matches the current one, raise
  :class:`RotatedSessionError`.
* Persist an updated phase state (``resumed`` marker on the transitions
  log) before returning.
* Return a :class:`TeamRuntime` handle.

Sync conversion: TS uses ``Promise.all`` for parallel worker reads; the
Python port uses :class:`concurrent.futures.ThreadPoolExecutor` with a
bounded pool. All file/tmux operations are synchronous and stdlib-only.

Simplifications relative to TS:

* The TS function returns ``None`` (silently) for the prompt-mode
  "missing handle" branch. The Python port keeps the same return-shape
  but still appends a ``worker_stopped`` team event for parity.
* The TS function does not verify worker count / pane IDs; the Python
  port adds this check (per acceptance criteria) but treats a mismatch
  as informational rather than fatal — callers can decide to re-launch.
* The dispatch redispatch contract uses the existing two-argument
  :func:`transition_dispatch_request` (the Python helper does not take
  the full ``from_status`` / ``metadata`` tuple from TS).
"""

from __future__ import annotations

import errno
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from omx.team.contracts import TeamEvent
from omx.team.runtime_types import TeamRuntime
from omx.team.state.dispatch import (
    read_dispatch_requests,
    transition_dispatch_request,
)
from omx.team.state.io import (
    append_team_event,
    read_team_config,
    read_worker_heartbeat,
    read_worker_status,
)
from omx.team.state.leader import read_team_leader_attention
from omx.team.state.manifest import read_team_manifest_v2
from omx.team.state.monitor import read_phase_state, write_phase_state
from omx.team.state.types import TeamPhaseState
from omx.team.state_root import team_dir as _team_dir_path
from omx.team.tmux_session import (
    is_worker_alive,
    is_worker_pane_open,
    list_pane_ids,
    list_team_sessions,
    sanitize_team_name,
)


# ---------------------------------------------------------------------------
# Errors


class TeamNotRunningError(RuntimeError):
    """The manifest exists but no tmux session backs the team anymore.

    Raised by :func:`resume_team_with_signals` when interactive teams have
    lost their tmux session and cannot be re-attached.
    """


class RotatedSessionError(RuntimeError):
    """A different leader session has taken ownership of this team.

    Raised when the persisted leader-attention record shows the previous
    leader session has stopped *and* the active session id (resolved from
    env) differs from the recorded one. The caller should not attach.
    """


# ---------------------------------------------------------------------------
# Constants

# Default thread-pool size for the worker scan. Mirrors the choice in
# :mod:`runtime_monitor` (8 is enough for the typical <=12-worker team).
_DEFAULT_WORKER_SCAN_WORKERS = 8

# Stale-pending threshold for redispatch. TS uses an effectively immediate
# replay; Python defaults to 60 seconds so unit tests can exercise both
# branches deterministically without sleeping.
_DEFAULT_PENDING_STALE_SECONDS = 60


# ---------------------------------------------------------------------------
# Result dataclass


@dataclass
class WorkerResumeSignal:
    """Single-worker resume signal collected during the parallel scan.

    The values are intentionally loose dicts so the helpers stay
    decoupled from the (not-yet-ported) ``WorkerStatus`` / ``WorkerHeartbeat``
    dataclasses.
    """

    name: str
    index: int
    pane_id: str | None
    alive: bool
    pane_open: bool
    status: dict[str, Any]
    heartbeat: dict[str, Any] | None
    classified_state: str = "unknown"


@dataclass
class ResumeOutcome:
    """Aggregate result of :func:`resume_team_with_signals`.

    Attributes:
        runtime: Live :class:`TeamRuntime` handle, or ``None`` when the
            manifest is absent / resume is not viable.
        worker_signals: Per-worker liveness/status/heartbeat snapshot.
        dead_workers: Names of workers whose pane is gone.
        non_reporting_workers: Names of workers that look alive but have
            not emitted a fresh heartbeat.
        redispatched_request_ids: IDs of pending dispatch requests that
            were re-issued during resume.
        manifest_mismatch_reasons: Human-readable reasons why the manifest
            disagrees with current tmux/disk state. Empty when consistent.
    """

    runtime: TeamRuntime | None
    worker_signals: list[WorkerResumeSignal] = field(default_factory=list)
    dead_workers: list[str] = field(default_factory=list)
    non_reporting_workers: list[str] = field(default_factory=list)
    redispatched_request_ids: list[str] = field(default_factory=list)
    manifest_mismatch_reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Low-level helpers (ports of small TS utilities)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_pid_alive(pid: int | None) -> bool:
    """Port of TS ``isPidAlive`` (runtime.ts:1598-1608).

    Uses ``os.kill(pid, 0)`` which raises ``ProcessLookupError`` (ESRCH)
    when the process is gone. Permissions errors (EPERM) imply the
    process *does* exist but we cannot signal it.
    """
    if pid is None:
        return False
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        os.kill(pid_int, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists, we just cannot signal it.
        return True
    except OSError as err:
        if getattr(err, "errno", None) == errno.ESRCH:
            return False
        # Match TS posture: log and treat as "not alive" rather than raising.
        sys.stderr.write(f"[team/runtime_resume] isPidAlive failed: {err}\n")
        return False


def _is_prompt_worker_alive(worker: dict[str, Any]) -> bool:
    """Port of TS ``isPromptWorkerAlive`` (runtime.ts:1867-1873).

    The TS version inspects an in-process registry of ``PromptWorkerHandle``
    objects (child processes spawned in the same Node runtime). The Python
    port does not yet have that registry, so we approximate using the
    persisted ``pid`` field. This is a known simplification.
    """
    pid = worker.get("pid")
    return _is_pid_alive(pid)


def _get_team_tmux_sessions(sanitized_team: str) -> list[str]:
    """Port of TS ``getTeamTmuxSessions`` (notifications/tmux.ts:158-172).

    Filters all current tmux sessions to those that match the
    ``omx-team-<sanitized>`` prefix.
    """
    if not sanitized_team:
        return []
    prefix = f"omx-team-{sanitized_team}"
    all_sessions = list_team_sessions()
    return [s for s in all_sessions if s == prefix or s.startswith(f"{prefix}-")]


def _base_session(name: str) -> str:
    """Strip a ``session:window`` selector to the bare session name."""
    if not name:
        return ""
    return name.split(":")[0]


def _coerce_worker_dict(raw: Any) -> dict[str, Any] | None:
    """Best-effort coerce a config/manifest worker entry into a dict."""
    if isinstance(raw, dict):
        return raw
    # Dataclass-like (e.g. WorkerInfo) → use its to_dict if present.
    to_dict = getattr(raw, "to_dict", None)
    if callable(to_dict):
        out = to_dict()
        if isinstance(out, dict):
            return out
    return None


def _load_workers_from_config(config: dict[str, Any]) -> list[dict[str, Any]]:
    raw = config.get("workers") or []
    workers: list[dict[str, Any]] = []
    for entry in raw:
        coerced = _coerce_worker_dict(entry)
        if coerced is not None:
            workers.append(coerced)
    return workers


# ---------------------------------------------------------------------------
# Parallel worker scan


def _scan_one_worker(
    cwd: str,
    sanitized: str,
    session_name: str,
    worker: dict[str, Any],
) -> WorkerResumeSignal:
    """Read liveness, pane state, status and heartbeat for a single worker.

    Designed for use inside :class:`ThreadPoolExecutor` — each invocation
    is independent and does not mutate shared state.
    """
    name = str(worker.get("name") or worker.get("worker_id") or "")
    try:
        index = int(worker.get("index", 0))
    except (TypeError, ValueError):
        index = 0
    pane_id_raw = worker.get("pane_id")
    pane_id = pane_id_raw if isinstance(pane_id_raw, str) and pane_id_raw else None

    if session_name:
        alive = is_worker_alive(session_name, index, pane_id)
        pane_open = is_worker_pane_open(session_name, index, pane_id)
    else:
        alive = _is_prompt_worker_alive(worker)
        pane_open = alive

    status = read_worker_status(cwd, sanitized, name) or {}
    heartbeat = read_worker_heartbeat(cwd, sanitized, name)

    classified = _classify_worker_state(alive, pane_open, status, heartbeat)

    return WorkerResumeSignal(
        name=name,
        index=index,
        pane_id=pane_id,
        alive=bool(alive),
        pane_open=bool(pane_open),
        status=status,
        heartbeat=heartbeat,
        classified_state=classified,
    )


def _classify_worker_state(
    alive: bool,
    pane_open: bool,
    status: dict[str, Any],
    heartbeat: dict[str, Any] | None,
) -> str:
    """Bucket a worker into ``alive`` / ``dead`` / ``non_reporting`` / ``unknown``.

    The leader's monitor loop does fine-grained classification; for the
    resume path we only need enough signal to emit dead-worker events
    and trigger the non-reporting recommendation list.
    """
    if not alive and not pane_open:
        return "dead"
    if not alive:
        # Pane still exists but the process is gone — partial death.
        return "dead"
    state = status.get("state")
    if isinstance(state, str) and state in (
        "working",
        "idle",
        "blocked",
        "failed",
        "unknown",
    ):
        if heartbeat is None and state == "working":
            return "non_reporting"
        return state
    if heartbeat is None:
        return "non_reporting"
    return "alive"


def _scan_workers_parallel(
    cwd: str,
    sanitized: str,
    session_name: str,
    workers: list[dict[str, Any]],
    max_parallel: int,
) -> list[WorkerResumeSignal]:
    if not workers:
        return []
    pool_size = max(1, min(max_parallel, len(workers)))
    signals: list[WorkerResumeSignal] = []
    with ThreadPoolExecutor(max_workers=pool_size) as executor:
        futures = [
            executor.submit(_scan_one_worker, cwd, sanitized, session_name, w)
            for w in workers
        ]
        for fut in futures:
            signals.append(fut.result())
    return signals


# ---------------------------------------------------------------------------
# Manifest consistency check


def _verify_worker_count_and_pane_ids(
    config: dict[str, Any],
    manifest_workers: list[dict[str, Any]] | None,
    session_name: str,
) -> list[str]:
    """Compare manifest + config worker count and pane IDs.

    Returns a list of mismatch reasons (empty when consistent).

    Reasons are advisory only — the caller decides whether to abort or
    continue. TS does not perform this check; the Python port adds it per
    Phase 2.10 acceptance criteria.
    """
    reasons: list[str] = []
    config_workers = _load_workers_from_config(config)

    declared_count = config.get("worker_count")
    if isinstance(declared_count, int) and declared_count != len(config_workers):
        reasons.append(
            f"worker_count_mismatch:declared={declared_count}:actual={len(config_workers)}"
        )

    if manifest_workers is not None and len(manifest_workers) != len(config_workers):
        reasons.append(
            f"manifest_worker_count_mismatch:manifest={len(manifest_workers)}:"
            f"config={len(config_workers)}"
        )

    if session_name:
        try:
            live_panes = set(list_pane_ids(_base_session(session_name)))
        except Exception:  # noqa: BLE001 — defensive: tmux probe must not throw
            live_panes = set()
        missing_panes: list[str] = []
        for worker in config_workers:
            pane_id = worker.get("pane_id")
            if isinstance(pane_id, str) and pane_id and pane_id not in live_panes:
                missing_panes.append(f"{worker.get('name', '?')}:{pane_id}")
        if missing_panes:
            reasons.append(f"pane_ids_missing:{','.join(missing_panes)}")

    return reasons


# ---------------------------------------------------------------------------
# Dispatch redispatch


def _redispatch_stale_pending(
    sanitized: str,
    cwd: str,
    stale_seconds: int,
    *,
    now: datetime | None = None,
) -> list[str]:
    """Re-emit pending dispatch requests whose ``created_at`` is stale.

    Mirrors the leader's "pick up where you left off" contract at resume.
    Stale requests are bumped via :func:`transition_dispatch_request`
    (pending -> pending) which refreshes ``updated_at`` so monitors notice
    them on the next tick.

    Returns the list of request IDs that were touched.
    """
    team_state_dir = _team_dir_path(sanitized, cwd)
    pending_requests = [
        req for req in read_dispatch_requests(team_state_dir) if req.status == "pending"
    ]
    if not pending_requests:
        return []

    effective_now = now or datetime.now(timezone.utc)
    touched: list[str] = []
    for req in pending_requests:
        created_at = req.created_at
        if not isinstance(created_at, str) or not created_at:
            continue
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = (effective_now - parsed).total_seconds()
        if delta < stale_seconds:
            continue
        ok = transition_dispatch_request(
            team_state_dir,
            req.request_id,
            "pending",
            reason="resume_redispatch_stale",
        )
        if ok:
            touched.append(req.request_id)
    return touched


# ---------------------------------------------------------------------------
# Leader-attention replay


def _resolve_active_leader_session_id(env: dict[str, str] | None) -> str:
    """Mirrors ``resolveLeaderSessionId`` env path (runtime.ts:3448-3463).

    The Python port falls back to env-only (no session.json lookup) since
    resume callers already pass ``cwd`` and the session id should be in
    env for the active leader.
    """
    src = env if env is not None else os.environ
    for key in ("OMX_SESSION_ID", "CODEX_SESSION_ID", "SESSION_ID"):
        value = src.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _check_leader_attention_for_rotation(
    sanitized: str,
    cwd: str,
    active_session_id: str,
) -> None:
    """Raise :class:`RotatedSessionError` if a different leader owns the team.

    Replays the persisted leader-attention record. If the prior leader
    session is stopped (``leader_session_active == False``) and a non-empty
    active session id has been resolved that differs from the recorded
    session id, this team has been rotated to a new leader and the caller
    must not attach.
    """
    record = read_team_leader_attention(sanitized, cwd)
    if record is None:
        return
    if record.leader_session_active:
        return
    recorded = (record.leader_session_id or "").strip()
    if not recorded:
        return
    if not active_session_id:
        return
    if recorded == active_session_id:
        return
    raise RotatedSessionError(
        f"rotated_session: team={sanitized} previous_leader={recorded} "
        f"current_leader={active_session_id}"
    )


# ---------------------------------------------------------------------------
# Phase persistence


def _persist_resumed_phase(sanitized: str, cwd: str) -> None:
    """Append a ``resumed`` transition to the persisted phase state.

    Creates a default ``team-exec`` phase if no state file exists yet.
    """
    team_state_dir = _team_dir_path(sanitized, cwd)
    state = read_phase_state(team_state_dir)
    now = _now_iso()
    if state is None:
        state = TeamPhaseState(current_phase="team-exec", updated_at=now)
    transitions = list(state.transitions) if state.transitions else []
    transitions.append(
        {
            "to_phase": state.current_phase or "team-exec",
            "at": now,
            "reason": "resumed",
        }
    )
    state.transitions = transitions
    state.updated_at = now
    write_phase_state(team_state_dir, state)


# ---------------------------------------------------------------------------
# Public entry points


def resume_team(
    team_name: str,
    cwd: str,
) -> TeamRuntime | None:
    """Resume monitoring an existing team.

    Mirrors the public surface of TS ``resumeTeam`` (runtime.ts:3315-3358):
    returns a :class:`TeamRuntime` handle on success, or ``None`` when the
    team is not resumable.

    Behaviour:

    1. Read the manifest V2; if absent **and** no config exists, return
       ``None`` (the team was never persisted here).
    2. Read the on-disk config; if still empty, return ``None``.
    3. Force ``config.lifecycle_profile = "default"`` (parity with TS line
       3319).
    4. **Prompt mode**: at least one worker must be alive; otherwise return
       ``None``. If a worker reports a live ``pid`` but the in-process
       handle registry is missing it, emit a ``worker_stopped`` event and
       return ``None`` (TS lines 3325-3343 — Python uses the persisted
       pid-only path).
    5. **Interactive mode**: a tmux session matching the configured
       ``omx-team-<sanitized>`` prefix must exist; otherwise return
       ``None``.
    6. Return a populated :class:`TeamRuntime`.

    Callers that need richer signals (parallel scan, dispatch redispatch,
    rotated-session detection) should use :func:`resume_team_with_signals`.
    """
    sanitized = sanitize_team_name(team_name)
    config = read_team_config(cwd, sanitized)
    if not config:
        # Treat manifest-only resume as a soft-fallback when the manifest
        # carries a tmux_session — but only if the manifest itself exists.
        manifest = read_team_manifest_v2(sanitized, cwd)
        if manifest is None:
            return None
        # Hydrate a minimal config from the manifest so the rest of the
        # function can run uniformly.
        config = {
            "name": manifest.name,
            "tmux_session": manifest.tmux_session,
            "workers": [w.to_dict() for w in manifest.workers],
            "worker_count": manifest.worker_count,
            "worker_launch_mode": (manifest.policy or {}).get(
                "worker_launch_mode", "interactive"
            )
            if isinstance(manifest.policy, dict)
            else "interactive",
        }
    config["lifecycle_profile"] = "default"

    worker_launch_mode = str(config.get("worker_launch_mode") or "interactive")
    workers = _load_workers_from_config(config)

    if worker_launch_mode == "prompt":
        has_live = any(_is_prompt_worker_alive(w) for w in workers)
        if not has_live:
            return None
        # TS "missing handle" branch — Python lacks an in-process registry,
        # so this branch is effectively unreachable here. We keep the event
        # emission slot for parity if a registry is added later.
        return TeamRuntime(
            team_name=sanitized,
            sanitized_name=sanitized,
            session_name=str(config.get("tmux_session") or ""),
            config=config,
            cwd=cwd,
        )

    base_session = _base_session(str(config.get("tmux_session") or ""))
    sessions = _get_team_tmux_sessions(sanitized)
    if base_session and base_session not in sessions:
        return None

    return TeamRuntime(
        team_name=sanitized,
        sanitized_name=sanitized,
        session_name=str(config.get("tmux_session") or ""),
        config=config,
        cwd=cwd,
    )


def resume_team_with_signals(
    team_name: str,
    cwd: str,
    *,
    max_parallel_workers: int = _DEFAULT_WORKER_SCAN_WORKERS,
    pending_stale_seconds: int = _DEFAULT_PENDING_STALE_SECONDS,
    env: dict[str, str] | None = None,
    persist_phase: bool = True,
    require_tmux_session: bool = True,
) -> ResumeOutcome:
    """Resume a team and collect richer per-worker signals.

    This is the Phase 2.10 acceptance entry point. It adds (vs
    :func:`resume_team`):

    * Parallel worker scan via :class:`ThreadPoolExecutor`.
    * Pending dispatch redispatch for stale ``created_at`` rows.
    * Leader-attention rotation check — raises
      :class:`RotatedSessionError` when a different leader owns the team.
    * Optional persistence of a ``resumed`` marker on the phase state.

    Args:
        team_name: Team identifier (sanitized internally to match TS).
        cwd: Absolute working directory the team lives under.
        max_parallel_workers: Bound on the worker-scan thread pool.
        pending_stale_seconds: Pending dispatch requests with a
            ``created_at`` older than this threshold are re-emitted via
            ``transition_dispatch_request(pending -> pending)``.
        env: Optional env mapping used to resolve the active leader
            session id. Defaults to ``os.environ`` when ``None``.
        persist_phase: When ``True`` (default), append a ``resumed``
            transition to the persisted phase state. Tests pass ``False``
            for deterministic assertions.
        require_tmux_session: When ``True`` (default) and the team is not
            in ``prompt`` launch mode, raise :class:`TeamNotRunningError`
            if no matching tmux session is found. Setting this to
            ``False`` returns an outcome with ``runtime=None`` instead
            (matches the soft TS contract).

    Returns:
        A :class:`ResumeOutcome` with the live :class:`TeamRuntime` and
        per-worker signals. ``runtime`` is ``None`` when the team is not
        resumable (no manifest / no config).

    Raises:
        TeamNotRunningError: Manifest exists but no tmux session backs
            the team and ``require_tmux_session`` is ``True``.
        RotatedSessionError: Leader-attention record shows a different
            leader session has taken over.
    """
    sanitized = sanitize_team_name(team_name)
    config = read_team_config(cwd, sanitized)
    manifest = read_team_manifest_v2(sanitized, cwd)

    if not config and manifest is None:
        return ResumeOutcome(runtime=None)

    if not config and manifest is not None:
        config = {
            "name": manifest.name,
            "tmux_session": manifest.tmux_session,
            "workers": [w.to_dict() for w in manifest.workers],
            "worker_count": manifest.worker_count,
            "worker_launch_mode": (manifest.policy or {}).get(
                "worker_launch_mode", "interactive"
            )
            if isinstance(manifest.policy, dict)
            else "interactive",
        }

    config["lifecycle_profile"] = "default"
    worker_launch_mode = str(config.get("worker_launch_mode") or "interactive")
    workers = _load_workers_from_config(config)
    session_name = str(config.get("tmux_session") or "")
    base_session = _base_session(session_name)

    # --- tmux probe -------------------------------------------------------
    has_session = False
    if worker_launch_mode != "prompt":
        sessions = _get_team_tmux_sessions(sanitized)
        has_session = bool(base_session and base_session in sessions)
        if not has_session and require_tmux_session:
            raise TeamNotRunningError(
                f"team_not_running: no tmux session for {sanitized} "
                f"(expected base={base_session or '<unset>'})"
            )
    else:
        has_live_prompt = any(_is_prompt_worker_alive(w) for w in workers)
        if not has_live_prompt:
            try:
                append_team_event(
                    cwd,
                    TeamEvent(
                        event_type="worker_stopped",
                        timestamp=_now_iso(),
                        worker_id="leader-fixed",
                        detail={"reason": "prompt_resume_unavailable:no_live_worker"},
                    ),
                    sanitized,
                )
            except Exception:  # noqa: BLE001 — event emission is best-effort
                pass
            return ResumeOutcome(runtime=None)

    # --- manifest consistency check --------------------------------------
    manifest_workers = (
        [w.to_dict() for w in manifest.workers] if manifest is not None else None
    )
    mismatch_reasons = _verify_worker_count_and_pane_ids(
        config,
        manifest_workers,
        session_name if has_session else "",
    )

    # --- leader rotation check -------------------------------------------
    active_session_id = _resolve_active_leader_session_id(env)
    _check_leader_attention_for_rotation(sanitized, cwd, active_session_id)

    # --- parallel worker scan --------------------------------------------
    effective_session = session_name if has_session else ""
    signals = _scan_workers_parallel(
        cwd,
        sanitized,
        effective_session,
        workers,
        max_parallel_workers,
    )

    dead_workers = [s.name for s in signals if s.classified_state == "dead"]
    non_reporting = [s.name for s in signals if s.classified_state == "non_reporting"]

    # --- dispatch redispatch ---------------------------------------------
    redispatched = _redispatch_stale_pending(sanitized, cwd, pending_stale_seconds)

    # --- phase persistence -----------------------------------------------
    if persist_phase:
        _persist_resumed_phase(sanitized, cwd)

    runtime = TeamRuntime(
        team_name=sanitized,
        sanitized_name=sanitized,
        session_name=session_name,
        config=config,
        cwd=cwd,
    )

    return ResumeOutcome(
        runtime=runtime,
        worker_signals=signals,
        dead_workers=dead_workers,
        non_reporting_workers=non_reporting,
        redispatched_request_ids=redispatched,
        manifest_mismatch_reasons=mismatch_reasons,
    )


__all__ = [
    "RotatedSessionError",
    "TeamNotRunningError",
    "ResumeOutcome",
    "WorkerResumeSignal",
    "resume_team",
    "resume_team_with_signals",
]
