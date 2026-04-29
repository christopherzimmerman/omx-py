"""tmux session detection for notifications.

Detects the current tmux session name and pane ID for inclusion in
notification payloads.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from dataclasses import dataclass

from omx.notifications.tmux_detector import build_capture_pane_argv

_TMUX_PANE_TARGET_RE = re.compile(r"^%\d+$")
_DEFAULT_CAPTURE_LINES = 12
_MAX_CAPTURE_LINES = 2000
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[A-Za-z])")
_OMX_METADATA_SEGMENT_RE = re.compile(r"^\[OMX(?:[#\]].*)?$")
_HUD_STATUS_SEGMENT_RE = re.compile(
    r"^(?:ralph:\d+/(?:\d+|\?)|autopilot:[\w-]+|ralplan:(?:\d+/(?:\d+|\?)|[\w-]+)"
    r"|interview:[\w:-]+|research:[\w-]+|qa:[\w-]+|team:(?:\d+\s+workers|[\w.-]+)"
    r"|ultrawork|turns:\d+|tokens:[\dkm.]+|quota:[\w%,.]+|session:[\dhms]+"
    r"|last:\d+[smh](?:\s+ago)?|total-turns:\d+|tmux:[\w:.-]+)$",
    re.IGNORECASE,
)
_BRANCH_METADATA_SEGMENT_RE = re.compile(
    r"^(?:(?:fix|feat|feature|chore|refactor|hotfix|release|docs|doc|test|tests|ci|build|perf|revert|bugfix|spike|wip)"
    r"/[A-Za-z0-9._/-]+|HEAD(?: -> [A-Za-z0-9._/-]+)?|detached)$"
)


def _is_metadata_only_tmux_segment(segment: str) -> bool:
    return bool(
        _OMX_METADATA_SEGMENT_RE.match(segment)
        or _HUD_STATUS_SEGMENT_RE.match(segment)
        or _BRANCH_METADATA_SEGMENT_RE.match(segment)
    )


def _is_metadata_only_tmux_line(line: str) -> bool:
    normalized = _ANSI_RE.sub("", line).strip()
    if not normalized:
        return False
    segments = [s.strip() for s in normalized.split("|") if s.strip()]
    if not segments or any(not _is_metadata_only_tmux_segment(s) for s in segments):
        return False
    has_explicit = any(
        _OMX_METADATA_SEGMENT_RE.match(s) or _HUD_STATUS_SEGMENT_RE.match(s)
        for s in segments
    )
    return has_explicit or (
        len(segments) == 1 and bool(_BRANCH_METADATA_SEGMENT_RE.match(segments[0]))
    )


def sanitize_tmux_alert_text(raw: str | None) -> str | None:
    """Remove metadata-only tmux lines from alert-facing payload text.

    Args:
        raw: Raw tmux capture text.

    Returns:
        Cleaned text, or None if empty.
    """
    if not isinstance(raw, str):
        return None
    filtered = [
        line for line in raw.split("\n") if not _is_metadata_only_tmux_line(line)
    ]
    joined = "\n".join(filtered).strip()
    return joined or None


@dataclass
class TmuxPaneCaptureResult:
    """Result of capturing a tmux pane.

    Attributes:
        content: Captured content, or None.
        live: Whether the pane was verified live.
    """

    content: str | None = None
    live: bool = False


def _exec_tmux(args: list[str]) -> str:
    """Execute a tmux command and return stdout."""
    result = subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        timeout=3,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    return result.stdout.strip()


def get_current_tmux_session() -> str | None:
    """Get the current tmux session name.

    Returns:
        Session name string, or None if not in tmux.
    """
    if os.environ.get("TMUX"):
        try:
            tmux_pane = os.environ.get("TMUX_PANE")
            pane_safe = (
                tmux_pane
                if tmux_pane and _TMUX_PANE_TARGET_RE.match(tmux_pane)
                else None
            )
            if pane_safe:
                name = _exec_tmux(["display-message", "-p", "-t", pane_safe, "#S"])
            else:
                name = _exec_tmux(["display-message", "-p", "#S"])
            if name:
                return name
        except Exception:
            pass

    if os.environ.get("OMX_TMUX_PID_FALLBACK") != "1":
        return None

    return _detect_tmux_session_by_pid()


def _detect_tmux_session_by_pid() -> str | None:
    """Detect tmux session by walking the process tree."""
    if sys.platform == "win32":
        return None
    try:
        output = _exec_tmux(["list-panes", "-a", "-F", "#{pane_pid} #{session_name}"])
        if not output:
            return None

        pane_pids: dict[int, str] = {}
        for line in output.split("\n"):
            parts = line.strip().split(" ", 1)
            if len(parts) == 2:
                try:
                    pane_pids[int(parts[0])] = parts[1]
                except ValueError:
                    pass

        if not pane_pids:
            return None

        current_pid = os.getpid()
        visited: set[int] = set()
        while current_pid > 1 and current_pid not in visited:
            visited.add(current_pid)
            if current_pid in pane_pids:
                return pane_pids[current_pid]
            try:
                result = subprocess.run(
                    ["ps", "-o", "ppid=", "-p", str(current_pid)],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                ppid = int(result.stdout.strip())
                if ppid <= 1:
                    break
                current_pid = ppid
            except Exception:
                break

        return None
    except Exception:
        return None


def get_team_tmux_sessions(team_name: str) -> list[str]:
    """List active omx-team tmux sessions for a given team.

    Args:
        team_name: Team name to search for.

    Returns:
        List of matching session names.
    """
    sanitized = re.sub(r"[^a-zA-Z0-9-]", "", team_name)
    if not sanitized:
        return []

    prefix = f"omx-team-{sanitized}"
    try:
        output = _exec_tmux(["list-sessions", "-F", "#{session_name}"])
        return [
            s
            for s in output.strip().split("\n")
            if s == prefix or s.startswith(f"{prefix}-")
        ]
    except Exception:
        return []


def capture_tmux_pane(pane_id: str | None = None, lines: int = 12) -> str | None:
    """Capture the last N lines of output from a tmux pane.

    Args:
        pane_id: tmux pane ID (defaults to TMUX_PANE env).
        lines: Number of lines to capture.

    Returns:
        Captured output, or None.
    """
    return capture_tmux_pane_with_liveness(pane_id, lines).content


def capture_tmux_pane_with_liveness(
    pane_id: str | None = None,
    lines: int = 12,
) -> TmuxPaneCaptureResult:
    """Capture tmux pane content with liveness verification.

    Args:
        pane_id: tmux pane ID (defaults to TMUX_PANE env).
        lines: Number of lines to capture.

    Returns:
        TmuxPaneCaptureResult with content and liveness flag.
    """
    target = pane_id or os.environ.get("TMUX_PANE")
    if not target:
        return TmuxPaneCaptureResult()
    if not os.environ.get("TMUX") and not pane_id:
        return TmuxPaneCaptureResult()
    if not _TMUX_PANE_TARGET_RE.match(target):
        return TmuxPaneCaptureResult()

    safe_lines = (
        int(lines) if isinstance(lines, (int, float)) else _DEFAULT_CAPTURE_LINES
    )
    clamped_lines = max(1, min(_MAX_CAPTURE_LINES, safe_lines))

    try:
        pane_status = _exec_tmux(
            ["list-panes", "-t", target, "-F", "#{pane_dead} #{pane_pid}"]
        )
        first_line = pane_status.split("\n")[0].strip() if pane_status else ""
        parts = first_line.split(None, 1)
        pane_dead = parts[0] if len(parts) > 0 else ""
        pane_pid_raw = parts[1] if len(parts) > 1 else ""
        try:
            pane_pid = int(pane_pid_raw)
        except ValueError:
            return TmuxPaneCaptureResult()
        if pane_dead == "1":
            return TmuxPaneCaptureResult()

        # Check if pane process is alive
        try:
            os.kill(pane_pid, 0)
        except OSError:
            return TmuxPaneCaptureResult()

        result = subprocess.run(
            ["tmux", *build_capture_pane_argv(target, clamped_lines)],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        output = result.stdout.strip()
        return TmuxPaneCaptureResult(content=output or None, live=True)
    except Exception:
        return TmuxPaneCaptureResult()


def format_tmux_info() -> str | None:
    """Format tmux session info for human-readable display.

    Returns:
        Formatted tmux info string, or None if not in tmux.
    """
    session = get_current_tmux_session()
    if not session:
        return None
    return f"tmux: {session}"


def get_current_tmux_pane_id() -> str | None:
    """Get the current tmux pane ID (e.g., "%0").

    Returns:
        Pane ID string, or None.
    """
    env_pane = os.environ.get("TMUX_PANE")
    if os.environ.get("TMUX") and env_pane and re.match(r"^%\d+$", env_pane):
        return env_pane

    if os.environ.get("TMUX"):
        try:
            pane_id = _exec_tmux(["display-message", "-p", "#{pane_id}"])
            if pane_id and re.match(r"^%\d+$", pane_id):
                return pane_id
        except Exception:
            pass

    if os.environ.get("OMX_TMUX_PID_FALLBACK") != "1":
        return None

    return _detect_tmux_pane_by_pid()


def _detect_tmux_pane_by_pid() -> str | None:
    """Detect tmux pane ID by walking the process tree."""
    if sys.platform == "win32":
        return None
    try:
        output = _exec_tmux(["list-panes", "-a", "-F", "#{pane_pid} #{pane_id}"])
        if not output:
            return None

        pane_pids: dict[int, str] = {}
        for line in output.split("\n"):
            parts = line.strip().split(" ", 1)
            if len(parts) == 2:
                try:
                    pane_pids[int(parts[0])] = parts[1]
                except ValueError:
                    pass

        if not pane_pids:
            return None

        current_pid = os.getpid()
        visited: set[int] = set()
        while current_pid > 1 and current_pid not in visited:
            visited.add(current_pid)
            if current_pid in pane_pids:
                return pane_pids[current_pid]
            try:
                result = subprocess.run(
                    ["ps", "-o", "ppid=", "-p", str(current_pid)],
                    capture_output=True,
                    text=True,
                    timeout=1,
                )
                ppid = int(result.stdout.strip())
                if ppid <= 1:
                    break
                current_pid = ppid
            except Exception:
                break

        return None
    except Exception:
        return None
