"""Git commit hygiene for team workers.

Port of src/team/commit-hygiene.ts.
"""

from __future__ import annotations

import subprocess


def has_uncommitted_changes(cwd: str) -> bool:
    """Check if there are uncommitted changes in the working directory."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    return bool(result.stdout.strip())


def get_current_branch(cwd: str) -> str | None:
    """Get the current git branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def create_worker_branch(cwd: str, branch_name: str) -> bool:
    """Create and checkout a new git branch for a worker.

    Args:
        cwd: Working directory for the git operation.
        branch_name: Name for the new branch.

    Returns:
        True if the branch was created successfully.
    """
    result = subprocess.run(
        ["git", "checkout", "-b", branch_name],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    return result.returncode == 0


def merge_worker_branch(cwd: str, branch_name: str) -> tuple[bool, str]:
    """Merge a worker branch back into the current branch with --no-ff.

    Args:
        cwd: Working directory for the git operation.
        branch_name: Branch to merge.

    Returns:
        Tuple of (success, combined stdout+stderr output).
    """
    result = subprocess.run(
        ["git", "merge", "--no-ff", branch_name],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    output = result.stdout + result.stderr
    return result.returncode == 0, output.strip()
