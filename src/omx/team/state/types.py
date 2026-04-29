"""Team state type definitions.

Port of src/team/state/types.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

DEFAULT_MAX_WORKERS = 20
ABSOLUTE_MAX_WORKERS = 20


class TeamTaskStatus(StrEnum):
    PENDING = "pending"
    BLOCKED = "blocked"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class TeamWorkerState(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    BLOCKED = "blocked"
    DONE = "done"
    FAILED = "failed"
    DRAINING = "draining"
    UNKNOWN = "unknown"


class TeamDispatchRequestKind(StrEnum):
    INBOX = "inbox"
    MAILBOX = "mailbox"
    NUDGE = "nudge"


class TeamDispatchTransportPreference(StrEnum):
    HOOK_PREFERRED = "hook_preferred_with_fallback"
    TRANSPORT_DIRECT = "transport_direct"
    PROMPT_STDIN = "prompt_stdin"


class TeamWorkerIntegrationStatus(StrEnum):
    IDLE = "idle"
    INTEGRATED = "integrated"
    INTEGRATION_FAILED = "integration_failed"
    CHERRY_PICK_CONFLICT = "cherry_pick_conflict"
    REBASE_CONFLICT = "rebase_conflict"


TERMINAL_TASK_STATUSES = {TeamTaskStatus.COMPLETED, TeamTaskStatus.FAILED}


def is_terminal_task_status(status: str) -> bool:
    return status in TERMINAL_TASK_STATUSES


def can_transition_task_status(from_status: str, to_status: str) -> bool:
    """Check if a task status transition is valid."""
    valid = {
        "pending": {"in_progress", "blocked", "failed"},
        "blocked": {"pending", "in_progress", "failed"},
        "in_progress": {"completed", "failed", "blocked", "pending"},
    }
    return to_status in valid.get(from_status, set())


@dataclass
class TeamTaskClaim:
    """Lease-based task claim."""

    owner: str = ""
    token: str = ""
    leased_until: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "token": self.token,
            "leased_until": self.leased_until,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamTaskClaim:
        return cls(
            owner=d.get("owner", ""),
            token=d.get("token", ""),
            leased_until=d.get("leased_until", ""),
        )


@dataclass
class WorkerInfo:
    """Worker metadata."""

    name: str
    index: int = 0
    role: str = "executor"
    worker_cli: str = "codex"
    assigned_tasks: list[str] = field(default_factory=list)
    pid: int | None = None
    pane_id: str = ""
    working_dir: str = ""
    worktree_path: str | None = None
    worktree_branch: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "index": self.index,
            "role": self.role,
            "worker_cli": self.worker_cli,
            "assigned_tasks": self.assigned_tasks,
            "pane_id": self.pane_id,
            "working_dir": self.working_dir,
        }
        if self.pid is not None:
            d["pid"] = self.pid
        if self.worktree_path:
            d["worktree_path"] = self.worktree_path
        if self.worktree_branch:
            d["worktree_branch"] = self.worktree_branch
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkerInfo:
        return cls(
            name=d.get("name", ""),
            index=d.get("index", 0),
            role=d.get("role", "executor"),
            worker_cli=d.get("worker_cli", "codex"),
            assigned_tasks=d.get("assigned_tasks", []),
            pid=d.get("pid"),
            pane_id=d.get("pane_id", ""),
            working_dir=d.get("working_dir", ""),
            worktree_path=d.get("worktree_path"),
            worktree_branch=d.get("worktree_branch"),
        )


@dataclass
class WorkerHeartbeat:
    """Worker heartbeat signal."""

    pid: int = 0
    last_turn_at: str = ""
    turn_count: int = 0
    alive: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "last_turn_at": self.last_turn_at,
            "turn_count": self.turn_count,
            "alive": self.alive,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkerHeartbeat:
        return cls(
            pid=d.get("pid", 0),
            last_turn_at=d.get("last_turn_at", ""),
            turn_count=d.get("turn_count", 0),
            alive=d.get("alive", True),
        )


@dataclass
class WorkerStatus:
    """Worker current state."""

    state: str = "unknown"
    current_task_id: str | None = None
    reason: str | None = None
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"state": self.state, "updated_at": self.updated_at}
        if self.current_task_id:
            d["current_task_id"] = self.current_task_id
        if self.reason:
            d["reason"] = self.reason
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkerStatus:
        return cls(
            state=d.get("state", "unknown"),
            current_task_id=d.get("current_task_id"),
            reason=d.get("reason"),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class TeamConfig:
    """Team configuration."""

    name: str = ""
    task: str = ""
    agent_type: str = "executor"
    worker_launch_mode: str = "interactive"
    worker_count: int = 2
    workers: list[WorkerInfo] = field(default_factory=list)
    tmux_session: str = ""
    next_task_id: int = 1
    lifecycle_profile: str = "default"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "task": self.task,
            "agent_type": self.agent_type,
            "worker_launch_mode": self.worker_launch_mode,
            "worker_count": self.worker_count,
            "workers": [w.to_dict() for w in self.workers],
            "tmux_session": self.tmux_session,
            "next_task_id": self.next_task_id,
            "lifecycle_profile": self.lifecycle_profile,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamConfig:
        return cls(
            name=d.get("name", ""),
            task=d.get("task", ""),
            agent_type=d.get("agent_type", "executor"),
            worker_launch_mode=d.get("worker_launch_mode", "interactive"),
            worker_count=d.get("worker_count", 2),
            workers=[WorkerInfo.from_dict(w) for w in d.get("workers", [])],
            tmux_session=d.get("tmux_session", ""),
            next_task_id=d.get("next_task_id", 1),
            lifecycle_profile=d.get("lifecycle_profile", "default"),
        )


@dataclass
class TeamDispatchRequest:
    """Dispatch request for worker notification."""

    request_id: str = ""
    kind: str = "inbox"
    team_name: str = ""
    to_worker: str = ""
    worker_index: int | None = None
    pane_id: str | None = None
    trigger_message: str = ""
    message_id: str | None = None
    inbox_correlation_key: str | None = None
    transport_preference: str = "transport_direct"
    fallback_allowed: bool = True
    status: str = "pending"
    attempt_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    notified_at: str | None = None
    delivered_at: str | None = None
    failed_at: str | None = None
    last_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "request_id": self.request_id,
            "kind": self.kind,
            "team_name": self.team_name,
            "to_worker": self.to_worker,
            "trigger_message": self.trigger_message,
            "transport_preference": self.transport_preference,
            "fallback_allowed": self.fallback_allowed,
            "status": self.status,
            "attempt_count": self.attempt_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        for f in (
            "worker_index",
            "pane_id",
            "message_id",
            "inbox_correlation_key",
            "notified_at",
            "delivered_at",
            "failed_at",
            "last_reason",
        ):
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamDispatchRequest:
        return cls(
            request_id=d.get("request_id", ""),
            kind=d.get("kind", "inbox"),
            team_name=d.get("team_name", ""),
            to_worker=d.get("to_worker", ""),
            worker_index=d.get("worker_index"),
            pane_id=d.get("pane_id"),
            trigger_message=d.get("trigger_message", ""),
            message_id=d.get("message_id"),
            inbox_correlation_key=d.get("inbox_correlation_key"),
            transport_preference=d.get("transport_preference", "transport_direct"),
            fallback_allowed=d.get("fallback_allowed", True),
            status=d.get("status", "pending"),
            attempt_count=d.get("attempt_count", 0),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            notified_at=d.get("notified_at"),
            delivered_at=d.get("delivered_at"),
            failed_at=d.get("failed_at"),
            last_reason=d.get("last_reason"),
        )


@dataclass
class TeamMailboxMessage:
    """Direct message between workers."""

    message_id: str = ""
    from_worker: str = ""
    to_worker: str = ""
    body: str = ""
    created_at: str = ""
    notified_at: str | None = None
    delivered_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "message_id": self.message_id,
            "from_worker": self.from_worker,
            "to_worker": self.to_worker,
            "body": self.body,
            "created_at": self.created_at,
        }
        if self.notified_at:
            d["notified_at"] = self.notified_at
        if self.delivered_at:
            d["delivered_at"] = self.delivered_at
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamMailboxMessage:
        return cls(
            message_id=d.get("message_id", ""),
            from_worker=d.get("from_worker", ""),
            to_worker=d.get("to_worker", ""),
            body=d.get("body", ""),
            created_at=d.get("created_at", ""),
            notified_at=d.get("notified_at"),
            delivered_at=d.get("delivered_at"),
        )


@dataclass
class TaskApprovalRecord:
    """Task approval decision record."""

    task_id: str = ""
    required: bool = False
    status: str = "pending"  # pending, approved, rejected
    reviewer: str = ""
    decision_reason: str = ""
    decided_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "required": self.required,
            "status": self.status,
            "reviewer": self.reviewer,
            "decision_reason": self.decision_reason,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TaskApprovalRecord:
        return cls(
            task_id=d.get("task_id", ""),
            required=d.get("required", False),
            status=d.get("status", "pending"),
            reviewer=d.get("reviewer", ""),
            decision_reason=d.get("decision_reason", ""),
            decided_at=d.get("decided_at", ""),
        )


@dataclass
class TeamPhaseState:
    """Team phase tracking."""

    current_phase: str = "planning"
    max_fix_attempts: int = 3
    current_fix_attempt: int = 0
    transitions: list[dict[str, str]] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_phase": self.current_phase,
            "max_fix_attempts": self.max_fix_attempts,
            "current_fix_attempt": self.current_fix_attempt,
            "transitions": self.transitions,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamPhaseState:
        return cls(
            current_phase=d.get("current_phase", "planning"),
            max_fix_attempts=d.get("max_fix_attempts", 3),
            current_fix_attempt=d.get("current_fix_attempt", 0),
            transitions=d.get("transitions", []),
            updated_at=d.get("updated_at", ""),
        )


@dataclass
class TeamMonitorSnapshot:
    """Cached team monitor state."""

    task_status_by_id: dict[str, str] = field(default_factory=dict)
    worker_alive_by_name: dict[str, bool] = field(default_factory=dict)
    worker_state_by_name: dict[str, str] = field(default_factory=dict)
    worker_turn_count_by_name: dict[str, int] = field(default_factory=dict)
    worker_task_id_by_name: dict[str, str] = field(default_factory=dict)
    completed_event_task_ids: dict[str, bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "taskStatusById": self.task_status_by_id,
            "workerAliveByName": self.worker_alive_by_name,
            "workerStateByName": self.worker_state_by_name,
            "workerTurnCountByName": self.worker_turn_count_by_name,
            "workerTaskIdByName": self.worker_task_id_by_name,
            "completedEventTaskIds": self.completed_event_task_ids,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamMonitorSnapshot:
        return cls(
            task_status_by_id=d.get("taskStatusById", {}),
            worker_alive_by_name=d.get("workerAliveByName", {}),
            worker_state_by_name=d.get("workerStateByName", {}),
            worker_turn_count_by_name=d.get("workerTurnCountByName", {}),
            worker_task_id_by_name=d.get("workerTaskIdByName", {}),
            completed_event_task_ids=d.get("completedEventTaskIds", {}),
        )
