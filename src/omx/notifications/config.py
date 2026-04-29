"""Notification configuration reader.

Reads notification config from .omx-config.json and provides
backward compatibility with the old stopHookCallbacks format.
"""

from __future__ import annotations

import json
import os
import re
import warnings
from pathlib import Path

from omx.notifications.hook_config import (
    get_hook_config,
    merge_hook_config_into_notification_config,
)
from omx.notifications.temp_contract import (
    get_temp_builtin_selectors,
    is_notify_temp_env_active,
    is_openclaw_selected_in_temp_contract,
    read_notify_temp_contract_from_env,
)
from omx.notifications.types import (
    DiscordBotNotificationConfig,
    DiscordNotificationConfig,
    EventNotificationConfig,
    FullNotificationConfig,
    NotificationPlatform,
    NotificationsBlock,
    ReplyConfig,
    SlackNotificationConfig,
    TelegramNotificationConfig,
    VerbosityLevel,
)
from omx.utils.paths import codex_home


def _config_file() -> Path:
    return codex_home() / ".omx-config.json"


def _read_raw_config() -> dict | None:
    """Read raw config from .omx-config.json."""
    path = _config_file()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _migrate_stop_hook_callbacks(raw: dict) -> FullNotificationConfig | None:
    """Migrate old stopHookCallbacks format."""
    callbacks = raw.get("stopHookCallbacks")
    if not callbacks or not isinstance(callbacks, dict):
        return None

    config = FullNotificationConfig(
        enabled=True,
        events={"session-end": EventNotificationConfig(enabled=True)},
    )

    telegram = callbacks.get("telegram")
    if isinstance(telegram, dict) and telegram.get("enabled"):
        config.telegram = TelegramNotificationConfig(
            enabled=True,
            bot_token=telegram.get("botToken", ""),
            chat_id=telegram.get("chatId", ""),
        )

    discord = callbacks.get("discord")
    if isinstance(discord, dict) and discord.get("enabled"):
        config.discord = DiscordNotificationConfig(
            enabled=True,
            webhook_url=discord.get("webhookUrl", ""),
        )

    return config


