"""Team task state management — claim, transition, release.

Port of src/team/state/tasks.ts.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import StrEnum
from typing import Any

from omx.team.state.types import (
    TeamTaskClaim,
    TeamTaskStatus,
    can_transition_task_status,
    is_terminal_task_status,
)

CLAIM_LEASE_MINUTES = 15


# --- Bulk task CRUD (TS parity: createTask / readTask / updateTask / listTasks) ---
#
# Storage divergence from TS: TS V2 uses per-task files at
# `tasks/task-{id}.json` for finer-grained locking. The Python port currently
# uses a bulk `tasks.json` array. These wrappers preserve the TS public surface
# while storing in the bulk file; per-task storage migration is deferred.


def create_task(
    cwd: str,
    team_name: str,
    description: str,
    role: str | None = None,
    file_paths: list[str] | None = None,
    depends_on: list[str] | None = None,
    status: str = "pending",
    owner: str | None = None,
) -> "TeamTask":
    """Create a new task and persist it.

    TS source: state.ts::createTask. Increments ``next_task_id`` in team config
    after the task is safely persisted.

    Returns the created TeamTask.
    """
    from omx.team.contracts import TaskStatus, TeamTask
    from omx.team.state.io import (
        read_team_config,
        read_tasks,
        write_tasks,
        write_team_config,
    )

    config = read_team_config(cwd, team_name)
    next_id = int(config.get("next_task_id", 1))
    task_id = str(next_id)

    task = TeamTask(
        task_id=task_id,
        description=description,
        role=role,
        file_paths=file_paths or [],
        depends_on=depends_on,
        status=TaskStatus(status),
        owner=owner,
        created_at=_now_iso(),
    )

    tasks = read_tasks(cwd, team_name)
    tasks.append(task)
    write_tasks(cwd, tasks, team_name)

    config["next_task_id"] = next_id + 1
    write_team_config(cwd, config, team_name)

    return task


def read_task(cwd: str, team_name: str, task_id: str) -> "TeamTask | None":
    """Read a single task by id; returns None if absent."""
    from omx.team.state.io import read_tasks

    for task in read_tasks(cwd, team_name):
        if task.task_id == task_id:
            return task
    return None


def list_tasks(cwd: str, team_name: str) -> "list[TeamTask]":
    """List all tasks for a team."""
    from omx.team.state.io import read_tasks

    return read_tasks(cwd, team_name)


def update_task(
    cwd: str,
    team_name: str,
    task_id: str,
    updates: dict[str, Any],
) -> "TeamTask | None":
    """Merge ``updates`` into the task with ``task_id`` and persist atomically.

    Returns the updated TeamTask, or None if the task doesn't exist.
    Raises ValueError on invalid ``status`` updates (TS parity).
    """
    from omx.team.contracts import TeamTask
    from omx.team.state.io import read_tasks, write_tasks

    if "status" in updates and updates["status"] not in (
        "pending",
        "blocked",
        "in_progress",
        "completed",
        "failed",
    ):
        raise ValueError(f"Invalid task status: {updates['status']}")

    tasks = read_tasks(cwd, team_name)
    found_index = -1
    for i, t in enumerate(tasks):
        if t.task_id == task_id:
            found_index = i
            break
    if found_index < 0:
        return None

    existing = tasks[found_index]
    existing_dict = existing.to_dict()
    merged = {**existing_dict, **updates}
    # Preserve immutable fields
    merged["task_id"] = existing.task_id
    merged["created_at"] = existing.created_at
    # depends_on/blocked_by precedence (TS parity)
    if (
        "depends_on" not in updates
        and "blocked_by" not in updates
        and existing.depends_on is None
        and existing.blocked_by is not None
    ):
        merged["depends_on"] = list(existing.blocked_by)

    updated = TeamTask.from_dict(merged)
    tasks[found_index] = updated
    write_tasks(cwd, tasks, team_name)
    return updated


class TaskReadinessReason(StrEnum):
    """Reason a task is not ready to be claimed."""

    BLOCKED_DEPENDENCY = "blocked_dependency"


@dataclass
class TaskReadiness:
    """Result of computing whether a task can be claimed.

    Mirrors the TS discriminated union:
        | { ready: true }
        | { ready: false; reason: 'blocked_dependency'; dependencies: string[] }

    When ``ready`` is True, ``reason`` and ``dependencies`` are unset/empty.
    When ``ready`` is False, ``reason`` is set and ``dependencies`` lists the
    task IDs that are blocking readiness (incomplete deps or missing deps).
    """

    ready: bool
    reason: TaskReadinessReason | None = None
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        if self.ready:
            return {"ready": True}
        return {
            "ready": False,
            "reason": self.reason.value if self.reason else None,
            "dependencies": list(self.dependencies),
        }


def compute_task_readiness(cwd: str, team_name: str, task_id: str) -> TaskReadiness:
    """Compute whether a task is ready to be claimed.

    A task is ready iff every task ID in ``depends_on`` (falling back to
    ``blocked_by``) resolves to a task with status ``completed``. Missing
    dependency IDs and dependencies in any non-completed status (including
    ``failed``) are reported as blockers, matching the TS implementation.

    Port of src/team/state/tasks.ts::computeTaskReadiness. The TS version
    is parameterized over a ``readTask`` dependency; here we read the tasks
    file once and resolve dependencies in-memory.

    Args:
        cwd: Working directory containing the .omx team state.
        team_name: Team name (used as the team-state directory key).
        task_id: ID of the task whose readiness is being computed.

    Returns:
        TaskReadiness describing the result.
    """
    # Local import to avoid a circular import: state.io imports contracts
    # which is fine, but keep this lazy in case io grows other dependencies.
    from omx.team.contracts import TaskStatus
    from omx.team.state.io import read_tasks

    tasks = read_tasks(cwd, team_name)
    by_id: dict[str, Any] = {t.task_id: t for t in tasks}

    task = by_id.get(task_id)
    if task is None:
        return TaskReadiness(
            ready=False,
            reason=TaskReadinessReason.BLOCKED_DEPENDENCY,
            dependencies=[],
        )

    dep_ids: list[str] = []
    if task.depends_on is not None:
        dep_ids = list(task.depends_on)
    elif task.blocked_by is not None:
        dep_ids = list(task.blocked_by)

    if not dep_ids:
        return TaskReadiness(ready=True)

    incomplete: list[str] = []
    for dep_id in dep_ids:
        dep = by_id.get(dep_id)
        # Missing dep or dep not in 'completed' state is incomplete (TS parity).
        if dep is None or dep.status != TaskStatus.COMPLETED:
            incomplete.append(dep_id)

    if incomplete:
        return TaskReadiness(
            ready=False,
            reason=TaskReadinessReason.BLOCKED_DEPENDENCY,
            dependencies=incomplete,
        )

    return TaskReadiness(ready=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_until() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=CLAIM_LEASE_MINUTES)
    ).isoformat()


def _is_claim_expired(claim: TeamTaskClaim) -> bool:
    """Check if a task claim lease has expired."""
    if not claim.leased_until:
        return True
    try:
        expiry = datetime.fromisoformat(claim.leased_until.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > expiry
    except (ValueError, TypeError):
        return True


def claim_task(
    task_data: dict[str, Any],
    worker_name: str,
) -> dict[str, Any]:
    """Claim a task for a worker, acquiring a lease token.

    Args:
        task_data: The task dict to claim.
        worker_name: Name of the claiming worker.

    Returns:
        Dict with "ok", "task", "claim_token", and optional "error".
    """
    status = task_data.get("status", "pending")

    if is_terminal_task_status(status):
        return {"ok": False, "error": f"task is already terminal: {status}"}

    existing_claim = task_data.get("claim")
    if existing_claim and isinstance(existing_claim, dict):
        claim = TeamTaskClaim.from_dict(existing_claim)
        if claim.owner and claim.owner != worker_name and not _is_claim_expired(claim):
            return {"ok": False, "error": f"task claimed by {claim.owner}"}

    token = uuid.uuid4().hex[:16]
    task_data["status"] = TeamTaskStatus.IN_PROGRESS
    task_data["claim"] = TeamTaskClaim(
        owner=worker_name,
        token=token,
        leased_until=_lease_until(),
    ).to_dict()
    task_data["owner"] = worker_name

    return {"ok": True, "task": task_data, "claim_token": token}


def transition_task_status(
    task_data: dict[str, Any],
    from_status: str,
    to_status: str,
    claim_token: str,
    terminal_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transition a task's status, validating claim ownership.

    Args:
        task_data: The task dict.
        from_status: Expected current status.
        to_status: Target status.
        claim_token: Claim token for authorization.
        terminal_data: Optional result/error data for terminal states.

    Returns:
        Dict with "ok", "task", and optional "error".
    """
    current = task_data.get("status", "pending")
    if current != from_status:
        return {
            "ok": False,
            "error": f"status mismatch: expected {from_status}, got {current}",
        }

    if not can_transition_task_status(from_status, to_status):
        return {
            "ok": False,
            "error": f"invalid transition: {from_status} -> {to_status}",
        }

    # Validate claim token
    existing_claim = task_data.get("claim")
    if existing_claim and isinstance(existing_claim, dict):
        if existing_claim.get("token") != claim_token:
            return {"ok": False, "error": "claim token mismatch"}

    task_data["status"] = to_status

    if is_terminal_task_status(to_status):
        task_data["completed_at"] = _now_iso()
        if terminal_data:
            if "result" in terminal_data:
                task_data["result"] = terminal_data["result"]
            if "error" in terminal_data:
                task_data["error"] = terminal_data["error"]

    return {"ok": True, "task": task_data}


