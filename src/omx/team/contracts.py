"""Team contracts — status enums and type definitions.

Port of src/team/contracts.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TaskStatus(StrEnum):
    """Lifecycle status of a team task."""

    PENDING = "pending"
    BLOCKED = "blocked"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class DispatchRequestStatus(StrEnum):
    """Status of a team dispatch request."""

    PENDING = "pending"
    NOTIFIED = "notified"
    DELIVERED = "delivered"
    FAILED = "failed"


class WorkerIntegrationStatus(StrEnum):
    """Git integration status of a worker's branch."""

    IDLE = "idle"
    INTEGRATED = "integrated"
    INTEGRATION_FAILED = "integration_failed"
    CHERRY_PICK_CONFLICT = "cherry_pick_conflict"
    REBASE_CONFLICT = "rebase_conflict"


class TaskApprovalStatus(StrEnum):
    """Approval gate status for a task."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


TERMINAL_TASK_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED}

TEAM_EVENT_TYPES = [
    "task_completed",
    "task_failed",
    "worker_state_changed",
    "worker_idle",
    "worker_stopped",
    "message_received",
    "leader_notification_deferred",
    "all_workers_idle",
    "shutdown_ack",
    "shutdown_gate",
    "worker_merge_conflict",
    "worker_cherry_pick_conflict",
    "worker_rebase_conflict",
    "worker_stale_diff",
    "worker_stale_heartbeat",
    "worker_stale_stdout",
]


@dataclass
class TeamTask:
    """A unit of work assigned to a team worker.

    Attributes:
        task_id: Unique task identifier.
        description: Human-readable task description.
        status: Current lifecycle status.
        owner: Worker ID that owns this task.
        role: Preferred worker role for this task.
        file_paths: File paths in scope for this task.
        domains: Domain tags for routing.
        approval: Approval gate status.
        created_at: ISO timestamp when created.
        started_at: ISO timestamp when work began.
        completed_at: ISO timestamp when completed/failed.
        error: Error message if failed.
    """

    task_id: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    owner: str | None = None
    role: str | None = None
    file_paths: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    approval: TaskApprovalStatus = TaskApprovalStatus.APPROVED
    created_at: str = ""
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "description": self.description,
            "status": self.status.value,
            "approval": self.approval.value,
            "created_at": self.created_at,
        }
        for f in ("owner", "role", "started_at", "completed_at", "error"):
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        if self.file_paths:
            d["file_paths"] = self.file_paths
        if self.domains:
            d["domains"] = self.domains
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamTask:
        return cls(
            task_id=d["task_id"],
            description=d["description"],
            status=TaskStatus(d.get("status", "pending")),
            owner=d.get("owner"),
            role=d.get("role"),
            file_paths=d.get("file_paths", []),
            domains=d.get("domains", []),
            approval=TaskApprovalStatus(d.get("approval", "approved")),
            created_at=d.get("created_at", ""),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
            error=d.get("error"),
        )


@dataclass
class TeamWorker:
    """A worker pane in a team session.

    Attributes:
        worker_id: Unique worker identifier.
        pane_id: Tmux pane ID for this worker.
        role: Agent role assigned to this worker.
        cli: CLI tool in use ("codex" or "claude").
        status: Current worker status ("idle", "busy", "stopped").
        current_task_id: Task currently being worked on.
        assigned_tasks: History of assigned task IDs.
        integration_status: Git integration status.
    """

    worker_id: str
    pane_id: str
    role: str = "executor"
    cli: str = "codex"
    status: str = "idle"
    current_task_id: str | None = None
    assigned_tasks: list[str] = field(default_factory=list)
    integration_status: WorkerIntegrationStatus = WorkerIntegrationStatus.IDLE

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "worker_id": self.worker_id,
            "pane_id": self.pane_id,
            "role": self.role,
            "cli": self.cli,
            "status": self.status,
            "integration_status": self.integration_status.value,
            "assigned_tasks": self.assigned_tasks,
        }
        if self.current_task_id:
            d["current_task_id"] = self.current_task_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamWorker:
        return cls(
            worker_id=d["worker_id"],
            pane_id=d["pane_id"],
            role=d.get("role", "executor"),
            cli=d.get("cli", "codex"),
            status=d.get("status", "idle"),
            current_task_id=d.get("current_task_id"),
            assigned_tasks=d.get("assigned_tasks", []),
            integration_status=WorkerIntegrationStatus(
                d.get("integration_status", "idle")
            ),
        )


@dataclass
class TeamEvent:
    """An event emitted during team orchestration.

    Attributes:
        event_type: Event type name (e.g. "task_completed").
        timestamp: ISO timestamp of the event.
        worker_id: Associated worker ID (if applicable).
        task_id: Associated task ID (if applicable).
        detail: Additional event metadata.
    """

    event_type: str
    timestamp: str
    worker_id: str | None = None
    task_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
        }
        if self.worker_id:
            d["worker_id"] = self.worker_id
        if self.task_id:
            d["task_id"] = self.task_id
        if self.detail:
            d["detail"] = self.detail
        return d
