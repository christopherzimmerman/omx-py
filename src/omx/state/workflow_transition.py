"""Workflow transition rules — controls which mode combinations are allowed.

Port of src/state/workflow-transition.ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TRACKED_WORKFLOW_MODES: list[str] = [
    "autopilot",
    "autoresearch",
    "team",
    "ralph",
    "ultrawork",
    "ultraqa",
    "ralplan",
    "deep-interview",
]

ALLOWED_OVERLAP_PAIRS: set[str] = {"ralph|team"}

AUTO_COMPLETE_TRANSITIONS: set[str] = {
    "deep-interview->ralplan",
    "deep-interview->autoresearch",
    "ralplan->team",
    "ralplan->ralph",
    "ralplan->autopilot",
    "ralplan->autoresearch",
}

PLANNING_LIKE_MODES: set[str] = {"deep-interview", "ralplan"}
EXECUTION_LIKE_MODES: set[str] = {
    "autopilot",
    "autoresearch",
    "team",
    "ralph",
    "ultrawork",
    "ultraqa",
}


def is_tracked_workflow_mode(mode: str) -> bool:
    return mode in TRACKED_WORKFLOW_MODES


def _build_pair_key(a: str, b: str) -> str:
    return "|".join(sorted([a, b]))


def _is_allowed_overlap(a: str, b: str) -> bool:
    if a == "ultrawork" or b == "ultrawork":
        return True
    return _build_pair_key(a, b) in ALLOWED_OVERLAP_PAIRS


def _is_auto_complete_transition(a: str, b: str) -> bool:
    return f"{a}->{b}" in AUTO_COMPLETE_TRANSITIONS


def _is_rollback_transition(current_modes: list[str], requested: str) -> bool:
    return requested in PLANNING_LIKE_MODES and any(
        m in EXECUTION_LIKE_MODES for m in current_modes
    )


def _normalize_tracked_modes(modes: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for m in modes:
        if m in TRACKED_WORKFLOW_MODES and m not in seen:
            seen.add(m)
            result.append(m)
    return result


@dataclass
class WorkflowTransitionDecision:
    """Result of evaluating whether a workflow mode transition is permitted.

    Attributes:
        allowed: Whether the transition is allowed.
        kind: Transition type ("allow", "overlap", "auto-complete", "deny").
        current_modes: Active tracked modes before the transition.
        requested_mode: The mode being requested.
        resulting_modes: Modes that would be active after the transition.
        auto_complete_modes: Modes that would be auto-completed.
        transition_message: Human-readable transition description.
        denial_reason: Reason for denial (e.g. "rollback").
    """

    allowed: bool
    kind: str  # "allow", "overlap", "auto-complete", "deny"
    current_modes: list[str]
    requested_mode: str
    resulting_modes: list[str]
    auto_complete_modes: list[str] = field(default_factory=list)
    transition_message: str | None = None
    denial_reason: str | None = None


def evaluate_workflow_transition(
    current_active_modes: list[str],
    requested_mode: str,
) -> WorkflowTransitionDecision:
    """Evaluate whether activating a new workflow mode is allowed.

    Checks for re-entry, auto-complete transitions, allowed overlaps,
    and rollback constraints.

    Args:
        current_active_modes: List of currently active tracked modes.
        requested_mode: The mode being requested.

    Returns:
        A WorkflowTransitionDecision describing the outcome.
    """
    current = _normalize_tracked_modes(current_active_modes)

    if requested_mode in current:
        return WorkflowTransitionDecision(
            allowed=True,
            kind="allow",
            current_modes=current,
            requested_mode=requested_mode,
            resulting_modes=current,
        )

    if not current:
        return WorkflowTransitionDecision(
            allowed=True,
            kind="allow",
            current_modes=current,
            requested_mode=requested_mode,
            resulting_modes=[requested_mode],
        )

    auto_complete = [
        m for m in current if _is_auto_complete_transition(m, requested_mode)
    ]
    survivable = [m for m in current if m not in auto_complete]

    if auto_complete and all(
        _is_allowed_overlap(m, requested_mode) for m in survivable
    ):
        return WorkflowTransitionDecision(
            allowed=True,
            kind="auto-complete",
            current_modes=current,
            requested_mode=requested_mode,
            resulting_modes=_normalize_tracked_modes([*survivable, requested_mode]),
            auto_complete_modes=auto_complete,
            transition_message=f"mode transiting: {auto_complete[0]} -> {requested_mode}",
        )

    if all(_is_allowed_overlap(m, requested_mode) for m in current):
        return WorkflowTransitionDecision(
            allowed=True,
            kind="overlap",
            current_modes=current,
            requested_mode=requested_mode,
            resulting_modes=_normalize_tracked_modes([*current, requested_mode]),
        )

    return WorkflowTransitionDecision(
        allowed=False,
        kind="deny",
        current_modes=current,
        requested_mode=requested_mode,
        resulting_modes=current,
        denial_reason="rollback"
        if _is_rollback_transition(current, requested_mode)
        else None,
    )


def build_workflow_transition_error(
    current_active_modes: list[str],
    requested_mode: str,
    action: str = "activate",
) -> str:
    """Build a human-readable error message for a denied workflow transition.

    Args:
        current_active_modes: Currently active modes.
        requested_mode: The mode that was denied.
        action: Verb for the error message (e.g. "activate").

    Returns:
        Formatted error string with remediation instructions.
    """
    decision = evaluate_workflow_transition(current_active_modes, requested_mode)
    active_msg = _format_active_modes(decision.current_modes)
    overlap = " + ".join([*decision.current_modes, requested_mode])

    if decision.denial_reason == "rollback":
        return (
            f"Cannot {action} {requested_mode}: {active_msg}. "
            "Execution-to-planning rollback auto-complete is not allowed. "
            "First clear current state first and retry if this action is intended. "
            "Clear incompatible workflow state yourself via `omx state clear --mode <mode>` "
            "or the `omx_state.*` MCP tools, then retry."
        )
    return (
        f"Cannot {action} {requested_mode}: {active_msg}. "
        f"Unsupported workflow overlap: {overlap}. "
        "Current state is unchanged. "
        "Clear incompatible workflow state yourself via `omx state clear --mode <mode>` "
        "or the `omx_state.*` MCP tools, then retry."
    )


def _format_active_modes(modes: list[str]) -> str:
    if not modes:
        return "no tracked workflows"
    if len(modes) == 1:
        return f"{modes[0]} is already active"
    if len(modes) == 2:
        return f"{modes[0]} and {modes[1]} are already active"
    return f"{', '.join(modes[:-1])}, and {modes[-1]} are already active"


def build_workflow_transition_message(source_mode: str, requested_mode: str) -> str:
    """Build the canonical transition log message."""
    return f"mode transiting: {source_mode} -> {requested_mode}"


def assert_workflow_transition_allowed(
    current_active_modes: list[str],
    requested_mode: str,
    action: str = "activate",
) -> None:
    """Raise ``RuntimeError`` if the requested workflow transition is denied.

    Args:
        current_active_modes: Currently active tracked modes.
        requested_mode: The mode being requested.
        action: Verb for error messages (``"activate"``, ``"start"``, ``"write"``).

    Raises:
        RuntimeError: If the transition is not allowed.
    """
    decision = evaluate_workflow_transition(current_active_modes, requested_mode)
    if decision.allowed:
        return
    raise RuntimeError(
        build_workflow_transition_error(current_active_modes, requested_mode, action)
    )


def read_active_workflow_modes(
    cwd: str,
    session_id: str | None = None,
) -> list[str]:
    """Read tracked workflow modes that are currently active on disk.

    Walks every tracked workflow mode's read-scoped paths and returns the
    modes whose state file has ``active === True``.

    Args:
        cwd: Working directory.
        session_id: Optional session scope.

    Returns:
        Ordered list of active tracked workflow mode names.

    Raises:
        RuntimeError: If a state file exists but cannot be parsed.
    """
    # Local import to avoid a circular dependency: state.paths is pure, but
    # callers may use this from inside modes/base.py.
    from omx.state.paths import get_read_scoped_state_paths

    active: list[str] = []
    for mode in TRACKED_WORKFLOW_MODES:
        candidate_paths = get_read_scoped_state_paths(mode, cwd, session_id)
        for candidate in candidate_paths:
            path = Path(candidate)
            if not path.exists():
                continue
            try:
                parsed: Any = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Cannot read {mode} workflow state at {path}. "
                    f"Repair or clear that workflow state yourself via "
                    f"`omx state clear --mode {mode}` or the `omx_state.*` MCP tools."
                ) from exc
            if isinstance(parsed, dict) and parsed.get("active") is True:
                active.append(mode)
            break
    return active


def pick_primary_workflow_mode(
    current_primary: Any,
    resulting_modes: list[str],
    fallback_mode: str,
) -> str:
    """Pick a stable primary workflow mode given current + resulting set."""
    normalized = current_primary.strip() if isinstance(current_primary, str) else ""
    if normalized and normalized in resulting_modes:
        return normalized
    return resulting_modes[0] if resulting_modes else fallback_mode
