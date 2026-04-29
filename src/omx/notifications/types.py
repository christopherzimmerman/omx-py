"""Notification system types.

Defines types for the multi-platform lifecycle notification system.
Supports Discord, Telegram, Slack, and generic webhooks across
session lifecycle events (start, stop, end, ask-user-question).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NotificationEvent(StrEnum):
    """Events that can trigger notifications."""

    SESSION_START = "session-start"
    SESSION_STOP = "session-stop"
    SESSION_END = "session-end"
    SESSION_IDLE = "session-idle"
    ASK_USER_QUESTION = "ask-user-question"


class VerbosityLevel(StrEnum):
    """Verbosity levels for notification filtering.

    - verbose: all text/tool call output
    - agent:   per-agent-call events (includes ask-user-question)
    - session: start/idle/stop/end + tmux tail snippet [DEFAULT]
    - minimal: start/stop/end only, no idle, no tmux tail
    """

    VERBOSE = "verbose"
    AGENT = "agent"
    SESSION = "session"
    MINIMAL = "minimal"


class NotificationPlatform(StrEnum):
    """Supported notification platforms."""

    DISCORD = "discord"
    DISCORD_BOT = "discord-bot"
    TELEGRAM = "telegram"
    SLACK = "slack"
    WEBHOOK = "webhook"


@dataclass
class DiscordNotificationConfig:
    """Discord webhook configuration.

    Attributes:
        enabled: Whether this platform is enabled.
        webhook_url: Discord webhook URL.
        username: Optional username override for the webhook bot.
        mention: Optional mention to prepend to messages.
    """

    enabled: bool = False
    webhook_url: str = ""
    username: str | None = None
    mention: str | None = None


@dataclass
class DiscordBotNotificationConfig:
    """Discord Bot API configuration (bot token + channel ID).

    Attributes:
        enabled: Whether this platform is enabled.
        bot_token: Discord bot token.
        channel_id: Channel ID to send messages to.
        mention: Optional mention to prepend to messages.
    """

    enabled: bool = False
    bot_token: str | None = None
    channel_id: str | None = None
    mention: str | None = None


@dataclass
class TelegramNotificationConfig:
    """Telegram platform configuration.

    Attributes:
        enabled: Whether this platform is enabled.
        bot_token: Telegram bot token.
        chat_id: Chat ID to send messages to.
        parse_mode: Parse mode: Markdown or HTML (default: Markdown).
    """

    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""
    parse_mode: str = "Markdown"


@dataclass
class SlackNotificationConfig:
    """Slack platform configuration.

    Attributes:
        enabled: Whether this platform is enabled.
        webhook_url: Slack incoming webhook URL.
        channel: Optional channel override.
        username: Optional username override.
        mention: Optional mention to prepend to messages.
    """

    enabled: bool = False
    webhook_url: str = ""
    channel: str | None = None
    username: str | None = None
    mention: str | None = None


@dataclass
class WebhookNotificationConfig:
    """Generic webhook configuration.

    Attributes:
        enabled: Whether this platform is enabled.
        url: Webhook URL (POST with JSON body).
        headers: Optional custom headers.
        method: Optional HTTP method override (default: POST).
    """

    enabled: bool = False
    url: str = ""
    headers: dict[str, str] | None = None
    method: str = "POST"


@dataclass
class CustomWebhookCommandConfig:
    """Generic custom webhook command config.

    Attributes:
        enabled: Whether this transport is enabled.
        url: Webhook URL.
        headers: Optional custom headers.
        method: HTTP method (POST or PUT).
        timeout: Request timeout in seconds.
    """

    enabled: bool | None = None
    url: str = ""
    headers: dict[str, str] | None = None
    method: str = "POST"
    timeout: int | None = None


@dataclass
class CustomCliCommandConfig:
    """Generic custom CLI command config.

    Attributes:
        enabled: Whether this transport is enabled.
        command: CLI command to execute.
        timeout: Command timeout in seconds.
    """

    enabled: bool | None = None
    command: str = ""
    timeout: int | None = None


@dataclass
class EventNotificationConfig:
    """Per-event notification configuration.

    Attributes:
        enabled: Whether this event triggers notifications.
        message_template: Custom message template (optional).
        discord: Platform overrides for this event.
        discord_bot: Platform overrides for this event.
        telegram: Platform overrides for this event.
        slack: Platform overrides for this event.
        webhook: Platform overrides for this event.
    """

    enabled: bool = True
    message_template: str | None = None
    discord: DiscordNotificationConfig | None = None
    discord_bot: DiscordBotNotificationConfig | None = None
    telegram: TelegramNotificationConfig | None = None
    slack: SlackNotificationConfig | None = None
    webhook: WebhookNotificationConfig | None = None


@dataclass
class FullNotificationConfig:
    """Top-level notification configuration.

    Attributes:
        enabled: Global enable/disable for all notifications.
        verbosity: Notification verbosity level (default: session).
        discord: Default Discord webhook config.
        discord_bot: Default Discord Bot API config.
        telegram: Default Telegram config.
        slack: Default Slack config.
        webhook: Default webhook config.
        openclaw: OpenClaw gateway config.
        custom_webhook_command: Custom webhook transport alias.
        custom_cli_command: Custom CLI transport alias.
        events: Per-event configuration.
    """

    enabled: bool = False
    verbosity: VerbosityLevel | None = None
    discord: DiscordNotificationConfig | None = None
    discord_bot: DiscordBotNotificationConfig | None = None
    telegram: TelegramNotificationConfig | None = None
    slack: SlackNotificationConfig | None = None
    webhook: WebhookNotificationConfig | None = None
    openclaw: dict[str, bool] | None = None
    custom_webhook_command: CustomWebhookCommandConfig | None = None
    custom_cli_command: CustomCliCommandConfig | None = None
    events: dict[str, EventNotificationConfig] | None = None


@dataclass
class FullNotificationPayload:
    """Payload sent with each notification.

    Attributes:
        event: The event that triggered this notification.
        session_id: Session identifier.
        message: Pre-formatted message text.
        timestamp: ISO timestamp.
        tmux_session: Current tmux session name (if in tmux).
        project_path: Project directory path.
        project_name: Basename of the project directory.
        modes_used: Active OMX modes during this session.
        context_summary: Context summary of what was done.
        duration_ms: Session duration in milliseconds.
        agents_spawned: Number of agents spawned.
        agents_completed: Number of agents completed.
        reason: Stop/end reason.
        active_mode: Active mode name (for stop events).
        iteration: Current iteration (for stop events).
        max_iterations: Max iterations (for stop events).
        question: Question text (for ask-user-question events).
        incomplete_tasks: Incomplete task count.
        tmux_pane_id: tmux pane ID for reply injection target.
        tmux_tail: Captured tmux pane output (tail lines).
        tmux_tail_live: Whether tmux tail came from a live pane.
        agent_name: Agent name (populated by extensibility plugins).
        agent_type: Agent type (populated by extensibility plugins).
    """

    event: NotificationEvent | str
    session_id: str
    message: str = ""
    timestamp: str = ""
    tmux_session: str | None = None
    project_path: str | None = None
    project_name: str | None = None
    modes_used: list[str] | None = None
    context_summary: str | None = None
    duration_ms: int | None = None
    agents_spawned: int | None = None
    agents_completed: int | None = None
    reason: str | None = None
    active_mode: str | None = None
    iteration: int | None = None
    max_iterations: int | None = None
    question: str | None = None
    incomplete_tasks: int | None = None
    tmux_pane_id: str | None = None
    tmux_tail: str | None = None
    tmux_tail_live: bool | None = None
    agent_name: str | None = None
    agent_type: str | None = None


@dataclass
class NotificationResult:
    """Result of a notification send attempt.

    Attributes:
        platform: The notification platform.
        success: Whether the send was successful.
        error: Error message if failed.
        message_id: Platform message ID if available.
    """

    platform: NotificationPlatform | str
    success: bool
    error: str | None = None
    message_id: str | None = None


@dataclass
class DispatchResult:
    """Result of dispatching notifications for an event.

    Attributes:
        event: The notification event.
        results: Individual platform results.
        any_success: Whether at least one notification was sent successfully.
    """

    event: NotificationEvent | str
    results: list[NotificationResult] = field(default_factory=list)
    any_success: bool = False


@dataclass
class ReplyConfig:
    """Reply injection configuration.

    Attributes:
        enabled: Whether reply injection is enabled.
        poll_interval_ms: Polling interval in milliseconds (default: 3000).
        max_message_length: Maximum message length (default: 500).
        rate_limit_per_minute: Max messages per minute (default: 10).
        include_prefix: Include visual prefix like [reply:discord] (default: True).
        authorized_discord_user_ids: Authorized Discord user IDs.
    """

    enabled: bool = True
    poll_interval_ms: int = 3000
    max_message_length: int = 500
    rate_limit_per_minute: int = 10
    include_prefix: bool = True
    authorized_discord_user_ids: list[str] = field(default_factory=list)


@dataclass
class NotificationsBlock(FullNotificationConfig):
    """Top-level notifications block (supports both flat and profiled config).

    Attributes:
        default_profile: Default profile name (used when profiles are defined).
        profiles: Named notification profiles.
    """

    default_profile: str | None = None
    profiles: dict[str, FullNotificationConfig] | None = None


# Legacy types kept for backward compatibility with basic adapters
@dataclass
class NotificationPayload:
    """Legacy payload for sending a notification to external services.

    Attributes:
        title: Notification title/subject.
        body: Notification body text.
        channel: Target channel or topic.
        urgency: Priority level ("low", "normal", "high").
        metadata: Optional additional payload data.
    """

    title: str
    body: str
    channel: str = ""
    urgency: str = "normal"
    metadata: dict[str, Any] | None = None
