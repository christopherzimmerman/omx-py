"""Plugin distribution sync -- skill mirror comparison.

Port of src/catalog/skill-mirror.ts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class DirectoryMirrorMismatch:
    """Describes a mismatch between expected and actual directory mirrors.

    Attributes:
        kind: Type of mismatch.
        path: Relative path of mismatched file (for 'content' kind).
        expected: Expected file list (for 'file-list' kind).
        actual: Actual file list (for 'file-list' kind).
    """

    kind: str  # "missing-directory" | "file-list" | "content" | "not-directory"
    path: str | None = None
    expected: list[str] | None = None
    actual: list[str] | None = None


@dataclass
class SkillMirrorMismatch:
    """Describes a mismatch in skill mirror directories.

    Attributes:
        kind: Type of mismatch.
        skill_name: Name of the mismatched skill.
        message: Human-readable description.
        expected: Expected skill/file list.
        actual: Actual skill/file list.
    """

    kind: str  # "skill-list" | "unexpected-entry" | "skill-directory"
    message: str
    skill_name: str | None = None
    expected: list[str] | None = None
    actual: list[str] | None = None


def _list_relative_files(directory: str, base: str | None = None) -> list[str]:
    """Recursively list files relative to *base*, using forward slashes."""
    base = base or directory
    result: list[str] = []
    try:
        entries = sorted(os.listdir(directory))
    except OSError:
        return result
    for entry in entries:
        full = os.path.join(directory, entry)
        if os.path.isdir(full):
            result.extend(_list_relative_files(full, base))
        elif os.path.isfile(full):
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            result.append(rel)
    return sorted(result)


def compare_directory_mirror(
    expected_dir: str,
    actual_dir: str,
    expected_content_transform: Callable[[str, bytes], bytes] | None = None,
) -> DirectoryMirrorMismatch | None:
    """Compare two directory trees for exact mirror equality.

    Args:
        expected_dir: Path to the expected directory.
        actual_dir: Path to the actual directory.
        expected_content_transform: Optional transform applied to expected file bytes.

    Returns:
        A mismatch description or ``None`` if directories match.
    """
    ep, ap = Path(expected_dir), Path(actual_dir)
    if not ep.exists() or not ap.exists():
        return DirectoryMirrorMismatch(kind="missing-directory")
    if not ep.is_dir() or not ap.is_dir():
        return DirectoryMirrorMismatch(kind="not-directory")

    expected_files = _list_relative_files(expected_dir)
    actual_files = _list_relative_files(actual_dir)
    if expected_files != actual_files:
        return DirectoryMirrorMismatch(
            kind="file-list",
            expected=expected_files,
            actual=actual_files,
        )

    for file in expected_files:
        raw_expected = Path(expected_dir, file).read_bytes()
        actual_content = Path(actual_dir, file).read_bytes()
        expected_content = (
            expected_content_transform(file, raw_expected)
            if expected_content_transform
            else raw_expected
        )
        if expected_content != actual_content:
            return DirectoryMirrorMismatch(kind="content", path=file)

    return None


def compare_skill_mirror(
    expected_skills_dir: str,
    actual_skills_dir: str,
    expected_skill_names: list[str],
    expected_content_transform: Callable[[str, bytes], bytes] | None = None,
) -> SkillMirrorMismatch | None:
    """Compare skill directories for mirror equality.

    Args:
        expected_skills_dir: Directory containing expected skill subdirectories.
        actual_skills_dir: Directory containing actual skill subdirectories.
        expected_skill_names: List of expected skill names.
        expected_content_transform: Optional content transform for expected files.

    Returns:
        A mismatch description or ``None`` if skills match.
    """
    if not Path(actual_skills_dir).exists():
        return SkillMirrorMismatch(
            kind="skill-list", message="actual skills directory is missing"
        )

    try:
        entries = sorted(os.listdir(actual_skills_dir))
    except OSError:
        entries = []

    unexpected = sorted(e for e in entries if not Path(actual_skills_dir, e).is_dir())
    if unexpected:
        return SkillMirrorMismatch(
            kind="unexpected-entry",
            message=f"unexpected non-directory entries: {', '.join(unexpected)}",
            actual=unexpected,
        )

    actual_names = sorted(e for e in entries if Path(actual_skills_dir, e).is_dir())
    sorted_expected = sorted(expected_skill_names)
    if actual_names != sorted_expected:
        return SkillMirrorMismatch(
            kind="skill-list",
            message="skill directory list differs",
            expected=sorted_expected,
            actual=actual_names,
        )

    for name in sorted_expected:
        mismatch = compare_directory_mirror(
            os.path.join(expected_skills_dir, name),
            os.path.join(actual_skills_dir, name),
            expected_content_transform,
        )
        if mismatch:
            path_detail = f" ({mismatch.path})" if mismatch.path else ""
            return SkillMirrorMismatch(
                kind="skill-directory",
                skill_name=name,
                message=f"{name}: {mismatch.kind}{path_detail}",
                expected=mismatch.expected,
                actual=mismatch.actual,
            )

    return None


def assert_skill_mirror(
    expected_skills_dir: str,
    actual_skills_dir: str,
    expected_skill_names: list[str],
    expected_content_transform: Callable[[str, bytes], bytes] | None = None,
) -> None:
    """Assert skill mirror equality, raising on mismatch.

    Args:
        expected_skills_dir: Directory containing expected skill subdirectories.
        actual_skills_dir: Directory containing actual skill subdirectories.
        expected_skill_names: List of expected skill names.
        expected_content_transform: Optional content transform.

    Raises:
        RuntimeError: If a mismatch is detected.
    """
    mismatch = compare_skill_mirror(
        expected_skills_dir,
        actual_skills_dir,
        expected_skill_names,
        expected_content_transform,
    )
    if not mismatch:
        return
    parts = [
        "plugin_skill_mirror_out_of_sync",
        f"kind={mismatch.kind}",
    ]
    if mismatch.skill_name:
        parts.append(f"skill={mismatch.skill_name}")
    parts.append(f"message={mismatch.message}")
    if mismatch.expected is not None:
        parts.append(f"expected={json.dumps(mismatch.expected)}")
    if mismatch.actual is not None:
        parts.append(f"actual={json.dumps(mismatch.actual)}")
    raise RuntimeError("\n".join(parts))
