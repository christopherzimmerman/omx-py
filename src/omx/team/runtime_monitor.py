"""TS-parity ``monitorTeam`` port.

Port of ``monitorTeam`` from ``src/team/runtime.ts:2587-2833``. Produces a
structured :class:`omx.team.runtime_types.TeamSnapshot` with detailed task
counts, dead-worker detection, non-reporting detection, recommendations, and
optional performance metrics.

This is a Phase 2.8c port. The Phase 1 simple
:func:`omx.team.runtime.monitor_team` (returns a dict) is preserved unchanged
for back-compat; the new TS-style function is exposed here as
:func:`monitor_team_ts`.

Sync conversion: per-worker reads (status, heartbeat, liveness) run in
parallel via :class:`concurrent.futures.ThreadPoolExecutor` to mirror the TS
``Promise.all`` worker scan.

Simplifications vs TS (intentionally deferred to later phases):

- ``readTeamManifestV2`` / ``resolveDispatchPolicy`` — no manifest needed
  for the snapshot view this function returns.
- ``reclaimExpiredTaskClaim`` — claim reclamation belongs to the dispatch
  loop, not the snapshot path.
- ``buildRebalanceDecisions`` / inline ``assignTask`` — monitor-time
  auto-assignment is part of the larger dispatch port.
- ``hasStructuredVerificationEvidence`` — verification-gate logic ports
  with the QA pipeline.
- ``emitMonitorDerivedEvents`` / ``integrateWorkerCommitsIntoLeader`` /
  ``deliverPendingMailboxMessages`` / leader mailbox pruning — separate
  subsystems.
- ``syncRootTeamModeStateOnTerminalPhase`` / ``reconcilePhaseStateForMonitor``
  — phase reconciliation only reads the persisted phase state; it does not
  write a new one (the snapshot is read-mostly).
- ``isPromptWorkerAlive`` — prompt-mode liveness falls back to
  :func:`omx.team.tmux_session.is_worker_alive` for now.

The :class:`TeamSnapshot` shape and required fields (counts, dead workers,
non-reporting workers, recommendations, performance) match TS line-for-line.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from omx.team.contracts import TaskStatus, TeamTask
from omx.team.followup_planner import all_tasks_terminal
from omx.team.runtime_types import (
    TeamSnapshot,
    TeamSnapshotPerformance,
    TeamSnapshotTasks,
    TeamSnapshotWorker,
)
from omx.team.state.io import read_worker_heartbeat, read_worker_status
from omx.team.team_ops import (
    team_list_tasks,
    team_read_config,
    team_read_monitor_snapshot,
    team_read_phase,
)
from omx.team.tmux_session import is_worker_alive, sanitize_team_name

# Maximum parallel worker reads. Mirrors a safe default for stdlib
# ``ThreadPoolExecutor``; large enough to overlap I/O for typical teams
# (<= 12 workers per AGENTS.md) and bounded enough to avoid thread thrash.
_DEFAULT_WORKER_SCAN_WORKERS = 8

# Turns-without-progress threshold for the "non-reporting" recommendation.
# Mirrors TS literal at runtime.ts:2672.
_NON_REPORTING_TURN_THRESHOLD = 5


def _now_ms() -> float:
    """Monotonic millisecond clock for perf timings."""
    return time.perf_counter() * 1000.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _round2(value: float) -> float:
    """Round to two decimal places, mirroring TS ``Number(x.toFixed(2))``."""
    return float(f"{value:.2f}")


def _scan_one_worker(
    cwd: str,
    sanitized: str,
    session_name: str,
    worker: dict[str, Any],
) -> dict[str, Any]:
    """Read liveness, status, heartbeat for a single worker.

    Mirrors the inline lambda inside the TS ``Promise.all(config.workers.map(...))``
    block (runtime.ts:2624-2635). Returns a plain dict so the function is
    pickle-friendly for ``ThreadPoolExecutor`` and easy to assert on in tests.
    """
    worker_name = str(worker.get("name") or worker.get("worker_id") or "")
    worker_index = int(worker.get("index", 0))
    pane_id = worker.get("pane_id") or None

    alive = (
        is_worker_alive(session_name, worker_index, pane_id) if session_name else False
    )
    status = read_worker_status(cwd, sanitized, worker_name) or {}
    heartbeat = read_worker_heartbeat(cwd, sanitized, worker_name)

    return {
        "worker": worker,
        "name": worker_name,
        "alive": bool(alive),
        "status": status,
        "heartbeat": heartbeat,
    }


def _build_task_counts(tasks: list[TeamTask]) -> TeamSnapshotTasks:
    """Aggregate task counts by status, mirroring TS lines 2713-2720."""
    counts = TeamSnapshotTasks(items=list(tasks))
    counts.total = len(tasks)
    for t in tasks:
        if t.status == TaskStatus.PENDING:
            counts.pending += 1
        elif t.status == TaskStatus.BLOCKED:
            counts.blocked += 1
        elif t.status == TaskStatus.IN_PROGRESS:
            counts.in_progress += 1
        elif t.status == TaskStatus.COMPLETED:
            counts.completed += 1
        elif t.status == TaskStatus.FAILED:
            counts.failed += 1
    return counts


def _compute_turns_without_progress(
    *,
    heartbeat: dict[str, Any] | None,
    status: dict[str, Any],
    current_task: TeamTask | None,
    current_task_id: str,
    previous_turns: int | None,
    previous_task_id: str,
) -> int:
    """Port of the ``turnsWithoutProgress`` heuristic (runtime.ts:2643-2652).

    Returns ``max(0, heartbeat.turn_count - previous_turns)`` when *every*
    guard holds: heartbeat present, previous-turn count known, worker state
    is ``working``, current task exists and is pending/in_progress, and the
    worker has stayed on the same task since the previous snapshot. Returns
    0 otherwise.
    """
    if heartbeat is None or previous_turns is None:
        return 0
    if status.get("state") != "working":
        return 0
    if current_task is None:
        return 0
    if current_task.status not in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
        return 0
    if not current_task_id or previous_task_id != current_task_id:
        return 0
    turn_count = int(heartbeat.get("turn_count", 0))
    return max(0, turn_count - previous_turns)


def _build_recommendations(
    *,
    workers: list[TeamSnapshotWorker],
    dead_workers: list[str],
    non_reporting_workers: list[str],
    in_progress_by_owner: dict[str, list[TeamTask]],
    task_counts: TeamSnapshotTasks,
    all_terminal: bool,
    workers_total: int,
    worker_launch_mode: str,
) -> tuple[list[str], bool]:
    """Build the recommendations list and the ``deadWorkerStall`` flag.

    Mirrors the recommendation/phase logic at runtime.ts:2663-2753 (minus
    the rebalance/auto-assign and verification-evidence branches, which are
    deferred to later phases). The Python port adds a "pending without idle"
    hint that the simple Phase 1 monitor already produced, kept for callers
    that consume the snapshot in CLIs.
    """
    recs: list[str] = []

    # Dead worker rebalance hints (runtime.ts:2666-2670).
    for name in dead_workers:
        for t in in_progress_by_owner.get(name, []):
            recs.append(f"Reassign task-{t.task_id} from dead {name}")

    # Non-reporting hints (runtime.ts:2672-2675).
    for name in non_reporting_workers:
        recs.append(f"Send reminder to non-reporting {name}")

    # Pending tasks with no dead-worker blockers — informational hint kept
    # from the Phase 1 monitor for CLI parity.
    if task_counts.pending > 0 and not dead_workers:
        recs.append(f"{task_counts.pending} pending tasks ready for assignment")

    # Blocked tasks — informational hint.
    if task_counts.blocked > 0:
        recs.append(f"{task_counts.blocked} blocked tasks waiting on dependencies")

    # Dead worker stall detection (runtime.ts:2734-2738, 2751-2753).
    dead_worker_stall = (
        worker_launch_mode == "prompt"
        and workers_total > 0
        and len(dead_workers) >= workers_total
        and not all_terminal
    )
    if dead_worker_stall:
        recs.append(
            "All workers are dead while work remains; mark the team failed "
            "or restart with fresh workers."
        )

    return recs, dead_worker_stall


def _resolve_phase(
    *,
    persisted_phase: str | None,
    dead_worker_stall: bool,
    all_terminal: bool,
    task_counts: TeamSnapshotTasks,
) -> str:
    """Resolve the current phase string for the snapshot.

    Mirrors the simplified phase-resolution branch at runtime.ts:2740-2749.
    The full ``reconcilePhaseStateForMonitor`` write-back is deferred; here
    we *infer* the target phase but do not persist a new value (read-only
    snapshot path). Persisted phase wins unless we observe a terminal/stall
    transition that the persisted value cannot represent.
    """
    if dead_worker_stall:
        return "failed"

    if all_terminal and task_counts.total > 0:
        if task_counts.failed > 0:
            # Failed tasks present — surface as "failed" only if no completions
            # are still being audited. Otherwise prefer the persisted phase.
            if task_counts.completed == 0:
                return "failed"
        else:
            return "complete"

    if persisted_phase:
        return persisted_phase

    # No persisted phase yet — derive a sensible default from task shape.
    if task_counts.total == 0:
        return "team-plan"
    return "team-exec"


def monitor_team_ts(
    team_name: str,
    cwd: str,
    *,
    measure_performance: bool = True,
    max_parallel_workers: int = _DEFAULT_WORKER_SCAN_WORKERS,
) -> TeamSnapshot | None:
    """TS-parity team snapshot.

    Port of :func:`monitorTeam` (``team/runtime.ts:2587-2833``).

    Args:
        team_name: Team identifier (sanitized internally to match TS).
        cwd: Absolute working directory the team lives under.
        measure_performance: When ``True`` (default), include a
            :class:`TeamSnapshotPerformance` section. Set to ``False`` for
            deterministic unit tests.
        max_parallel_workers: Upper bound on the
            :class:`~concurrent.futures.ThreadPoolExecutor` size used for
            the worker scan. Defaults to ``8``; teams with fewer workers
            scale the pool down to the worker count.

    Returns:
        A populated :class:`TeamSnapshot`, or ``None`` when no team config
        exists at ``cwd`` for ``team_name`` (mirrors TS line 2591).
    """
    monitor_start_ms = _now_ms()
    sanitized = sanitize_team_name(team_name)

    config = team_read_config(cwd, sanitized)
    if not config:
        return None

    session_name = str(config.get("tmux_session") or "")
    worker_launch_mode = str(config.get("worker_launch_mode") or "interactive")
    config_workers: list[dict[str, Any]] = [
        w for w in (config.get("workers") or []) if isinstance(w, dict)
    ]

    # --- Tasks --------------------------------------------------------------
    list_tasks_start_ms = _now_ms()
    task_view = team_list_tasks(cwd, sanitized)
    list_tasks_ms = _now_ms() - list_tasks_start_ms

    task_by_id: dict[str, TeamTask] = {t.task_id: t for t in task_view}
    in_progress_by_owner: dict[str, list[TeamTask]] = {}
    for t in task_view:
        if t.status != TaskStatus.IN_PROGRESS or not t.owner:
            continue
        in_progress_by_owner.setdefault(t.owner, []).append(t)

    # --- Previous snapshot for turns-without-progress -----------------------
    previous_snapshot = team_read_monitor_snapshot(sanitized, cwd)
    previous_turns_by_name: dict[str, int] = (
        dict(previous_snapshot.worker_turn_count_by_name) if previous_snapshot else {}
    )
    previous_task_by_name: dict[str, str] = (
        dict(previous_snapshot.worker_task_id_by_name) if previous_snapshot else {}
    )

    # --- Worker scan (parallel) --------------------------------------------
    worker_scan_start_ms = _now_ms()
    worker_signals: list[dict[str, Any]] = []
    if config_workers:
        pool_size = max(1, min(max_parallel_workers, len(config_workers)))
        with ThreadPoolExecutor(max_workers=pool_size) as executor:
            futures = [
                executor.submit(_scan_one_worker, cwd, sanitized, session_name, worker)
                for worker in config_workers
            ]
            for fut in futures:
                worker_signals.append(fut.result())
    worker_scan_ms = _now_ms() - worker_scan_start_ms

    # --- Per-worker snapshot + classification -------------------------------
    workers: list[TeamSnapshotWorker] = []
    dead_workers: list[str] = []
    non_reporting_workers: list[str] = []

    for signal in worker_signals:
        worker_dict = signal["worker"]
        name = signal["name"]
        alive = bool(signal["alive"])
        status: dict[str, Any] = signal["status"] or {}
        heartbeat: dict[str, Any] | None = signal["heartbeat"]

        current_task_id = str(status.get("current_task_id") or "")
        current_task = task_by_id.get(current_task_id) if current_task_id else None

        previous_turns = previous_turns_by_name.get(name) if previous_snapshot else None
        previous_task_id = (
            previous_task_by_name.get(name, "") if previous_snapshot else ""
        )

        turns_without_progress = _compute_turns_without_progress(
            heartbeat=heartbeat,
            status=status,
            current_task=current_task,
            current_task_id=current_task_id,
            previous_turns=previous_turns,
            previous_task_id=previous_task_id,
        )

        assigned = worker_dict.get("assigned_tasks") or []
        if not isinstance(assigned, list):
            assigned = []

        workers.append(
            TeamSnapshotWorker(
                name=name,
                alive=alive,
                status=status,
                heartbeat=heartbeat,
                assigned_tasks=list(assigned),
                turns_without_progress=turns_without_progress,
            )
        )

        if not alive:
            dead_workers.append(name)
        elif turns_without_progress > _NON_REPORTING_TURN_THRESHOLD:
            non_reporting_workers.append(name)

    # --- Task counts + terminal flag ---------------------------------------
    task_counts = _build_task_counts(task_view)
    all_terminal = all_tasks_terminal(task_view)

    # --- Recommendations + phase -------------------------------------------
    recommendations, dead_worker_stall = _build_recommendations(
        workers=workers,
        dead_workers=dead_workers,
        non_reporting_workers=non_reporting_workers,
        in_progress_by_owner=in_progress_by_owner,
        task_counts=task_counts,
        all_terminal=all_terminal,
        workers_total=len(config_workers),
        worker_launch_mode=worker_launch_mode,
    )

    persisted_phase_state = team_read_phase(sanitized, cwd)
    persisted_phase = (
        persisted_phase_state.current_phase if persisted_phase_state else None
    )
    phase = _resolve_phase(
        persisted_phase=persisted_phase,
        dead_worker_stall=dead_worker_stall,
        all_terminal=all_terminal,
        task_counts=task_counts,
    )

    # --- Performance -------------------------------------------------------
    performance: TeamSnapshotPerformance | None = None
    if measure_performance:
        total_ms = _now_ms() - monitor_start_ms
        performance = TeamSnapshotPerformance(
            list_tasks_ms=_round2(list_tasks_ms),
            worker_scan_ms=_round2(worker_scan_ms),
            # Mailbox delivery is not part of this port; leave at 0.0.
            mailbox_delivery_ms=0.0,
            total_ms=_round2(total_ms),
            updated_at=_now_iso(),
        )

    return TeamSnapshot(
        team_name=sanitized,
        phase=phase,
        workers=workers,
        tasks=task_counts,
        all_tasks_terminal=all_terminal,
        dead_workers=dead_workers,
        non_reporting_workers=non_reporting_workers,
        recommendations=recommendations,
        performance=performance,
    )


__all__ = ["monitor_team_ts"]
