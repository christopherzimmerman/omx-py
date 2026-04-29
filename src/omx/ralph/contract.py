"""Ralph phase contract and validation.

Port of src/ralph/contract.ts.
"""

from __future__ import annotations

from typing import Any

RALPH_PHASES = ["investigate", "plan", "execute", "verify"]

PHASE_ALIASES: dict[str, str] = {
    "investigating": "investigate",
    "planning": "plan",
    "executing": "execute",
    "verifying": "verify",
    "verification": "verify",
    "implementation": "execute",
}


def validate_and_normalize_ralph_state(
    state: dict[str, Any],
) -> dict[str, Any]:
    """Validate and normalize ralph state phase values.

    Resolves phase aliases, defaults active sessions to "investigate",
    and rejects invalid phase values.

    Args:
        state: Ralph mode state dictionary (mutated in place).

    Returns:
        Dict with "ok" bool, "state", and optional "error" key.
    """
    phase = state.get("current_phase", "")

    if isinstance(phase, str):
        normalized = phase.strip().lower()
        if normalized in RALPH_PHASES:
            state["current_phase"] = normalized
            return {"ok": True, "state": state}
        alias = PHASE_ALIASES.get(normalized)
        if alias:
            state["current_phase"] = alias
            return {"ok": True, "state": state}

    # If no phase set but active, default to investigate
    if state.get("active") and not phase:
        state["current_phase"] = "investigate"
        return {"ok": True, "state": state}

    if phase and phase not in RALPH_PHASES:
        return {
            "ok": False,
            "error": f"ralph.current_phase must be one of: {', '.join(RALPH_PHASES)}",
            "state": state,
        }

    return {"ok": True, "state": state}
