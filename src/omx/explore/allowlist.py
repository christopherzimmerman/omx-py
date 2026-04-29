"""Explore allowlist — commands permitted in read-only mode.

Port of crates/omx-explore/src/main.rs allowlist.
"""

from __future__ import annotations

ALLOWED_COMMANDS = {
    "rg",
    "grep",
    "ls",
    "find",
    "cat",
    "head",
    "tail",
    "pwd",
    "wc",
    "printf",
    "tree",
    "file",
    "stat",
    "git",
    "echo",
    "cmd",
}

# Git subcommands that are safe for read-only
ALLOWED_GIT_SUBCOMMANDS = {
    "log",
    "show",
    "diff",
    "status",
    "branch",
    "tag",
    "blame",
    "shortlog",
    "describe",
    "rev-parse",
}


def is_command_allowed(args: list[str]) -> bool:
    """Check if a command is permitted in read-only explore mode.

    Args:
        args: Command and arguments (e.g. ["git", "log"]).

    Returns:
        True if the command is in the allowlist (including safe git subcommands).
    """
    if not args:
        return False
    base = args[0]
    if base not in ALLOWED_COMMANDS:
        return False
    if base == "git" and len(args) > 1:
        return args[1] in ALLOWED_GIT_SUBCOMMANDS
    return True
