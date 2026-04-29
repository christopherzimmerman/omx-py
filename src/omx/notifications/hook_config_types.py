"""Hook notification configuration types.

Schema for the hookTemplates key in .omx-config.json -- user-customizable
message templates with per-event, per-platform overrides.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PlatformTemplateOverride:
    """Per-platform message template override.

    Attributes:
        template: Message template with {{variable}} placeholders.
        enabled: Whether to send this event to this platform.
    """

    template: str | None = None
    enabled: bool | None = None


@dataclass
class HookEventConfig:
    """Per-event hook configuration.

    Attributes:
        enabled: Whether this event fires notifications.
        template: Default message template for this event (all platforms).
        platforms: Per-platform template overrides.
    """

    enabled: bool = True
    template: str | None = None
    platforms: dict[str, PlatformTemplateOverride] | None = None


@dataclass
class HookNotificationConfig:
    """Top-level schema for the hookTemplates key in .omx-config.json.

    Attributes:
        version: Schema version for future migration.
        enabled: Global enable/disable.
        events: Default templates per event.
        default_template: Global default template (fallback).
    """

    version: int = 1
    enabled: bool = True
    events: dict[str, HookEventConfig] | None = None
    default_template: str | None = None
