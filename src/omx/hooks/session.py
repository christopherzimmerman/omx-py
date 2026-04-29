"""Session lifecycle management.

Port of src/hooks/session.ts.
Tracks session start/end, PID staleness, and session history.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.utils.paths import omx_logs_dir, omx_state_dir

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass
class SessionState:
    """Persistent session state written to .omx/session.json.

    Attributes:
        session_id: Unique session identifier.
        started_at: ISO timestamp when session started.
        cwd: Working directory at session start.
        pid: Process ID of the session owner.
        native_session_id: Upstream CLI session ID (if available).
        platform: sys.platform string.
        pid_start_ticks: Linux process start ticks for staleness detection.
        pid_cmdline: Linux process command line for identification.
    """

    session_id: str
    started_at: str
    cwd: str
    pid: int
    native_session_id: str | None = None
    platform: str | None = None
    pid_start_ticks: int | None = None
    pid_cmdline: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SessionState:
        return cls(
            session_id=d["session_id"],
            started_at=d["started_at"],
            cwd=d["cwd"],
            pid=d["pid"],
            native_session_id=d.get("native_session_id"),
            platform=d.get("platform"),
            pid_start_ticks=d.get("pid_start_ticks"),
            pid_cmdline=d.get("pid_cmdline"),
        )


def _session_file(cwd: str) -> Path:
    return Path(cwd) / ".omx" / "session.json"


def _history_file(cwd: str) -> Path:
    return omx_logs_dir(Path(cwd)) / "session-history.jsonl"


def _daily_log_file(cwd: str) -> Path:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return omx_logs_dir(Path(cwd)) / f"omx-{date}.jsonl"


def read_session_state(cwd: str) -> SessionState | None:
    """Read session.json and return the session state."""
    path = _session_file(cwd)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return SessionState.from_dict(data)
    except (json.JSONDecodeError, KeyError, OSError):
        return None


def is_session_state_usable(state: SessionState, cwd: str) -> bool:
    """Check if a session state is still valid (matching CWD and live PID)."""
    # Verify CWD matches
    try:
        if Path(state.cwd).resolve() != Path(cwd).resolve():
            return False
    except OSError:
        return False

    # Check if PID is still alive
    return _is_pid_alive(state.pid)


def read_usable_session_state(cwd: str) -> SessionState | None:
    """Read session state and validate it."""
    state = read_session_state(cwd)
    if state is None:
        return None
    if not is_session_state_usable(state, cwd):
        return None
    return state


def write_session_start(
    cwd: str,
    session_id: str | None = None,
    native_session_id: str | None = None,
) -> SessionState:
    """Create session.json and append to session history.

    Args:
        cwd: Working directory for the session.
        session_id: Explicit session ID (auto-generated if None).
        native_session_id: Upstream CLI session ID to record.

    Returns:
        The newly created SessionState.
    """
    if session_id is None:
        session_id = _generate_session_id()

    state = SessionState(
        session_id=session_id,
        started_at=datetime.now(timezone.utc).isoformat(),
        cwd=str(Path(cwd).resolve()),
        pid=os.getpid(),
        native_session_id=native_session_id,
        platform=sys.platform,
    )

    # Capture Linux process identity
    if sys.platform.startswith("linux"):
        state.pid_start_ticks = _read_pid_start_ticks(os.getpid())
        state.pid_cmdline = _read_pid_cmdline(os.getpid())

    # Write session.json
    session_path = _session_file(cwd)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")

    # Append to session history
    _append_to_history(
        cwd,
        {
            "event": "session_start",
            "timestamp": state.started_at,
            **state.to_dict(),
        },
    )

    return state


def write_session_end(cwd: str, session_id: str) -> None:
    """Archive the session to history and remove session.json.

    Args:
        cwd: Working directory of the session.
        session_id: ID of the session being ended.
    """
    now = datetime.now(timezone.utc).isoformat()

    _append_to_history(
        cwd,
        {
            "event": "session_end",
            "timestamp": now,
            "session_id": session_id,
        },
    )

    session_path = _session_file(cwd)
    if session_path.exists():
        session_path.unlink(missing_ok=True)


def reset_session_metrics(cwd: str, session_id: str | None = None) -> None:
    """Reset HUD/metrics counters to zero at session launch."""
    state_dir = omx_state_dir(Path(cwd))
    state_dir.mkdir(parents=True, exist_ok=True)

    metrics = {"tool_calls": 0, "tokens_in": 0, "tokens_out": 0, "errors": 0}
    metrics_path = state_dir / "metrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")


def append_to_log(cwd: str, entry: dict[str, Any]) -> None:
    """Append a JSONL entry to the daily log."""
    log_path = _daily_log_file(cwd)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry) + "\n"

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)


def _append_to_history(cwd: str, entry: dict[str, Any]) -> None:
    """Append a JSONL entry to the session history file."""
    history_path = _history_file(cwd)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry) + "\n"

    with open(history_path, "a", encoding="utf-8") as f:
        f.write(line)


def _generate_session_id() -> str:
    return uuid.uuid4().hex[:16]


def _is_pid_alive(pid: int) -> bool:
    """Check if a process is still running."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # os.kill on Windows calls TerminateProcess — cannot use signal 0
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle == 0:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _read_pid_start_ticks(pid: int) -> int | None:
    """Read process start time from /proc on Linux."""
    try:
        stat_path = Path(f"/proc/{pid}/stat")
        if stat_path.exists():
            parts = stat_path.read_text().split()
            if len(parts) > 21:
                return int(parts[21])
    except (OSError, ValueError, IndexError):
        pass
    return None


def _read_pid_cmdline(pid: int) -> str | None:
    """Read process command line from /proc on Linux."""
    try:
        cmdline_path = Path(f"/proc/{pid}/cmdline")
        if cmdline_path.exists():
            return (
                cmdline_path.read_bytes()
                .replace(b"\x00", b" ")
                .decode("utf-8", errors="replace")
                .strip()
            )
    except OSError:
        pass
    return None
