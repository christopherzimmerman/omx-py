"""Planning artifact reading.

Port of src/planning/artifacts.ts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from omx.utils.paths import omx_plans_dir

PRD_PATTERN = re.compile(r"^prd-.*\.md$", re.IGNORECASE)
TEST_SPEC_PATTERN = re.compile(r"^test-?spec-.*\.md$", re.IGNORECASE)
DEEP_INTERVIEW_SPEC_PATTERN = re.compile(r"^deep-interview-.*\.md$", re.IGNORECASE)


@dataclass
class PlanningArtifacts:
    """Collection of planning artifacts in a worktree.

    Attributes:
        plans_dir: Path to plans directory.
        specs_dir: Path to specs directory.
        prd_paths: Paths to PRD files.
        test_spec_paths: Paths to test spec files.
        deep_interview_spec_paths: Paths to deep interview spec files.
    """

    plans_dir: str = ""
    specs_dir: str = ""
    prd_paths: list[str] = field(default_factory=list)
    test_spec_paths: list[str] = field(default_factory=list)
    deep_interview_spec_paths: list[str] = field(default_factory=list)


@dataclass
class LatestPlanningArtifactSelection:
    """Selection of the latest planning artifacts.

    Attributes:
        prd_path: Path to latest PRD or None.
        test_spec_paths: Matching test spec paths.
        deep_interview_spec_paths: Matching deep interview spec paths.
    """

    prd_path: str | None = None
    test_spec_paths: list[str] = field(default_factory=list)
    deep_interview_spec_paths: list[str] = field(default_factory=list)


@dataclass
class ApprovedPlanContext:
    """Context for an approved plan.

    Attributes:
        source_path: Path to the approved plan file.
        test_spec_paths: Related test spec paths.
        deep_interview_spec_paths: Related deep interview spec paths.
    """

    source_path: str = ""
    test_spec_paths: list[str] = field(default_factory=list)
    deep_interview_spec_paths: list[str] = field(default_factory=list)


@dataclass
class ApprovedExecutionLaunchHint:
    """Hint for launching execution from an approved plan.

    Attributes:
        mode: Execution mode (team or ralph).
        command: Shell command extracted from the plan.
        task: Task description.
        source_path: Path to the plan file.
        test_spec_paths: Test spec paths.
        deep_interview_spec_paths: Deep interview spec paths.
        worker_count: Optional worker count.
        agent_type: Optional agent type.
        linked_ralph: Whether ralph is linked.
    """

    mode: str = ""  # "team" | "ralph"
    command: str = ""
    task: str = ""
    source_path: str = ""
    test_spec_paths: list[str] = field(default_factory=list)
    deep_interview_spec_paths: list[str] = field(default_factory=list)
    worker_count: int | None = None
    agent_type: str | None = None
    linked_ralph: bool | None = None


def _read_matching_paths(dir_path: str, pattern: re.Pattern[str]) -> list[str]:
    """Read matching file paths from a directory."""
    d = Path(dir_path)
    if not d.exists():
        return []
    try:
        files = sorted(
            str(d / f.name)
            for f in d.iterdir()
            if f.is_file() and pattern.match(f.name)
        )
        return files
    except OSError:
        return []


def read_planning_artifacts(cwd: str) -> PlanningArtifacts:
    """Read planning artifacts from a worktree.

    Args:
        cwd: Working directory.

    Returns:
        PlanningArtifacts with discovered file paths.
    """
    plans_dir = str(omx_plans_dir(Path(cwd)))
    specs_dir = str(Path(cwd) / ".omx" / "specs")
    return PlanningArtifacts(
        plans_dir=plans_dir,
        specs_dir=specs_dir,
        prd_paths=_read_matching_paths(plans_dir, PRD_PATTERN),
        test_spec_paths=_read_matching_paths(plans_dir, TEST_SPEC_PATTERN),
        deep_interview_spec_paths=_read_matching_paths(
            specs_dir, DEEP_INTERVIEW_SPEC_PATTERN
        ),
    )


def is_planning_complete(artifacts: PlanningArtifacts) -> bool:
    """Check if planning is complete (has both PRD and test specs).

    Args:
        artifacts: Planning artifacts to check.

    Returns:
        True if both PRD and test spec files exist.
    """
    return len(artifacts.prd_paths) > 0 and len(artifacts.test_spec_paths) > 0


def _artifact_slug(path: str, prefix_pattern: re.Pattern[str]) -> str | None:
    """Extract the slug from a planning artifact filename."""
    name = Path(path).name
    match = prefix_pattern.match(name)
    if match and "slug" in match.groupdict():
        return match.group("slug")
    return None


def _filter_artifacts_for_slug(
    paths: list[str],
    prefix_pattern: re.Pattern[str],
    slug: str | None,
) -> list[str]:
    """Filter artifacts to those matching a slug."""
    if not slug:
        return []
    return [p for p in paths if _artifact_slug(p, prefix_pattern) == slug]


def select_latest_planning_artifacts(
    artifacts: PlanningArtifacts,
) -> LatestPlanningArtifactSelection:
    """Select the latest planning artifacts by slug matching.

    Args:
        artifacts: All discovered planning artifacts.

    Returns:
        LatestPlanningArtifactSelection with the latest matched set.
    """
    latest_prd = artifacts.prd_paths[-1] if artifacts.prd_paths else None
    slug = (
        _artifact_slug(latest_prd, re.compile(r"^prd-(?P<slug>.*)\.md$", re.IGNORECASE))
        if latest_prd
        else None
    )
    return LatestPlanningArtifactSelection(
        prd_path=latest_prd,
        test_spec_paths=_filter_artifacts_for_slug(
            artifacts.test_spec_paths,
            re.compile(r"^test-?spec-(?P<slug>.*)\.md$", re.IGNORECASE),
            slug,
        ),
        deep_interview_spec_paths=_filter_artifacts_for_slug(
            artifacts.deep_interview_spec_paths,
            re.compile(r"^deep-interview-(?P<slug>.*)\.md$", re.IGNORECASE),
            slug,
        ),
    )


def read_latest_planning_artifacts(cwd: str) -> LatestPlanningArtifactSelection:
    """Read and select the latest planning artifacts from a worktree.

    Args:
        cwd: Working directory.

    Returns:
        LatestPlanningArtifactSelection.
    """
    return select_latest_planning_artifacts(read_planning_artifacts(cwd))


def read_approved_execution_launch_hint(
    cwd: str,
    mode: str,
) -> ApprovedExecutionLaunchHint | None:
    """Read an execution launch hint from the approved plan.

    Args:
        cwd: Working directory.
        mode: Execution mode ('team' or 'ralph').

    Returns:
        ApprovedExecutionLaunchHint or None if not found.
    """
    artifacts = read_planning_artifacts(cwd)
    if not is_planning_complete(artifacts):
        return None

    selection = select_latest_planning_artifacts(artifacts)
    if not selection.prd_path:
        return None

    prd_path = Path(selection.prd_path)
    if not prd_path.exists():
        return None

    try:
        content = prd_path.read_text(encoding="utf-8")
    except OSError:
        return None

    def _decode_quoted(raw: str) -> str | None:
        normalized = raw.strip()
        if not normalized:
            return None
        try:
            return json.loads(normalized)
        except (json.JSONDecodeError, ValueError):
            if (normalized.startswith('"') and normalized.endswith('"')) or (
                normalized.startswith("'") and normalized.endswith("'")
            ):
                return normalized[1:-1]
            return None

    if mode == "team":
        team_pattern = re.compile(
            r"(?P<command>(?:omx\s+team|\$team)\s+(?P<ralph>ralph\s+)?"
            r"(?P<count>\d+)(?::(?P<role>[a-z][a-z0-9-]*))?\s+"
            r"""(?P<task>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'))""",
            re.IGNORECASE,
        )
        matches = list(team_pattern.finditer(content))
        if not matches:
            return None
        last = matches[-1]
        task = _decode_quoted(last.group("task"))
        if not task:
            return None
        return ApprovedExecutionLaunchHint(
            mode=mode,
            command=last.group("command"),
            task=task,
            worker_count=int(last.group("count")),
            agent_type=last.group("role") or None,
            linked_ralph=bool(last.group("ralph") and last.group("ralph").strip()),
            source_path=selection.prd_path,
            test_spec_paths=selection.test_spec_paths,
            deep_interview_spec_paths=selection.deep_interview_spec_paths,
        )

    # ralph mode
    ralph_pattern = re.compile(
        r"""(?P<command>(?:omx\s+ralph|\$ralph)\s+(?P<task>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'))""",
        re.IGNORECASE,
    )
    matches = list(ralph_pattern.finditer(content))
    if not matches:
        return None
    last = matches[-1]
    task = _decode_quoted(last.group("task"))
    if not task:
        return None
    return ApprovedExecutionLaunchHint(
        mode=mode,
        command=last.group("command"),
        task=task,
        source_path=selection.prd_path,
        test_spec_paths=selection.test_spec_paths,
        deep_interview_spec_paths=selection.deep_interview_spec_paths,
    )
