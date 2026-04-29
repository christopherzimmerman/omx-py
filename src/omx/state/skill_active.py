"""Skill-active state tracking.

Port of src/state/skill-active.ts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omx.state.paths import get_state_path

SKILL_ACTIVE_STATE_MODE = "skill-active"
SKILL_ACTIVE_STATE_FILE = f"{SKILL_ACTIVE_STATE_MODE}-state.json"

CANONICAL_WORKFLOW_SKILLS: list[str] = [
    "autopilot",
    "autoresearch",
    "team",
    "ralph",
    "ultrawork",
    "ultraqa",
    "ralplan",
    "deep-interview",
]


def read_skill_active_state(path: Path) -> dict[str, Any] | None:
    """Read skill-active state from a file."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def list_active_skills(state: dict[str, Any]) -> list[dict[str, Any]]:
    """List active skills from a skill-active state dict.

    Args:
        state: Skill-active state dictionary.

    Returns:
        List of active skill entries with 'skill' keys.
    """
    entries = state.get("active_skills", [])
    return [e for e in entries if isinstance(e, dict) and e.get("active")]


def read_visible_skill_active_state(
    cwd: str,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Read the visible skill-active state.

    Args:
        cwd: Working directory.
        session_id: Optional session scope.

    Returns:
        Skill-active state dict or None.
    """
    paths_to_check = []
    if session_id:
        paths_to_check.append(get_state_path(SKILL_ACTIVE_STATE_MODE, cwd, session_id))
    paths_to_check.append(get_state_path(SKILL_ACTIVE_STATE_MODE, cwd))

    for p in paths_to_check:
        result = read_skill_active_state(Path(p) if isinstance(p, str) else p)
        if result is not None:
            return result
    return None


def sync_canonical_skill_state_for_mode(
    cwd: str,
    mode: str,
    active: bool,
    current_phase: str | None = None,
    session_id: str | None = None,
    now_iso: str | None = None,
    source: str | None = None,
) -> None:
    """Sync the skill-active state file when a workflow mode changes.

    Updates the aggregated skill-active state to reflect the current
    activation status of a canonical workflow mode.

    Args:
        cwd: Working directory for state resolution.
        mode: Workflow mode name being updated.
        active: Whether the mode is now active.
        current_phase: Optional current phase for the mode.
        session_id: Optional session scope.
    """
    if mode not in CANONICAL_WORKFLOW_SKILLS:
        return

    skill_path = get_state_path(SKILL_ACTIVE_STATE_MODE, cwd, session_id)
    skill_path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if skill_path.exists():
        try:
            existing = json.loads(skill_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    active_skills: list[dict[str, Any]] = existing.get("active_skills", [])

    # Update or add the entry for this mode
    found = False
    for entry in active_skills:
        if entry.get("skill") == mode:
            entry["active"] = active
            if current_phase:
                entry["phase"] = current_phase
            found = True
            break

    if not found and active:
        entry: dict[str, Any] = {"skill": mode, "active": True}
        if current_phase:
            entry["phase"] = current_phase
        active_skills.append(entry)

    existing["active_skills"] = active_skills
    existing["active"] = any(e.get("active") for e in active_skills)

    skill_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
