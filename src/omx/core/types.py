"""Core type definitions — enums, dataclasses, commands, events, snapshots.

Port of omx-runtime-core/src/lib.rs types.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


RUNTIME_SCHEMA_VERSION: int = 1

RUNTIME_COMMAND_NAMES: list[str] = [
    "acquire-authority",
    "renew-authority",
    "queue-dispatch",
    "mark-notified",
    "mark-delivered",
    "mark-failed",
    "request-replay",
    "capture-snapshot",
    "create-mailbox-message",
    "mark-mailbox-notified",
    "mark-mailbox-delivered",
]

RUNTIME_EVENT_NAMES: list[str] = [
    "authority-acquired",
    "authority-renewed",
    "dispatch-queued",
    "dispatch-notified",
    "dispatch-delivered",
    "dispatch-failed",
    "replay-requested",
    "snapshot-captured",
    "mailbox-message-created",
    "mailbox-notified",
    "mailbox-delivered",
]


# --- Enums ---


class WorkerCli(StrEnum):
    """Supported CLI tools for worker panes."""

    CODEX = "Codex"
    CLAUDE = "Claude"

    @classmethod
    def from_label(cls, label: str) -> WorkerCli | str:
        """Resolve a label string to a WorkerCli variant or pass through.

        Args:
            label: Raw CLI label string (case-insensitive).

        Returns:
            A WorkerCli enum member, or the raw string if unrecognized.
        """
        normalized = label.strip().lower()
        if normalized == "claude":
            return cls.CLAUDE
        if normalized == "codex":
            return cls.CODEX
        return label  # "Other" case — just return the raw string


def submit_presses_for_worker_cli(worker_cli: WorkerCli | str) -> int:
    """Return the number of Enter key presses needed to submit in the given CLI.

    Args:
        worker_cli: The CLI tool in use.

    Returns:
        1 for Claude (single Enter), 2 for Codex/other (double Enter).
    """
    if worker_cli == WorkerCli.CLAUDE:
        return 1
    return 2


class DispatchTransportKind(StrEnum):
    """Transport mechanisms for dispatch delivery."""

    TMUX = "tmux"


class DispatchOutcomeReason(StrEnum):
    """Detailed reason codes for dispatch delivery outcomes."""

    DELIVERED_CONFIRMED = "tmux_send_keys_confirmed"
    DELIVERED_CONFIRMED_ACTIVE_TASK = "tmux_send_keys_confirmed_active_task"
    DELIVERED_UNCONFIRMED = "tmux_send_keys_unconfirmed"
    DEFERRED_LEADER_PANE_MISSING = "leader_pane_missing_deferred"
    DEFERRED_SHELL_NOT_INJECTABLE = "deferred_shell"
    FAILED_MISSING_TARGET = "missing_tmux_target"
    FAILED_TARGET_RESOLUTION = "target_resolution_failed"
    FAILED_PREFLIGHT = "preflight_failed"
    FAILED_SEND = "send_failed"


# --- Queue Transition ---


@dataclass(frozen=True)
class QueueTransition:
    """Result of classifying a dispatch attempt into a queue status transition.

    Attributes:
        status: Target queue status ("pending", "notified", or "failed").
        reason: Machine-readable reason code for the transition.
    """

    status: str  # "pending", "notified", or "failed"
    reason: DispatchOutcomeReason

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason.value}


def classify_dispatch_outcome(
    target_present: bool,
    target_resolved: bool,
    preflight_ok: bool,
    send_ok: bool,
    confirmed: bool,
    active_task: bool,
    retry_remaining: bool,
) -> QueueTransition:
    """Determine the queue transition for a dispatch attempt based on delivery signals.

    Evaluates a series of boolean conditions in priority order to decide
    whether a dispatch should move to notified, remain pending, or fail.

    Args:
        target_present: Whether the target pane exists.
        target_resolved: Whether target resolution succeeded.
        preflight_ok: Whether preflight checks passed.
        send_ok: Whether the send-keys operation succeeded.
        confirmed: Whether delivery was confirmed by pane content.
        active_task: Whether the target pane has an active task running.
        retry_remaining: Whether retry budget remains if unconfirmed.

    Returns:
        A QueueTransition with the resulting status and reason.
    """
    if not target_present:
        return QueueTransition("failed", DispatchOutcomeReason.FAILED_MISSING_TARGET)
    if not target_resolved:
        return QueueTransition("failed", DispatchOutcomeReason.FAILED_TARGET_RESOLUTION)
    if not preflight_ok:
        return QueueTransition("failed", DispatchOutcomeReason.FAILED_PREFLIGHT)
    if not send_ok:
        return QueueTransition("failed", DispatchOutcomeReason.FAILED_SEND)
    if confirmed:
        reason = (
            DispatchOutcomeReason.DELIVERED_CONFIRMED_ACTIVE_TASK
            if active_task
            else DispatchOutcomeReason.DELIVERED_CONFIRMED
        )
        return QueueTransition("notified", reason)
    if retry_remaining:
        return QueueTransition("pending", DispatchOutcomeReason.DELIVERED_UNCONFIRMED)
    return QueueTransition("failed", DispatchOutcomeReason.DELIVERED_UNCONFIRMED)


# --- Runtime Commands (tagged union via type field) ---


@dataclass(frozen=True)
class RuntimeCommand:
    """Base for all runtime commands. Use classmethods to construct."""

    command: str
    owner: str | None = None
    lease_id: str | None = None
    leased_until: str | None = None
    request_id: str | None = None
    target: str | None = None
    metadata: Any | None = None
    channel: str | None = None
    reason: str | None = None
    cursor: str | None = None
    message_id: str | None = None
    from_worker: str | None = None
    to_worker: str | None = None
    body: str | None = None

    @classmethod
    def acquire_authority(
        cls, owner: str, lease_id: str, leased_until: str
    ) -> RuntimeCommand:
        return cls(
            command="AcquireAuthority",
            owner=owner,
            lease_id=lease_id,
            leased_until=leased_until,
        )

    @classmethod
    def renew_authority(
        cls, owner: str, lease_id: str, leased_until: str
    ) -> RuntimeCommand:
        return cls(
            command="RenewAuthority",
            owner=owner,
            lease_id=lease_id,
            leased_until=leased_until,
        )

    @classmethod
    def queue_dispatch(
        cls, request_id: str, target: str, metadata: Any | None = None
    ) -> RuntimeCommand:
        return cls(
            command="QueueDispatch",
            request_id=request_id,
            target=target,
            metadata=metadata,
        )

    @classmethod
    def mark_notified(cls, request_id: str, channel: str) -> RuntimeCommand:
        return cls(command="MarkNotified", request_id=request_id, channel=channel)

    @classmethod
    def mark_delivered(cls, request_id: str) -> RuntimeCommand:
        return cls(command="MarkDelivered", request_id=request_id)

    @classmethod
    def mark_failed(cls, request_id: str, reason: str) -> RuntimeCommand:
        return cls(command="MarkFailed", request_id=request_id, reason=reason)

    @classmethod
    def request_replay(cls, cursor: str | None = None) -> RuntimeCommand:
        return cls(command="RequestReplay", cursor=cursor)

    @classmethod
    def capture_snapshot(cls) -> RuntimeCommand:
        return cls(command="CaptureSnapshot")

    @classmethod
    def create_mailbox_message(
        cls, message_id: str, from_worker: str, to_worker: str, body: str
    ) -> RuntimeCommand:
        return cls(
            command="CreateMailboxMessage",
            message_id=message_id,
            from_worker=from_worker,
            to_worker=to_worker,
            body=body,
        )

    @classmethod
    def mark_mailbox_notified(cls, message_id: str) -> RuntimeCommand:
        return cls(command="MarkMailboxNotified", message_id=message_id)

    @classmethod
    def mark_mailbox_delivered(cls, message_id: str) -> RuntimeCommand:
        return cls(command="MarkMailboxDelivered", message_id=message_id)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"command": self.command}
        for f in (
            "owner",
            "lease_id",
            "leased_until",
            "request_id",
            "target",
            "metadata",
            "channel",
            "reason",
            "cursor",
            "message_id",
            "from_worker",
            "to_worker",
            "body",
        ):
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        return d


# --- Runtime Events ---


@dataclass
class RuntimeEvent:
    """Runtime event emitted by engine processing."""

    event: str
    owner: str | None = None
    lease_id: str | None = None
    leased_until: str | None = None
    request_id: str | None = None
    target: str | None = None
    metadata: Any | None = None
    channel: str | None = None
    reason: str | None = None
    cursor: str | None = None
    message_id: str | None = None
    from_worker: str | None = None
    to_worker: str | None = None
    body: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"event": self.event}
        for f in (
            "owner",
            "lease_id",
            "leased_until",
            "request_id",
            "target",
            "metadata",
            "channel",
            "reason",
            "cursor",
            "message_id",
            "from_worker",
            "to_worker",
            "body",
        ):
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RuntimeEvent:
        return cls(
            event=d["event"],
            owner=d.get("owner"),
            lease_id=d.get("lease_id"),
            leased_until=d.get("leased_until"),
            request_id=d.get("request_id"),
            target=d.get("target"),
            metadata=d.get("metadata"),
            channel=d.get("channel"),
            reason=d.get("reason"),
            cursor=d.get("cursor"),
            message_id=d.get("message_id"),
            from_worker=d.get("from_worker"),
            to_worker=d.get("to_worker"),
            body=d.get("body"),
        )


# --- Snapshots ---


@dataclass
class AuthoritySnapshot:
    """Point-in-time view of authority lease state.

    Attributes:
        owner: Current lease holder identifier.
        lease_id: Unique lease identifier.
        leased_until: ISO timestamp when the lease expires.
        stale: Whether the lease has been marked stale.
        stale_reason: Human-readable reason for staleness.
    """

    owner: str | None = None
    lease_id: str | None = None
    leased_until: str | None = None
    stale: bool = False
    stale_reason: str | None = None

    @classmethod
    def acquire(cls, owner: str, lease_id: str, leased_until: str) -> AuthoritySnapshot:
        return cls(owner=owner, lease_id=lease_id, leased_until=leased_until)

    def mark_stale(self, reason: str) -> None:
        self.stale = True
        self.stale_reason = reason

    def clear_stale(self) -> None:
        self.stale = False
        self.stale_reason = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "lease_id": self.lease_id,
            "leased_until": self.leased_until,
            "stale": self.stale,
            "stale_reason": self.stale_reason,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AuthoritySnapshot:
        return cls(
            owner=d.get("owner"),
            lease_id=d.get("lease_id"),
            leased_until=d.get("leased_until"),
            stale=d.get("stale", False),
            stale_reason=d.get("stale_reason"),
        )


@dataclass
class BacklogSnapshot:
    """Summary counts of dispatch records by status.

    Attributes:
        pending: Number of dispatches awaiting delivery.
        notified: Number of dispatches sent but not yet confirmed.
        delivered: Number of successfully delivered dispatches.
        failed: Number of dispatches that failed.
    """

    pending: int = 0
    notified: int = 0
    delivered: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pending": self.pending,
            "notified": self.notified,
            "delivered": self.delivered,
            "failed": self.failed,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BacklogSnapshot:
        return cls(
            pending=d.get("pending", 0),
            notified=d.get("notified", 0),
            delivered=d.get("delivered", 0),
            failed=d.get("failed", 0),
        )


@dataclass
class ReplaySnapshot:
    """Point-in-time view of replay/cursor state.

    Attributes:
        cursor: Current replay cursor position.
        pending_events: Number of events awaiting replay.
        last_replayed_event_id: ID of the most recently replayed event.
        deferred_leader_notification: Whether a leader notification is deferred.
    """

    cursor: str | None = None
    pending_events: int = 0
    last_replayed_event_id: str | None = None
    deferred_leader_notification: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "cursor": self.cursor,
            "pending_events": self.pending_events,
            "last_replayed_event_id": self.last_replayed_event_id,
            "deferred_leader_notification": self.deferred_leader_notification,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReplaySnapshot:
        return cls(
            cursor=d.get("cursor"),
            pending_events=d.get("pending_events", 0),
            last_replayed_event_id=d.get("last_replayed_event_id"),
            deferred_leader_notification=d.get("deferred_leader_notification", False),
        )


@dataclass
class ReadinessSnapshot:
    """Whether the runtime is ready to process dispatches.

    Attributes:
        ready: True if no blocking conditions exist.
        reasons: List of human-readable reasons preventing readiness.
    """

    ready: bool = False
    reasons: list[str] = field(default_factory=lambda: ["authority lease not acquired"])

    @classmethod
    def make_ready(cls) -> ReadinessSnapshot:
        return cls(ready=True, reasons=[])

    @classmethod
    def blocked(cls, reason: str) -> ReadinessSnapshot:
        return cls(ready=False, reasons=[reason])

    def add_reason(self, reason: str) -> None:
        self.ready = False
        self.reasons.append(reason)

    def to_dict(self) -> dict[str, Any]:
        return {"ready": self.ready, "reasons": self.reasons}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ReadinessSnapshot:
        return cls(ready=d.get("ready", False), reasons=d.get("reasons", []))


@dataclass
class RuntimeSnapshot:
    """Complete point-in-time snapshot of runtime engine state.

    Attributes:
        schema_version: Schema version for forward-compatibility.
        authority: Current authority lease state.
        backlog: Dispatch queue summary counts.
        replay: Replay cursor and deduplication state.
        readiness: Whether the runtime is ready to operate.
    """

    schema_version: int = RUNTIME_SCHEMA_VERSION
    authority: AuthoritySnapshot = field(default_factory=AuthoritySnapshot)
    backlog: BacklogSnapshot = field(default_factory=BacklogSnapshot)
    replay: ReplaySnapshot = field(default_factory=ReplaySnapshot)
    readiness: ReadinessSnapshot = field(default_factory=ReadinessSnapshot)

    def is_ready(self) -> bool:
        return self.readiness.ready

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "authority": self.authority.to_dict(),
            "backlog": self.backlog.to_dict(),
            "replay": self.replay.to_dict(),
            "readiness": self.readiness.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RuntimeSnapshot:
        return cls(
            schema_version=d.get("schema_version", RUNTIME_SCHEMA_VERSION),
            authority=AuthoritySnapshot.from_dict(d.get("authority", {})),
            backlog=BacklogSnapshot.from_dict(d.get("backlog", {})),
            replay=ReplaySnapshot.from_dict(d.get("replay", {})),
            readiness=ReadinessSnapshot.from_dict(d.get("readiness", {})),
        )
