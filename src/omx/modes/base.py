"""Mode state base class and lifecycle management.

Port of src/modes/base.ts. All execution modes (autopilot, autoresearch,
deep-interview, ralph, ultrawork, team, ultraqa, ralplan) share this base.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any


class ModeName(StrEnum):
    """Canonical execution mode names."""

    AUTOPILOT = "autopilot"
    AUTORESEARCH = "autoresearch"
    DEEP_INTERVIEW = "deep-interview"
    RALPH = "ralph"
    ULTRAWORK = "ultrawork"
    TEAM = "team"
    ULTRAQA = "ultraqa"
    RALPLAN = "ralplan"


class DeprecatedModeName(StrEnum):
    """Deprecated mode names."""

    ULTRAPILOT = "ultrapilot"
    PIPELINE = "pipeline"
    ECOMODE = "ecomode"


_DEPRECATED_MODES: dict[str, str] = {
    "ultrapilot": 'Use "team" instead. ultrapilot has been merged into team mode.',
    "pipeline": 'Use "team" instead. pipeline has been merged into team mode.',
    "ecomode": 'Use "ultrawork" instead. ecomode has been merged into ultrawork mode.',
}


@dataclass
class ModeState:
    """Base state for all execution modes.

    Attributes:
        active: Whether the mode is currently active.
        mode: Mode name.
        iteration: Current iteration counter.
        max_iterations: Maximum allowed iterations.
        current_phase: Current phase of the mode.
        run_outcome: Optional run outcome string.
        task_description: Description of the task.
        started_at: ISO timestamp of mode start.
        completed_at: ISO timestamp of mode completion.
        last_turn_at: ISO timestamp of last turn.
        error: Error message if mode errored.
        extra: Additional mode-specific fields.
    """

    active: bool = False
    mode: str = ""
    iteration: int = 0
    max_iterations: int = 50
    current_phase: str = ""
    run_outcome: str | None = None
    task_description: str | None = None
    started_at: str = ""
    completed_at: str | None = None
    last_turn_at: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for JSON persistence."""
        d: dict[str, Any] = {
            "active": self.active,
            "mode": self.mode,
            "iteration": self.iteration,
            "max_iterations": self.max_iterations,
            "current_phase": self.current_phase,
            "started_at": self.started_at,
        }
        if self.run_outcome is not None:
            d["run_outcome"] = self.run_outcome
        if self.task_description is not None:
            d["task_description"] = self.task_description
        if self.completed_at is not None:
            d["completed_at"] = self.completed_at
        if self.last_turn_at is not None:
            d["last_turn_at"] = self.last_turn_at
        if self.error is not None:
            d["error"] = self.error
        d.update(self.extra)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ModeState:
        """Deserialize from a dict."""
        known = {
            "active",
            "mode",
            "iteration",
            "max_iterations",
            "current_phase",
            "run_outcome",
            "task_description",
            "started_at",
            "completed_at",
            "last_turn_at",
            "error",
        }
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            active=data.get("active", False),
            mode=data.get("mode", ""),
            iteration=data.get("iteration", 0),
            max_iterations=data.get("max_iterations", 50),
            current_phase=data.get("current_phase", ""),
            run_outcome=data.get("run_outcome"),
            task_description=data.get("task_description"),
            started_at=data.get("started_at", ""),
            completed_at=data.get("completed_at"),
            last_turn_at=data.get("last_turn_at"),
            error=data.get("error"),
            extra=extra,
        )


def get_deprecation_warning(mode: str) -> str | None:
    """Check if a mode name is deprecated and return a warning message.

    Args:
        mode: Mode name string.

    Returns:
        Warning message or ``None`` if not deprecated.
    """
    warning = _DEPRECATED_MODES.get(mode)
    if not warning:
        return None
    return f'[DEPRECATED] Mode "{mode}" is deprecated. {warning}'


def _state_dir(project_root: str | None = None) -> Path:
    return Path(project_root or os.getcwd()) / ".omx" / "state"


def _state_path(
    mode: str, project_root: str | None = None, session_id: str | None = None
) -> Path:
    base = _state_dir(project_root)
    if session_id:
        base = base / "sessions" / session_id
    return base / f"{mode}-state.json"


def read_mode_state(mode: str, project_root: str | None = None) -> ModeState | None:
    """Read current mode state from disk.

    Args:
        mode: Mode name.
        project_root: Project root directory.

    Returns:
        Deserialized mode state or ``None``.
    """
    path = _state_path(mode, project_root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ModeState.from_dict(data) if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def write_mode_state(
    state: ModeState, project_root: str | None = None, session_id: str | None = None
) -> None:
    """Write mode state to disk.

    Args:
        state: Mode state to persist.
        project_root: Project root directory.
        session_id: Optional session identifier.
    """
    path = _state_path(state.mode, project_root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def cancel_mode(mode: str, project_root: str | None = None) -> None:
    """Cancel an active mode.

    Args:
        mode: Mode name.
        project_root: Project root directory.
    """
    state = read_mode_state(mode, project_root)
    if state and state.active:
        state.active = False
        state.current_phase = "cancelled"
        state.completed_at = datetime.now(timezone.utc).isoformat()
        write_mode_state(state, project_root)


def cancel_all_modes(project_root: str | None = None) -> list[str]:
    """Cancel all active modes.

    Args:
        project_root: Project root directory.

    Returns:
        List of cancelled mode names.
    """
    state_dir = _state_dir(project_root)
    cancelled: list[str] = []
    if not state_dir.exists():
        return cancelled
    for f in state_dir.iterdir():
        if f.name.endswith("-state.json") and f.is_file():
            mode = f.name.removesuffix("-state.json")
            state = read_mode_state(mode, project_root)
            if state and state.active:
                cancel_mode(mode, project_root)
                cancelled.append(mode)
    return cancelled


def list_active_modes(project_root: str | None = None) -> list[tuple[str, ModeState]]:
    """List all active modes.

    Args:
        project_root: Project root directory.

    Returns:
        List of (mode_name, state) tuples for active modes.
    """
    state_dir = _state_dir(project_root)
    active: list[tuple[str, ModeState]] = []
    if not state_dir.exists():
        return active
    for f in state_dir.iterdir():
        if f.name.endswith("-state.json") and f.is_file():
            mode = f.name.removesuffix("-state.json")
            state = read_mode_state(mode, project_root)
            if state and state.active:
                active.append((mode, state))
    return active
