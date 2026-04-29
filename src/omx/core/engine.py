"""Runtime engine — processes commands, emits events, persists state.

Port of omx-runtime-core/src/engine.rs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from omx.core.authority import AuthorityLease
from omx.core.dispatch import DispatchLog, DispatchStatus
from omx.core.mailbox import MailboxLog
from omx.core.replay import ReplayState
from omx.core.types import (
    RUNTIME_SCHEMA_VERSION,
    ReadinessSnapshot,
    RuntimeCommand,
    RuntimeEvent,
    RuntimeSnapshot,
)


class EngineError(Exception):
    """Wraps authority, dispatch, mailbox, I/O, and JSON errors."""


def _lock_file(f: Any, exclusive: bool) -> None:
    """Platform-appropriate file locking."""
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK if exclusive else msvcrt.LK_NBRLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)


class RuntimeEngine:
    """Processes RuntimeCommands, maintains state, emits RuntimeEvents."""

    def __init__(self) -> None:
        self.authority = AuthorityLease()
        self.dispatch = DispatchLog()
        self.mailbox = MailboxLog()
        self.replay = ReplayState()
        self.event_log: list[RuntimeEvent] = []
        self._state_dir: Path | None = None

    def with_state_dir(self, path: Path) -> RuntimeEngine:
        """Configure the state directory for persistence and return self for chaining."""
        self._state_dir = path
        return self

    def process(self, command: RuntimeCommand) -> RuntimeEvent:
        """Process a command and return the resulting event."""
        try:
            event = self._dispatch(command)
        except Exception as exc:
            raise EngineError(str(exc)) from exc
        self.event_log.append(event)
        return event

    def _dispatch(self, cmd: RuntimeCommand) -> RuntimeEvent:
        match cmd.command:
            case "AcquireAuthority":
                self.authority.acquire(cmd.owner, cmd.lease_id, cmd.leased_until)  # type: ignore[arg-type]
                return RuntimeEvent(
                    event="AuthorityAcquired",
                    owner=cmd.owner,
                    lease_id=cmd.lease_id,
                    leased_until=cmd.leased_until,
                )
            case "RenewAuthority":
                self.authority.renew(cmd.owner, cmd.lease_id, cmd.leased_until)  # type: ignore[arg-type]
                return RuntimeEvent(
                    event="AuthorityRenewed",
                    owner=cmd.owner,
                    lease_id=cmd.lease_id,
                    leased_until=cmd.leased_until,
                )
            case "QueueDispatch":
                self.dispatch.queue(cmd.request_id, cmd.target, cmd.metadata)  # type: ignore[arg-type]
                return RuntimeEvent(
                    event="DispatchQueued",
                    request_id=cmd.request_id,
                    target=cmd.target,
                    metadata=cmd.metadata,
                )
            case "MarkNotified":
                self.dispatch.mark_notified(cmd.request_id, cmd.channel)  # type: ignore[arg-type]
                return RuntimeEvent(
                    event="DispatchNotified",
                    request_id=cmd.request_id,
                    channel=cmd.channel,
                )
            case "MarkDelivered":
                self.dispatch.mark_delivered(cmd.request_id)  # type: ignore[arg-type]
                return RuntimeEvent(
                    event="DispatchDelivered", request_id=cmd.request_id
                )
            case "MarkFailed":
                self.dispatch.mark_failed(cmd.request_id, cmd.reason)  # type: ignore[arg-type]
                return RuntimeEvent(
                    event="DispatchFailed",
                    request_id=cmd.request_id,
                    reason=cmd.reason,
                )
            case "RequestReplay":
                self.replay.request_replay(cmd.cursor)
                return RuntimeEvent(event="ReplayRequested", cursor=cmd.cursor)
            case "CaptureSnapshot":
                return RuntimeEvent(event="SnapshotCaptured")
            case "CreateMailboxMessage":
                self.mailbox.create(
                    cmd.message_id, cmd.from_worker, cmd.to_worker, cmd.body
                )  # type: ignore[arg-type]
                return RuntimeEvent(
                    event="MailboxMessageCreated",
                    message_id=cmd.message_id,
                    from_worker=cmd.from_worker,
                    to_worker=cmd.to_worker,
                    body=cmd.body,
                )
            case "MarkMailboxNotified":
                self.mailbox.mark_notified(cmd.message_id)  # type: ignore[arg-type]
                return RuntimeEvent(event="MailboxNotified", message_id=cmd.message_id)
            case "MarkMailboxDelivered":
                self.mailbox.mark_delivered(cmd.message_id)  # type: ignore[arg-type]
                return RuntimeEvent(event="MailboxDelivered", message_id=cmd.message_id)
            case _:
                raise ValueError(f"unknown command: {cmd.command}")

    def snapshot(self) -> RuntimeSnapshot:
        """Capture a complete point-in-time snapshot of the engine state."""
        return RuntimeSnapshot(
            schema_version=RUNTIME_SCHEMA_VERSION,
            authority=self.authority.to_snapshot(),
            backlog=self.dispatch.to_backlog_snapshot(),
            replay=self.replay.to_snapshot(),
            readiness=derive_readiness(self.authority, self.dispatch, self.replay),
        )

    def compact(self) -> None:
        """Remove events for dispatches that reached Delivered or Failed status."""
        terminal_ids = {
            r.request_id
            for r in self.dispatch.records
            if r.status in (DispatchStatus.DELIVERED, DispatchStatus.FAILED)
        }
        self.event_log = [
            e
            for e in self.event_log
            if e.request_id is None
            or e.request_id not in terminal_ids
            or e.event
            not in (
                "DispatchQueued",
                "DispatchNotified",
                "DispatchDelivered",
                "DispatchFailed",
            )
        ]

    def persist(self) -> None:
        """Write engine state to disk with file locking."""
        if self._state_dir is None:
            raise EngineError("no state_dir configured")
        self._state_dir.mkdir(parents=True, exist_ok=True)

        lock_path = self._state_dir / "engine.lock"
        with open(lock_path, "w") as lock_f:
            _lock_file(lock_f, exclusive=True)

            snapshot_json = json.dumps(self.snapshot().to_dict(), indent=2)
            (self._state_dir / "snapshot.json").write_text(
                snapshot_json, encoding="utf-8"
            )

            events_json = json.dumps([e.to_dict() for e in self.event_log], indent=2)
            (self._state_dir / "events.json").write_text(events_json, encoding="utf-8")

            mailbox_json = json.dumps(self.mailbox.to_dict(), indent=2)
            (self._state_dir / "mailbox.json").write_text(
                mailbox_json, encoding="utf-8"
            )

    def write_compatibility_view(self) -> None:
        """Write individual section files for legacy TS readers."""
        if self._state_dir is None:
            raise EngineError("no state_dir configured")
        self._state_dir.mkdir(parents=True, exist_ok=True)

        snap = self.snapshot()
        for name, data in [
            ("authority.json", snap.authority.to_dict()),
            ("backlog.json", snap.backlog.to_dict()),
            ("readiness.json", snap.readiness.to_dict()),
            ("replay.json", snap.replay.to_dict()),
            ("dispatch.json", self.dispatch.to_dict()),
            ("mailbox.json", self.mailbox.to_dict()),
        ]:
            (self._state_dir / name).write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )

    @classmethod
    def load(cls, state_dir: Path) -> RuntimeEngine:
        """Load engine state from disk by replaying events."""
        lock_path = state_dir / "engine.lock"
        if not lock_path.exists():
            lock_path.touch()

        with open(lock_path) as lock_f:
            _lock_file(lock_f, exclusive=False)

            events_json = (state_dir / "events.json").read_text(encoding="utf-8")
            events_data: list[dict[str, Any]] = json.loads(events_json)
            events = [RuntimeEvent.from_dict(e) for e in events_data]

            mailbox_log: MailboxLog | None = None
            mailbox_path = state_dir / "mailbox.json"
            if mailbox_path.exists():
                try:
                    mailbox_data = json.loads(mailbox_path.read_text(encoding="utf-8"))
                    mailbox_log = MailboxLog.from_dict(mailbox_data)
                except (json.JSONDecodeError, KeyError):
                    pass

        engine = cls()
        engine._state_dir = state_dir

        # Replay all events to rebuild state
        for event in events:
            _replay_event(engine, event)

        # Backfill mailbox bodies from mailbox.json for legacy compatibility
        if mailbox_log is not None:
            body_by_id = {r.message_id: r.body for r in mailbox_log.records}
            for event in events:
                if event.event == "MailboxMessageCreated" and event.body is None:
                    body = body_by_id.get(event.message_id, "")  # type: ignore[arg-type]
                    if body:
                        event.body = body
            engine.mailbox = mailbox_log

        engine.event_log = events
        return engine


def _replay_event(engine: RuntimeEngine, event: RuntimeEvent) -> None:
    """Replay a single event to rebuild engine state."""
    match event.event:
        case "AuthorityAcquired":
            try:
                engine.authority.acquire(
                    event.owner, event.lease_id, event.leased_until
                )  # type: ignore[arg-type]
            except Exception:
                pass
        case "AuthorityRenewed":
            try:
                engine.authority.renew(event.owner, event.lease_id, event.leased_until)  # type: ignore[arg-type]
            except Exception:
                pass
        case "DispatchQueued":
            engine.dispatch.queue(event.request_id, event.target, event.metadata)  # type: ignore[arg-type]
        case "DispatchNotified":
            try:
                engine.dispatch.mark_notified(event.request_id, event.channel)  # type: ignore[arg-type]
            except Exception:
                pass
        case "DispatchDelivered":
            try:
                engine.dispatch.mark_delivered(event.request_id)  # type: ignore[arg-type]
            except Exception:
                pass
        case "DispatchFailed":
            try:
                engine.dispatch.mark_failed(event.request_id, event.reason)  # type: ignore[arg-type]
            except Exception:
                pass
        case "ReplayRequested":
            engine.replay.request_replay(event.cursor)
        case "SnapshotCaptured":
            pass
        case "MailboxMessageCreated":
            engine.mailbox.create(
                event.message_id,
                event.from_worker,
                event.to_worker,  # type: ignore[arg-type]
                event.body or "",
            )
        case "MailboxNotified":
            try:
                engine.mailbox.mark_notified(event.message_id)  # type: ignore[arg-type]
            except Exception:
                pass
        case "MailboxDelivered":
            try:
                engine.mailbox.mark_delivered(event.message_id)  # type: ignore[arg-type]
            except Exception:
                pass


def derive_readiness(
    authority: AuthorityLease,
    _dispatch: DispatchLog,
    replay: ReplayState,
) -> ReadinessSnapshot:
    """Determine whether the runtime is ready or blocked."""
    reasons: list[str] = []

    if not authority.is_held():
        reasons.append("authority lease not acquired")
    elif authority.is_stale():
        stale_detail = authority.to_snapshot().stale_reason or ""
        reasons.append(f"authority lease is stale: {stale_detail}")

    snap = replay.to_snapshot()
    if snap.pending_events > 0:
        reasons.append(f"replay has {snap.pending_events} pending events")

    if not reasons:
        return ReadinessSnapshot.make_ready()

    result = ReadinessSnapshot.blocked(reasons[0])
    for reason in reasons[1:]:
        result.add_reason(reason)
    return result
