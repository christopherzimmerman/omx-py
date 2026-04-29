"""Dynamic task rebalancing policy.

Port of src/team/rebalance-policy.ts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class RebalanceDecision:
    """A decision to reassign a task from one worker to another."""

    task_id: str
    from_worker: str
    to_worker: str
    reason: str


def build_rebalance_decisions(
    tasks: list[dict[str, Any]],
    workers: list[dict[str, Any]],
) -> list[RebalanceDecision]:
    """Identify tasks that should be rebalanced between workers.

    Args:
        tasks: List of task dicts with status, owner fields.
        workers: List of worker dicts with name, alive fields.

    Returns:
        List of rebalance decisions.
    """
    decisions: list[RebalanceDecision] = []

    dead_workers = {w["name"] for w in workers if not w.get("alive", True)}
    alive_workers = [w for w in workers if w.get("alive", True)]

    if not alive_workers:
        return decisions

    # Find tasks owned by dead workers
    for task in tasks:
        owner = task.get("owner", "")
        if owner in dead_workers and task.get("status") == "in_progress":
            # Assign to least-loaded alive worker
            load = {w["name"]: 0 for w in alive_workers}
            for t in tasks:
                t_owner = t.get("owner", "")
                if t_owner in load and t.get("status") == "in_progress":
                    load[t_owner] += 1
            target = min(load, key=load.get)  # type: ignore[arg-type]
            decisions.append(
                RebalanceDecision(
                    task_id=task.get("id", task.get("task_id", "")),
                    from_worker=owner,
                    to_worker=target,
                    reason=f"worker {owner} is dead",
                )
            )

    return decisions
