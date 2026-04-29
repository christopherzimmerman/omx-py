"""Idle notification cooldown.

Prevents flooding users with session-idle notifications by enforcing a
minimum interval between dispatches.

Config key: notifications.idleCooldownSeconds in ~/.codex/.omx-config.json
Env var: OMX_IDLE_COOLDOWN_SECONDS (overrides config)
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from omx.utils.paths import codex_home

_DEFAULT_COOLDOWN_SECONDS = 60
_SESSION_ID_SAFE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,255}$")
_MAX_IDLE_FINGERPRINT_LENGTH = 512
_IDLE_NOTIFICATION_STATE_FILE = "idle-notif-cooldown.json"
_SESSION_IDLE_HOOK_STATE_FILE = "session-idle-hook-state.json"


def get_idle_notification_cooldown_seconds() -> int:
    """Read the idle notification cooldown in seconds.

    Resolution order:
      1. OMX_IDLE_COOLDOWN_SECONDS env var
      2. notifications.idleCooldownSeconds in ~/.codex/.omx-config.json
      3. Default: 60 seconds

    Returns:
        Cooldown in seconds.
    """
    env_val = os.environ.get("OMX_IDLE_COOLDOWN_SECONDS")
    if env_val is not None:
        try:
            parsed = float(env_val)
            if parsed >= 0:
                return int(parsed)
        except ValueError:
            pass

    try:
        config_path = codex_home() / ".omx-config.json"
        if config_path.exists():
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            notifications = raw.get("notifications") if isinstance(raw, dict) else None
            if isinstance(notifications, dict):
                val = notifications.get("idleCooldownSeconds")
                if isinstance(val, (int, float)):
                    return max(0, int(val))
    except Exception:
        pass

    return _DEFAULT_COOLDOWN_SECONDS


def _get_scoped_state_path(
    state_dir: str, file_name: str, session_id: str | None = None
) -> Path:
    """Get a session-scoped state file path."""
    if session_id and _SESSION_ID_SAFE_RE.match(session_id):
        return Path(state_dir) / "sessions" / session_id / file_name
    return Path(state_dir) / file_name


def _normalize_idle_fingerprint(fingerprint: str | None) -> str:
    """Normalize an idle fingerprint string."""
    if not isinstance(fingerprint, str):
        return ""
    normalized = fingerprint.strip()
    if not normalized:
        return ""
    if len(normalized) > _MAX_IDLE_FINGERPRINT_LENGTH:
        return normalized[:_MAX_IDLE_FINGERPRINT_LENGTH]
    return normalized


def _read_idle_state(path: Path) -> dict | None:
    """Read idle notification state from file."""
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return {
            "lastSentAt": data.get("lastSentAt")
            if isinstance(data.get("lastSentAt"), str)
            else None,
            "fingerprint": _normalize_idle_fingerprint(data.get("fingerprint")),
            "tmuxTailFingerprint": _normalize_idle_fingerprint(
                data.get("tmuxTailFingerprint")
            ),
        }
    except Exception:
        return None


def _write_idle_state(path: Path, patch: dict) -> None:
    """Write idle notification state to file."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = _read_idle_state(path) or {}
        state = {**previous, **patch}
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass


def should_send_idle_notification(
    state_dir: str,
    session_id: str | None = None,
    fingerprint: str | None = None,
) -> bool:
    """Check whether an idle notification should be sent.

    Args:
        state_dir: State directory path.
        session_id: Optional session ID for scoping.
        fingerprint: Optional idle-state fingerprint.

    Returns:
        True if the notification should be sent.
    """
    cooldown_secs = get_idle_notification_cooldown_seconds()
    normalized_fp = _normalize_idle_fingerprint(fingerprint)

    if cooldown_secs == 0:
        return True

    path = _get_scoped_state_path(state_dir, _IDLE_NOTIFICATION_STATE_FILE, session_id)
    state = _read_idle_state(path)
    if not state:
        return True

    if normalized_fp:
        return state.get("fingerprint") != normalized_fp

    last_sent = state.get("lastSentAt")
    if last_sent:
        try:
            last_ms = datetime.fromisoformat(
                last_sent.replace("Z", "+00:00")
            ).timestamp()
            elapsed_secs = datetime.now(timezone.utc).timestamp() - last_ms
            if elapsed_secs < cooldown_secs:
                return False
        except Exception:
            pass

    return True


