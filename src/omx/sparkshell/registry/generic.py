"""Generic shell command registry.

Port of crates/omx-sparkshell/src/registry/generic_shell.rs.
"""

from __future__ import annotations

# Read-only commands safe for exploration
READ_ONLY_COMMANDS = {
    "ls",
    "cat",
    "head",
    "tail",
    "find",
    "grep",
    "rg",
    "wc",
    "pwd",
    "echo",
    "printf",
    "tree",
    "file",
    "stat",
    "du",
    "sort",
    "uniq",
    "diff",
    "which",
    "type",
    "env",
    "printenv",
}

# Commands that modify state
MUTATING_COMMANDS = {
    "rm",
    "mv",
    "cp",
    "mkdir",
    "rmdir",
    "touch",
    "chmod",
    "chown",
    "sed",
    "awk",
    "tee",
    "truncate",
}


def is_read_only_command(command: str) -> bool:
    """Check if a command is read-only."""
    base = command.strip().split()[0] if command.strip() else ""
    return base in READ_ONLY_COMMANDS


def is_mutating_command(command: str) -> bool:
    """Check if a command modifies state."""
    base = command.strip().split()[0] if command.strip() else ""
    return base in MUTATING_COMMANDS
