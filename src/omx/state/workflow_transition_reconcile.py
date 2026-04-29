"""Workflow transition reconciliation.

Port of src/state/workflow-transition-reconcile.ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.state.workflow_transition import (
    TRACKED_WORKFLOW_MODES,
    WorkflowTransitionDecision,
    build_workflow_transition_error,
    evaluate_workflow_transition,
    is_tracked_workflow_mode,
)
from omx.state.paths import get_state_path
from omx.state.skill_active import (
    list_active_skills,
    read_visible_skill_active_state,
    sync_canonical_skill_state_for_mode,
)


@dataclass
class ReconciledWorkflowTransition:
    """Result of a reconciled workflow transition.

    Attributes:
        decision: The transition decision.
        transition_message: Optional human-readable message.
        auto_completed_modes: Modes that were auto-completed.
        completed_paths: Paths of completed state files.
    """

    decision: WorkflowTransitionDecision
    transition_message: str | None = None
    auto_completed_modes: list[str] = field(default_factory=list)
    completed_paths: list[str] = field(default_factory=list)


def _safe_string(value: Any) -> str:
    """Safely convert a value to string."""
    return value if isinstance(value, str) else ""


def _read_json_if_exists(
    path: str,
    *,
    mode: str | None = None,
    throw_on_parse_error: bool = False,
) -> dict[str, Any] | None:
    """Read a JSON file, returning None if missing or invalid."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        if throw_on_parse_error and mode:
            raise RuntimeError(
                f"Cannot read {mode} workflow state at {path}. "
                f"Repair or clear that workflow state yourself via "
                f"`omx state clear --mode {mode}` or the `omx_state.*` MCP tools."
            )
        return None


def _visible_tracked_modes(cwd: str, session_id: str | None = None) -> list[str]:
    """Discover currently visible tracked workflow modes."""
    canonical = read_visible_skill_active_state(cwd, session_id)
    canonical_modes = [
        entry["skill"]
        for entry in list_active_skills(canonical or {})
        if is_tracked_workflow_mode(entry["skill"])
    ]

    visible: set[str] = set(canonical_modes)
    for mode in TRACKED_WORKFLOW_MODES:
        candidate_paths = (
            [get_state_path(mode, cwd, session_id), get_state_path(mode, cwd)]
            if session_id
            else [get_state_path(mode, cwd)]
        )
        for cp in candidate_paths:
            state = _read_json_if_exists(cp, mode=mode, throw_on_parse_error=True)
            if state and state.get("active") is True:
                visible.add(mode)

    return list(visible)


def _complete_source_mode_state(
    cwd: str,
    source_mode: str,
    destination_mode: str,
    session_id: str | None,
    now_iso: str,
    source: str,
) -> list[str]:
    """Mark a source mode's state as completed during transition."""
    transition_message = f"mode transiting: {source_mode} -> {destination_mode}"
    candidate_paths = (
        [get_state_path(source_mode, cwd, session_id), get_state_path(source_mode, cwd)]
        if session_id
        else [get_state_path(source_mode, cwd)]
    )
    completed_paths: list[str] = []

    for cp in candidate_paths:
        existing = _read_json_if_exists(cp)
        if not existing or existing.get("active") is not True:
            continue

        next_state = {
            **existing,
            "active": False,
            "current_phase": "completed",
            "completed_at": _safe_string(existing.get("completed_at")).strip()
            or now_iso,
            "auto_completed_reason": transition_message,
            "completion_note": f"Auto-completed {source_mode} during allowlisted transition to {destination_mode}.",
            "transition_source": source,
            "transition_target_mode": destination_mode,
        }
        next_state.pop("run_outcome", None)

        p = Path(cp)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(next_state, indent=2), encoding="utf-8")
        completed_paths.append(cp)

    sync_canonical_skill_state_for_mode(
        cwd=cwd,
        mode=source_mode,
        active=False,
        current_phase="completed",
        session_id=session_id,
        now_iso=now_iso,
        source=source,
    )

    return completed_paths


def reconcile_workflow_transition(
    cwd: str,
    requested_mode: str,
    *,
    action: str = "activate",
    session_id: str | None = None,
    now_iso: str | None = None,
    source: str = "workflow-transition",
    current_modes: list[str] | None = None,
) -> ReconciledWorkflowTransition:
    """Reconcile a workflow mode transition.

    Evaluates whether the transition is allowed and auto-completes
    source modes as needed.

    Args:
        cwd: Working directory.
        requested_mode: Mode being requested.
        action: Action verb for error messages.
        session_id: Optional session ID.
        now_iso: Optional ISO timestamp.
        source: Transition source identifier.
        current_modes: Override for currently active modes.

    Returns:
        ReconciledWorkflowTransition describing the outcome.

    Raises:
        RuntimeError: If the transition is denied.
    """
    now_iso = now_iso or datetime.now(timezone.utc).isoformat()

    if current_modes is not None:
        active_modes = [m for m in current_modes if is_tracked_workflow_mode(m)]
    else:
        active_modes = _visible_tracked_modes(cwd, session_id)

    decision = evaluate_workflow_transition(active_modes, requested_mode)

    if not decision.allowed:
        raise RuntimeError(
            build_workflow_transition_error(active_modes, requested_mode, action)
        )

    completed_paths: list[str] = []
    for src_mode in decision.auto_complete_modes:
        completed_paths.extend(
            _complete_source_mode_state(
                cwd,
                src_mode,
                requested_mode,
                session_id,
                now_iso,
                source,
            )
        )

    return ReconciledWorkflowTransition(
        decision=decision,
        transition_message=decision.transition_message,
        auto_completed_modes=decision.auto_complete_modes,
        completed_paths=completed_paths,
    )
