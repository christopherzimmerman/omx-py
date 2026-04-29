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
