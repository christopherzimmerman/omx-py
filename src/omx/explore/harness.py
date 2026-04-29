"""Explore harness — read-only exploration with allowlist.

Port of crates/omx-explore/src/main.rs.
"""

from __future__ import annotations

from omx.explore.allowlist import is_command_allowed
from omx.sparkshell.exec import SparkshellResult, execute_command


def explore_execute(
    args: list[str],
    cwd: str | None = None,
) -> SparkshellResult:
    """Execute a command in explore mode with read-only enforcement.

    Validates the command against the allowlist before execution.

    Args:
        args: Command and arguments to execute.
        cwd: Working directory for the subprocess.

    Returns:
        SparkshellResult (error result if command is not allowed).
    """
    if not is_command_allowed(args):
        return SparkshellResult(
            exit_code=1,
            stdout="",
            stderr=f"Command not allowed in explore mode: {' '.join(args)}",
        )
    return execute_command(args, cwd=cwd)
