"""Notification dispatcher.

Sends notifications to configured platforms (Discord, Telegram, Slack, webhook).
All sends are best-effort with timeouts. Failures are swallowed to avoid
blocking hooks.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

from omx.notifications.config import parse_mention_allowed_mentions
from omx.notifications.types import (
    DiscordBotNotificationConfig,
    DiscordNotificationConfig,
    DispatchResult,
    FullNotificationConfig,
    FullNotificationPayload,
    NotificationResult,
    SlackNotificationConfig,
    TelegramNotificationConfig,
    WebhookNotificationConfig,
)

_SEND_TIMEOUT = 10
_DISCORD_MAX_CONTENT_LENGTH = 2000


def _compose_discord_content(
    message: str,
    mention: str | None,
) -> tuple[str, dict]:
    """Compose Discord message content with mention handling."""
    mention_parsed = parse_mention_allowed_mentions(mention)
    allowed_mentions: dict = {
        "parse": [],
    }
    if "users" in mention_parsed:
        allowed_mentions["users"] = mention_parsed["users"]
    if "roles" in mention_parsed:
        allowed_mentions["roles"] = mention_parsed["roles"]

    if mention:
        prefix = f"{mention}\n"
        max_body = _DISCORD_MAX_CONTENT_LENGTH - len(prefix)
        body = (
            message[: max_body - 1] + "\u2026" if len(message) > max_body else message
        )
        content = f"{prefix}{body}"
    else:
        content = (
            message[: _DISCORD_MAX_CONTENT_LENGTH - 1] + "\u2026"
            if len(message) > _DISCORD_MAX_CONTENT_LENGTH
            else message
        )

    return content, allowed_mentions


def _validate_discord_url(webhook_url: str) -> bool:
    """Validate a Discord webhook URL."""
    try:
        parsed = urlparse(webhook_url)
        allowed_hosts = ["discord.com", "discordapp.com"]
        if not any(
            parsed.hostname == h
            or (parsed.hostname and parsed.hostname.endswith(f".{h}"))
            for h in allowed_hosts
        ):
            return False
        return parsed.scheme == "https"
    except Exception:
        return False


def _validate_telegram_token(token: str) -> bool:
    """Validate a Telegram bot token format."""
    return bool(re.match(r"^[0-9]+:[A-Za-z0-9_-]+$", token))


def _validate_slack_url(webhook_url: str) -> bool:
    """Validate a Slack webhook URL."""
    try:
        parsed = urlparse(webhook_url)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and (
                parsed.hostname == "hooks.slack.com"
                or parsed.hostname.endswith(".hooks.slack.com")
            )
        )
    except Exception:
        return False


def _validate_webhook_url(url: str) -> bool:
    """Validate a generic webhook URL (HTTPS required)."""
    try:
        parsed = urlparse(url)
        return parsed.scheme == "https"
    except Exception:
        return False


def _http_post(
    url: str, body: bytes, headers: dict[str, str], timeout: int = _SEND_TIMEOUT
) -> int:
    """Send an HTTP POST request and return the status code."""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def send_discord(
    config: DiscordNotificationConfig,
    payload: FullNotificationPayload,
) -> NotificationResult:
    """Send a notification via Discord webhook.

    Args:
        config: Discord webhook configuration.
        payload: The notification payload.

    Returns:
        NotificationResult with send outcome.
    """
    if not config.enabled or not config.webhook_url:
        return NotificationResult(
            platform="discord", success=False, error="Not configured"
        )

    if not _validate_discord_url(config.webhook_url):
        return NotificationResult(
            platform="discord", success=False, error="Invalid webhook URL"
        )

    try:
        content, allowed_mentions = _compose_discord_content(
            payload.message, config.mention
        )
        body_dict: dict = {"content": content, "allowed_mentions": allowed_mentions}
        if config.username:
            body_dict["username"] = config.username

        body = json.dumps(body_dict).encode("utf-8")
        _http_post(config.webhook_url, body, {"Content-Type": "application/json"})
        return NotificationResult(platform="discord", success=True)
    except Exception as e:
        return NotificationResult(platform="discord", success=False, error=str(e))


def send_discord_bot(
    config: DiscordBotNotificationConfig,
    payload: FullNotificationPayload,
) -> NotificationResult:
    """Send a notification via Discord Bot API.

    Args:
        config: Discord Bot API configuration.
        payload: The notification payload.

    Returns:
        NotificationResult with send outcome.
    """
    if not config.enabled:
        return NotificationResult(
            platform="discord-bot", success=False, error="Not enabled"
        )

    if not config.bot_token or not config.channel_id:
        return NotificationResult(
            platform="discord-bot", success=False, error="Missing botToken or channelId"
        )

    try:
        content, allowed_mentions = _compose_discord_content(
            payload.message, config.mention
        )
        url = f"https://discord.com/api/v10/channels/{config.channel_id}/messages"
        body = json.dumps(
            {"content": content, "allowed_mentions": allowed_mentions}
        ).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bot {config.bot_token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_SEND_TIMEOUT) as resp:
            message_id = None
            try:
                data = json.loads(resp.read().decode("utf-8"))
                message_id = data.get("id")
            except Exception:
                pass
            return NotificationResult(
                platform="discord-bot", success=True, message_id=message_id
            )
    except Exception as e:
        return NotificationResult(platform="discord-bot", success=False, error=str(e))


def send_telegram(
    config: TelegramNotificationConfig,
    payload: FullNotificationPayload,
) -> NotificationResult:
    """Send a notification via Telegram bot.

    Args:
        config: Telegram configuration.
        payload: The notification payload.

    Returns:
        NotificationResult with send outcome.
    """
    if not config.enabled or not config.bot_token or not config.chat_id:
        return NotificationResult(
            platform="telegram", success=False, error="Not configured"
        )

    if not _validate_telegram_token(config.bot_token):
        return NotificationResult(
            platform="telegram", success=False, error="Invalid bot token format"
        )

    try:
        body = json.dumps(
            {
                "chat_id": config.chat_id,
                "text": payload.message,
                "parse_mode": config.parse_mode or "Markdown",
            }
        ).encode("utf-8")

        url = f"https://api.telegram.org/bot{config.bot_token}/sendMessage"
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_SEND_TIMEOUT) as resp:
            message_id = None
            try:
                data = json.loads(resp.read().decode("utf-8"))
                result = data.get("result", {})
                if "message_id" in result:
                    message_id = str(result["message_id"])
            except Exception:
                pass
            return NotificationResult(
                platform="telegram", success=True, message_id=message_id
            )
    except Exception as e:
        return NotificationResult(platform="telegram", success=False, error=str(e))


def send_slack(
    config: SlackNotificationConfig,
    payload: FullNotificationPayload,
) -> NotificationResult:
    """Send a notification via Slack webhook.

    Args:
        config: Slack configuration.
        payload: The notification payload.

    Returns:
        NotificationResult with send outcome.
    """
    if not config.enabled or not config.webhook_url:
        return NotificationResult(
            platform="slack", success=False, error="Not configured"
        )

    if not _validate_slack_url(config.webhook_url):
        return NotificationResult(
            platform="slack", success=False, error="Invalid webhook URL"
        )

    try:
        body_dict: dict = {"text": payload.message}
        if config.channel:
            body_dict["channel"] = config.channel
        if config.username:
            body_dict["username"] = config.username

        body = json.dumps(body_dict).encode("utf-8")
        _http_post(config.webhook_url, body, {"Content-Type": "application/json"})
        return NotificationResult(platform="slack", success=True)
    except Exception as e:
        return NotificationResult(platform="slack", success=False, error=str(e))


def send_webhook(
    config: WebhookNotificationConfig,
    payload: FullNotificationPayload,
) -> NotificationResult:
    """Send a notification via generic webhook.

    Args:
        config: Webhook configuration.
        payload: The notification payload.

    Returns:
        NotificationResult with send outcome.
    """
    if not config.enabled or not config.url:
        return NotificationResult(
            platform="webhook", success=False, error="Not configured"
        )

    if not _validate_webhook_url(config.url):
        return NotificationResult(
            platform="webhook", success=False, error="Invalid URL (HTTPS required)"
        )

    try:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if config.headers:
            headers.update(config.headers)

        body = json.dumps(
            {
                "event": payload.event,
                "session_id": payload.session_id,
                "message": payload.message,
                "timestamp": payload.timestamp,
                "tmux_session": payload.tmux_session,
                "project_name": payload.project_name,
                "project_path": payload.project_path,
                "modes_used": payload.modes_used,
                "duration_ms": payload.duration_ms,
                "reason": payload.reason,
                "active_mode": payload.active_mode,
                "question": payload.question,
            }
        ).encode("utf-8")

        method = config.method or "POST"
        req = urllib.request.Request(
            config.url,
            data=body,
            headers=headers,
            method=method,
        )
        with urllib.request.urlopen(req, timeout=_SEND_TIMEOUT):
            pass
        return NotificationResult(platform="webhook", success=True)
    except Exception as e:
        return NotificationResult(platform="webhook", success=False, error=str(e))


def _get_effective_platform_config(
    platform: str,
    config: FullNotificationConfig,
    event: str,
) -> object | None:
    """Get the effective platform config for an event."""
    event_config = (config.events or {}).get(event)
    attr = platform.replace("-", "_")
    if event_config:
        event_platform = getattr(event_config, attr, None)
        if event_platform and hasattr(event_platform, "enabled"):
            return event_platform

    return getattr(config, attr, None)


def dispatch_notifications(
    config: FullNotificationConfig,
    event: str,
    payload: FullNotificationPayload,
) -> DispatchResult:
    """Dispatch notifications to all configured platforms for an event.

    Args:
        config: Full notification configuration.
        event: The notification event name.
        payload: The notification payload.

    Returns:
        DispatchResult with all platform results.
    """
    results: list[NotificationResult] = []

    discord_config = _get_effective_platform_config("discord", config, event)
    if isinstance(discord_config, DiscordNotificationConfig) and discord_config.enabled:
        results.append(send_discord(discord_config, payload))

    telegram_config = _get_effective_platform_config("telegram", config, event)
    if (
        isinstance(telegram_config, TelegramNotificationConfig)
        and telegram_config.enabled
    ):
        results.append(send_telegram(telegram_config, payload))

    slack_config = _get_effective_platform_config("slack", config, event)
    if isinstance(slack_config, SlackNotificationConfig) and slack_config.enabled:
        results.append(send_slack(slack_config, payload))

    webhook_config = _get_effective_platform_config("webhook", config, event)
    if isinstance(webhook_config, WebhookNotificationConfig) and webhook_config.enabled:
        results.append(send_webhook(webhook_config, payload))

    discord_bot_config = _get_effective_platform_config("discord-bot", config, event)
    if (
        isinstance(discord_bot_config, DiscordBotNotificationConfig)
        and discord_bot_config.enabled
    ):
        results.append(send_discord_bot(discord_bot_config, payload))

    return DispatchResult(
        event=event,
        results=results,
        any_success=any(r.success for r in results),
    )
