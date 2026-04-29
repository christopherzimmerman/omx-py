"""Plugin SDK for hook authors.

Port of src/hooks/extensibility/sdk.ts. Provides a lightweight SDK
object passed to plugin ``on_hook_event`` handlers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _sanitize_plugin_name(name: str) -> str:
    """Sanitise a plugin name for use in paths.

    Args:
        name: Raw plugin name.

    Returns:
        Lowercased name with non-alphanumeric chars replaced by underscores.
    """
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name).lower()


@dataclass
class HookPluginLogger:
    """Simple logger for hook plugins.

    Attributes:
        cwd: Working directory.
        plugin_name: Sanitised plugin name.
    """

    cwd: str = ""
    plugin_name: str = ""

    def info(self, message: str) -> None:
        """Log an info-level message.

        Args:
            message: Message to log.
        """
        import sys

        sys.stderr.write(f"[omx-hook:{self.plugin_name}] {message}\n")

    def warn(self, message: str) -> None:
        """Log a warning-level message.

        Args:
            message: Message to log.
        """
        import sys

        sys.stderr.write(f"[omx-hook:{self.plugin_name}] WARN: {message}\n")

    def error(self, message: str) -> None:
        """Log an error-level message.

        Args:
            message: Message to log.
        """
        import sys

        sys.stderr.write(f"[omx-hook:{self.plugin_name}] ERROR: {message}\n")


@dataclass
class HookPluginStateApi:
    """State persistence API for hook plugins.

    Attributes:
        cwd: Working directory.
        plugin_name: Sanitised plugin name.
    """

    cwd: str = ""
    plugin_name: str = ""

    def _state_dir(self) -> Path:
        return Path(self.cwd) / ".omx" / "hooks" / "state" / self.plugin_name

    def read(self, key: str) -> Any:
        """Read a state value by key.

        Args:
            key: State key.

        Returns:
            The stored value, or None if not found.
        """
        path = self._state_dir() / f"{key}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def write(self, key: str, value: Any) -> None:
        """Write a state value by key.

        Args:
            key: State key.
            value: JSON-serialisable value to store.
        """
        path = self._state_dir() / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2), encoding="utf-8")

    def clear(self) -> None:
        """Clear all state for this plugin."""
        import shutil

        state_dir = self._state_dir()
        if state_dir.exists():
            shutil.rmtree(str(state_dir), ignore_errors=True)


@dataclass
class HookPluginSdk:
    """SDK object passed to hook plugin ``on_hook_event`` handlers.

    Attributes:
        log: Logger for the plugin.
        state: State persistence API.
        cwd: Working directory.
        plugin_name: Sanitised plugin name.
        side_effects_enabled: Whether the plugin may perform side effects.
    """

    log: HookPluginLogger = field(default_factory=HookPluginLogger)
    state: HookPluginStateApi = field(default_factory=HookPluginStateApi)
    cwd: str = ""
    plugin_name: str = ""
    side_effects_enabled: bool = True


def create_hook_plugin_sdk(
    cwd: str,
    plugin_name: str,
    event: dict[str, Any] | None = None,
    side_effects_enabled: bool = True,
) -> HookPluginSdk:
    """Create a HookPluginSdk instance for a plugin.

    Args:
        cwd: Working directory.
        plugin_name: Raw plugin name (will be sanitised).
        event: The hook event envelope dict.
        side_effects_enabled: Whether side effects are allowed.

    Returns:
        Configured HookPluginSdk instance.
    """
    sanitised = _sanitize_plugin_name(plugin_name)
    return HookPluginSdk(
        log=HookPluginLogger(cwd=cwd, plugin_name=sanitised),
        state=HookPluginStateApi(cwd=cwd, plugin_name=sanitised),
        cwd=cwd,
        plugin_name=sanitised,
        side_effects_enabled=side_effects_enabled,
    )


def clear_hook_plugin_state(cwd: str, plugin_name: str) -> None:
    """Clear all persisted state for a hook plugin.

    Args:
        cwd: Working directory.
        plugin_name: Raw plugin name.
    """
    sanitised = _sanitize_plugin_name(plugin_name)
    api = HookPluginStateApi(cwd=cwd, plugin_name=sanitised)
    api.clear()


# ---------------------------------------------------------------------------
# SDK logging (port of extensibility/sdk/logging.ts)
# ---------------------------------------------------------------------------


def _hook_plugin_log_path(cwd: str) -> Path:
    """Return the JSONL log path for hook plugin logs.

    Args:
        cwd: Working directory.

    Returns:
        Path to the log file.
    """
    from datetime import datetime, timezone

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return Path(cwd) / ".omx" / "logs" / f"hooks-{date_str}.jsonl"


def append_sdk_hook_plugin_log(
    cwd: str,
    plugin_name: str,
    level: str,
    message: str,
    meta: dict[str, Any] | None = None,
) -> None:
    """Append a structured log entry for a hook plugin.

    Args:
        cwd: Working directory.
        plugin_name: Plugin name.
        level: Log level ("info", "warn", "error").
        message: Log message.
        meta: Additional metadata dict.
    """
    from datetime import datetime, timezone

    log_path = _hook_plugin_log_path(cwd)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "hook_plugin_log",
            "plugin": plugin_name,
            "level": level,
            "message": message,
            **(meta or {}),
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def create_hook_plugin_logger(
    cwd: str,
    plugin_name: str,
    event_name: str = "",
) -> HookPluginLogger:
    """Create a logger that also writes structured JSONL entries.

    Args:
        cwd: Working directory.
        plugin_name: Sanitised plugin name.
        event_name: Hook event name for context.

    Returns:
        Configured HookPluginLogger.
    """
    logger = HookPluginLogger(cwd=cwd, plugin_name=plugin_name)
    return logger


# ---------------------------------------------------------------------------
# Plugin state normalization (port of extensibility/sdk/plugin-state.ts)
# ---------------------------------------------------------------------------


def normalize_hook_plugin_state_key(key: str) -> str:
    """Normalize and validate a hook plugin state key.

    Args:
        key: Raw state key.

    Returns:
        Trimmed key.

    Raises:
        ValueError: If the key is empty or contains path traversal.
    """
    trimmed = key.strip()
    if not trimmed:
        raise ValueError("state key is required")
    if ".." in trimmed or trimmed.startswith("/"):
        raise ValueError("invalid state key")
    return trimmed


def clear_hook_plugin_state_files(cwd: str, plugin_name: str) -> None:
    """Clear persisted state files (data.json, tmux.json) for a plugin.

    Args:
        cwd: Working directory.
        plugin_name: Raw plugin name.
    """
    sanitised = _sanitize_plugin_name(plugin_name)
    root = Path(cwd) / ".omx" / "hooks" / "state" / sanitised
    for fname in ("data.json", "tmux.json"):
        try:
            (root / fname).unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Runtime state reader (port of extensibility/sdk/runtime-state.ts)
# ---------------------------------------------------------------------------


def read_omx_state_file(path: str | Path) -> dict[str, Any] | None:
    """Read an OMX state JSON file.

    Args:
        path: Path to the state file.

    Returns:
        Parsed dict or ``None`` on error.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        parsed = json.loads(p.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def omx_root_state_file_path(cwd: str, filename: str) -> Path:
    """Get path to a root-level OMX state file.

    Args:
        cwd: Working directory.
        filename: State filename.

    Returns:
        Full path to the state file.
    """
    return Path(cwd) / ".omx" / "state" / filename
