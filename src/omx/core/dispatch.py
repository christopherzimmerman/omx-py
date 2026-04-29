"""Dispatch log and status transitions.

Port of omx-runtime-core/src/dispatch.rs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from omx.core.types import BacklogSnapshot


class DispatchStatus(StrEnum):
    PENDING = "pending"
    NOTIFIED = "notified"
    DELIVERED = "delivered"
    FAILED = "failed"


class DispatchError(Exception):
    """Base error for dispatch operations."""


class DispatchNotFound(DispatchError):
    """Raised when a dispatch record cannot be found by request_id."""

    def __init__(self, request_id: str) -> None:
        self.request_id = request_id
        super().__init__(f"dispatch record not found: {request_id}")


class InvalidTransition(DispatchError):
    """Raised when a dispatch status transition violates the state machine."""

    def __init__(
        self, request_id: str, from_status: DispatchStatus, to_status: DispatchStatus
    ) -> None:
        self.request_id = request_id
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(
            f"invalid transition for {request_id}: {from_status} -> {to_status}"
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class DispatchRecord:
    """A single dispatch request with lifecycle timestamps.

    Attributes:
        request_id: Unique identifier for this dispatch.
        target: Delivery target (e.g. tmux pane handle).
        status: Current lifecycle status.
        created_at: ISO timestamp when queued.
        notified_at: ISO timestamp when marked notified.
        delivered_at: ISO timestamp when delivery confirmed.
        failed_at: ISO timestamp when marked failed.
        reason: Channel name (on notify) or failure reason.
        metadata: Arbitrary payload attached to the dispatch.
    """

    request_id: str
    target: str
    status: DispatchStatus
    created_at: str
    notified_at: str | None = None
    delivered_at: str | None = None
    failed_at: str | None = None
    reason: str | None = None
    metadata: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "request_id": self.request_id,
            "target": self.target,
            "status": self.status.value,
            "created_at": self.created_at,
        }
        for f in ("notified_at", "delivered_at", "failed_at", "reason", "metadata"):
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DispatchRecord:
        return cls(
            request_id=d["request_id"],
            target=d["target"],
            status=DispatchStatus(d["status"]),
            created_at=d["created_at"],
            notified_at=d.get("notified_at"),
            delivered_at=d.get("delivered_at"),
            failed_at=d.get("failed_at"),
            reason=d.get("reason"),
            metadata=d.get("metadata"),
        )


@dataclass
class DispatchLog:
    """Ordered log of dispatch records with status transition methods."""

    records: list[DispatchRecord] = field(default_factory=list)

    def queue(self, request_id: str, target: str, metadata: Any | None = None) -> None:
        self.records.append(
            DispatchRecord(
                request_id=request_id,
                target=target,
                status=DispatchStatus.PENDING,
                created_at=_now_iso(),
                metadata=metadata,
            )
        )

    def mark_notified(self, request_id: str, channel: str) -> None:
        record = self._find(request_id)
        if record.status != DispatchStatus.PENDING:
            raise InvalidTransition(request_id, record.status, DispatchStatus.NOTIFIED)
        record.status = DispatchStatus.NOTIFIED
        record.notified_at = _now_iso()
        record.reason = channel

    def mark_delivered(self, request_id: str) -> None:
        record = self._find(request_id)
        if record.status != DispatchStatus.NOTIFIED:
            raise InvalidTransition(request_id, record.status, DispatchStatus.DELIVERED)
        record.status = DispatchStatus.DELIVERED
        record.delivered_at = _now_iso()

    def mark_failed(self, request_id: str, reason: str) -> None:
        record = self._find(request_id)
        if record.status not in (DispatchStatus.PENDING, DispatchStatus.NOTIFIED):
            raise InvalidTransition(request_id, record.status, DispatchStatus.FAILED)
        record.status = DispatchStatus.FAILED
        record.failed_at = _now_iso()
        record.reason = reason

    def to_backlog_snapshot(self) -> BacklogSnapshot:
        snap = BacklogSnapshot()
        for record in self.records:
            match record.status:
                case DispatchStatus.PENDING:
                    snap.pending += 1
                case DispatchStatus.NOTIFIED:
                    snap.notified += 1
                case DispatchStatus.DELIVERED:
                    snap.delivered += 1
                case DispatchStatus.FAILED:
                    snap.failed += 1
        return snap

    def to_dict(self) -> dict[str, Any]:
        return {"records": [r.to_dict() for r in self.records]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> DispatchLog:
        return cls(records=[DispatchRecord.from_dict(r) for r in d.get("records", [])])

    def _find(self, request_id: str) -> DispatchRecord:
        for record in self.records:
            if record.request_id == request_id:
                return record
        raise DispatchNotFound(request_id)