def _normalize_optional(value: str | None) -> str | None:
    """Normalize an optional string value."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def validate_mention(raw: str | None) -> str | None:
    """Validate a Discord mention format.

    Accepts: <@123456789012345678> (user), <@&123456789012345678> (role).

    Args:
        raw: Raw mention string.

    Returns:
        Validated mention string, or None.
    """
    mention = _normalize_optional(raw)
    if not mention:
        return None
    if re.match(r"^<@!?\d{17,20}>$", mention) or re.match(r"^<@&\d{17,20}>$", mention):
        return mention
    return None


def validate_slack_mention(raw: str | None) -> str | None:
    """Validate Slack mention format.

    Accepts: <@UXXXXXXXX> (user), <!channel>, <!here>, <!everyone>,
    <!subteam^SXXXXXXXXX> (user group).

    Args:
        raw: Raw mention string.

    Returns:
        Validated mention string, or None.
    """
    mention = _normalize_optional(raw)
    if not mention:
        return None
    if re.match(r"^<@[UW][A-Z0-9]{8,11}>$", mention):
        return mention
    if re.match(r"^<!(?:channel|here|everyone)>$", mention):
        return mention
    if re.match(r"^<!subteam\^S[A-Z0-9]{8,11}>$", mention):
        return mention
    return None


def parse_mention_allowed_mentions(
    mention: str | None,
) -> dict:
    """Parse a mention string into allowed_mentions payload.

    Args:
        mention: Validated mention string.

    Returns:
        Dict with optional 'users' and 'roles' keys.
    """
    if not mention:
        return {}
    user_match = re.match(r"^<@!?(\d{17,20})>$", mention)
    if user_match:
        return {"users": [user_match.group(1)]}
    role_match = re.match(r"^<@&(\d{17,20})>$", mention)
    if role_match:
        return {"roles": [role_match.group(1)]}
    return {}


def build_config_from_env() -> FullNotificationConfig | None:
    """Build notification config from environment variables.

    Returns:
        Config from env, or None if no platform configured.
    """
    config = FullNotificationConfig(enabled=False)
    has_any_platform = False

    discord_mention = validate_mention(os.environ.get("OMX_DISCORD_MENTION"))

    discord_bot_token = os.environ.get("OMX_DISCORD_NOTIFIER_BOT_TOKEN")
    discord_channel = os.environ.get("OMX_DISCORD_NOTIFIER_CHANNEL")
    if discord_bot_token and discord_channel:
        config.discord_bot = DiscordBotNotificationConfig(
            enabled=True,
            bot_token=discord_bot_token,
            channel_id=discord_channel,
            mention=discord_mention,
        )
        has_any_platform = True

    discord_webhook = os.environ.get("OMX_DISCORD_WEBHOOK_URL")
    if discord_webhook:
        config.discord = DiscordNotificationConfig(
            enabled=True,
            webhook_url=discord_webhook,
            mention=discord_mention,
        )
        has_any_platform = True

    telegram_token = os.environ.get("OMX_TELEGRAM_BOT_TOKEN") or os.environ.get(
        "OMX_TELEGRAM_NOTIFIER_BOT_TOKEN"
    )
    telegram_chat_id = (
        os.environ.get("OMX_TELEGRAM_CHAT_ID")
        or os.environ.get("OMX_TELEGRAM_NOTIFIER_CHAT_ID")
        or os.environ.get("OMX_TELEGRAM_NOTIFIER_UID")
    )
    if telegram_token and telegram_chat_id:
        config.telegram = TelegramNotificationConfig(
            enabled=True,
            bot_token=telegram_token,
            chat_id=telegram_chat_id,
        )
        has_any_platform = True

    slack_webhook = os.environ.get("OMX_SLACK_WEBHOOK_URL")
    if slack_webhook:
        slack_mention = validate_slack_mention(os.environ.get("OMX_SLACK_MENTION"))
        config.slack = SlackNotificationConfig(
            enabled=True,
            webhook_url=slack_webhook,
            mention=slack_mention,
        )
        has_any_platform = True

    if not has_any_platform:
        return None

    config.enabled = True
    return config


def _merge_env_into_file_config(
    file_config: FullNotificationConfig,
    env_config: FullNotificationConfig,
) -> FullNotificationConfig:
    """Merge env-sourced config into file-sourced config."""
    from dataclasses import replace

    merged = replace(file_config)

    if not merged.discord_bot and env_config.discord_bot:
        merged.discord_bot = env_config.discord_bot
    elif merged.discord_bot and env_config.discord_bot:
        merged.discord_bot = DiscordBotNotificationConfig(
            enabled=merged.discord_bot.enabled,
            bot_token=merged.discord_bot.bot_token or env_config.discord_bot.bot_token,
            channel_id=merged.discord_bot.channel_id
            or env_config.discord_bot.channel_id,
            mention=(
                validate_mention(merged.discord_bot.mention)
                if merged.discord_bot.mention is not None
                else env_config.discord_bot.mention
            ),
        )

    if not merged.discord and env_config.discord:
        merged.discord = env_config.discord
    elif merged.discord and env_config.discord:
        merged.discord = DiscordNotificationConfig(
            enabled=merged.discord.enabled,
            webhook_url=merged.discord.webhook_url or env_config.discord.webhook_url,
            mention=(
                validate_mention(merged.discord.mention)
                if merged.discord.mention is not None
                else env_config.discord.mention
            ),
        )
    elif merged.discord:
        merged.discord = DiscordNotificationConfig(
            enabled=merged.discord.enabled,
            webhook_url=merged.discord.webhook_url,
            mention=validate_mention(merged.discord.mention),
        )

    if not merged.telegram and env_config.telegram:
        merged.telegram = env_config.telegram

    if not merged.slack and env_config.slack:
        merged.slack = env_config.slack
    elif merged.slack and env_config.slack:
        merged.slack = SlackNotificationConfig(
            enabled=merged.slack.enabled,
            webhook_url=merged.slack.webhook_url or env_config.slack.webhook_url,
            mention=(
                validate_slack_mention(merged.slack.mention)
                if merged.slack.mention is not None
                else env_config.slack.mention
            ),
        )
    elif merged.slack:
        merged.slack = SlackNotificationConfig(
            enabled=merged.slack.enabled,
            webhook_url=merged.slack.webhook_url,
            mention=validate_slack_mention(merged.slack.mention),
        )

    return merged


def resolve_profile_config(
    notifications: NotificationsBlock,
    profile_name: str | None = None,
) -> FullNotificationConfig | None:
    """Resolve a named profile from the notifications block.

    Priority:
      1. Explicit profile_name argument
      2. OMX_NOTIFY_PROFILE environment variable
      3. default_profile field in config
      4. None (no profile selected -> fall back to flat config)

    Args:
        notifications: The notifications block.
        profile_name: Optional explicit profile name.

    Returns:
        The resolved profile config, or None.
    """
    profiles = notifications.profiles
    if not profiles:
        return None

    name = (
        profile_name
        or os.environ.get("OMX_NOTIFY_PROFILE")
        or notifications.default_profile
    )
    if not name:
        return None

    profile = profiles.get(name)
    if not profile:
        warnings.warn(
            f'[notifications] Profile "{name}" not found. '
            f"Available: {', '.join(profiles.keys())}",
            stacklevel=2,
        )
        return None

    return profile


def list_profiles() -> list[str]:
    """List available profile names from the config file.

    Returns:
        List of profile name strings.
    """
    raw = _read_raw_config()
    if not raw:
        return []
    notifications = raw.get("notifications")
    if not isinstance(notifications, dict) or not notifications.get("profiles"):
        return []
    return list(notifications["profiles"].keys())


def get_active_profile_name() -> str | None:
    """Get the active profile name based on resolution priority.

    Returns:
        Active profile name, or None if using flat config.
    """
    env_profile = os.environ.get("OMX_NOTIFY_PROFILE")
    if env_profile:
        return env_profile
    raw = _read_raw_config()
    if not raw:
        return None
    notifications = raw.get("notifications")
    if not isinstance(notifications, dict):
        return None
    profiles = notifications.get("profiles")
    if not profiles or not isinstance(profiles, dict):
        return None
    return notifications.get("defaultProfile")


def _apply_hook_config_if_present(
    config: FullNotificationConfig,
) -> FullNotificationConfig:
    """Apply hook config if present."""
    hook_config = get_hook_config()
    if not hook_config:
        return config
    return merge_hook_config_into_notification_config(hook_config, config)


def _has_custom_transport_alias(config: FullNotificationConfig) -> bool:
    """Check if config has a custom transport alias."""
    cli = config.custom_cli_command
    webhook = config.custom_webhook_command
    cli_enabled = bool(cli and cli.enabled is not False and cli.command)
    webhook_enabled = bool(webhook and webhook.enabled is not False and webhook.url)
    return cli_enabled or webhook_enabled


def _normalize_custom_transport_gate(
    config: FullNotificationConfig,
) -> FullNotificationConfig:
    """Normalize custom transport gate by enabling openclaw if needed."""
    if config.openclaw and config.openclaw.get("enabled"):
        return config
    if not _has_custom_transport_alias(config):
        return config
    from dataclasses import replace

    merged = replace(config)
    merged.openclaw = {"enabled": True}
    return merged


def _build_temp_mode_config_from_contract() -> FullNotificationConfig | None:
    """Build config from temp contract."""
    env = dict(os.environ)
    contract = read_notify_temp_contract_from_env(env)
    env_active = is_notify_temp_env_active(env)
    if not (contract and contract.active) and not env_active:
        return None

    selectors = get_temp_builtin_selectors(contract)
    env_config = build_config_from_env()
    config = FullNotificationConfig(enabled=False)

    if "discord" in selectors:
        if env_config and env_config.discord:
            config.discord = env_config.discord
        if env_config and env_config.discord_bot:
            config.discord_bot = env_config.discord_bot
    if "telegram" in selectors and env_config and env_config.telegram:
        config.telegram = env_config.telegram
    if "slack" in selectors and env_config and env_config.slack:
        config.slack = env_config.slack
    if is_openclaw_selected_in_temp_contract(contract):
        config.openclaw = {"enabled": True}

    config.enabled = bool(
        (config.discord and config.discord.enabled)
        or (config.discord_bot and config.discord_bot.enabled)
        or (config.telegram and config.telegram.enabled)
        or (config.slack and config.slack.enabled)
        or (config.openclaw and config.openclaw.get("enabled"))
    )

    return config


def _parse_notifications_block(raw_notif: dict) -> NotificationsBlock:
    """Parse raw dict into a NotificationsBlock."""
    block = NotificationsBlock(
        enabled=raw_notif.get("enabled", False),
        verbosity=raw_notif.get("verbosity"),
        default_profile=raw_notif.get("defaultProfile"),
    )

    # Parse platform configs
    if "discord" in raw_notif and isinstance(raw_notif["discord"], dict):
        d = raw_notif["discord"]
        block.discord = DiscordNotificationConfig(
            enabled=d.get("enabled", False),
            webhook_url=d.get("webhookUrl", ""),
            username=d.get("username"),
            mention=d.get("mention"),
        )
    if "discord-bot" in raw_notif and isinstance(raw_notif["discord-bot"], dict):
        d = raw_notif["discord-bot"]
        block.discord_bot = DiscordBotNotificationConfig(
            enabled=d.get("enabled", False),
            bot_token=d.get("botToken"),
            channel_id=d.get("channelId"),
            mention=d.get("mention"),
        )
    if "telegram" in raw_notif and isinstance(raw_notif["telegram"], dict):
        d = raw_notif["telegram"]
        block.telegram = TelegramNotificationConfig(
            enabled=d.get("enabled", False),
            bot_token=d.get("botToken", ""),
            chat_id=d.get("chatId", ""),
            parse_mode=d.get("parseMode", "Markdown"),
        )
    if "slack" in raw_notif and isinstance(raw_notif["slack"], dict):
        d = raw_notif["slack"]
        block.slack = SlackNotificationConfig(
            enabled=d.get("enabled", False),
            webhook_url=d.get("webhookUrl", ""),
            channel=d.get("channel"),
            username=d.get("username"),
            mention=d.get("mention"),
        )
    if "webhook" in raw_notif and isinstance(raw_notif["webhook"], dict):
        from omx.notifications.types import WebhookNotificationConfig

        d = raw_notif["webhook"]
        block.webhook = WebhookNotificationConfig(
            enabled=d.get("enabled", False),
            url=d.get("url", ""),
            headers=d.get("headers"),
            method=d.get("method", "POST"),
        )
    if "openclaw" in raw_notif and isinstance(raw_notif["openclaw"], dict):
        block.openclaw = raw_notif["openclaw"]

    # Parse events
    if "events" in raw_notif and isinstance(raw_notif["events"], dict):
        events = {}
        for event_name, evt in raw_notif["events"].items():
            if not isinstance(evt, dict):
                continue
            events[event_name] = EventNotificationConfig(
                enabled=evt.get("enabled", True),
                message_template=evt.get("messageTemplate"),
            )
        block.events = events

    # Parse profiles
    if "profiles" in raw_notif and isinstance(raw_notif["profiles"], dict):
        profiles = {}
        for pname, pval in raw_notif["profiles"].items():
            if isinstance(pval, dict):
                profiles[pname] = _parse_full_notification_config(pval)
        block.profiles = profiles

    return block


def _parse_full_notification_config(raw: dict) -> FullNotificationConfig:
    """Parse raw dict into FullNotificationConfig."""
    config = FullNotificationConfig(enabled=raw.get("enabled", False))
    config.verbosity = raw.get("verbosity")

    if "discord" in raw and isinstance(raw["discord"], dict):
        d = raw["discord"]
        config.discord = DiscordNotificationConfig(
            enabled=d.get("enabled", False),
            webhook_url=d.get("webhookUrl", ""),
            mention=d.get("mention"),
        )
    if "discord-bot" in raw and isinstance(raw["discord-bot"], dict):
        d = raw["discord-bot"]
        config.discord_bot = DiscordBotNotificationConfig(
            enabled=d.get("enabled", False),
            bot_token=d.get("botToken"),
            channel_id=d.get("channelId"),
            mention=d.get("mention"),
        )
    if "telegram" in raw and isinstance(raw["telegram"], dict):
        d = raw["telegram"]
        config.telegram = TelegramNotificationConfig(
            enabled=d.get("enabled", False),
            bot_token=d.get("botToken", ""),
            chat_id=d.get("chatId", ""),
        )
    if "slack" in raw and isinstance(raw["slack"], dict):
        d = raw["slack"]
        config.slack = SlackNotificationConfig(
            enabled=d.get("enabled", False),
            webhook_url=d.get("webhookUrl", ""),
            mention=d.get("mention"),
        )
    if "openclaw" in raw and isinstance(raw["openclaw"], dict):
        config.openclaw = raw["openclaw"]

    return config


def get_notification_config(
    profile_name: str | None = None,
) -> FullNotificationConfig | None:
    """Get the effective notification configuration.

    Reads config from file, env vars, and temp contracts with proper merge priority.

    Args:
        profile_name: Optional profile name to resolve.

    Returns:
        Resolved notification config, or None.
    """
    temp_config = _build_temp_mode_config_from_contract()
    if temp_config:
        return temp_config

    raw = _read_raw_config()

    if raw:
        notifications = raw.get("notifications")
        if isinstance(notifications, dict):
            block = _parse_notifications_block(notifications)

            # Try profile resolution first
            profile_config = resolve_profile_config(block, profile_name)
            if profile_config:
                if not isinstance(profile_config.enabled, bool):
                    return None
                env_config = build_config_from_env()
                merged = (
                    _merge_env_into_file_config(profile_config, env_config)
                    if env_config
                    else profile_config
                )
                return _apply_hook_config_if_present(
                    _normalize_custom_transport_gate(merged)
                )

            # Fall back to flat config
            if not isinstance(block.enabled, bool):
                return None
            env_config = build_config_from_env()
            if env_config:
                return _apply_hook_config_if_present(
                    _normalize_custom_transport_gate(
                        _merge_env_into_file_config(block, env_config)
                    )
                )
            env_mention = validate_mention(os.environ.get("OMX_DISCORD_MENTION"))
            if env_mention:
                from dataclasses import replace

                patched = replace(block)
                if patched.discord_bot and patched.discord_bot.mention is None:
                    patched.discord_bot = DiscordBotNotificationConfig(
                        enabled=patched.discord_bot.enabled,
                        bot_token=patched.discord_bot.bot_token,
                        channel_id=patched.discord_bot.channel_id,
                        mention=env_mention,
                    )
                if patched.discord and patched.discord.mention is None:
                    patched.discord = DiscordNotificationConfig(
                        enabled=patched.discord.enabled,
                        webhook_url=patched.discord.webhook_url,
                        mention=env_mention,
                    )
                return _apply_hook_config_if_present(
                    _normalize_custom_transport_gate(patched)
                )
            return _apply_hook_config_if_present(
                _normalize_custom_transport_gate(block)
            )

    env_config = build_config_from_env()
    if env_config:
        return _apply_hook_config_if_present(env_config)

    if raw:
        migrated = _migrate_stop_hook_callbacks(raw)
        if migrated:
            return _apply_hook_config_if_present(migrated)
        return None

    return None


# ── Verbosity ──────────────────────────────────────────────────────

VALID_VERBOSITY_LEVELS = list(VerbosityLevel)
DEFAULT_VERBOSITY = VerbosityLevel.SESSION

VERBOSITY_RANK: dict[VerbosityLevel, int] = {
    VerbosityLevel.MINIMAL: 0,
    VerbosityLevel.SESSION: 1,
    VerbosityLevel.AGENT: 2,
    VerbosityLevel.VERBOSE: 3,
}

EVENT_MIN_VERBOSITY: dict[str, VerbosityLevel] = {
    "session-start": VerbosityLevel.MINIMAL,
    "session-stop": VerbosityLevel.MINIMAL,
    "session-end": VerbosityLevel.MINIMAL,
    "session-idle": VerbosityLevel.SESSION,
    "ask-user-question": VerbosityLevel.AGENT,
}


def get_verbosity(config: FullNotificationConfig | None) -> VerbosityLevel:
    """Resolve the effective verbosity level.

    Priority: env var > config field > default ("session").

    Args:
        config: The notification config.

    Returns:
        The effective verbosity level.
    """
    env_val = os.environ.get("OMX_NOTIFY_VERBOSITY")
    if env_val and env_val in VALID_VERBOSITY_LEVELS:
        return VerbosityLevel(env_val)
    if config and config.verbosity and config.verbosity in VALID_VERBOSITY_LEVELS:
        return VerbosityLevel(config.verbosity)
    return DEFAULT_VERBOSITY


def is_event_allowed_by_verbosity(verbosity: VerbosityLevel, event: str) -> bool:
    """Check whether a given event is allowed at the specified verbosity level.

    Args:
        verbosity: Current verbosity level.
        event: The notification event name.

    Returns:
        True if the event is allowed.
    """
    required = EVENT_MIN_VERBOSITY.get(event, VerbosityLevel.SESSION)
    return VERBOSITY_RANK[verbosity] >= VERBOSITY_RANK[required]


def should_include_tmux_tail(verbosity: VerbosityLevel) -> bool:
    """Whether the given verbosity level should include tmux tail output.

    Args:
        verbosity: Current verbosity level.

    Returns:
        True if tmux tail should be included.
    """
    return VERBOSITY_RANK[verbosity] >= VERBOSITY_RANK[VerbosityLevel.SESSION]


def is_event_enabled(config: FullNotificationConfig, event: str) -> bool:
    """Check if a notification event is enabled in the config.

    Args:
        config: The notification config.
        event: The notification event name.

    Returns:
        True if the event is enabled.
    """
    if not config.enabled:
        return False

    verbosity = get_verbosity(config)
    if not is_event_allowed_by_verbosity(verbosity, event):
        return False

    event_config = (config.events or {}).get(event)

    if event_config and event_config.enabled is False:
        return False

    if not event_config:
        return bool(
            (config.discord and config.discord.enabled)
            or (config.discord_bot and config.discord_bot.enabled)
            or (config.telegram and config.telegram.enabled)
            or (config.slack and config.slack.enabled)
            or (config.webhook and config.webhook.enabled)
            or (config.openclaw and config.openclaw.get("enabled"))
            or _has_custom_transport_alias(config)
        )

    if (
        (event_config.discord and event_config.discord.enabled)
        or (event_config.discord_bot and event_config.discord_bot.enabled)
        or (event_config.telegram and event_config.telegram.enabled)
        or (event_config.slack and event_config.slack.enabled)
        or (event_config.webhook and event_config.webhook.enabled)
    ):
        return True

    return bool(
        (config.discord and config.discord.enabled)
        or (config.discord_bot and config.discord_bot.enabled)
        or (config.telegram and config.telegram.enabled)
        or (config.slack and config.slack.enabled)
        or (config.webhook and config.webhook.enabled)
        or (config.openclaw and config.openclaw.get("enabled"))
        or _has_custom_transport_alias(config)
    )


def get_enabled_platforms(
    config: FullNotificationConfig,
    event: str,
) -> list[str]:
    """Get the list of enabled platforms for an event.

    Args:
        config: The notification config.
        event: The notification event name.

    Returns:
        List of enabled platform name strings.
    """
    if not config.enabled:
        return []

    platforms: list[str] = []
    event_config = (config.events or {}).get(event)

    if event_config and event_config.enabled is False:
        return []

    for platform in NotificationPlatform:
        # Check event-level platform config
        event_platform = (
            getattr(event_config, platform.replace("-", "_"), None)
            if event_config
            else None
        )
        if event_platform and hasattr(event_platform, "enabled"):
            if event_platform.enabled:
                platforms.append(platform)
            continue

        # Check top-level platform config
        top_level = getattr(config, platform.replace("-", "_"), None)
        if top_level and hasattr(top_level, "enabled") and top_level.enabled:
            platforms.append(platform)

    return platforms


def get_reply_listener_platform_config(
    config: FullNotificationConfig | None,
) -> dict:
    """Get platform config for the reply listener.

    Args:
        config: The notification config.

    Returns:
        Dict with telegram and discord enablement and credentials.
    """
    if not config:
        return {"telegram_enabled": False, "discord_enabled": False}

    reply_platform_events = [
        "session-start",
        "ask-user-question",
        "session-stop",
        "session-idle",
        "session-end",
    ]

    def _get_enabled_reply_platform(platform_attr: str) -> object | None:
        top = getattr(config, platform_attr, None)
        if top and getattr(top, "enabled", False):
            return top
        for evt_name in reply_platform_events:
            evt_cfg = (config.events or {}).get(evt_name)
            if not evt_cfg:
                continue
            evt_plat = getattr(evt_cfg, platform_attr, None)
            if evt_plat and getattr(evt_plat, "enabled", False):
                return evt_plat
        return None

    telegram_config = _get_enabled_reply_platform("telegram")
    discord_bot_config = _get_enabled_reply_platform("discord_bot")

    telegram_enabled = bool(
        telegram_config
        and getattr(telegram_config, "bot_token", None)
        and getattr(telegram_config, "chat_id", None)
    )
    discord_enabled = bool(
        discord_bot_config
        and getattr(discord_bot_config, "bot_token", None)
        and getattr(discord_bot_config, "channel_id", None)
    )

    return {
        "telegram_enabled": telegram_enabled,
        "telegram_bot_token": getattr(telegram_config, "bot_token", None)
        if telegram_enabled
        else None,
        "telegram_chat_id": getattr(telegram_config, "chat_id", None)
        if telegram_enabled
        else None,
        "discord_enabled": discord_enabled,
        "discord_bot_token": getattr(discord_bot_config, "bot_token", None)
        if discord_enabled
        else None,
        "discord_channel_id": getattr(discord_bot_config, "channel_id", None)
        if discord_enabled
        else None,
        "discord_mention": getattr(discord_bot_config, "mention", None)
        if discord_enabled
        else None,
    }


def _parse_discord_user_ids(env_value: str | None, config_value: object) -> list[str]:
    """Parse Discord user IDs from env or config."""
    if env_value:
        ids = [
            uid.strip()
            for uid in env_value.split(",")
            if re.match(r"^\d{17,20}$", uid.strip())
        ]
        if ids:
            return ids

    if isinstance(config_value, list):
        ids = [
            uid
            for uid in config_value
            if isinstance(uid, str) and re.match(r"^\d{17,20}$", uid)
        ]
        if ids:
            return ids

    return []


_REPLY_POLL_INTERVAL_DEFAULT_MS = 3_000
_REPLY_POLL_INTERVAL_MIN_MS = 500
_REPLY_POLL_INTERVAL_MAX_MS = 60_000
_REPLY_RATE_LIMIT_DEFAULT = 10
_REPLY_RATE_LIMIT_MIN = 1
_REPLY_MAX_MSG_LENGTH_DEFAULT = 500
_REPLY_MAX_MSG_LENGTH_MIN = 1
_REPLY_MAX_MSG_LENGTH_MAX = 4_000


def _parse_integer_input(value: object) -> int | None:
    """Parse a value as an integer."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value != value:  # NaN
            return None
        return int(value)
    if isinstance(value, str):
        trimmed = value.strip()
        if not trimmed:
            return None
        try:
            return int(trimmed)
        except ValueError:
            return None
    return None