def record_idle_notification_sent(
    state_dir: str,
    session_id: str | None = None,
    fingerprint: str | None = None,
) -> None:
    """Record that an idle notification was sent.

    Args:
        state_dir: State directory path.
        session_id: Optional session ID for scoping.
        fingerprint: Optional idle-state fingerprint.
    """
    path = _get_scoped_state_path(state_dir, _IDLE_NOTIFICATION_STATE_FILE, session_id)
    normalized_fp = _normalize_idle_fingerprint(fingerprint)
    patch: dict = {"lastSentAt": datetime.now(timezone.utc).isoformat()}
    if normalized_fp:
        patch["fingerprint"] = normalized_fp
    _write_idle_state(path, patch)


def should_send_session_idle_hook_event(
    state_dir: str,
    session_id: str | None = None,
    fingerprint: str | None = None,
) -> bool:
    """Check whether the coarse session-idle hook event should be dispatched.

    Args:
        state_dir: State directory path.
        session_id: Optional session ID.
        fingerprint: Optional idle-state fingerprint.

    Returns:
        True if the event should be dispatched.
    """
    normalized_fp = _normalize_idle_fingerprint(fingerprint)
    if not normalized_fp:
        return True

    state = _read_idle_state(
        _get_scoped_state_path(state_dir, _SESSION_IDLE_HOOK_STATE_FILE, session_id)
    )
    if not state:
        return True

    return state.get("fingerprint") != normalized_fp


def record_session_idle_hook_event_sent(
    state_dir: str,
    session_id: str | None = None,
    fingerprint: str | None = None,
) -> None:
    """Record that the coarse session-idle hook event was dispatched.

    Args:
        state_dir: State directory path.
        session_id: Optional session ID.
        fingerprint: Optional idle-state fingerprint.
    """
    normalized_fp = _normalize_idle_fingerprint(fingerprint)
    patch: dict = {"lastSentAt": datetime.now(timezone.utc).isoformat()}
    if normalized_fp:
        patch["fingerprint"] = normalized_fp
    _write_idle_state(
        _get_scoped_state_path(state_dir, _SESSION_IDLE_HOOK_STATE_FILE, session_id),
        patch,
    )


def should_include_session_idle_tmux_tail(
    state_dir: str,
    session_id: str | None = None,
    tmux_tail_fingerprint: str | None = None,
) -> bool:
    """Check whether a session-idle notification should include tmux tail.

    Args:
        state_dir: State directory path.
        session_id: Optional session ID.
        tmux_tail_fingerprint: Fingerprint of parsed tmux tail.

    Returns:
        True if tmux tail should be included.
    """
    normalized_fp = _normalize_idle_fingerprint(tmux_tail_fingerprint)
    if not normalized_fp:
        return False

    state = _read_idle_state(
        _get_scoped_state_path(state_dir, _IDLE_NOTIFICATION_STATE_FILE, session_id)
    )
    if not state:
        return True

    return state.get("tmuxTailFingerprint") != normalized_fp


def record_session_idle_tmux_tail_sent(
    state_dir: str,
    session_id: str | None = None,
    tmux_tail_fingerprint: str | None = None,
) -> None:
    """Record the tmux-tail fingerprint last included in an idle notification.

    Args:
        state_dir: State directory path.
        session_id: Optional session ID.
        tmux_tail_fingerprint: Fingerprint of parsed tmux tail.
    """
    normalized_fp = _normalize_idle_fingerprint(tmux_tail_fingerprint)
    path = _get_scoped_state_path(state_dir, _IDLE_NOTIFICATION_STATE_FILE, session_id)
    patch: dict = {}
    if normalized_fp:
        patch["tmuxTailFingerprint"] = normalized_fp
    _write_idle_state(path, patch)
