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

from omx.ralph.contract import validate_and_normalize_ralph_state
from omx.runtime.run_outcome import apply_run_outcome_contract
from omx.state.mode_state_context import with_mode_runtime_context
from omx.state.paths import (
    get_base_state_dir,
    get_read_scoped_state_paths,
    get_state_path,
)
from omx.state.skill_active import sync_canonical_skill_state_for_mode
from omx.state.workflow_transition import (
    assert_workflow_transition_allowed,
    is_tracked_workflow_mode,
    read_active_workflow_modes,
)
from omx.state.workflow_transition_reconcile import reconcile_workflow_transition


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


# ---------------------------------------------------------------------------
# Dict-shaped mode-state lifecycle (port of TS startMode/updateModeState).
#
# These helpers operate directly on ``dict[str, Any]`` because mode-state JSON
# is extensible: each mode carries arbitrary side fields (autoresearch, ralph,
# team) that don't fit the ``ModeState`` dataclass. The TS source models this
# with ``[key: string]: unknown``; the Python port preserves the same shape.
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_ralph_mode_state_or_raise(state: dict[str, Any]) -> dict[str, Any]:
    original_phase = state.get("current_phase")
    result = validate_and_normalize_ralph_state(state)
    if not result.get("ok") or not isinstance(result.get("state"), dict):
        raise RuntimeError(result.get("error") or "Invalid ralph mode state")
    normalized: dict[str, Any] = result["state"]
    new_phase = normalized.get("current_phase")
    if (
        isinstance(original_phase, str)
        and isinstance(new_phase, str)
        and new_phase != original_phase
    ):
        normalized["ralph_phase_normalized_from"] = original_phase
    return normalized


def _apply_shared_run_outcome_contract_or_raise(
    state: dict[str, Any],
) -> dict[str, Any]:
    result = apply_run_outcome_contract(state)
    if not result.get("ok") or not isinstance(result.get("state"), dict):
        raise RuntimeError(result.get("error") or "Invalid run outcome state")
    return result["state"]


def _normalize_mode_state_or_raise(mode: str, state: dict[str, Any]) -> dict[str, Any]:
    normalized = (
        _normalize_ralph_mode_state_or_raise(state) if mode == "ralph" else state
    )
    return _apply_shared_run_outcome_contract_or_raise(normalized)


def assert_mode_start_allowed(
    mode: str, project_root: str | None = None, session_id: str | None = None
) -> None:
    """Raise if starting ``mode`` would violate the workflow-transition mutex.

    Args:
        mode: Mode being started.
        project_root: Project root directory; defaults to ``os.getcwd()``.
        session_id: Optional session scope.

    Raises:
        RuntimeError: If the transition is denied.
    """
    if not is_tracked_workflow_mode(mode):
        return
    cwd = project_root or os.getcwd()
    active_modes = read_active_workflow_modes(cwd, session_id)
    assert_workflow_transition_allowed(active_modes, mode, "start")


