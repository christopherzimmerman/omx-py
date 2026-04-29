"""Bounded shell command execution.

Port of crates/omx-sparkshell/src/exec.rs.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

DEFAULT_OUTPUT_LINE_LIMIT = 500
DEFAULT_TIMEOUT_SECONDS = 30


@dataclass
class SparkshellResult:
    """Result of a sparkshell command execution.

    Attributes:
        exit_code: Process exit code (-1 for timeout/not-found).
        stdout: Captured stdout (possibly truncated).
        stderr: Captured stderr.
        truncated: Whether stdout was truncated to line_limit.
        line_count: Number of stdout lines returned.
    """

    exit_code: int
    stdout: str
    stderr: str
    truncated: bool = False
    line_count: int = 0


def execute_command(
    args: list[str],
    *,
    cwd: str | None = None,
    line_limit: int = DEFAULT_OUTPUT_LINE_LIMIT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> SparkshellResult:
    """Execute a shell command with bounded output and timeout.

    Args:
        args: Command and arguments to execute.
        cwd: Working directory for the subprocess.
        line_limit: Maximum stdout lines before truncation.
        timeout: Seconds before the command is killed.

    Returns:
        SparkshellResult with exit code, output, and truncation info.
    """
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return SparkshellResult(
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            truncated=False,
        )
    except FileNotFoundError:
        return SparkshellResult(
            exit_code=-1,
            stdout="",
            stderr=f"Command not found: {args[0] if args else '(empty)'}",
        )

    stdout_lines = result.stdout.splitlines()
    truncated = len(stdout_lines) > line_limit
    if truncated:
        stdout_lines = stdout_lines[:line_limit]

    return SparkshellResult(
        exit_code=result.returncode,
        stdout="\n".join(stdout_lines),
        stderr=result.stderr,
        truncated=truncated,
        line_count=len(stdout_lines),
    )
