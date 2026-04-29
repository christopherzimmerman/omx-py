"""Skill and agent catalog discovery.

Port of src/catalog/reader.ts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omx.utils.paths import package_root, project_skills_dir, user_skills_dir


def discover_skills(project_root: Path | None = None) -> list[dict[str, Any]]:
    """Discover all available skills in scope precedence order.

    Project-level skills take precedence over user-level skills.

    Args:
        project_root: Project root for project-scoped skill discovery.

    Returns:
        List of dicts with "name", "path", and "scope" keys.
    """
    skills: list[dict[str, Any]] = []
    seen_names: set[str] = set()

    # Project skills take precedence
    for scope, skills_dir in [
        ("project", project_skills_dir(project_root)),
        ("user", user_skills_dir()),
    ]:
        if not skills_dir.exists():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            if entry.name in seen_names:
                continue
            seen_names.add(entry.name)
            skills.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "scope": scope,
                }
            )

    return skills


def discover_prompts() -> list[dict[str, Any]]:
    """Discover available agent prompts."""
    prompts_dir = package_root() / "assets" / "prompts"
    if not prompts_dir.exists():
        return []

    return [{"name": f.stem, "path": str(f)} for f in sorted(prompts_dir.glob("*.md"))]
