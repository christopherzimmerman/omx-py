"""State path resolution and validation.

Port of src/mcp/state-paths.ts.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STATE_MODE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STATE_FILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
STATE_FILE_SUFFIX = "-state.json"


def validate_session_id(session_id: str | None) -> str | None:
    """Validate and return a session ID, or None."""
    if session_id is None:
        return None
    if not isinstance(session_id, str):
        raise ValueError("session_id must be a string")
    if not SESSION_ID_PATTERN.match(session_id):
        raise ValueError("session_id must match ^[A-Za-z0-9_-]{1,64}$")
    return session_id


def validate_state_mode_segment(mode: str) -> str:
    """Validate a mode name for use as a path segment."""
    if not isinstance(mode, str):
        raise ValueError("mode must be a string")
    normalized = mode.strip()
    if not normalized:
        raise ValueError("mode must be a non-empty string")
    if ".." in normalized:
        raise ValueError('mode must not contain ".."')
    if "/" in normalized or "\\" in normalized:
        raise ValueError("mode must not contain path separators")
    if not STATE_MODE_SEGMENT_PATTERN.match(normalized):
        raise ValueError("mode must match ^[A-Za-z0-9_-]{1,64}$")
    return normalized


def validate_state_file_name(file_name: str) -> str:
    """Validate a state file name."""
    if not isinstance(file_name, str):
        raise ValueError("fileName must be a string")
    normalized = file_name.strip()
    if not normalized:
        raise ValueError("fileName must be a non-empty string")
    if ".." in normalized:
        raise ValueError('fileName must not contain ".."')
    if "/" in normalized or "\\" in normalized:
        raise ValueError("fileName must not contain path separators")
    if not STATE_FILE_NAME_PATTERN.match(normalized):
        raise ValueError("fileName must match ^[A-Za-z0-9._-]{1,128}$")
    return normalized


def get_state_filename(mode: str) -> str:
    return f"{validate_state_mode_segment(mode)}{STATE_FILE_SUFFIX}"


def resolve_working_directory(working_directory: str | None = None) -> Path:
    """Resolve the working directory, enforcing policy."""
    raw = (working_directory or "").strip()
    if "\0" in raw:
        raise ValueError("workingDirectory contains a NUL byte")
    if not raw:
        return Path.cwd().resolve()
    resolved = Path(raw).resolve()
    _enforce_working_directory_policy(resolved)
    return resolved


def _enforce_working_directory_policy(resolved: Path) -> None:
    """Enforce allowed working directory roots."""
    roots_env = os.environ.get("OMX_MCP_WORKDIR_ROOTS", "").strip()
    if not roots_env:
        return
    roots = [
        Path(r.strip()).resolve() for r in roots_env.split(os.pathsep) if r.strip()
    ]
    if not roots:
        return
    if not any(_is_within_root(resolved, root) for root in roots):
        raise ValueError(
            f'workingDirectory "{resolved}" is outside allowed roots (OMX_MCP_WORKDIR_ROOTS)'
        )


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def get_base_state_dir(working_directory: str | None = None) -> Path:
    """Get the base .omx/state directory."""
    team_root = os.environ.get("OMX_TEAM_STATE_ROOT", "").strip()
    if not working_directory and team_root:
        try:
            return resolve_working_directory(team_root)
        except ValueError:
            pass
    return resolve_working_directory(working_directory) / ".omx" / "state"


def get_state_dir(
    working_directory: str | None = None, session_id: str | None = None
) -> Path:
    """Get the state directory, optionally scoped to a session."""
    base = get_base_state_dir(working_directory)
    if session_id:
        return base / "sessions" / session_id
    return base


def get_state_path(
    mode: str, working_directory: str | None = None, session_id: str | None = None
) -> Path:
    """Get the full path to a mode's state file."""
    return get_state_dir(working_directory, session_id) / get_state_filename(mode)


def get_state_file_path(
    file_name: str, working_directory: str | None = None, session_id: str | None = None
) -> Path:
    """Get the full path to a named state file."""
    return get_state_dir(working_directory, session_id) / validate_state_file_name(
        file_name
    )


def list_session_dirs(working_directory: str | None = None) -> list[Path]:
    """List all session-scoped state directories."""
    sessions_root = get_base_state_dir(working_directory) / "sessions"
    if not sessions_root.exists():
        return []
    return [
        d
        for d in sessions_root.iterdir()
        if d.is_dir() and SESSION_ID_PATTERN.match(d.name)
    ]


def get_read_scoped_state_dirs(
    working_directory: str | None = None,
    session_id: str | None = None,
) -> list[Path]:
    """Get state directories in read precedence order."""
    validated = validate_session_id(session_id)
    if validated:
        session_dir = get_state_dir(working_directory, validated)
        base_dir = get_base_state_dir(working_directory)
        if session_dir.exists():
            return [session_dir]
        return [session_dir, base_dir]

    # Check for session from environment
    env_session = _read_session_id_from_env()
    if env_session:
        session_dir = get_state_dir(working_directory, env_session)
        base_dir = get_base_state_dir(working_directory)
        return [session_dir, base_dir]

    return [get_base_state_dir(working_directory)]


def get_read_scoped_state_paths(
    mode: str,
    working_directory: str | None = None,
    session_id: str | None = None,
) -> list[Path]:
    """Get state file paths in read precedence order."""
    filename = get_state_filename(mode)
    return [
        d / filename for d in get_read_scoped_state_dirs(working_directory, session_id)
    ]


def get_all_scoped_state_paths(
    mode: str, working_directory: str | None = None
) -> list[Path]:
    """Get all state paths for a mode (root + all sessions)."""
    paths = [get_state_path(mode, working_directory)]
    filename = get_state_filename(mode)
    for session_dir in list_session_dirs(working_directory):
        paths.append(session_dir / filename)
    return paths


def _read_session_id_from_env() -> str | None:
    """Read session ID from environment variables."""
    for var in ("OMX_SESSION_ID", "CODEX_SESSION_ID", "SESSION_ID"):
        val = os.environ.get(var, "").strip()
        if val:
            return validate_session_id(val)
    return None


def is_mode_state_filename(filename: str) -> bool:
    return filename.endswith(STATE_FILE_SUFFIX) and filename != "session.json"
