"""Core notifier interface.

Supports desktop notifications, Discord webhooks, and Telegram bots.
Legacy interface kept for backward compatibility.
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


@dataclass
class NotificationConfig:
    """Legacy notification config from .omx/notifications.json.

    Attributes:
        desktop: Whether desktop notifications are enabled.
        discord: Discord webhook config dict.
        telegram: Telegram bot config dict.
    """

    desktop: bool = False
    discord: dict | None = None
    telegram: dict | None = None


@dataclass
class NotificationPayload:
    """Legacy notification payload.

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
    root = Path(project_root) if project_root else Path.cwd()
    config_path = root / ".omx" / "notifications.json"
    if not config_path.exists():
        return None
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        return NotificationConfig(
            desktop=raw.get("desktop", False),
            discord=raw.get("discord"),
            telegram=raw.get("telegram"),
        )
    except Exception:
        return None


def _build_desktop_args(
    title: str,
    message: str,
    platform: str,
) -> tuple[str, list[str]] | None:
    """Build the command and args for a desktop notification.

    Args:
        title: Notification title.
        message: Notification message.
        platform: OS platform string.

    Returns:
        Tuple of (command, args) or None if unsupported.
    """
    if platform == "darwin":
        safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
        safe_message = message.replace("\\", "\\\\").replace('"', '\\"')
        return (
            "osascript",
            ["-e", f'display notification "{safe_message}" with title "{safe_title}"'],
        )
    elif platform == "linux":
        return ("notify-send", [title, message])
    elif platform == "win32":
        safe_title = title.replace("'", "''")
        safe_message = message.replace("'", "''")
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
            "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(0); "
            "$text = $xml.GetElementsByTagName('text'); "
            f"$text[0].AppendChild($xml.CreateTextNode('{safe_title}')) > $null; "
            f"$text[1].AppendChild($xml.CreateTextNode('{safe_message}')) > $null; "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('oh-my-codex').Show($xml)"
        )
        return ("powershell", ["-Command", ps])
    return None


def _send_desktop_notification(payload: NotificationPayload) -> None:
    """Send a desktop notification (best-effort)."""
    result = _build_desktop_args(payload.title, payload.message, sys.platform)
    if not result:
        return
    cmd, args = result
    try:
        subprocess.run(
            [cmd, *args],
            capture_output=True,
            timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception:
        pass


def _send_discord_notification(payload: NotificationPayload, webhook_url: str) -> None:
    """Send a Discord webhook notification (best-effort)."""
    color_map = {
        "info": 3447003,
        "success": 3066993,
        "warning": 15105570,
        "error": 15158332,
    }
    from datetime import datetime, timezone

    body = json.dumps(
        {
            "embeds": [
                {
                    "title": f"[OMX] {payload.title}",
                    "description": payload.message,
                    "color": color_map.get(payload.type, 3447003),
                    "footer": {"text": f"oh-my-codex | {payload.mode or 'general'}"},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            ],
        }
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def _send_telegram_notification(
    payload: NotificationPayload,
    bot_token: str,
    chat_id: str,
) -> None:
    """Send a Telegram bot notification (best-effort)."""
    text = f"*[OMX] {payload.title}*\n{payload.message}"
    body = json.dumps(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }
    ).encode("utf-8")

    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10):
            pass
    except Exception:
        pass


def notify(
    payload: NotificationPayload,
    config: NotificationConfig | None = None,
) -> None:
    """Send notification via all configured channels.

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

    if config.discord and config.discord.get("webhookUrl"):
        _send_discord_notification(payload, config.discord["webhookUrl"])

    if (
        config.telegram
        and config.telegram.get("botToken")
        and config.telegram.get("chatId")
    ):
        _send_telegram_notification(
            payload,
            config.telegram["botToken"],
            config.telegram["chatId"],
        )
