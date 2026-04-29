"""Terminal lifecycle outcome management.

Port of src/runtime/terminal-lifecycle.ts.
"""

from __future__ import annotations

from typing import Any

from omx.runtime.run_outcome import (
    normalize_lifecycle_outcome,
)


def infer_terminal_lifecycle_outcome(state: dict[str, Any]) -> str | None:
    """Infer a terminal lifecycle outcome from a state object.

    Checks lifecycle_outcome, terminal_outcome, and run_outcome fields.

    Args:
        state: Mode state dictionary.

    Returns:
        Canonical lifecycle outcome string, or None if non-terminal.
    """
    for field in ("lifecycle_outcome", "terminal_outcome"):
        value = state.get(field)
        if isinstance(value, str) and value.strip():
            result = normalize_lifecycle_outcome(value)
            if result:
                return result

    run_outcome = state.get("run_outcome")
    if isinstance(run_outcome, str) and run_outcome.strip():
        return _run_outcome_to_lifecycle(run_outcome)

    return None


def preferred_run_outcome_for_lifecycle(lifecycle: str) -> str:
    """Map a lifecycle outcome to the preferred run outcome.

    Args:
        lifecycle: Canonical lifecycle outcome string.

    Returns:
        Corresponding run outcome (defaults to "progress" if unknown).
    """
    mapping = {
        "finished": "finish",
        "blocked": "blocked_on_user",
        "failed": "failed",
        "userinterlude": "blocked_on_user",
        "askuserQuestion": "blocked_on_user",
    }
    return mapping.get(lifecycle, "progress")


def _run_outcome_to_lifecycle(run_outcome: str) -> str | None:
    mapping = {
        "finish": "finished",
        "blocked_on_user": "blocked",
        "failed": "failed",
        "cancelled": "failed",
    }
    return mapping.get(run_outcome.strip().lower())
