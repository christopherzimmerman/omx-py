"""Role routing and task assignment heuristics.

Port of src/team/role-router.ts.
"""

from __future__ import annotations

from typing import Any


# File extension to role mapping
EXTENSION_ROLES: dict[str, str] = {
    ".test.": "verifier",
    ".spec.": "verifier",
    "_test.": "verifier",
    ".css": "designer",
    ".scss": "designer",
    ".html": "designer",
    ".tsx": "executor",
    ".jsx": "executor",
    ".sql": "data-analyst",
    ".py": "executor",
    ".rs": "executor",
    ".go": "executor",
    ".ts": "executor",
    ".js": "executor",
}

# Keywords to role mapping
KEYWORD_ROLES: dict[str, str] = {
    "test": "verifier",
    "spec": "verifier",
    "bug": "debugger",
    "fix": "debugger",
    "debug": "debugger",
    "design": "designer",
    "ui": "designer",
    "ux": "designer",
    "review": "quality-reviewer",
    "security": "security-reviewer",
    "perf": "executor",
    "refactor": "executor",
    "deploy": "devops",
    "ci": "devops",
    "infrastructure": "devops",
}


def infer_role_from_task(description: str, file_paths: list[str] | None = None) -> str:
    """Infer the best agent role for a task based on its description and file paths.

    Args:
        description: Task description text.
        file_paths: Optional list of file paths the task touches.

    Returns:
        Inferred role name (defaults to "executor").
    """
    desc_lower = description.lower()

    # Check keywords in description
    for keyword, role in KEYWORD_ROLES.items():
        if keyword in desc_lower:
            return role

    # Check file extensions
    if file_paths:
        for path in file_paths:
            for ext, role in EXTENSION_ROLES.items():
                if ext in path.lower():
                    return role

    return "executor"


def route_task_to_role(
    task: dict[str, Any],
    available_roles: list[str] | None = None,
) -> str:
    """Route a task to the best available role.

    Args:
        task: Task dict with description and optional file_paths.
        available_roles: Optional list of available roles to choose from.

    Returns:
        The selected role name.
    """
    inferred = infer_role_from_task(
        task.get("description", ""),
        task.get("file_paths"),
    )

    if available_roles and inferred not in available_roles:
        # Fall back to executor if available, else first available
        if "executor" in available_roles:
            return "executor"
        return available_roles[0] if available_roles else "executor"

    return inferred
