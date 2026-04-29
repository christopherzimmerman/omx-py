"""Replay state for event deduplication and cursor tracking.

Port of omx-runtime-core/src/replay.rs.
"""

from __future__ import annotations

from omx.core.types import ReplaySnapshot


class ReplayState:
    """Tracks replay cursor and seen event IDs for deduplication."""

    def __init__(self) -> None:
        self._cursor: str | None = None
        self._seen_event_ids: set[str] = set()
        self._deferred_leader_notification: bool = False

    def request_replay(self, cursor: str | None) -> None:
        """Set the replay cursor to begin replay from the given position."""
        self._cursor = cursor

    def record_event(self, event_id: str) -> bool:
        """Returns True if the event is new, False if already seen."""
        if event_id in self._seen_event_ids:
            return False
        self._seen_event_ids.add(event_id)
        return True

    def defer_leader_notification(self) -> None:
        """Mark that a leader notification should be deferred."""
        self._deferred_leader_notification = True

    def clear_deferred(self) -> None:
        """Clear the deferred leader notification flag."""
        self._deferred_leader_notification = False

    @property
    def cursor(self) -> str | None:
        return self._cursor

    @property
    def seen_count(self) -> int:
        return len(self._seen_event_ids)

    @property
    def is_deferred(self) -> bool:
        return self._deferred_leader_notification

    def to_snapshot(self) -> ReplaySnapshot:
        return ReplaySnapshot(
            cursor=self._cursor,
            pending_events=0,
            last_replayed_event_id=None,
            deferred_leader_notification=self._deferred_leader_notification,
        )
