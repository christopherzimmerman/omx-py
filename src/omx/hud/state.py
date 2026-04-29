"""HUD state management."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omx.utils.paths import omx_state_dir


def read_hud_state(project_root: Path | None = None) -> dict[str, Any]:
    """Read the HUD state file."""
    path = omx_state_dir(project_root) / "hud-state.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_hud_state(state: dict[str, Any], project_root: Path | None = None) -> None:
    """Write the HUD state file."""
    path = omx_state_dir(project_root) / "hud-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