def _read_mode_state_dict(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def read_mode_state_dict(
    mode: str, project_root: str | None = None, session_id: str | None = None
) -> dict[str, Any] | None:
    """Read mode state as a raw dict, following read-scope precedence.

    Unlike :func:`read_mode_state`, this preserves every extension field on
    the underlying JSON document (the TS ``[key: string]: unknown`` shape).

    Args:
        mode: Mode name.
        project_root: Project root directory.
        session_id: Optional session scope.

    Returns:
        The raw dict, or ``None`` if no readable state file exists.
    """
    cwd = project_root or os.getcwd()
    for candidate in get_read_scoped_state_paths(mode, cwd, session_id):
        state = _read_mode_state_dict(Path(candidate))
        if state is not None:
            return state
    return None


def _write_state_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def start_mode(
    mode: str,
    task_description: str,
    max_iterations: int = 50,
    project_root: str | None = None,
    *,
    initial_state: dict[str, Any] | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Start a workflow mode.

    Reconciles the workflow-transition mutex (auto-completing source modes
    when an allowlisted handoff applies), writes the initial state document,
    and syncs the canonical ``skill-active`` state.

    Args:
        mode: Mode being started (one of the tracked workflow modes plus the
            non-tracked helpers like ``"pipeline"`` etc.).
        task_description: Human-readable task description.
        max_iterations: Maximum allowed iterations for the run.
        project_root: Project root; defaults to ``os.getcwd()``.
        initial_state: Optional extra fields merged into the initial state
            (useful for mode-specific fields like ``goal``/``budget``).
        session_id: Optional session scope.

    Returns:
        The initial state dict that was persisted.

    Raises:
        RuntimeError: If the workflow-transition mutex denies the start.
    """
    cwd = project_root or os.getcwd()
    base_dir = get_base_state_dir(cwd)
    base_dir.mkdir(parents=True, exist_ok=True)

    transition_message: str | None = None
    if is_tracked_workflow_mode(mode):
        transition = reconcile_workflow_transition(
            cwd,
            mode,
            action="start",
            session_id=session_id,
            source="start_mode",
        )
        transition_message = transition.transition_message

    state_path = get_state_path(mode, cwd, session_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    base_state: dict[str, Any] = {
        "active": True,
        "mode": mode,
        "iteration": 0,
        "max_iterations": max_iterations,
        "current_phase": "starting",
        "task_description": task_description,
        "started_at": _now_iso(),
    }
    if transition_message:
        base_state["transition_message"] = transition_message
    if mode == "ralph" and session_id:
        base_state["owner_omx_session_id"] = session_id
    if initial_state:
        # Caller-provided fields win, but never let them clobber the
        # canonical lifecycle fields silently — they can be overridden, but
        # the explicit dict-merge ordering documents intent.
        base_state = {**base_state, **initial_state}
        base_state.setdefault("active", True)
        base_state.setdefault("mode", mode)

    with_context = with_mode_runtime_context({}, base_state)
    normalized = _normalize_mode_state_or_raise(mode, with_context)
    _write_state_atomic(state_path, normalized)

    if is_tracked_workflow_mode(mode):
        sync_canonical_skill_state_for_mode(
            cwd=cwd,
            mode=mode,
            active=True,
            current_phase=normalized.get("current_phase")
            if isinstance(normalized.get("current_phase"), str)
            else None,
            session_id=session_id,
            source="start_mode",
        )
    return normalized


def update_mode_state(
    mode: str,
    updates: dict[str, Any],
    project_root: str | None = None,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Merge ``updates`` into the current mode state document.

    Mirrors the TS ``updateModeState`` semantics:

    - Reads the current state (session-scoped if ``session_id`` provided).
    - Merges ``updates`` shallowly; ``run_outcome`` is **only** carried over
      if it was explicitly provided in ``updates`` (the TS source strips it
      otherwise so the run-outcome contract recomputes a fresh value).
    - Re-applies the ralph contract (if ``mode == "ralph"``) and the shared
      run-outcome contract.
    - Re-runs ``with_mode_runtime_context`` so a re-activation captures the
      current ``TMUX_PANE`` even if pane info was missing.
    - Persists the merged document atomically.
    - Syncs canonical ``skill-active`` state for tracked modes.

    Args:
        mode: Mode being updated.
        updates: Fields to merge into the existing state.
        project_root: Project root; defaults to ``os.getcwd()``.
        session_id: Optional session scope (TS ``explicitSessionId``).

    Returns:
        The merged state dict.

    Raises:
        RuntimeError: If no state file exists for ``mode``.
    """
    cwd = project_root or os.getcwd()
    current = read_mode_state_dict(mode, cwd, session_id)
    if current is None:
        raise RuntimeError(f"Mode {mode} not found")

    state_path = get_state_path(mode, cwd, session_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    updated: dict[str, Any] = {**current, **updates}
    if "run_outcome" not in updates:
        updated.pop("run_outcome", None)
    if (
        mode == "ralph"
        and session_id
        and not isinstance(updated.get("owner_omx_session_id"), str)
    ):
        updated["owner_omx_session_id"] = session_id

    normalized = _normalize_mode_state_or_raise(mode, updated)
    with_context = with_mode_runtime_context(current, normalized)
    _write_state_atomic(state_path, with_context)

    if is_tracked_workflow_mode(mode):
        sync_canonical_skill_state_for_mode(
            cwd=cwd,
            mode=mode,
            active=with_context.get("active") is True,
            current_phase=with_context.get("current_phase")
            if isinstance(with_context.get("current_phase"), str)
            else None,
            session_id=session_id,
            source="update_mode_state",
        )
    return with_context
