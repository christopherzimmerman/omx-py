"""Task dependency and follow-up planning.

Port of src/team/followup-planner.ts.
"""

from __future__ import annotations

from omx.team.contracts import TaskStatus, TeamTask


def get_pending_tasks(tasks: list[TeamTask]) -> list[TeamTask]:
    """Get all tasks that are ready to be assigned."""
    return [t for t in tasks if t.status == TaskStatus.PENDING]


def get_blocked_tasks(tasks: list[TeamTask]) -> list[TeamTask]:
    """Get all blocked tasks."""
    return [t for t in tasks if t.status == TaskStatus.BLOCKED]


def get_completed_tasks(tasks: list[TeamTask]) -> list[TeamTask]:
    """Get all completed tasks."""
    return [t for t in tasks if t.status == TaskStatus.COMPLETED]


def all_tasks_terminal(tasks: list[TeamTask]) -> bool:
    """Check if all tasks have reached a terminal state."""
    if not tasks:
        return True
    return all(t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED) for t in tasks)
