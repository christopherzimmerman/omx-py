"""Lifecycle event deduplication.

Prevents duplicate lifecycle notifications by tracking fingerprints
of recently sent events per session.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from omx.notifications.types import FullNotificationPayload

_SESSION_ID_SAFE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,255}$")
_LIFECYCLE_DEDUPE_FILE = "lifecycle-notif-state.json"
_LIFECYCLE_DEDUPE_WINDOW_MS = 5_000
_DEDUPED_EVENTS = {"session-start", "session-stop", "session-end"}


def should_dedupe_lifecycle_notification(event: str) -> bool:
    """Check if this event type should be deduplicated.

    Args:
        event: The notification event name.

    Returns:
        True if the event should be deduplicated.
    """
    return event in _DEDUPED_EVENTS


def _normalize_fingerprint(payload: FullNotificationPayload) -> str:
    """Create a fingerprint from payload for deduplication."""
    return json.dumps(
        {
            "event": payload.event,
            "reason": payload.reason or "",
            "activeMode": payload.active_mode or "",
            "question": payload.question or "",
            "incompleteTasks": payload.incomplete_tasks or 0,
        },
        sort_keys=True,
    )


def _get_state_path(state_dir: str, session_id: str) -> Path:
    """Get the deduplication state file path."""
    if _SESSION_ID_SAFE_RE.match(session_id):
        return Path(state_dir) / "sessions" / session_id / _LIFECYCLE_DEDUPE_FILE
    return Path(state_dir) / _LIFECYCLE_DEDUPE_FILE


def _read_state(path: Path) -> dict:
    """Read deduplication state from file."""
    try:
        if not path.exists():
            return {}
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _write_state(path: Path, state: dict) -> None:
    """Write deduplication state to file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def _stable_serialize(value: object) -> str:
    """Serialize a value with stable key ordering."""
    if value is None or not isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ",".join(_stable_serialize(item) for item in value) + "]"
    entries = sorted(
        ((k, v) for k, v in value.items() if v is not None),
        key=lambda x: x[0],
    )
    parts = [f"{json.dumps(k)}:{_stable_serialize(v)}" for k, v in entries]
    return "{" + ",".join(parts) + "}"


def create_lifecycle_broadcast_fingerprint(value: object) -> str:
    """Create a stable fingerprint from a value for broadcast deduplication.

    Args:
        value: The value to fingerprint.

    Returns:
        Stable serialized string.
    """
    return _stable_serialize(value)


def _should_send_fingerprint(
    previous: dict | None,
    fingerprint: str,
    now_ms: float,
) -> bool:
    """Check if a fingerprint should trigger a send."""
    if not previous or previous.get("fingerprint") != fingerprint:
        return True
    sent_at = previous.get("sentAt")
    if not sent_at:
        return False
    try:
        previous_ms = (
            datetime.fromisoformat(sent_at.replace("Z", "+00:00")).timestamp() * 1000
        )
        return now_ms - previous_ms >= _LIFECYCLE_DEDUPE_WINDOW_MS
    except Exception:
        return False


def _should_send_scoped(
    state_dir: str,
    session_id: str | None,
    bucket: str,
    event_key: str,
    fingerprint: str,
    now_ms: float | None = None,
) -> bool:
    """Check if a scoped lifecycle broadcast should be sent."""
    if not session_id or not state_dir:
        return True
    if now_ms is None:
        now_ms = datetime.now(timezone.utc).timestamp() * 1000

    path = _get_state_path(state_dir, session_id)
    state = _read_state(path)
    bucket_state = state.get(bucket, {})
    if not isinstance(bucket_state, dict):
        bucket_state = {}
    return _should_send_fingerprint(bucket_state.get(event_key), fingerprint, now_ms)


def _record_scoped_sent(
    state_dir: str,
    session_id: str | None,
    bucket: str,
    event_key: str,
    fingerprint: str,
    now_ms: float | None = None,
) -> None:
    """Record that a scoped lifecycle broadcast was sent."""
    if not session_id or not state_dir:
        return
    if now_ms is None:
        now_ms = datetime.now(timezone.utc).timestamp() * 1000

    path = _get_state_path(state_dir, session_id)
    state = _read_state(path)
    bucket_state = state.get(bucket, {})
    if not isinstance(bucket_state, dict):
        bucket_state = {}
    bucket_state[event_key] = {
        "fingerprint": fingerprint,
        "sentAt": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
    }
    state[bucket] = bucket_state
    _write_state(path, state)


def should_send_lifecycle_notification(
    state_dir: str,
    payload: FullNotificationPayload,
    now_ms: float | None = None,
) -> bool:
    """Check whether a lifecycle notification should be sent.

    Args:
        state_dir: State directory path.
        payload: The notification payload.
        now_ms: Current time in milliseconds (defaults to now).

    Returns:
        True if the notification should be sent.
    """
    if not should_dedupe_lifecycle_notification(payload.event):
        return True
    return _should_send_scoped(
        state_dir,
        payload.session_id,
        "events",
        payload.event,
        _normalize_fingerprint(payload),
        now_ms,
    )


def record_lifecycle_notification_sent(
    state_dir: str,
    payload: FullNotificationPayload,
    now_ms: float | None = None,
) -> None:
    """Record that a lifecycle notification was sent.

    Args:
        state_dir: State directory path.
        payload: The notification payload.
        now_ms: Current time in milliseconds (defaults to now).
    """
    if not should_dedupe_lifecycle_notification(payload.event):
        return
    _record_scoped_sent(
        state_dir,
        payload.session_id,
        "events",
        payload.event,
        _normalize_fingerprint(payload),
        now_ms,
    )


def should_send_lifecycle_hook_broadcast(
    state_dir: str,
    session_id: str | None,
    event_key: str,
    fingerprint: str,
    now_ms: float | None = None,
) -> bool:
    """Check whether a lifecycle hook broadcast should be sent.

    Args:
        state_dir: State directory path.
        session_id: Session ID.
        event_key: Event key for deduplication.
        fingerprint: Event fingerprint.
        now_ms: Current time in milliseconds.

    Returns:
        True if the broadcast should be sent.
    """
    return _should_send_scoped(
        state_dir, session_id, "hookEvents", event_key, fingerprint, now_ms
    )


def record_lifecycle_hook_broadcast_sent(
    state_dir: str,
    session_id: str | None,
    event_key: str,
    fingerprint: str,
    now_ms: float | None = None,
) -> None:
    """Record that a lifecycle hook broadcast was sent.

    Args:
        state_dir: State directory path.
        session_id: Session ID.
        event_key: Event key for deduplication.
        fingerprint: Event fingerprint.
        now_ms: Current time in milliseconds.
    """
    _record_scoped_sent(
        state_dir, session_id, "hookEvents", event_key, fingerprint, now_ms
    )
