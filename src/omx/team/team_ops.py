"""MCP-aligned team operations gateway.

Port of src/team/team-ops.ts.
"""

from __future__ import annotations

from typing import Any


def create_team_task(
    team_name: str,
    cwd: str,
    description: str,
    role: str | None = None,
    file_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Create a new task in the team.

    Args:
        team_name: Team name.
        cwd: Working directory.
        description: Task description.
        role: Optional agent role.
        file_paths: Optional file scope.

    Returns:
        Created task dict.
    """
    from datetime import datetime, timezone

    from omx.team.state.io import read_team_config, read_tasks, write_tasks
    from omx.team.contracts import TeamTask

    config = read_team_config(cwd, team_name)
    next_id = config.get("next_task_id", 1)
    task_id = f"task-{next_id}"

    task = TeamTask(
        task_id=task_id,
        description=description,
        role=role,
        file_paths=file_paths or [],
        created_at=datetime.now(timezone.utc).isoformat(),
    )

    tasks = read_tasks(cwd, team_name)
    tasks.append(task)
    write_tasks(cwd, tasks, team_name)

    # Update next_task_id
    config["next_task_id"] = next_id + 1
    from omx.team.state.io import write_team_config

    write_team_config(cwd, config, team_name)

    return task.to_dict()


def list_team_tasks(team_name: str, cwd: str) -> list[dict[str, Any]]:
    """List all tasks in a team.

    Args:
        team_name: Team name.
        cwd: Working directory.

    Returns:
        List of task dicts.
    """
    from omx.team.state.io import read_tasks

    return [t.to_dict() for t in read_tasks(cwd, team_name)]
