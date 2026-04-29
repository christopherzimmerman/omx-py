"""Dynamic worker scaling for team mode.

Port of src/team/scaling.ts.
Handles adding/removing workers based on workload.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from omx.team.state.types import DEFAULT_MAX_WORKERS, ABSOLUTE_MAX_WORKERS


@dataclass
class ScalingDecision:
    """A decision to scale the team up or down."""

    action: str  # "scale_up", "scale_down", "no_change"
    target_count: int
    reason: str


def evaluate_scaling(
    current_workers: int,
    pending_tasks: int,
    in_progress_tasks: int,
    idle_workers: int,
    dead_workers: int,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> ScalingDecision:
    """Evaluate whether the team should scale up or down.

    Args:
        current_workers: Current number of active workers.
        pending_tasks: Number of pending tasks.
        in_progress_tasks: Number of in-progress tasks.
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
    """Resolve the maximum worker count from environment or default.

    Returns:
        Maximum worker count, clamped to ABSOLUTE_MAX_WORKERS.
    """
    env_val = os.environ.get("OMX_TEAM_MAX_WORKERS", "").strip()
    if env_val:
        try:
            val = int(env_val)
            return min(max(1, val), ABSOLUTE_MAX_WORKERS)
        except ValueError:
            pass
    return DEFAULT_MAX_WORKERS
