"""Slack webhook notification adapter."""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

from omx.notifications.types import NotificationPayload, NotificationResult


def send_slack(payload: NotificationPayload) -> NotificationResult:
    """Send a notification via Slack webhook."""
    webhook_url = os.environ.get("OMX_SLACK_WEBHOOK", "")
    if not webhook_url:
        return NotificationResult(
            sent=False, provider="slack", error="no webhook configured"
        )

    data = json.dumps(
        {
            "text": f"*{payload.title}*\n{payload.body}",
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
            return NotificationResult(sent=True, provider="slack")
    except (urllib.error.URLError, OSError) as exc:
        return NotificationResult(sent=False, provider="slack", error=str(exc))
