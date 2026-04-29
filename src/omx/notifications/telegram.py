"""Telegram bot notification adapter."""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

from omx.notifications.types import NotificationPayload, NotificationResult


def send_telegram(payload: NotificationPayload) -> NotificationResult:
    """Send a notification via Telegram bot."""
    token = os.environ.get("OMX_TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("OMX_TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return NotificationResult(
            sent=False, provider="telegram", error="bot token or chat_id not configured"
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = json.dumps(
        {
            "chat_id": chat_id,
            "text": f"**{payload.title}**\n{payload.body}",
            "parse_mode": "Markdown",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10):
            return NotificationResult(sent=True, provider="telegram")
    except (urllib.error.URLError, OSError) as exc:
        return NotificationResult(sent=False, provider="telegram", error=str(exc))
