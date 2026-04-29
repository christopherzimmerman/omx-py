"""Triage state persistence.

Port of src/hooks/triage-state.ts. Session-scoped state helper for
prompt-routing triage decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SESSION_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_STATE_FILENAME = "prompt-routing-state.json"

_CLARIFYING_STARTERS = (
    "yes",
    "no",
    "yeah",
    "nope",
    "ok",
    "okay",
    "the ",
    "that",
    "those",
    "it",
)


@dataclass
class TriageStateLastTriage:
    """The last triage decision.

    Attributes:
        lane: Triage lane ("HEAVY" or "LIGHT").
        destination: Target skill/agent.
        reason: Reason for the decision.
        prompt_signature: SHA-256 signature of the normalized prompt.
        turn_id: Turn identifier.
        created_at: ISO timestamp.
    """

    lane: str  # "HEAVY" | "LIGHT"
    destination: str  # "autopilot" | "explore" | "executor" | "designer" | "researcher"
    reason: str
    prompt_signature: str
    turn_id: str
    created_at: str


@dataclass
class TriageStateFile:
    """Persisted triage state.

    Attributes:
        version: Schema version (always 1).
        last_triage: Last triage decision or ``None``.
        suppress_followup: Whether to suppress triage on short follow-ups.
    """

    version: int  # always 1
    last_triage: TriageStateLastTriage | None
    suppress_followup: bool


def _resolve_state_path(
    working_directory: str | None = None,
    session_id: str | None = None,
) -> str | None:
    if isinstance(session_id, str) and not SESSION_ID_PATTERN.match(session_id):
        return None
    cwd = working_directory or os.getcwd()
    if session_id:
        state_dir = os.path.join(cwd, ".omx", "state", "sessions", session_id)
    else:
        state_dir = os.path.join(cwd, ".omx", "state")
    return os.path.join(state_dir, _STATE_FILENAME)


def _is_triage_state_file(value: Any) -> TriageStateFile | None:
    """Validate and convert a parsed dict to a TriageStateFile."""
    if not isinstance(value, dict):
        return None
    if value.get("version") != 1:
        return None
    if not isinstance(value.get("suppress_followup"), bool):
        return None

    lt_raw = value.get("last_triage")
    if lt_raw is None:
        return TriageStateFile(
            version=1, last_triage=None, suppress_followup=value["suppress_followup"]
        )

    if not isinstance(lt_raw, dict):
        return None
    valid_lanes = {"HEAVY", "LIGHT"}
    valid_destinations = {"autopilot", "explore", "executor", "designer", "researcher"}
    if lt_raw.get("lane") not in valid_lanes:
        return None
    if lt_raw.get("destination") not in valid_destinations:
        return None
    for key in ("reason", "prompt_signature", "turn_id", "created_at"):
        if not isinstance(lt_raw.get(key), str):
            return None

    last_triage = TriageStateLastTriage(
        lane=lt_raw["lane"],
        destination=lt_raw["destination"],
        reason=lt_raw["reason"],
        prompt_signature=lt_raw["prompt_signature"],
        turn_id=lt_raw["turn_id"],
        created_at=lt_raw["created_at"],
    )
    return TriageStateFile(
        version=1,
        last_triage=last_triage,
        suppress_followup=value["suppress_followup"],
    )


def read_triage_state(
    cwd: str | None = None,
    session_id: str | None = None,
) -> TriageStateFile | None:
    """Read triage state from disk.

    Args:
        cwd: Working directory.
        session_id: Optional session identifier.

    Returns:
        Parsed triage state or ``None``.
    """
    try:
        file_path = _resolve_state_path(cwd, session_id)
        if not file_path:
            return None
        raw = Path(file_path).read_text(encoding="utf-8")
        parsed = json.loads(raw)
        return _is_triage_state_file(parsed)
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _triage_state_to_dict(state: TriageStateFile) -> dict[str, Any]:
    lt: dict[str, Any] | None = None
    if state.last_triage:
        lt = {
            "lane": state.last_triage.lane,
            "destination": state.last_triage.destination,
            "reason": state.last_triage.reason,
            "prompt_signature": state.last_triage.prompt_signature,
            "turn_id": state.last_triage.turn_id,
            "created_at": state.last_triage.created_at,
        }
    return {
        "version": state.version,
        "last_triage": lt,
        "suppress_followup": state.suppress_followup,
    }


def write_triage_state(
    state: TriageStateFile,
    cwd: str | None = None,
    session_id: str | None = None,
) -> None:
    """Write triage state to disk atomically.

    Args:
        state: Triage state to persist.
        cwd: Working directory.
        session_id: Optional session identifier.
    """
    try:
        file_path = _resolve_state_path(cwd, session_id)
        if not file_path:
            return
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(_triage_state_to_dict(state), indent=2)
        tmp_path = p.with_suffix(".tmp")
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(p)
    except OSError:
        pass


def prompt_signature(normalized_prompt: str) -> str:
    """Return a sha256 hex digest of the normalized prompt, prefixed with ``sha256:``.

    Args:
        normalized_prompt: Normalized prompt text.

    Returns:
        Signature string.
    """
    digest = hashlib.sha256(normalized_prompt.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def should_suppress_followup(
    previous: TriageStateFile | None,
    current_prompt: str,
    current_has_keyword: bool,
) -> bool:
    """Check whether triage should be suppressed for a follow-up prompt.

    Args:
        previous: Previous triage state.
        current_prompt: Current normalized prompt (trimmed, lowercased).
        current_has_keyword: Whether keyword routing matched.

    Returns:
        True if suppression should apply.
    """
    if current_has_keyword:
        return False
    if not previous or not previous.last_triage:
        return False
    if not previous.suppress_followup:
        return False
    for token in _CLARIFYING_STARTERS:
        if current_prompt.startswith(token):
            return True
    return False
