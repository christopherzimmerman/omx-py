"""Progress verification and evidence tracking.

Port of src/team/progress-evidence.ts.
"""

from __future__ import annotations

import subprocess


def has_worker_made_progress(cwd: str, since_commit: str | None = None) -> bool:
    """Check if the worker has made git progress since a given commit.

    Args:
        cwd: Working directory to check.
        since_commit: Commit hash to compare against (default: HEAD~1).

    Returns:
        True if there are new commits or changed files.
    """
    ref = since_commit or "HEAD~1"
    result = subprocess.run(
        ["git", "diff", "--stat", ref, "HEAD"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    return bool(result.stdout.strip())


def count_commits_since(cwd: str, since_commit: str) -> int:
    """Count commits since a given reference.

    Args:
        cwd: Working directory.
        since_commit: Starting commit reference.

    Returns:
        Number of commits since the reference.
    """
    result = subprocess.run(
        ["git", "rev-list", "--count", f"{since_commit}..HEAD"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0
