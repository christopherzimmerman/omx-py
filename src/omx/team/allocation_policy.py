"""Worker allocation policy — task-to-worker assignment scoring.

Port of src/team/allocation-policy.ts.
"""

from __future__ import annotations

from dataclasses import dataclass

from omx.team.contracts import TaskStatus, TeamTask, TeamWorker


@dataclass(frozen=True)
class AllocationDecision:
    """Result of choosing a worker to own a task.

    Attributes:
        owner: Worker ID chosen for the task.
        reason: Human-readable justification for the choice.
    """

    owner: str
    reason: str


def choose_task_owner(
    task: TeamTask,
    workers: list[TeamWorker],
    all_tasks: list[TeamTask],
) -> AllocationDecision | None:
    """Score workers and pick the best one for a task.

    Scoring considers role matching, load balancing, and file path
    scope overlap.

    Args:
        task: The task to assign.
        workers: Available workers to consider.
        all_tasks: All tasks (for load calculation).

    Returns:
        AllocationDecision for the best worker, or None if no workers.
    """
    if not workers:
        return None

    best_worker: TeamWorker | None = None
    best_score = float("-inf")
    best_reason = ""

    for worker in workers:
        score = 0.0
        reason_parts: list[str] = []

        # Role matching
        if task.role and task.role == worker.role:
            score += 18
            reason_parts.append("matches worker role")

        # Load balancing: penalize workers with more assigned tasks
        assigned_count = sum(
            1
            for t in all_tasks
            if t.owner == worker.worker_id
            and t.status not in (TaskStatus.COMPLETED, TaskStatus.FAILED)
        )
        score -= assigned_count * 4
        blocked_count = sum(
            1
            for t in all_tasks
            if t.owner == worker.worker_id and t.status == TaskStatus.BLOCKED
        )
        score -= blocked_count

        # File path scope overlap
        if task.file_paths and worker.assigned_tasks:
            worker_tasks = [t for t in all_tasks if t.task_id in worker.assigned_tasks]
            worker_paths = {p for t in worker_tasks for p in t.file_paths}
            overlap = len(set(task.file_paths) & worker_paths)
            if overlap > 0:
                score += overlap * 3
                reason_parts.append("keeps work grouped")

        if not reason_parts:
            reason_parts.append("balances current load")

        if score > best_score:
            best_score = score
            best_worker = worker
            best_reason = ", ".join(reason_parts)

    if best_worker is None:
        return None

    return AllocationDecision(owner=best_worker.worker_id, reason=best_reason)