def _normalize_integer(
    value: int | None,
    fallback: int,
    min_val: int,
    max_val: int | None = None,
) -> int:
    """Normalize an integer to within bounds."""
    if value is None:
        return fallback
    if value < min_val:
        return min_val
    if max_val is not None and value > max_val:
        return max_val
    return value


def get_reply_config() -> ReplyConfig | None:
    """Get the reply listener configuration.

    Returns:
        ReplyConfig if reply is enabled, else None.
    """
    notif_config = get_notification_config()
    if not notif_config or not notif_config.enabled:
        return None

    plat_config = get_reply_listener_platform_config(notif_config)
    if not plat_config["discord_enabled"] and not plat_config["telegram_enabled"]:
        return None

    raw = _read_raw_config()
    reply_raw = _read_reply_settings(raw)

    enabled = os.environ.get("OMX_REPLY_ENABLED") == "true" or (
        isinstance(reply_raw, dict) and reply_raw.get("enabled") is True
    )
    if not enabled:
        return None

    authorized_discord_user_ids = _parse_discord_user_ids(
        os.environ.get("OMX_REPLY_DISCORD_USER_IDS"),
        reply_raw.get("authorizedDiscordUserIds")
        if isinstance(reply_raw, dict)
        else None,
    )

    if plat_config["discord_enabled"] and not authorized_discord_user_ids:
        import sys

        print(
            "[notifications] Discord reply listening disabled: authorizedDiscordUserIds is empty.",
            file=sys.stderr,
        )

    poll_env = _parse_integer_input(os.environ.get("OMX_REPLY_POLL_INTERVAL_MS"))
    poll_cfg = (
        _parse_integer_input(reply_raw.get("pollIntervalMs"))
        if isinstance(reply_raw, dict)
        else None
    )
    poll_interval = _normalize_integer(
        poll_env if poll_env is not None else poll_cfg,
        _REPLY_POLL_INTERVAL_DEFAULT_MS,
        _REPLY_POLL_INTERVAL_MIN_MS,
        _REPLY_POLL_INTERVAL_MAX_MS,
    )

    rate_env = _parse_integer_input(os.environ.get("OMX_REPLY_RATE_LIMIT"))
    rate_cfg = (
        _parse_integer_input(reply_raw.get("rateLimitPerMinute"))
        if isinstance(reply_raw, dict)
        else None
    )
    rate_limit = _normalize_integer(
        rate_env if rate_env is not None else rate_cfg,
        _REPLY_RATE_LIMIT_DEFAULT,
        _REPLY_RATE_LIMIT_MIN,
    )

    msg_len_cfg = (
        _parse_integer_input(reply_raw.get("maxMessageLength"))
        if isinstance(reply_raw, dict)
        else None
    )
    max_message_length = _normalize_integer(
        msg_len_cfg,
        _REPLY_MAX_MSG_LENGTH_DEFAULT,
        _REPLY_MAX_MSG_LENGTH_MIN,
        _REPLY_MAX_MSG_LENGTH_MAX,
    )

    include_prefix = os.environ.get("OMX_REPLY_INCLUDE_PREFIX") != "false" and (
        not isinstance(reply_raw, dict) or reply_raw.get("includePrefix") is not False
    )

    return ReplyConfig(
        enabled=True,
        poll_interval_ms=poll_interval,
        max_message_length=max_message_length,
        rate_limit_per_minute=rate_limit,
        include_prefix=include_prefix,
        authorized_discord_user_ids=authorized_discord_user_ids,
    )


def _read_reply_settings(raw: dict | None) -> dict | None:
    """Read reply settings from raw config."""
    if not raw:
        return None
    notifications = raw.get("notifications")
    if not isinstance(notifications, dict):
        return None
    return notifications.get("reply")
