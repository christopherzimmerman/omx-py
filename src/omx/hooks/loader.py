"""Plugin discovery and loading.

Port of src/hooks/extensibility/loader.ts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_PLUGIN_TIMEOUT_MS = 1500
ON_HOOK_EVENT_PATTERN = re.compile(r"onHookEvent|on_hook_event")


def hooks_dir(cwd: str) -> Path:
    return Path(cwd) / ".omx" / "hooks"


def is_hook_plugins_enabled() -> bool:
    """Check if hook plugins are enabled (ON by default, opt-out with '0')."""
    val = os.environ.get("OMX_HOOK_PLUGINS", "").strip().lower()
    return val not in ("0", "false", "no")


def resolve_hook_plugin_timeout_ms(fallback: int = DEFAULT_PLUGIN_TIMEOUT_MS) -> int:
    """Resolve the plugin execution timeout from environment or fallback.

    Args:
        fallback: Default timeout if env var is unset or invalid.

    Returns:
        Timeout in milliseconds, clamped to [100, 60000].
    """
    raw = os.environ.get("OMX_HOOK_PLUGIN_TIMEOUT_MS", "").strip()
    if not raw:
        return fallback
    try:
        val = int(raw)
        return max(100, min(60000, val))
    except ValueError:
        return fallback


def sanitize_plugin_id(file_name: str) -> str:
    """Normalize a plugin filename to a valid ID."""
    name = Path(file_name).stem
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower()


def discover_hook_plugins(cwd: str) -> list[Path]:
    """Discover available hook plugins in .omx/hooks/."""
    plugin_dir = hooks_dir(cwd)
    if not plugin_dir.exists():
        return []

    plugins: list[Path] = []
    for entry in sorted(plugin_dir.iterdir()):
        if entry.is_file() and entry.suffix in (".py", ".sh", ".js"):
            plugins.append(entry)
    return plugins
