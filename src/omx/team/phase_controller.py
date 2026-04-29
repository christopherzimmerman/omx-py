"""Team phase state machine controller.

Port of src/team/phase-controller.ts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from omx.team.state.monitor import read_phase_state, write_phase_state
from omx.team.state.types import TeamPhaseState

TEAM_PHASES = ["planning", "executing", "verifying", "fixing", "completed", "failed"]

VALID_TRANSITIONS: dict[str, set[str]] = {
    "planning": {"executing", "failed"},
    "executing": {"verifying", "fixing", "failed"},
    "verifying": {"completed", "fixing", "failed"},
    "fixing": {"verifying", "failed"},
}


def can_transition_phase(from_phase: str, to_phase: str) -> bool:
    """Check if a phase transition is valid."""
    return to_phase in VALID_TRANSITIONS.get(from_phase, set())


def transition_phase(
    team_dir: Path,
    to_phase: str,
    reason: str = "",
) -> TeamPhaseState:
    """Transition the team phase.

    Args:
        team_dir: Path to team state directory.
        to_phase: Target phase.
        reason: Reason for the transition.

    Returns:
        Updated phase state.

    Raises:
        ValueError: If the transition is not valid.
    """
    phase = read_phase_state(team_dir) or TeamPhaseState()
    now = datetime.now(timezone.utc).isoformat()

    if not can_transition_phase(phase.current_phase, to_phase):
        raise ValueError(
            f"Invalid phase transition: {phase.current_phase} -> {to_phase}"
        )

    if to_phase == "fixing":
        phase.current_fix_attempt += 1
        if phase.current_fix_attempt > phase.max_fix_attempts:
            to_phase = "failed"
            reason = f"exceeded max fix attempts ({phase.max_fix_attempts})"

    phase.transitions.append(
        {
            "from": phase.current_phase,
            "to": to_phase,
            "at": now,
            "reason": reason,
        }
    )
    phase.current_phase = to_phase
    phase.updated_at = now

    write_phase_state(team_dir, phase)
    return phase
