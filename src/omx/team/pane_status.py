"""Team pane status monitoring.

Port of src/team/pane-status.ts.
Monitors tmux pane content to determine worker state.
"""

from __future__ import annotations

import re
from typing import Any

from omx.team.tmux_session import capture_pane


# Patterns indicating active work
ACTIVE_PATTERNS = [
    re.compile(
        r"Running|Thinking|Generating|Working|Reading|Writing|Searching", re.IGNORECASE
    ),
    re.compile(r"⠋|⠙|⠹|⠸|⠼|⠴|⠦|⠧|⠇|⠏"),  # Spinner characters
    re.compile(r"\.\.\.$"),  # Trailing ellipsis
]

# Patterns indicating ready/idle state
READY_PATTERNS = [
    re.compile(r"[>$#%]\s*$"),
    re.compile(r"What can I help|How can I help|Enter a prompt", re.IGNORECASE),
]

# Patterns indicating an error
ERROR_PATTERNS = [
    re.compile(r"Error:|ERROR|FATAL|panic|Traceback", re.IGNORECASE),
    re.compile(r"rate.?limit|quota.?exceeded|too many requests", re.IGNORECASE),
]

# Trust/permission prompts
TRUST_PATTERNS = [
    re.compile(r"Do you trust the contents of this directory\?", re.IGNORECASE),
    re.compile(r"Bypass Permissions mode", re.IGNORECASE),
]


def classify_pane_state(pane_id: str, capture_lines: int = 20) -> dict[str, Any]:
    """Classify the current state of a tmux pane.

    Args:
        pane_id: Tmux pane target.
        capture_lines: Number of lines to capture.

    Returns:
        Dict with "state", "has_error", "has_trust_prompt", "active_indicator".
    """
    captured = capture_pane(pane_id, lines=capture_lines)
    if not captured.strip():
        return {
            "state": "empty",
            "has_error": False,
            "has_trust_prompt": False,
            "active_indicator": None,
        }

    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    tail = "\n".join(lines[-10:]) if lines else ""

    has_trust = any(p.search(tail) for p in TRUST_PATTERNS)
    has_error = any(p.search(tail) for p in ERROR_PATTERNS)
    has_active = any(p.search(tail) for p in ACTIVE_PATTERNS)
    has_ready = any(p.search(tail) for p in READY_PATTERNS)

    if has_trust:
        state = "trust_prompt"
    elif has_active:
        state = "active"
    elif has_ready:
        state = "ready"
    elif has_error:
        state = "error"
    else:
        state = "unknown"

    return {
        "state": state,
        "has_error": has_error,
        "has_trust_prompt": has_trust,
        "active_indicator": next(
            (p.pattern for p in ACTIVE_PATTERNS if p.search(tail)),
            None,
        ),
    }


def is_worker_alive(pane_id: str) -> bool:
    """Check if a worker pane has any content (alive check).

    Args:
        pane_id: Tmux pane target.

    Returns:
        True if the pane has visible content.
    """
    captured = capture_pane(pane_id, lines=3)
    return bool(captured.strip())


def pane_has_active_task(pane_id: str) -> bool:
    """Check if a pane shows an active task being processed.

    Args:
        pane_id: Tmux pane target.

    Returns:
        True if active work indicators are detected.
    """
    state = classify_pane_state(pane_id, capture_lines=8)
    return state["state"] == "active"


def pane_looks_ready(pane_id: str) -> bool:
    """Check if a pane shows a CLI prompt (ready for input).

    Args:
        pane_id: Tmux pane target.

    Returns:
        True if the pane appears ready for input.
    """
    state = classify_pane_state(pane_id, capture_lines=8)
    return state["state"] == "ready"