def release_task_claim(
    task_data: dict[str, Any],
    claim_token: str,
) -> dict[str, Any]:
    """Release a task claim, returning it to pending.

    Args:
        task_data: The task dict.
        claim_token: Claim token for authorization.

    Returns:
        Dict with "ok", "task", and optional "error".
    """
    existing_claim = task_data.get("claim")
    if existing_claim and isinstance(existing_claim, dict):
        if existing_claim.get("token") != claim_token:
            return {"ok": False, "error": "claim token mismatch"}

    task_data["status"] = TeamTaskStatus.PENDING
    task_data["claim"] = None
    task_data["owner"] = None
    return {"ok": True, "task": task_data}


def reclaim_expired_task(task_data: dict[str, Any]) -> dict[str, Any]:
    """Reclaim a task with an expired lease, returning it to pending.

    Args:
        task_data: The task dict.

    Returns:
        Dict with "ok", "task", "reclaimed", and optional "error".
    """
    existing_claim = task_data.get("claim")
    if not existing_claim or not isinstance(existing_claim, dict):
        return {"ok": True, "task": task_data, "reclaimed": False}

    claim = TeamTaskClaim.from_dict(existing_claim)
    if not _is_claim_expired(claim):
        return {"ok": True, "task": task_data, "reclaimed": False}

    task_data["status"] = TeamTaskStatus.PENDING
    task_data["claim"] = None
    task_data["owner"] = None
    return {"ok": True, "task": task_data, "reclaimed": True}
