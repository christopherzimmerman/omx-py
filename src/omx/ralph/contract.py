"""Ralph phase contract and validation.

Port of src/ralph/contract.ts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

RALPH_PHASES: list[str] = [
    "starting",
    "executing",
    "verifying",
    "fixing",
    "blocked_on_user",
    "complete",
    "failed",
    "cancelled",
]

_RALPH_PHASE_SET: set[str] = set(RALPH_PHASES)

RALPH_TERMINAL_PHASE_SET: set[str] = {
    "blocked_on_user",
    "complete",
    "failed",
    "cancelled",
}

LEGACY_PHASE_ALIASES: dict[str, str] = {
    # Pre-port Python 4-phase contract (investigate/plan/execute/verify) folds
    # onto the TS phases: the front half of that pipeline maps to "starting",
    # the back half to the matching canonical TS phase.
    "investigate": "starting",
    "investigating": "starting",
    "plan": "starting",
    "planning": "starting",
    "start": "starting",
    "started": "starting",
    "execute": "executing",
    "execution": "executing",
    "implementation": "executing",
    "verify": "verifying",
    "verification": "verifying",
    "fix": "fixing",
    "blocked": "blocked_on_user",
    "blocked-on-user": "blocked_on_user",
    "completed": "complete",
    "fail": "failed",
    "error": "failed",
    "cancel": "cancelled",
}


def _as_finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (
        value != value or value in (float("inf"), float("-inf"))
    ):
        return None
    return value


def _is_iso_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or value.strip() == "":
        return False
    # Accept the trailing 'Z' shorthand that TS Date.parse handles.
    candidate = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_ralph_phase(raw_phase: Any) -> dict[str, Any]:
    """Normalize a candidate Ralph phase value.

    Mirrors TS ``normalizeRalphPhase``. Returns one of:

    - ``{"phase": <canonical>}`` when ``raw_phase`` is already canonical.
    - ``{"phase": <canonical>, "warning": ...}`` when a legacy alias was
      coerced.
    - ``{"error": ...}`` when the value is not a recognized phase.
    """
    if not isinstance(raw_phase, str) or raw_phase.strip() == "":
        return {"error": "ralph.current_phase must be a non-empty string"}

    normalized = raw_phase.strip().lower()
    if normalized in _RALPH_PHASE_SET:
        return {"phase": normalized}

    alias = LEGACY_PHASE_ALIASES.get(normalized)
    if alias:
        return {
            "phase": alias,
            "warning": f'normalized legacy Ralph phase "{raw_phase}" -> "{alias}"',
        }

    return {
        "error": "ralph.current_phase must be one of: " + ", ".join(RALPH_PHASES),
    }


def validate_and_normalize_ralph_state(
    state: dict[str, Any],
    *,
    now_iso: str | None = None,
) -> dict[str, Any]:
    """Validate and normalize a Ralph mode-state document.

    Port of TS ``validateAndNormalizeRalphState``:

    - Coerces ``current_phase`` through :func:`normalize_ralph_phase`.
    - Fills lifecycle defaults (``iteration``, ``max_iterations``,
      ``current_phase``, ``started_at``) when ``active is True``.
    - Validates ``iteration`` and ``max_iterations`` shape.
    - When ``current_phase`` is terminal, requires ``active=False`` and
      stamps ``completed_at`` if missing.
    - Rejects malformed ``started_at`` / ``completed_at`` timestamps.

    Args:
        state: Ralph mode-state dict (not mutated; a shallow copy is
            returned in the ``state`` field).
        now_iso: Optional ISO timestamp used for defaults (``started_at``,
            ``completed_at``). Defaults to ``datetime.now(timezone.utc)``.

    Returns:
        Dict with ``ok`` bool, ``state`` (shallow-copy of inputs with
        normalizations applied), and optional ``error`` / ``warning``
        keys, matching the TS ``RalphStateValidationResult`` shape.
    """
    stamp = now_iso or _now_iso()
    nxt: dict[str, Any] = {**state}
    warning: str | None = None

    if nxt.get("current_phase") is not None:
        phase_result = normalize_ralph_phase(nxt["current_phase"])
        if "error" in phase_result:
            return {"ok": False, "error": phase_result["error"], "state": state}
        nxt["current_phase"] = phase_result["phase"]
        if phase_result.get("warning"):
            warning = phase_result["warning"]

    if nxt.get("active") is True:
        if nxt.get("iteration") is None:
            nxt["iteration"] = 0
        if nxt.get("max_iterations") is None:
            nxt["max_iterations"] = 50
        if nxt.get("current_phase") is None:
            nxt["current_phase"] = "starting"
        if nxt.get("started_at") is None:
            nxt["started_at"] = stamp

    if nxt.get("iteration") is not None:
        value = _as_finite_number(nxt["iteration"])
        if value is None or not isinstance(value, int) or value < 0:
            return {
                "ok": False,
                "error": "ralph.iteration must be a finite integer >= 0",
                "state": state,
            }

    if nxt.get("max_iterations") is not None:
        value = _as_finite_number(nxt["max_iterations"])
        if value is None or not isinstance(value, int) or value <= 0:
            return {
                "ok": False,
                "error": "ralph.max_iterations must be a finite integer > 0",
                "state": state,
            }

    phase = nxt.get("current_phase")
    if isinstance(phase, str) and phase in RALPH_TERMINAL_PHASE_SET:
        if nxt.get("active") is True:
            return {
                "ok": False,
                "error": "terminal Ralph phases require active=false",
                "state": state,
            }
        if nxt.get("completed_at") is None:
            nxt["completed_at"] = stamp

    if nxt.get("started_at") is not None and not _is_iso_timestamp(nxt["started_at"]):
        return {
            "ok": False,
            "error": "ralph.started_at must be an ISO8601 timestamp",
            "state": state,
        }
    if nxt.get("completed_at") is not None and not _is_iso_timestamp(
        nxt["completed_at"]
    ):
        return {
            "ok": False,
            "error": "ralph.completed_at must be an ISO8601 timestamp",
            "state": state,
        }

    result: dict[str, Any] = {"ok": True, "state": nxt}
    if warning is not None:
        result["warning"] = warning
    return result
