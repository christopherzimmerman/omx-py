"""Local desktop notifications.

External notification platforms (Discord, Slack, Telegram, webhook, OpenClaw)
have been removed. Only local desktop notifications via the OS notification
service are supported.
"""

from omx.notifications.notifier import (
    NotificationConfig,
    NotificationPayload,
    load_notification_config,
    notify,
)

__all__ = [
    "NotificationConfig",
    "NotificationPayload",
    "load_notification_config",
    "notify",
]
