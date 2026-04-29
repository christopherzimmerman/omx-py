"""Config.toml generation, reading, and merging."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omx.config.toml_writer import dumps
from omx.utils.paths import codex_config_path
from omx.utils.toml_read import read_toml


def read_config(path: Path | None = None) -> dict[str, Any]:
    """Read the Codex config.toml file."""
    return read_toml(path or codex_config_path())


def write_config(config: dict[str, Any], path: Path | None = None) -> None:
    """Write a config dict to config.toml."""
    target = path or codex_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(config), encoding="utf-8")


def merge_config(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge overlay into base, returning a new dict.

    Nested dicts are merged recursively; all other values are overwritten.

    Args:
        base: Base configuration dict.
        overlay: Overlay dict whose values take precedence.

    Returns:
        New merged dict (does not mutate inputs).
    """
    merged = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged
