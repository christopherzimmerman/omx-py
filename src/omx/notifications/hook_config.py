"""Hook notification config reader.

Reads hookTemplates from .omx-config.json for user-customizable message templates.
Config is stored under the notifications.hookTemplates key.
Env var OMX_HOOK_CONFIG overrides to a separate file path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from omx.notifications.hook_config_types import (
    HookEventConfig,
    HookNotificationConfig,
    PlatformTemplateOverride,
)
from omx.notifications.types import (
    EventNotificationConfig,
    FullNotificationConfig,
)
from omx.utils.paths import codex_home

_SENTINEL = object()
_cached_config: HookNotificationConfig | None | object = _SENTINEL


def get_hook_config() -> HookNotificationConfig | None:
    """Read and cache the hook notification config.

    Primary source: notifications.hookTemplates key in codex_home()/.omx-config.json.
    Env var override: OMX_HOOK_CONFIG points to a separate file containing the
    HookNotificationConfig JSON directly.

    Returns:
        The hook config, or None if absent/disabled.
    """
    global _cached_config
    if _cached_config is not _SENTINEL:
        return _cached_config  # type: ignore[return-value]

    env_override = os.environ.get("OMX_HOOK_CONFIG")

    if env_override:
        p = Path(env_override)
        if not p.exists():
            _cached_config = None
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not raw or raw.get("enabled") is False:
                _cached_config = None
                return None
            _cached_config = _parse_hook_config(raw)
            return _cached_config
        except Exception:
            _cached_config = None
            return None

    config_path = codex_home() / ".omx-config.json"
    if not config_path.exists():
        _cached_config = None
        return None

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
        if not raw or not isinstance(raw, dict):
            _cached_config = None
            return None
        notifications = raw.get("notifications")
        if not notifications or not isinstance(notifications, dict):
            _cached_config = None
            return None
        hook_templates = notifications.get("hookTemplates")
        if not hook_templates or hook_templates.get("enabled") is False:
            _cached_config = None
            return None
        _cached_config = _parse_hook_config(hook_templates)
        return _cached_config
    except Exception:
        _cached_config = None
        return None


def reset_hook_config_cache() -> None:
    """Clear the cached hook config. Call in tests to reset state."""
    global _cached_config
    _cached_config = _SENTINEL


def _parse_hook_config(raw: dict) -> HookNotificationConfig:
    """Parse raw dict into HookNotificationConfig."""
    events = None
    raw_events = raw.get("events")
    if isinstance(raw_events, dict):
        events = {}
        for name, evt in raw_events.items():
            if not isinstance(evt, dict):
                continue
            platforms = None
            raw_platforms = evt.get("platforms")
            if isinstance(raw_platforms, dict):
                platforms = {}
                for pname, pval in raw_platforms.items():
                    if isinstance(pval, dict):
                        platforms[pname] = PlatformTemplateOverride(
                            template=pval.get("template"),
                            enabled=pval.get("enabled"),
                        )
            events[name] = HookEventConfig(
                enabled=evt.get("enabled", True),
                template=evt.get("template"),
                platforms=platforms,
            )

    return HookNotificationConfig(
        version=raw.get("version", 1),
        enabled=raw.get("enabled", True),
        events=events,
        default_template=raw.get("defaultTemplate"),
    )


def resolve_event_template(
    hook_config: HookNotificationConfig | None,
    event: str,
    platform: str,
) -> str | None:
    """Resolve the template for a specific event and platform.

    Cascade: platform override > event template > defaultTemplate > None.

    Args:
        hook_config: The hook notification config.
        event: The notification event name.
        platform: The notification platform name.

    Returns:
        The resolved template string, or None.
    """
    if not hook_config:
        return None

    if hook_config.events:
        event_config = hook_config.events.get(event)
        if event_config:
            if event_config.platforms:
                platform_override = event_config.platforms.get(platform)
                if platform_override and platform_override.template:
                    return platform_override.template
            if event_config.template:
                return event_config.template

    return hook_config.default_template or None


def merge_hook_config_into_notification_config(
    hook_config: HookNotificationConfig,
    notif_config: FullNotificationConfig,
) -> FullNotificationConfig:
    """Merge hook config event enabled/disabled flags into a FullNotificationConfig.

    Hook config takes precedence for event gating.

    Args:
        hook_config: The hook notification config.
        notif_config: The full notification config to merge into.

    Returns:
        A new FullNotificationConfig with merged event flags.
    """
    if not hook_config.events:
        return notif_config

    from dataclasses import replace

    merged = replace(notif_config)
    events = dict(merged.events or {})

    for event_name, hook_event_config in hook_config.events.items():
        if not hook_event_config:
            continue
        existing = events.get(event_name)
        if existing:
            from dataclasses import replace as _replace

            events[event_name] = _replace(existing, enabled=hook_event_config.enabled)
        else:
            events[event_name] = EventNotificationConfig(
                enabled=hook_event_config.enabled
            )

    merged.events = events
    return merged
