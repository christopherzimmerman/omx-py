"""Git worktree lifecycle management for team workers.

Port of src/team/worktree.ts.
Each worker gets an isolated git worktree to avoid conflicts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def create_worktree(
    cwd: str,
    branch_name: str,
    worktree_path: str | None = None,
) -> dict[str, Any]:
    """Create a git worktree for a worker.

    Args:
        cwd: Main repository working directory.
        branch_name: Branch name for the worktree.
        worktree_path: Optional explicit path for the worktree.

    Returns:
        Dict with "ok", "path", "branch", and optional "error".
    """
    if worktree_path is None:
        worktree_path = str(Path(cwd).parent / f".omx-worktree-{branch_name}")

    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, worktree_path],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )

    if result.returncode != 0:
        # Try without -b if branch already exists
        result = subprocess.run(
            ["git", "worktree", "add", worktree_path, branch_name],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )

    if result.returncode == 0:
        return {"ok": True, "path": worktree_path, "branch": branch_name}
    return {
        "ok": False,
        "error": result.stderr.strip(),
        "path": worktree_path,
        "branch": branch_name,
    }


def remove_worktree(cwd: str, worktree_path: str, force: bool = False) -> bool:
    """Remove a git worktree.

    Args:
        cwd: Main repository working directory.
        worktree_path: Path of the worktree to remove.
        force: Force removal even with uncommitted changes.

    Returns:
        True if removal succeeded.
    """
    args = ["git", "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(worktree_path)

    result = subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=False)
    return result.returncode == 0


def list_worktrees(cwd: str) -> list[dict[str, str]]:
    """List all git worktrees.

    Args:
        cwd: Repository working directory.

    Returns:
        List of dicts with "path", "branch", "head" fields.
    """
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    if result.returncode != 0:
        return []

    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line.split(" ", 1)[1]}
        elif line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
        elif not line.strip() and current:
            worktrees.append(current)
            current = {}

    if current:
        worktrees.append(current)

    return worktrees


def prune_worktrees(cwd: str) -> None:
    """Prune stale worktree metadata.

    Args:
        cwd: Repository working directory.
    """
    subprocess.run(
        ["git", "worktree", "prune"],
        capture_output=True,
        cwd=cwd,
        check=False,
    )
