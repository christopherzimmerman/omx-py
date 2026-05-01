"""Cross-platform subprocess execution and platform detection."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def is_windows() -> bool:
    return sys.platform == "win32"


def is_macos() -> bool:
    return sys.platform == "darwin"


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    capture: bool = True,
    check: bool = True,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess command with sensible defaults.

    Args:
        args: Command and arguments.
        cwd: Working directory for the subprocess.
        capture: Whether to capture stdout/stderr.
        check: Whether to raise on non-zero exit code.
        timeout: Seconds before killing the process.
        env: Extra environment variables (merged with os.environ).

    Returns:
        CompletedProcess with text output.

    Raises:
        subprocess.CalledProcessError: If check=True and exit code != 0.
    """
    merged_env = {**os.environ, **(env or {})}
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=capture,
        text=True,
        check=check,
        timeout=timeout,
        env=merged_env,
    )


def which(command: str) -> Path | None:
    """Find a command on PATH, returning its path or None."""
    import shutil

    result = shutil.which(command)
    return Path(result) if result else None


SUPPORTED_CLIS = ("codex", "claude")


class UnsupportedCliError(ValueError):
    """Raised when OMX_CLI is set to a value other than codex or claude."""


def resolve_cli(env: dict[str, str] | None = None) -> tuple[Path, str] | None:
    """Resolve the active provider CLI (codex or claude).

    Resolution order:
      1. ``OMX_CLI`` env var (values: ``codex`` or ``claude``). If set but not
         on PATH, returns None — fall back is not attempted, because the user
         explicitly chose this CLI.
      2. ``codex`` on PATH (default).
      3. ``claude`` on PATH.

    Args:
        env: Environment dict to read OMX_CLI from. Defaults to os.environ.

    Returns:
        ``(path, cli_name)`` if a CLI was found, else ``None``.

    Raises:
        UnsupportedCliError: If OMX_CLI is set to an unsupported value.
    """
    env_map = env if env is not None else os.environ
    forced = (env_map.get("OMX_CLI") or "").strip().lower()
    if forced:
        if forced not in SUPPORTED_CLIS:
            raise UnsupportedCliError(forced)
        path = which(forced)
        return (path, forced) if path else None

    for name in SUPPORTED_CLIS:
        path = which(name)
        if path:
            return (path, name)
    return None
