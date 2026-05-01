"""Core notifier interface.

Supports desktop notifications only. External platforms (Discord, Slack,
Telegram) have been removed for security.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class NotificationConfig:
    """Notification config.

    Attributes:
        desktop: Whether desktop notifications are enabled.
    """

    desktop: bool = False


@dataclass
class NotificationPayload:
    """Notification payload.

    Attributes:
        title: Notification title.
        message: Notification message text.
        type: Notification type (info, success, warning, error).
        mode: Active OMX mode.
        project_path: Project directory path.
    """

    title: str
    message: str
    type: str = "info"
    mode: str | None = None
    project_path: str | None = None


def load_notification_config(
    project_root: str | None = None,
) -> NotificationConfig | None:
    """Load notification config from .omx/notifications.json.

    Args:
        project_root: Project root directory (defaults to cwd).

    Returns:
        NotificationConfig if found, else None.
    """
    import json

    root = Path(project_root) if project_root else Path.cwd()
    config_path = root / ".omx" / "notifications.json"
    if not config_path.exists():
        return None
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return NotificationConfig(desktop=raw.get("desktop", False))
    except Exception:
        return None


def _send_desktop_notification(payload: NotificationPayload) -> None:
    """Send a desktop notification (best-effort)."""
    if sys.platform == "darwin":
        safe_title = payload.title.replace("\\", "\\\\").replace('"', '\\"')
        safe_msg = payload.message.replace("\\", "\\\\").replace('"', '\\"')
        cmd = [
            "osascript",
            "-e",
            f'display notification "{safe_msg}" with title "{safe_title}"',
        ]
    elif sys.platform == "linux":
        cmd = ["notify-send", payload.title, payload.message]
    elif sys.platform == "win32":
        safe_title = payload.title.replace("'", "''")
        safe_msg = payload.message.replace("'", "''")
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
            "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0); "
            "$text = $xml.GetElementsByTagName('text'); "
            f"$text[0].AppendChild($xml.CreateTextNode('{safe_title}')) > $null; "
            f"$text[1].AppendChild($xml.CreateTextNode('{safe_msg}')) > $null; "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('oh-my-codex').Show($xml)"
        )
        cmd = ["powershell", "-Command", ps]
    else:
        return

    try:
        subprocess.run(
            cmd,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception:
        pass


def notify(
    payload: NotificationPayload,
    config: NotificationConfig | None = None,
) -> None:
    """Send notification via desktop only.

    Args:
        payload: The notification to send.
        config: Optional config (loaded from .omx/notifications.json if not provided).
    """
    if config is None:
        config = load_notification_config()
        if config is None:
            return

    if config.desktop:
        _send_desktop_notification(payload)
