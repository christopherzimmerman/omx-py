"""State operations — read, write, clear, list, status.

Port of src/state/operations.ts.
"""

from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path
from typing import Any

from omx.state.mode_state_context import with_mode_runtime_context
from omx.state.paths import (
    get_all_scoped_state_paths,
    get_read_scoped_state_dirs,
    get_read_scoped_state_paths,
    get_state_path,
    is_mode_state_filename,
    validate_state_mode_segment,
)
from omx.state.skill_active import (
    SKILL_ACTIVE_STATE_MODE,
    sync_canonical_skill_state_for_mode,
)

SUPPORTED_MODES: list[str] = [
    "autopilot",
    "autoresearch",
    "team",
    "ralph",
    "ultrawork",
    "ultraqa",
    "ralplan",
    "deep-interview",
    "skill-active",
]


def _write_atomic(path: Path, data: str) -> None:
    """Write a file atomically via tmp + rename."""
    tmp_name = f"{path.name}.tmp.{os.getpid()}.{int(time.time())}.{random.randint(0, 0xFFFF):04x}"
    tmp_path = path.parent / tmp_name
    try:
        tmp_path.write_text(data, encoding="utf-8")
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def state_read(mode: str, cwd: str, session_id: str | None = None) -> dict[str, Any]:
    """Read state for a specific mode."""
    paths = get_read_scoped_state_paths(mode, cwd, session_id)
    for path in paths:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
    return {"exists": False, "mode": mode}


def state_write(
    mode: str,
    cwd: str,
    fields: dict[str, Any],
    session_id: str | None = None,
) -> dict[str, Any]:
    """Write/update state for a specific mode."""
    validated_mode = validate_state_mode_segment(mode)
    path = get_state_path(validated_mode, cwd, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Merge fields
    merged = {**existing, **fields}
    merged = with_mode_runtime_context(existing, merged)

    _write_atomic(path, json.dumps(merged, indent=2))

    # Sync skill-active state
    if validated_mode != SKILL_ACTIVE_STATE_MODE:
        sync_canonical_skill_state_for_mode(
            cwd,
            validated_mode,
            active=merged.get("active") is True,
            current_phase=merged.get("current_phase"),
            session_id=session_id,
        )

    return {"success": True, "mode": validated_mode, "path": str(path)}


def state_clear(
    mode: str,
    cwd: str,
    session_id: str | None = None,
    all_sessions: bool = False,
) -> dict[str, Any]:
    """Clear/delete state for a specific mode."""
    validated_mode = validate_state_mode_segment(mode)

    if not all_sessions:
        path = get_state_path(validated_mode, cwd, session_id)
        if path.exists():
            path.unlink()
        if validated_mode != SKILL_ACTIVE_STATE_MODE:
            sync_canonical_skill_state_for_mode(
                cwd,
                validated_mode,
                active=False,
                session_id=session_id,
            )
        return {"cleared": True, "mode": validated_mode, "path": str(path)}

    removed: list[str] = []
    for path in get_all_scoped_state_paths(validated_mode, cwd):
        if path.exists():
            path.unlink()
            removed.append(str(path))

    if validated_mode != SKILL_ACTIVE_STATE_MODE:
        sync_canonical_skill_state_for_mode(cwd, validated_mode, active=False)

    return {
        "cleared": True,
        "mode": validated_mode,
        "all_sessions": True,
        "removed": len(removed),
        "paths": removed,
    }


def state_list_active(cwd: str, session_id: str | None = None) -> dict[str, Any]:
    """List all currently active modes."""
    state_dirs = get_read_scoped_state_dirs(cwd, session_id)
    active: list[str] = []
    seen: set[str] = set()

    for state_dir in state_dirs:
        if not state_dir.exists():
            continue
        for f in state_dir.iterdir():
            if not f.is_file() or not is_mode_state_filename(f.name):
                continue
            mode = f.name.removesuffix("-state.json")
            if mode == SKILL_ACTIVE_STATE_MODE or mode in seen:
                continue
            seen.add(mode)
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if data.get("active"):
                    active.append(mode)
            except (json.JSONDecodeError, OSError):
                pass

    return {"active_modes": active}


def state_get_status(
    cwd: str,
    session_id: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Get detailed status for a specific mode or all modes."""
    state_dirs = get_read_scoped_state_dirs(cwd, session_id)
    statuses: dict[str, Any] = {}
    seen: set[str] = set()

    for state_dir in state_dirs:
        if not state_dir.exists():
            continue
        for f in state_dir.iterdir():
            if not f.is_file() or not is_mode_state_filename(f.name):
                continue
            m = f.name.removesuffix("-state.json")
            if mode and m != mode:
                continue
            if m in seen:
                continue
            seen.add(m)
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                statuses[m] = {
                    "active": data.get("active"),
                    "phase": data.get("current_phase"),
                    "path": str(f),
                    "data": data,
                }
            except (json.JSONDecodeError, OSError):
                statuses[m] = {"error": "malformed state file"}

    return {"statuses": statuses}
