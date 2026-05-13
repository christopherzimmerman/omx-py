"""Scale-down for team mode — drain + remove workers.

Port of ``scaleDown`` from ``src/team/scaling.ts`` (lines 624–800).
Phase 3b — sync-only, stdlib-only.

High-level flow (mirrors TS step-for-step):

1. Assert dynamic scaling is enabled (``OMX_TEAM_SCALING_ENABLED``).
2. Acquire the team-level scaling lock.
3. Read the team config; bail if the team isn't on disk.
4. Resolve target workers:
   - explicit ``worker_names`` -> match by name (error on miss), or
   - implicit ``count`` -> pick idle workers, plus non-idle when ``force``.
5. Apply the minimum-1-worker guard.
6. Phase 1 — write ``state='draining'`` for every target.
7. Phase 2 — poll worker status until each target is idle/done/draining
   (or its pane is gone) or ``drain_timeout_ms`` elapses. Skipped when
   ``force=True``.
8. Phase 3 — kill tmux panes, roll back any detached worktrees, and
   remove the per-worker worktree-root AGENTS.md.
9. Phase 4 — filter removed workers out of the config, decrement
   ``worker_count``, save the config, and emit a ``team_leader_nudge``
   event.

Simplifications relative to TS:

- ``teardownWorkerPanes`` (async, batches kill calls and skips
  leader/HUD panes) is folded into a per-worker
  :func:`omx.team.tmux_session.kill_worker` loop. ``kill_worker`` already
  guards against the leader pane id.
- Time is measured with ``time.monotonic`` instead of ``Date.now`` for
  the drain deadline.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from omx.team.contracts import TeamEvent
from omx.team.state.io import (
    append_team_event,
    read_team_config,
    read_worker_status,
    write_team_config,
    write_worker_status,
)
from omx.team.state.locks import with_scaling_lock
from omx.team.state.types import TeamWorkerState
from omx.team.state_root import team_dir as _team_dir
from omx.team.tmux_session import (
    is_worker_alive,
    kill_worker,
    sanitize_team_name,
)
from omx.team.worker_bootstrap import remove_worker_worktree_root_agents_file
from omx.team.worktree import (
    EnsureWorktreeResult,
    rollback_provisioned_worktrees,
)

__all__ = [
    "ScaleDownOptions",
    "ScaleDownResult",
    "ScaleError",
    "is_scaling_enabled",
    "assert_scaling_enabled",
    "scale_down",
]


# Re-export the canonical env gate + ScaleError from team.scaling (Phase 3a).
from omx.team.scaling import (
    ScaleError as ScaleError,
    assert_scaling_enabled as assert_scaling_enabled,
    is_scaling_enabled as is_scaling_enabled,
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScaleDownOptions:
    """Caller-supplied options for :func:`scale_down`.

    Attributes:
        worker_names: Explicit worker names to remove. Takes precedence
            over ``count`` when provided and non-empty.
        count: Number of idle workers to remove. Used only when
            ``worker_names`` is unset/empty. Defaults to 1.
        force: Skip the drain wait and pick up non-idle workers when
            there aren't enough idle ones. Default ``False``.
        drain_timeout_ms: Max time (ms) to wait for workers to drain.
            Default 30 000 ms, matching TS.
    """

    worker_names: list[str] | None = None
    count: int | None = None
    force: bool = False
    drain_timeout_ms: int = 30_000


@dataclass(frozen=True)
class ScaleDownResult:
    """Successful scale-down outcome.

    Attributes:
        removed_workers: Names of workers that were torn down.
        new_worker_count: Number of workers remaining in the config.
    """

    ok: bool = True
    removed_workers: list[str] = field(default_factory=list)
    new_worker_count: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DRAINED_STATES = frozenset(
    {
        TeamWorkerState.IDLE.value,
        TeamWorkerState.DONE.value,
        TeamWorkerState.DRAINING.value,
    }
)

# States the TS scaleDown treats as "removable without force" when picking
# idle workers by count: idle, done, or unknown (missing status file).
_IDLE_LIKE_STATES = frozenset(
    {
        TeamWorkerState.IDLE.value,
        TeamWorkerState.DONE.value,
        TeamWorkerState.UNKNOWN.value,
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_worker_state(team_name: str, worker_name: str, cwd: str) -> str:
    """Read a worker status state, defaulting to ``unknown`` on missing.

    TS ``readWorkerStatus`` returns ``{state:'unknown'}`` for a missing
    or malformed status file; the Python state-layer helper returns
    ``None`` in that case, so we coerce here.
    """
    status = read_worker_status(cwd, team_name, worker_name)
    if status is None:
        return TeamWorkerState.UNKNOWN.value
    state = status.get("state")
    if not isinstance(state, str):
        return TeamWorkerState.UNKNOWN.value
    return state


def _resolve_target_workers(
    team_name: str,
    cwd: str,
    config: dict[str, Any],
    options: ScaleDownOptions,
) -> list[dict[str, Any]] | ScaleError:
    """Pick the workers to drain based on caller options.

    Returns the selected worker list (in config order) or a
    :class:`ScaleError` describing why selection failed.
    """
    workers: list[dict[str, Any]] = list(config.get("workers") or [])

    # Explicit names branch.
    if options.worker_names:
        targets: list[dict[str, Any]] = []
        for name in options.worker_names:
            match = next((w for w in workers if w.get("name") == name), None)
            if match is None:
                return ScaleError(error=f"Worker {name} not found in team {team_name}")
            targets.append(match)
        return targets

    # Implicit count branch.
    count = options.count if options.count is not None else 1
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        return ScaleError(error=f"count must be a positive integer (got {count})")

    idle_workers: list[dict[str, Any]] = []
    for worker in workers:
        name = worker.get("name")
        if not isinstance(name, str) or not name:
            continue
        state = _read_worker_state(team_name, name, cwd)
        if state in _IDLE_LIKE_STATES:
            idle_workers.append(worker)

    if len(idle_workers) < count and not options.force:
        return ScaleError(
            error=(
                f"Not enough idle workers to remove: found {len(idle_workers)}, "
                f"requested {count}. Use force=true to remove busy workers."
            )
        )

    targets = idle_workers[:count]
    if options.force and len(targets) < count:
        remaining = count - len(targets)
        target_names = {w.get("name") for w in targets}
        non_idle = [w for w in workers if w.get("name") not in target_names]
        targets.extend(non_idle[:remaining])

    return targets


def _write_draining_status(
    team_name: str,
    cwd: str,
    targets: list[dict[str, Any]],
) -> None:
    """Phase 1: flip every target to ``state='draining'``."""
    for worker in targets:
        name = worker.get("name")
        if not isinstance(name, str) or not name:
            continue
        write_worker_status(
            cwd,
            team_name,
            name,
            state=TeamWorkerState.DRAINING.value,
            reason="scale_down requested by leader",
        )


def _wait_for_drain(
    team_name: str,
    cwd: str,
    targets: list[dict[str, Any]],
    session_name: str,
    drain_timeout_ms: int,
    poll_interval_s: float = 2.0,
) -> None:
    """Phase 2: poll until every target drains or the deadline passes.

    A worker counts as drained when its state is idle/done/draining or
    its tmux pane is no longer alive. ``poll_interval_s`` is exposed so
    tests can drive the loop to completion without sleeping.
    """
    if not targets:
        return
    deadline_s = time.monotonic() + max(0.0, drain_timeout_ms / 1000.0)
    while True:
        all_drained = True
        for worker in targets:
            name = worker.get("name")
            if not isinstance(name, str) or not name:
                continue
            state = _read_worker_state(team_name, name, cwd)
            if state in _DRAINED_STATES:
                continue
            pane_id = worker.get("pane_id")
            index = int(worker.get("index", 0))
            if not is_worker_alive(
                session_name,
                index,
                pane_id if isinstance(pane_id, str) else None,
            ):
                continue
            all_drained = False
            break

        if all_drained:
            return
        if time.monotonic() >= deadline_s:
            return
        time.sleep(poll_interval_s)


def _teardown_worker_panes(
    targets: list[dict[str, Any]],
    session_name: str,
    leader_pane_id: str | None,
) -> None:
    """Phase 3a: best-effort kill every target's tmux pane.

    ``kill_worker`` already short-circuits when the worker pane id
    equals ``leader_pane_id``; we still pass it explicitly to mirror
    the TS ``teardownWorkerPanes`` contract.
    """
    for worker in targets:
        pane_id = worker.get("pane_id")
        index = int(worker.get("index", 0))
        try:
            kill_worker(
                session_name,
                index,
                pane_id if isinstance(pane_id, str) else None,
                leader_pane_id,
            )
        except Exception:  # noqa: BLE001 — best-effort teardown
            continue


def _collect_detached_worktrees(
    targets: list[dict[str, Any]],
) -> list[EnsureWorktreeResult]:
    """Reconstruct ``EnsureWorktreeResult`` records for detached worktrees.

    Mirrors the TS filter at scaling.ts:748-766: only workers that were
    actually provisioned with a detached worktree are eligible for
    rollback. Branch-mode worktrees are skipped (the TS code does the
    same — branch-mode rollback is owned by the team-level shutdown
    flow).
    """
    out: list[EnsureWorktreeResult] = []
    for worker in targets:
        if worker.get("worktree_created") is not True:
            continue
        if worker.get("worktree_detached") is not True:
            continue
        repo_root = worker.get("worktree_repo_root")
        worktree_path = worker.get("worktree_path")
        if not isinstance(repo_root, str) or not repo_root:
            continue
        if not isinstance(worktree_path, str) or not worktree_path:
            continue
        out.append(
            EnsureWorktreeResult(
                enabled=True,
                repo_root=repo_root,
                worktree_path=worktree_path,
                detached=True,
                branch_name=None,
                created=True,
                reused=False,
                created_branch=False,
            )
        )
    return out


def _rollback_detached_worktrees(
    targets: list[dict[str, Any]],
) -> ScaleError | None:
    """Roll back any detached worktrees provisioned for the targets.

    Returns a :class:`ScaleError` (matching the TS error string
    ``scale_down_worktree_cleanup_failed:...``) when rollback raises.
    """
    detached = _collect_detached_worktrees(targets)
    if not detached:
        return None
    try:
        rollback_provisioned_worktrees(detached)
    except Exception as err:  # noqa: BLE001 — TS catches all and stringifies
        return ScaleError(error=f"scale_down_worktree_cleanup_failed:{err}")
    return None


def _remove_worktree_agents_files(
    team_name: str,
    cwd: str,
    config: dict[str, Any],
    targets: list[dict[str, Any]],
) -> None:
    """Best-effort cleanup of per-worker worktree-root AGENTS.md files."""
    fallback_state_root = config.get("team_state_root")
    if not isinstance(fallback_state_root, str) or not fallback_state_root:
        fallback_state_root = str(_team_dir(team_name, cwd))
    for worker in targets:
        worktree_path = worker.get("worktree_path")
        if not isinstance(worktree_path, str) or not worktree_path:
            continue
        name = worker.get("name")
        if not isinstance(name, str) or not name:
            continue
        team_state_root = worker.get("team_state_root")
        if not isinstance(team_state_root, str) or not team_state_root:
            team_state_root = fallback_state_root
        try:
            remove_worker_worktree_root_agents_file(
                team_name, name, team_state_root, worktree_path
            )
        except Exception:  # noqa: BLE001 — best-effort
            continue


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def scale_down(
    team_name: str,
    cwd: str,
    options: ScaleDownOptions | None = None,
    env: dict[str, str] | None = None,
    *,
    poll_interval_s: float = 2.0,
) -> ScaleDownResult | ScaleError:
    """Drain and remove workers from a running team.

    Args:
        team_name: Caller-supplied team name. Sanitized before use.
        cwd: Working directory rooting the team's ``.omx/team`` state.
        options: Optional :class:`ScaleDownOptions`. ``None`` means
            "remove one idle worker".
        env: Optional environment mapping for the scaling-enabled gate.
            Defaults to :data:`os.environ`.
        poll_interval_s: Sleep between drain polls. Exposed for tests;
            callers should use the default.

    Returns:
        :class:`ScaleDownResult` on success or :class:`ScaleError` on
        any caller-recoverable failure (team missing, unknown worker
        name, minimum-worker guard, worktree rollback).

    Raises:
        RuntimeError: When dynamic scaling is disabled (the env gate is
        asserted before the scaling lock is acquired, matching TS).
        TimeoutError: When the scaling lock cannot be acquired.
    """
    assert_scaling_enabled(env)

    opts = options or ScaleDownOptions()
    sanitized = sanitize_team_name(team_name)
    drain_timeout_ms = opts.drain_timeout_ms
    if not isinstance(drain_timeout_ms, int) or drain_timeout_ms < 0:
        drain_timeout_ms = 30_000

    lock_dir = _team_dir(sanitized, cwd)
    with with_scaling_lock(lock_dir):
        config = read_team_config(cwd, sanitized)
        if not config:
            return ScaleError(error=f"Team {sanitized} not found")

        targets_or_err = _resolve_target_workers(sanitized, cwd, config, opts)
        if isinstance(targets_or_err, ScaleError):
            return targets_or_err
        targets = targets_or_err

        if not targets:
            return ScaleError(error="No workers selected for removal")

        current_workers: list[dict[str, Any]] = list(config.get("workers") or [])
        if len(current_workers) - len(targets) < 1:
            return ScaleError(
                error="Cannot remove all workers — at least 1 must remain"
            )

        session_name = str(config.get("tmux_session") or "")
        leader_pane_id_raw = config.get("leader_pane_id")
        leader_pane_id = (
            leader_pane_id_raw if isinstance(leader_pane_id_raw, str) else None
        )

        # Phase 1 — flip every target to draining.
        _write_draining_status(sanitized, cwd, targets)

        # Phase 2 — wait for drain unless forced.
        if not opts.force:
            _wait_for_drain(
                sanitized,
                cwd,
                targets,
                session_name,
                drain_timeout_ms,
                poll_interval_s=poll_interval_s,
            )

        # Phase 3 — teardown panes + worktrees.
        _teardown_worker_panes(targets, session_name, leader_pane_id)
        rollback_err = _rollback_detached_worktrees(targets)
        if rollback_err is not None:
            return rollback_err
        _remove_worktree_agents_files(sanitized, cwd, config, targets)

        removed_names = [
            w.get("name") for w in targets if isinstance(w.get("name"), str)
        ]

        # Phase 4 — filter, save, emit event.
        removed_set = {name for name in removed_names}
        remaining = [w for w in current_workers if w.get("name") not in removed_set]
        config["workers"] = remaining
        new_count = len(remaining)
        config["worker_count"] = new_count
        write_team_config(cwd, config, sanitized)

        try:
            append_team_event(
                cwd,
                TeamEvent(
                    event_type="team_leader_nudge",
                    timestamp=_now_iso(),
                    worker_id="leader-fixed",
                    detail={
                        "reason": (
                            f"scale_down: removed {len(removed_names)} worker(s) "
                            f"[{', '.join(removed_names)}], new count={new_count}"
                        ),
                    },
                ),
                sanitized,
            )
        except Exception:  # noqa: BLE001 — event emission is best-effort
            pass

        return ScaleDownResult(
            ok=True,
            removed_workers=removed_names,
            new_worker_count=new_count,
        )
