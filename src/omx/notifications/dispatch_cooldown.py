"""Dispatch notification cooldown.

Prevents flooding users with team dispatch notifications by enforcing a
minimum interval between dispatches.

Config key: notifications.dispatchCooldownSeconds in ~/.codex/.omx-config.json
Env var: OMX_DISPATCH_COOLDOWN_SECONDS (overrides config)
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


def get_dispatch_notification_cooldown_seconds() -> int:
    """Read the dispatch notification cooldown in seconds.

    Resolution order:
      1. OMX_DISPATCH_COOLDOWN_SECONDS env var
      2. notifications.dispatchCooldownSeconds in ~/.codex/.omx-config.json
      3. Default: 60 seconds

    Returns:
        Cooldown in seconds.
    """
    env_val = os.environ.get("OMX_DISPATCH_COOLDOWN_SECONDS")
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
                val = notifications.get("dispatchCooldownSeconds")
                if isinstance(val, (int, float)):
                    return max(0, int(val))
    except Exception:
        pass

    return _DEFAULT_COOLDOWN_SECONDS


def _get_cooldown_state_path(state_dir: str, session_id: str | None = None) -> Path:
    """Get the dispatch cooldown state file path."""
    if session_id and _SESSION_ID_SAFE_RE.match(session_id):
        return (
            Path(state_dir) / "sessions" / session_id / "dispatch-notif-cooldown.json"
        )
    return Path(state_dir) / "dispatch-notif-cooldown.json"


def should_send_dispatch_notification(
    state_dir: str,
    session_id: str | None = None,
) -> bool:
    """Check whether the dispatch notification cooldown has elapsed.

    Args:
        state_dir: State directory path.
        session_id: Optional session ID for scoping.

    Returns:
        True if the notification should be sent.
    """
    cooldown_secs = get_dispatch_notification_cooldown_seconds()
    if cooldown_secs == 0:
        return True

    path = _get_cooldown_state_path(state_dir, session_id)
    try:
        if not path.exists():
            return True
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("lastSentAt"), str):
            last_ms = datetime.fromisoformat(
                data["lastSentAt"].replace("Z", "+00:00")
            ).timestamp()
            elapsed_secs = datetime.now(timezone.utc).timestamp() - last_ms
            if elapsed_secs < cooldown_secs:
                return False
    except Exception:
        pass

    return True


def record_dispatch_notification_sent(
    state_dir: str,
    session_id: str | None = None,
) -> None:
    """Record that a dispatch notification was sent.

    Args:
        state_dir: State directory path.
        session_id: Optional session ID for scoping.
    """
    path = _get_cooldown_state_path(state_dir, session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"lastSentAt": datetime.now(timezone.utc).isoformat()}, indent=2
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
