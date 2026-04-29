"""Mailbox log for inter-worker messaging.

Port of omx-runtime-core/src/mailbox.rs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class MailboxError(Exception):
    """Base error for mailbox operations."""


class MailboxNotFound(MailboxError):
    """Raised when a mailbox record cannot be found by message_id."""

    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        super().__init__(f"mailbox record not found: {message_id}")


class MailboxAlreadyDelivered(MailboxError):
    """Raised when attempting to transition an already-delivered message."""

    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        super().__init__(f"mailbox message already delivered: {message_id}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class MailboxRecord:
    """A single inter-worker mailbox message with delivery timestamps.

    Attributes:
        message_id: Unique message identifier.
        from_worker: Sender worker ID.
        to_worker: Recipient worker ID.
        body: Message content.
        created_at: ISO timestamp when created.
        notified_at: ISO timestamp when recipient was notified.
        delivered_at: ISO timestamp when recipient acknowledged delivery.
    """

    message_id: str
    from_worker: str
    to_worker: str
    body: str
    created_at: str
    notified_at: str | None = None
    delivered_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "from_worker": self.from_worker,
            "to_worker": self.to_worker,
            "body": self.body,
            "created_at": self.created_at,
            "notified_at": self.notified_at,
            "delivered_at": self.delivered_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MailboxRecord:
        return cls(
            message_id=d["message_id"],
            from_worker=d["from_worker"],
            to_worker=d["to_worker"],
            body=d.get("body", ""),
            created_at=d["created_at"],
            notified_at=d.get("notified_at"),
            delivered_at=d.get("delivered_at"),
        )


@dataclass
class MailboxLog:
    """Ordered log of inter-worker mailbox messages."""

    records: list[MailboxRecord] = field(default_factory=list)

    def create(
        self, message_id: str, from_worker: str, to_worker: str, body: str
    ) -> None:
        self.records.append(
            MailboxRecord(
                message_id=message_id,
                from_worker=from_worker,
                to_worker=to_worker,
                body=body,
                created_at=_now_iso(),
            )
        )

    def mark_notified(self, message_id: str) -> None:
        record = self._find(message_id)
        if record.delivered_at is not None:
            raise MailboxAlreadyDelivered(message_id)
        record.notified_at = _now_iso()

    def mark_delivered(self, message_id: str) -> None:
        record = self._find(message_id)
        if record.delivered_at is not None:
            raise MailboxAlreadyDelivered(message_id)
        record.delivered_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {"records": [r.to_dict() for r in self.records]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MailboxLog:
        return cls(records=[MailboxRecord.from_dict(r) for r in d.get("records", [])])

    def _find(self, message_id: str) -> MailboxRecord:
        for record in self.records:
            if record.message_id == message_id:
                return record
        raise MailboxNotFound(message_id)
