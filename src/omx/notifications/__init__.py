"""Notification system -- multi-platform lifecycle notifications for oh-my-codex."""

from omx.notifications.types import (
    DiscordBotNotificationConfig,
    DiscordNotificationConfig,
    DispatchResult,
    EventNotificationConfig,
    FullNotificationConfig,
    FullNotificationPayload,
    NotificationEvent,
    NotificationPlatform,
    NotificationResult,
    NotificationsBlock,
    ReplyConfig,
    SlackNotificationConfig,
    TelegramNotificationConfig,
    VerbosityLevel,
    WebhookNotificationConfig,
)
from omx.notifications.config import (
    build_config_from_env,
    get_active_profile_name,
    get_enabled_platforms,
    get_notification_config,
    get_reply_config,
    get_reply_listener_platform_config,
    get_verbosity,
    is_event_allowed_by_verbosity,
    is_event_enabled,
    list_profiles,
    resolve_profile_config,
    should_include_tmux_tail,
    validate_mention,
    validate_slack_mention,
)
from omx.notifications.dispatcher import (
    dispatch_notifications,
    send_discord,
    send_discord_bot,
    send_slack,
    send_telegram,
    send_webhook,
)
from omx.notifications.formatter import (
    format_ask_user_question,
    format_notification,
    format_session_end,
    format_session_idle,
    format_session_start,
    format_session_stop,
    parse_tmux_tail,
)
from omx.notifications.template_engine import (
    compute_template_variables,
    get_default_template,
    interpolate_template,
    validate_template,
)
from omx.notifications.hook_config import (
    get_hook_config,
    merge_hook_config_into_notification_config,
    reset_hook_config_cache,
    resolve_event_template,
)
from omx.notifications.hook_config_types import (
    HookEventConfig,
    HookNotificationConfig,
    PlatformTemplateOverride,
)
from omx.notifications.lifecycle_dedupe import (
    create_lifecycle_broadcast_fingerprint,
    record_lifecycle_hook_broadcast_sent,
    record_lifecycle_notification_sent,
    should_send_lifecycle_hook_broadcast,
    should_send_lifecycle_notification,
)
from omx.notifications.idle_cooldown import (
    get_idle_notification_cooldown_seconds,
    record_idle_notification_sent,
    should_send_idle_notification,
)
from omx.notifications.dispatch_cooldown import (
    get_dispatch_notification_cooldown_seconds,
    record_dispatch_notification_sent,
    should_send_dispatch_notification,
)
from omx.notifications.session_registry import (
    SessionMapping,
    load_all_mappings,
    lookup_by_message_id,
    prune_stale,
    register_message,
    remove_messages_by_pane,
    remove_session,
)
from omx.notifications.reply_listener import (
    DaemonResponse,
    ReplyListenerState,
    get_reply_listener_status,
    is_daemon_running,
    sanitize_reply_input,
)
from omx.notifications.tmux_notify import (
    capture_tmux_pane,
    format_tmux_info,
    get_current_tmux_pane_id,
    get_current_tmux_session,
    get_team_tmux_sessions,
    sanitize_tmux_alert_text,
)
from omx.notifications.notifier import (
    NotificationConfig,
    load_notification_config,
    notify,
)

__all__ = [
    # Types
    "NotificationEvent",
    "NotificationPlatform",
    "VerbosityLevel",
    "DiscordNotificationConfig",
    "DiscordBotNotificationConfig",
    "TelegramNotificationConfig",
    "SlackNotificationConfig",
    "WebhookNotificationConfig",
    "EventNotificationConfig",
    "FullNotificationConfig",
    "FullNotificationPayload",
    "NotificationResult",
    "DispatchResult",
    "ReplyConfig",
    "NotificationsBlock",
    # Config
    "get_notification_config",
    "build_config_from_env",
    "is_event_enabled",
    "get_enabled_platforms",
    "get_reply_config",
    "get_reply_listener_platform_config",
    "resolve_profile_config",
    "list_profiles",
    "get_active_profile_name",
    "get_verbosity",
    "is_event_allowed_by_verbosity",
    "should_include_tmux_tail",
    "validate_mention",
    "validate_slack_mention",
    # Dispatcher
    "dispatch_notifications",
    "send_discord",
    "send_discord_bot",
    "send_telegram",
    "send_slack",
    "send_webhook",
    # Formatter
    "format_notification",
    "format_session_start",
    "format_session_stop",
    "format_session_end",
    "format_session_idle",
    "format_ask_user_question",
    "parse_tmux_tail",
    # Template engine
    "interpolate_template",
    "validate_template",
    "compute_template_variables",
    "get_default_template",
    # Hook config
    "get_hook_config",
    "reset_hook_config_cache",
    "resolve_event_template",
    "merge_hook_config_into_notification_config",
    "HookNotificationConfig",
    "HookEventConfig",
    "PlatformTemplateOverride",
    # Lifecycle dedupe
    "should_send_lifecycle_notification",
    "record_lifecycle_notification_sent",
    "should_send_lifecycle_hook_broadcast",
    "record_lifecycle_hook_broadcast_sent",
    "create_lifecycle_broadcast_fingerprint",
    # Idle cooldown
    "get_idle_notification_cooldown_seconds",
    "should_send_idle_notification",
    "record_idle_notification_sent",
    # Dispatch cooldown
    "get_dispatch_notification_cooldown_seconds",
    "should_send_dispatch_notification",
    "record_dispatch_notification_sent",
    # Session registry
    "SessionMapping",
    "register_message",
    "load_all_mappings",
    "lookup_by_message_id",
    "remove_session",
    "remove_messages_by_pane",
    "prune_stale",
    # Reply listener
    "ReplyListenerState",
    "DaemonResponse",
    "get_reply_listener_status",
    "is_daemon_running",
    "sanitize_reply_input",
    # Tmux
    "get_current_tmux_session",
    "get_current_tmux_pane_id",
    "get_team_tmux_sessions",
    "format_tmux_info",
    "capture_tmux_pane",
    "sanitize_tmux_alert_text",
    # Legacy notifier
    "NotificationConfig",
    "notify",
    "load_notification_config",
]
