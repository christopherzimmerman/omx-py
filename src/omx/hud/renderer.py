"""HUD statusline renderer."""

from __future__ import annotations


from omx.hud.state import read_hud_state
from omx.state.operations import state_list_active
from omx.state.paths import resolve_working_directory


def render_statusline(cwd: str | None = None, preset: str | None = None) -> str:
    """Render the HUD statusline string showing active modes and metrics.

    Args:
        cwd: Working directory override for state resolution.
        preset: Optional HUD preset name (reserved for future use).

    Returns:
        Formatted statusline string (e.g. "[autopilot] tools:5").
    """
    resolved = str(resolve_working_directory(cwd))

    # Get active modes
    result = state_list_active(resolved)
    active_modes = result.get("active_modes", [])

    # Read HUD state for metrics
    hud = read_hud_state()
    tool_calls = hud.get("tool_calls", 0)

    parts: list[str] = []

    if active_modes:
        parts.append(f"[{','.join(active_modes)}]")
    else:
        parts.append("[idle]")

    if tool_calls:
        parts.append(f"tools:{tool_calls}")

    return " ".join(parts)
