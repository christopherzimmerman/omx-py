"""HUD state management.

Adds session-aware mode state readers (ralph, ultrawork) and HUD config
normalization, in addition to the legacy ``hud-state.json`` accessors.

Port targets:
- src/hud/state.ts: readRalphState, readUltraworkState, normalizeHudConfig.
- The richer ``readAllState`` orchestrator and skill-active integration live
  in higher-level modules; this file exposes the primitives the HUD renderer
  and watch loop need directly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from omx.hud.types import (
    DEFAULT_HUD_CONFIG,
    HudConfig,
    HudGitConfig,
    RalphStateForHud,
    ResolvedHudConfig,
    ResolvedHudGitConfig,
    UltraworkStateForHud,
)
from omx.state.paths import get_read_scoped_state_paths
from omx.utils.paths import omx_state_dir

# Re-export legacy hud-state.json shape for backwards compatibility.

T = TypeVar("T")

_VALID_PRESETS = {"minimal", "focused", "full"}
_VALID_GIT_DISPLAYS = {"branch", "repo-branch"}


__all__ = [
    # Legacy raw state file accessors.
    "read_hud_state",
    "write_hud_state",
    # New session-aware readers + normalizer.
    "read_ralph_state",
    "read_ultrawork_state",
    "normalize_hud_config",
    "read_hud_config",
    # Re-export dataclasses for ergonomic imports from this module.
    "RalphStateForHud",
    "UltraworkStateForHud",
    "HudConfig",
    "ResolvedHudConfig",
]


# ---------------------------------------------------------------------------
# Legacy hud-state.json file accessors (compaction notify counters etc.)
# ---------------------------------------------------------------------------


def read_hud_state(project_root: Path | None = None) -> dict[str, Any]:
    """Read the HUD state file (``.omx/state/hud-state.json``)."""
    path = omx_state_dir(project_root) / "hud-state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_hud_state(state: dict[str, Any], project_root: Path | None = None) -> None:
    """Write the HUD state file."""
    path = omx_state_dir(project_root) / "hud-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _read_json_file(path: Path) -> dict[str, Any] | None:
    """Read a JSON file and return its parsed contents, or None on error."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    if isinstance(parsed, dict):
        return parsed
    return None


def _read_session_aware_mode_state(
    cwd: str, mode: str, session_id: str | None = None
) -> dict[str, Any] | None:
    """Read the first available mode state file in session-aware precedence."""
    try:
        candidates = get_read_scoped_state_paths(mode, cwd, session_id)
    except ValueError:
        return None

    if session_id:
        if not candidates:
            return None
        return _read_json_file(candidates[0])

    for candidate in candidates:
        data = _read_json_file(candidate)
        if data is not None:
            return data
    return None


# ---------------------------------------------------------------------------
# Mode state readers
# ---------------------------------------------------------------------------


def _coerce_ralph(data: dict[str, Any]) -> RalphStateForHud:
    """Construct a ``RalphStateForHud`` from a parsed JSON payload."""
    iteration = data.get("iteration")
    if not isinstance(iteration, int) or isinstance(iteration, bool):
        iteration = None
    max_iterations = data.get("max_iterations")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool):
        max_iterations = None
    return RalphStateForHud(
        active=bool(data.get("active")),
        iteration=iteration,
        max_iterations=max_iterations,
    )


def _coerce_ultrawork(data: dict[str, Any]) -> UltraworkStateForHud:
    """Construct an ``UltraworkStateForHud`` from a parsed JSON payload."""
    reinforcement_count = data.get("reinforcement_count")
    if not isinstance(reinforcement_count, int) or isinstance(
        reinforcement_count, bool
    ):
        reinforcement_count = None
    return UltraworkStateForHud(
        active=bool(data.get("active")),
        reinforcement_count=reinforcement_count,
    )


