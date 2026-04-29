"""Persistent run state management.

Port of src/runtime/run-state.ts.
"""

from __future__ import annotations

import json
import os
import random
import time
from datetime import datetime, timezone
from typing import Any

from omx.runtime.run_outcome import (
    infer_run_outcome,
    is_terminal_run_outcome,
)
from omx.state.paths import get_state_file_path

RUN_STATE_FILENAME = "run-state.json"
RUN_STATE_VERSION = 1


def build_run_state(raw: dict[str, Any]) -> dict[str, Any]:
    """Construct a normalized run-state dict from raw mode state.

    Infers the run outcome, sets timestamps, and carries through
    optional lifecycle fields.

    Args:
        raw: Raw mode state dictionary.

    Returns:
        Normalized run-state dict suitable for persistence.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    outcome = infer_run_outcome(raw)

    state: dict[str, Any] = {
        "version": RUN_STATE_VERSION,
        "mode": raw.get("mode", ""),
        "active": not is_terminal_run_outcome(outcome),
        "outcome": outcome,
        "updated_at": now_iso,
    }

    # Carry through optional fields
    for field in (
        "current_phase",
        "task_description",
        "started_at",
        "completed_at",
        "iteration",
        "max_iterations",
        "error",
        "owner_omx_session_id",
        "lifecycle_outcome",
    ):
        if field in raw and raw[field] is not None:
            state[field] = raw[field]

    if is_terminal_run_outcome(outcome) and "completed_at" not in state:
        state["completed_at"] = now_iso

    if not state.get("started_at"):
        state["started_at"] = now_iso

    return state


def read_run_state(
    working_directory: str | None = None, session_id: str | None = None
) -> dict[str, Any] | None:
    """Read run state from disk."""
    path = get_state_file_path(RUN_STATE_FILENAME, working_directory, session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def sync_run_state(
    mode_state: dict[str, Any],
    working_directory: str | None = None,
    session_id: str | None = None,
) -> None:
    """Update the run-state file atomically from current mode state.

    Args:
        mode_state: Current mode state to derive run state from.
        working_directory: Override working directory for path resolution.
        session_id: Optional session scope for the state file.
    """
    run_state = build_run_state(mode_state)
    path = get_state_file_path(RUN_STATE_FILENAME, working_directory, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp_name = f"{path.name}.tmp.{os.getpid()}.{int(time.time())}.{random.randint(0, 0xFFFF):04x}"
    tmp_path = path.parent / tmp_name
    try:
        tmp_path.write_text(json.dumps(run_state, indent=2), encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
