"""Tmux pane integration for HUD.

Port of src/hud/tmux.ts.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable

from omx.hud.constants import HUD_TMUX_HEIGHT_LINES


@dataclass
class TmuxPaneSnapshot:
    """Snapshot of a tmux pane.

    Attributes:
        pane_id: Tmux pane ID (e.g. %0).
        current_command: Currently running command.
        start_command: Command that started the pane.
    """

    pane_id: str = ""
    current_command: str = ""
    start_command: str = ""


def parse_tmux_pane_snapshot(output: str) -> list[TmuxPaneSnapshot]:
    """Parse tmux list-panes output into snapshots.

    Args:
        output: Raw tmux output with tab-separated fields.

    Returns:
        List of TmuxPaneSnapshot instances.
    """
    result: list[TmuxPaneSnapshot] = []
    for line in output.split("\n"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t")
        pane_id = parts[0].strip() if parts else ""
        current_command = parts[1].strip() if len(parts) > 1 else ""
        start_command = "\t".join(parts[2:]).strip() if len(parts) > 2 else ""
        if pane_id.startswith("%"):
            result.append(
                TmuxPaneSnapshot(
                    pane_id=pane_id,
                    current_command=current_command,
                    start_command=start_command,
                )
            )
    return result


def is_hud_watch_pane(pane: TmuxPaneSnapshot) -> bool:
    """Check if a pane is a HUD watch pane.

    Args:
        pane: Tmux pane snapshot.

    Returns:
        True if the pane appears to be running omx hud --watch.
    """
    command = f"{pane.start_command} {pane.current_command}".lower()
    return bool(
        re.search(r"\bhud\b", command)
        and re.search(r"--watch\b", command)
        and (
            re.search(r"\bomx(?:\.js)?\b", command)
            or re.search(r"\bnode\b", command)
            or re.search(r"\bpython\b", command)
        )
    )


def find_hud_watch_pane_ids(
    panes: list[TmuxPaneSnapshot],
    current_pane_id: str | None = None,
) -> list[str]:
    """Find pane IDs that are running HUD watch.

    Args:
        panes: List of pane snapshots.
        current_pane_id: ID of the current pane to exclude.

    Returns:
        List of HUD watch pane IDs.
    """
    return [
        p.pane_id
        for p in panes
        if p.pane_id != current_pane_id and is_hud_watch_pane(p)
    ]


def parse_pane_id_from_tmux_output(raw_output: str) -> str | None:
    """Parse a pane ID from tmux command output.

    Args:
        raw_output: Raw tmux output.

    Returns:
        Pane ID string or None.
    """
    pane_id = raw_output.split("\n")[0].strip() if raw_output else ""
    return pane_id if pane_id.startswith("%") else None


def shell_escape_single(value: str) -> str:
    """Shell-escape a string using single-quote wrapping.

    Args:
        value: String to escape.

    Returns:
        Shell-safe single-quoted string.
    """
    return "'" + value.replace("'", "'\\''") + "'"


def build_hud_watch_command(
    omx_bin: str,
    preset: str | None = None,
    session_id: str | None = None,
) -> str:
    """Build the HUD watch command for a tmux pane.

    Args:
        omx_bin: Path to the OMX binary.
        preset: Optional preset name.
        session_id: Optional session ID.

    Returns:
        Shell command string.
    """
    safe_preset = ""
    if preset in ("minimal", "focused", "full"):
        safe_preset = f" --preset={preset}"
    safe_session_id = session_id.strip() if isinstance(session_id, str) else ""
    session_prefix = (
        f"OMX_SESSION_ID={shell_escape_single(safe_session_id)} "
        if safe_session_id
        else ""
    )
    return f"{session_prefix}python {shell_escape_single(omx_bin)} hud --watch{safe_preset}"


def _default_exec_tmux_sync(args: list[str]) -> str:
    """Execute a tmux command synchronously."""
    kwargs = {"encoding": "utf-8", "capture_output": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    result = subprocess.run(["tmux", *args], **kwargs)  # noqa: S603, S607
    return result.stdout or ""


def list_current_window_panes(
    current_pane_id: str | None = None,
    exec_tmux_sync: Callable[[list[str]], str] | None = None,
) -> list[TmuxPaneSnapshot]:
    """List panes in the current tmux window.

    Args:
        current_pane_id: Optional target pane ID.
        exec_tmux_sync: Optional tmux executor override.

    Returns:
        List of TmuxPaneSnapshot instances.
    """
    exec_fn = exec_tmux_sync or _default_exec_tmux_sync
    try:
        args = ["list-panes"]
        if current_pane_id:
            args.extend(["-t", current_pane_id])
        args.extend(
            ["-F", "#{pane_id}\t#{pane_current_command}\t#{pane_start_command}"]
        )
        return parse_tmux_pane_snapshot(exec_fn(args))
    except Exception:
        return []


def create_hud_watch_pane(
    cwd: str,
    hud_cmd: str,
    *,
    height_lines: int | None = None,
    full_width: bool = False,
    target_pane_id: str | None = None,
    exec_tmux_sync: Callable[[list[str]], str] | None = None,
) -> str | None:
    """Create a tmux pane running the HUD watch command.

    Args:
        cwd: Working directory.
        hud_cmd: HUD command to execute.
        height_lines: Pane height in lines.
        full_width: Whether to use full width.
        target_pane_id: Target pane for split.
        exec_tmux_sync: Optional tmux executor override.

    Returns:
        Created pane ID or None on failure.
    """
    exec_fn = exec_tmux_sync or _default_exec_tmux_sync
    h = (
        max(1, int(height_lines))
        if height_lines is not None and height_lines > 0
        else HUD_TMUX_HEIGHT_LINES
    )
    args = ["split-window", "-v"]
    if full_width:
        args.append("-f")
    args.extend(["-l", str(h), "-d"])
    if target_pane_id:
        args.extend(["-t", target_pane_id])
    args.extend(["-c", cwd, "-P", "-F", "#{pane_id}", hud_cmd])
    try:
        return parse_pane_id_from_tmux_output(exec_fn(args))
    except Exception:
        return None


def kill_tmux_pane(
    pane_id: str,
    exec_tmux_sync: Callable[[list[str]], str] | None = None,
) -> bool:
    """Kill a tmux pane by ID.

    Args:
        pane_id: Pane ID to kill.
        exec_tmux_sync: Optional tmux executor override.

    Returns:
        True if the pane was killed.
    """
    if not pane_id.startswith("%"):
        return False
    exec_fn = exec_tmux_sync or _default_exec_tmux_sync
    try:
        exec_fn(["kill-pane", "-t", pane_id])
        return True
    except Exception:
        return False


def resize_tmux_pane(
    pane_id: str,
    height_lines: int,
    exec_tmux_sync: Callable[[list[str]], str] | None = None,
) -> bool:
    """Resize a tmux pane.

    Args:
        pane_id: Pane ID to resize.
        height_lines: Desired height in lines.
        exec_tmux_sync: Optional tmux executor override.

    Returns:
        True if the pane was resized.
    """
    if not pane_id.startswith("%"):
        return False
    exec_fn = exec_tmux_sync or _default_exec_tmux_sync
    h = max(1, int(height_lines)) if height_lines > 0 else HUD_TMUX_HEIGHT_LINES
    try:
        exec_fn(["resize-pane", "-t", pane_id, "-y", str(h)])
        return True
    except Exception:
        return False
