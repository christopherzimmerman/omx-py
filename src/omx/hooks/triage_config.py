"""Triage feature gate config reader.

Port of src/hooks/triage-config.ts. Reads
``promptRouting.triage.enabled`` from ``codexHome()/.omx-config.json``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from omx.utils.paths import codex_home


class TriageConfigStatus(StrEnum):
    """Triage feature gate status."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    DEFAULTED = "defaulted"
    INVALID = "invalid"


@dataclass
class TriageConfig:
    """Resolved triage feature gate configuration.

    Attributes:
        enabled: Whether triage is enabled.
        status: Status of the configuration resolution.
        source: Where the config came from.
        path: Path to the config file.
    """

    enabled: bool
    status: TriageConfigStatus
    source: str  # "default" | "file" | "invalid"
    path: str


_cached_triage_config: TriageConfig | None = None


def read_triage_config() -> TriageConfig:
    """Read and cache the triage feature gate config.

    Returns:
        Resolved triage configuration. Result is cached for process lifetime.
    """
    global _cached_triage_config
    if _cached_triage_config is not None:
        return _cached_triage_config

    path = str(Path(codex_home()) / ".omx-config.json")
    p = Path(path)

    if not p.exists():
        _cached_triage_config = TriageConfig(
            enabled=True,
            status=TriageConfigStatus.DEFAULTED,
            source="default",
            path=path,
        )
        return _cached_triage_config

    try:
        raw: Any = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _cached_triage_config = TriageConfig(
            enabled=False,
            status=TriageConfigStatus.INVALID,
            source="invalid",
            path=path,
        )
        return _cached_triage_config

    if not isinstance(raw, dict):
        _cached_triage_config = TriageConfig(
            enabled=False,
            status=TriageConfigStatus.INVALID,
            source="invalid",
            path=path,
        )
        return _cached_triage_config

    prompt_routing = raw.get("promptRouting")
    if prompt_routing is None:
        _cached_triage_config = TriageConfig(
            enabled=True,
            status=TriageConfigStatus.DEFAULTED,
            source="default",
            path=path,
        )
        return _cached_triage_config
    if not isinstance(prompt_routing, dict):
        _cached_triage_config = TriageConfig(
            enabled=False,
            status=TriageConfigStatus.INVALID,
            source="invalid",
            path=path,
        )
        return _cached_triage_config

    triage = prompt_routing.get("triage")
    if triage is None:
        _cached_triage_config = TriageConfig(
            enabled=True,
            status=TriageConfigStatus.DEFAULTED,
            source="default",
            path=path,
        )
        return _cached_triage_config
    if not isinstance(triage, dict):
        _cached_triage_config = TriageConfig(
            enabled=False,
            status=TriageConfigStatus.INVALID,
            source="invalid",
            path=path,
        )
        return _cached_triage_config

    triage_enabled = triage.get("enabled")
    if triage_enabled is None:
        _cached_triage_config = TriageConfig(
            enabled=True,
            status=TriageConfigStatus.DEFAULTED,
            source="default",
            path=path,
        )
        return _cached_triage_config
    if not isinstance(triage_enabled, bool):
        _cached_triage_config = TriageConfig(
            enabled=False,
            status=TriageConfigStatus.INVALID,
            source="invalid",
            path=path,
        )
        return _cached_triage_config

    _cached_triage_config = TriageConfig(
        enabled=triage_enabled,
        status=TriageConfigStatus.ENABLED
        if triage_enabled
        else TriageConfigStatus.DISABLED,
        source="file",
        path=path,
    )
    return _cached_triage_config


def reset_triage_config_cache() -> None:
    """Clear the cached triage config. Call in tests to reset state."""
    global _cached_triage_config
    _cached_triage_config = None
