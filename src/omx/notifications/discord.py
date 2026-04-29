"""Discord webhook notification adapter."""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

from omx.notifications.types import NotificationPayload, NotificationResult


def send_discord(payload: NotificationPayload) -> NotificationResult:
    """Send a notification via Discord webhook."""
    webhook_url = os.environ.get("OMX_DISCORD_WEBHOOK", "")
    if not webhook_url:
        return NotificationResult(
            sent=False, provider="discord", error="no webhook configured"
        )

    data = json.dumps(
        {
            "embeds": [
                {
                    "title": payload.title,
                    "description": payload.body,
                    "color": 0x5865F2,
                }
            ],
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10):
            return NotificationResult(sent=True, provider="discord")
    except (urllib.error.URLError, OSError) as exc:
        return NotificationResult(sent=False, provider="discord", error=str(exc))
