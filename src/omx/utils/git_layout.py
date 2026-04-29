"""Git repository layout detection.

Port of src/utils/git-layout.ts. Detects .git directory, worktree
roots, and common-dir pointers without shelling out to git.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitLayout:
    """Resolved git directory layout.

    Attributes:
        git_dir: Path to the resolved .git directory.
        common_dir: Path to the git common directory (shared across worktrees).
        worktree_root: Root of the working tree that contains ``.git``.
    """

    git_dir: str
    common_dir: str
    worktree_root: str


def _read_trimmed_file(path: Path) -> str | None:
    """Read a file and return its stripped content, or None on error."""
    try:
        content = path.read_text(encoding="utf-8").strip()
        return content or None
    except OSError:
        return None


def _resolve_git_dir_pointer(path: Path) -> str | None:
    """Resolve a ``gitdir: <path>`` pointer file."""
    raw = _read_trimmed_file(path)
    if not raw:
        return None
    match = re.match(r"^gitdir:\s*(.+)$", raw, re.IGNORECASE)
    if not match:
        return None
    return str((path.parent / match.group(1).strip()).resolve())


def _resolve_git_common_dir(git_dir: str) -> str:
    """Resolve the common-dir from a git directory."""
    common_dir_content = _read_trimmed_file(Path(git_dir) / "commondir")
    if common_dir_content:
        return str((Path(git_dir) / common_dir_content).resolve())
    return git_dir


def find_git_layout(start_cwd: str) -> GitLayout | None:
    """Walk up from *start_cwd* to find the git layout.

    Args:
        start_cwd: Starting directory for the upward search.

    Returns:
        A ``GitLayout`` if a ``.git`` directory or file is found, else ``None``.
    """
    current = Path(start_cwd).resolve()
    while True:
        candidate = current / ".git"
        try:
            if candidate.is_dir():
                git_dir = str(candidate)
                return GitLayout(
                    git_dir=git_dir,
                    common_dir=_resolve_git_common_dir(git_dir),
                    worktree_root=str(current),
                )
            if candidate.is_file():
                git_dir = _resolve_git_dir_pointer(candidate)
                if git_dir:
                    return GitLayout(
                        git_dir=git_dir,
                        common_dir=_resolve_git_common_dir(git_dir),
                        worktree_root=str(current),
                    )
        except OSError:
            pass
        parent = current.parent
        if parent == current:
            return None
        current = parent


def read_git_layout_file(base_dir: str, *parts: str) -> str | None:
    """Read and strip a file relative to *base_dir*.

    Args:
        base_dir: Base directory.
        *parts: Path components relative to *base_dir*.

    Returns:
        Stripped file content or ``None``.
    """
    return _read_trimmed_file(Path(base_dir, *parts))