def read_ralph_state(
    cwd: str, session_id: str | None = None
) -> RalphStateForHud | None:
    """Read the ralph mode state for the HUD.

    Args:
        cwd: Working directory.
        session_id: Optional session ID for session-scoped reads.

    Returns:
        ``RalphStateForHud`` when ralph is active, otherwise ``None``.
    """
    data = _read_session_aware_mode_state(cwd, "ralph", session_id)
    if data is None:
        return None
    state = _coerce_ralph(data)
    return state if state.active else None


def read_ultrawork_state(
    cwd: str, session_id: str | None = None
) -> UltraworkStateForHud | None:
    """Read the ultrawork mode state for the HUD.

    Args:
        cwd: Working directory.
        session_id: Optional session ID for session-scoped reads.

    Returns:
        ``UltraworkStateForHud`` when ultrawork is active, otherwise ``None``.
    """
    data = _read_session_aware_mode_state(cwd, "ultrawork", session_id)
    if data is None:
        return None
    state = _coerce_ultrawork(data)
    return state if state.active else None


# ---------------------------------------------------------------------------
# HUD config normalization
# ---------------------------------------------------------------------------


def _sanitize_optional_string(value: object) -> str | None:
    """Return a stripped non-empty string, else None."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def normalize_hud_config(
    raw: HudConfig | dict[str, Any] | None,
) -> ResolvedHudConfig:
    """Normalize a raw HUD config into a fully resolved instance.

    Accepts the dataclass form (``HudConfig``) used inside Python, the raw
    JSON-decoded ``dict`` returned from disk, or ``None``. Unknown values are
    discarded and defaults from ``DEFAULT_HUD_CONFIG`` are applied.

    Args:
        raw: Raw config (dict or dataclass) or None.

    Returns:
        Normalized ``ResolvedHudConfig``.
    """
    resolved = ResolvedHudConfig(
        preset=DEFAULT_HUD_CONFIG.preset,
        git=ResolvedHudGitConfig(
            display=DEFAULT_HUD_CONFIG.git.display,
            remote_name=DEFAULT_HUD_CONFIG.git.remote_name,
            repo_label=DEFAULT_HUD_CONFIG.git.repo_label,
        ),
    )
    if raw is None:
        return resolved

    if isinstance(raw, HudConfig):
        preset = raw.preset
        git_raw: HudGitConfig | dict[str, Any] | None = raw.git
    elif isinstance(raw, dict):
        preset = raw.get("preset")
        git_raw = raw.get("git")
    else:
        return resolved

    if isinstance(preset, str) and preset in _VALID_PRESETS:
        resolved.preset = preset

    if git_raw is None:
        return resolved

    if isinstance(git_raw, HudGitConfig):
        git_display = git_raw.display
        git_remote_name = git_raw.remote_name
        git_repo_label = git_raw.repo_label
    elif isinstance(git_raw, dict):
        git_display = git_raw.get("display")
        # Accept TS camelCase and Python snake_case keys.
        git_remote_name = git_raw.get("remoteName")
        if git_remote_name is None:
            git_remote_name = git_raw.get("remote_name")
        git_repo_label = git_raw.get("repoLabel")
        if git_repo_label is None:
            git_repo_label = git_raw.get("repo_label")
    else:
        return resolved

    if isinstance(git_display, str) and git_display in _VALID_GIT_DISPLAYS:
        resolved.git.display = git_display

    remote_name = _sanitize_optional_string(git_remote_name)
    if remote_name:
        resolved.git.remote_name = remote_name

    repo_label = _sanitize_optional_string(git_repo_label)
    if repo_label:
        resolved.git.repo_label = repo_label

    return resolved


def read_hud_config(cwd: str) -> ResolvedHudConfig:
    """Read and normalize the on-disk HUD config (``.omx/hud-config.json``).

    Args:
        cwd: Working directory.

    Returns:
        Resolved HUD config with defaults applied.
    """
    config_path = Path(cwd) / ".omx" / "hud-config.json"
    raw = _read_json_file(config_path)
    return normalize_hud_config(raw)
