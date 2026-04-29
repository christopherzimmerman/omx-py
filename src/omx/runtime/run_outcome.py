"""Run outcome classification and normalization.

Port of src/runtime/run-outcome.ts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Terminal outcomes halt the run loop
TERMINAL_RUN_OUTCOMES = {"finish", "blocked_on_user", "failed", "cancelled"}
# Non-terminal outcomes continue iteration
NON_TERMINAL_RUN_OUTCOMES = {"progress", "continue"}

# Lifecycle outcomes (used in state persistence)
TERMINAL_LIFECYCLE_OUTCOMES = {
    "finished",
    "blocked",
    "failed",
    "userinterlude",
    "askuserQuestion",
}

# Alias mappings for normalization
RUN_OUTCOME_ALIASES: dict[str, str] = {
    "finished": "finish",
    "complete": "finish",
    "completed": "finish",
    "done": "finish",
    "blocked": "blocked_on_user",
    "blocked-on-user": "blocked_on_user",
    "canceled": "cancelled",
    "cancel": "cancelled",
    "aborted": "cancelled",
    "abort": "cancelled",
}

LIFECYCLE_OUTCOME_ALIASES: dict[str, str] = {
    "finish": "finished",
    "complete": "finished",
    "completed": "finished",
    "done": "finished",
    "blocked_on_user": "blocked",
    "blocked-on-user": "blocked",
    "canceled": "failed",
    "cancelled": "failed",
    "aborted": "failed",
}


def classify_run_outcome(value: Any) -> str:
    """Normalize an arbitrary value to a canonical run outcome string.

    Maps aliases (e.g. "finished" -> "finish") and defaults unknown
    values to "progress".

    Args:
        value: Raw outcome value from state or user input.

    Returns:
        One of: "finish", "blocked_on_user", "failed", "cancelled",
        "progress", or "continue".
    """
    if not isinstance(value, str) or not value.strip():
        return "progress"
    normalized = value.strip().lower()
    if normalized in TERMINAL_RUN_OUTCOMES:
        return normalized
    if normalized in NON_TERMINAL_RUN_OUTCOMES:
        return normalized
    return RUN_OUTCOME_ALIASES.get(normalized, "progress")


def is_terminal_run_outcome(outcome: str) -> bool:
    """Return True if the outcome halts the run loop."""
    return outcome in TERMINAL_RUN_OUTCOMES


def normalize_lifecycle_outcome(value: Any) -> str | None:
    """Normalize a lifecycle outcome value to a canonical form.

    Args:
        value: Raw lifecycle outcome string.

    Returns:
        Canonical lifecycle outcome, or None if invalid/empty.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized in TERMINAL_LIFECYCLE_OUTCOMES:
        return normalized
    return LIFECYCLE_OUTCOME_ALIASES.get(normalized)


def infer_run_outcome(state: dict[str, Any]) -> str:
    """Derive a run outcome from a mode state object.

    Checks in priority order: explicit run_outcome, lifecycle_outcome,
    active flag, then current_phase.

    Args:
        state: Mode state dictionary.

    Returns:
        Canonical run outcome string.
    """
    # Priority: explicit run_outcome → lifecycle_outcome → active flag → phase
    explicit = state.get("run_outcome")
    if isinstance(explicit, str) and explicit.strip():
        return classify_run_outcome(explicit)

    lifecycle = state.get("lifecycle_outcome") or state.get("terminal_outcome")
    if isinstance(lifecycle, str) and lifecycle.strip():
        mapped = _lifecycle_to_run_outcome(lifecycle)
        if mapped:
            return mapped

    if state.get("active") is False:
        if state.get("error"):
            return "failed"
        return "finish"

    phase = state.get("current_phase", "")
    if isinstance(phase, str) and phase.strip().lower() in (
        "completed",
        "finished",
        "done",
    ):
        return "finish"

    return "progress"


def apply_run_outcome_contract(state: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize run outcome state.

    Sets completed_at timestamp and active=False for terminal outcomes.

    Args:
        state: Mode state dictionary (mutated in place).

    Returns:
        Dict with "ok" bool, "state", and optional "error" key.
    """
    try:
        outcome = infer_run_outcome(state)
        now_iso = datetime.now(timezone.utc).isoformat()

        if is_terminal_run_outcome(outcome):
            state["run_outcome"] = outcome
            if not state.get("completed_at"):
                state["completed_at"] = now_iso
            state["active"] = False
        else:
            # Non-terminal: ensure active if not explicitly set
            if "active" not in state:
                state["active"] = True

        return {"ok": True, "state": state}
    except Exception as e:
        return {"ok": False, "error": str(e), "state": state}


def _lifecycle_to_run_outcome(lifecycle: str) -> str | None:
    normalized = lifecycle.strip().lower()
    mapping = {
        "finished": "finish",
        "blocked": "blocked_on_user",
        "failed": "failed",
        "userinterlude": "blocked_on_user",
        "askuserquestion": "blocked_on_user",
    }
    return mapping.get(normalized)
