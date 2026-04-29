"""Mode state context — captures tmux pane info on state transitions.

Port of src/state/mode-state-context.ts.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any


def capture_tmux_pane_from_env() -> str | None:
    """Get TMUX_PANE from environment, if set."""
    value = os.environ.get("TMUX_PANE", "").strip()
    return value if value else None


def with_mode_runtime_context(
    existing: dict[str, Any],
    next_state: dict[str, Any],
) -> dict[str, Any]:
    """Enrich state with tmux pane context on activation transitions.

    Captures the TMUX_PANE environment variable into the state when a
    mode transitions to active, preserving the pane association.

    Args:
        existing: Previous state dict (to detect activation transitions).
        next_state: New state dict to enrich.

    Returns:
        The enriched next_state dict (mutated in place and returned).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    was_active = existing.get("active") is True
    is_active = next_state.get("active") is True
    has_pane = bool(next_state.get("tmux_pane_id", ""))

    if is_active and (not was_active or not has_pane):
        pane = capture_tmux_pane_from_env()
        if pane:
            next_state["tmux_pane_id"] = pane
            if not next_state.get("tmux_pane_set_at"):
                next_state["tmux_pane_set_at"] = now_iso

    return next_state
