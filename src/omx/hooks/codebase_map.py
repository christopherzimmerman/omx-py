"""Codebase structure snapshot generator.

Port of src/hooks/codebase-map.ts. Generates a compact map of the
project's source structure using ``git ls-files``.
"""

from __future__ import annotations

import subprocess
from pathlib import PurePosixPath

MAX_MAP_CHARS = 1000
MAX_FILES_PER_DIR = 10
MAX_DIRS = 14
SOURCE_EXTS = frozenset({".ts", ".tsx", ".js", ".mjs", ".py"})


def _get_tracked_source_files(cwd: str) -> list[str]:
    """Return git-tracked source files relative to cwd.

    Args:
        cwd: Working directory to run git in.

    Returns:
        List of relative file paths with source extensions.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
        if result.returncode != 0:
            return []
        return [
            f
            for f in result.stdout.strip().split("\n")
            if f and PurePosixPath(f).suffix in SOURCE_EXTS
        ]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return []


def _group_by_top_dir(files: list[str]) -> dict[str, list[str]]:
    """Group file paths by their top-level directory.

    Args:
        files: List of relative file paths.

    Returns:
        Dict mapping directory names to file lists.
    """
    groups: dict[str, list[str]] = {}
    for f in files:
        sep = f.find("/")
        directory = f[:sep] if sep >= 0 else "."
        groups.setdefault(directory, []).append(f)
    return groups


def _sort_dirs(dirs: list[str]) -> list[str]:
    """Sort directories: priority dirs first, then alpha, dotfiles last.

    Args:
        dirs: List of directory names.

    Returns:
        Sorted directory names.
    """
    priority = ["src", "scripts", "bin", "prompts", "agents", "skills", "templates"]

    def key(d: str) -> tuple[int, int, str]:
        if d in priority:
            return (0, priority.index(d), d)
        if d.startswith(".") or d == ".":
            return (2, 0, d)
        return (1, 0, d)

    return sorted(dirs, key=key)


def _build_dir_line(directory: str, files: list[str]) -> str:
    """Build a compact directory summary line.

    Args:
        directory: Directory name or '.'.
        files: Files within the directory.

    Returns:
        Formatted line like ``  src/hooks/: agents-overlay, codebase-map``.
    """
    names: list[str] = []
    for f in files[:MAX_FILES_PER_DIR]:
        name = PurePosixPath(f).stem
        # Keep 'index' only if it's the sole file
        if name == "index" and len(files) > 1:
            continue
        names.append(name)

    if not names:
        return ""

    label = "(root)" if directory == "." else f"{directory}/"
    return f"  {label}: {', '.join(names)}"


def generate_codebase_map(cwd: str) -> str:
    """Generate a compact codebase map for the project.

    Uses ``git ls-files`` to list tracked source files, groups them
    by directory, and produces a compact text summary.

    Args:
        cwd: Project root directory.

    Returns:
        Codebase map string, or empty string on error or no files.
    """
    try:
        files = _get_tracked_source_files(cwd)
        if not files:
            return ""

        grouped = _group_by_top_dir(files)
        sorted_dirs = _sort_dirs(list(grouped.keys()))

        lines: list[str] = []
        for directory in sorted_dirs[:MAX_DIRS]:
            dir_files = grouped.get(directory, [])

            if directory == "src":
                # Sub-group by immediate subdirectory
                sub_grouped: dict[str, list[str]] = {}
                for f in dir_files:
                    parts = f.split("/")
                    sub_dir = f"src/{parts[1]}" if len(parts) >= 3 else "src"
                    sub_grouped.setdefault(sub_dir, []).append(f)

                for sub in sorted(sub_grouped.keys())[:MAX_DIRS]:
                    line = _build_dir_line(sub, sub_grouped[sub])
                    if line:
                        lines.append(line)
            else:
                line = _build_dir_line(directory, dir_files)
                if line:
                    lines.append(line)

        if not lines:
            return ""

        body = "\n".join(lines)
        return body if len(body) <= MAX_MAP_CHARS else body[: MAX_MAP_CHARS - 3] + "..."

    except Exception:
        return ""
