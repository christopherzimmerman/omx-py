"""Team orchestration — high-level team lifecycle.

Port of src/team/orchestrator.ts.
"""

from __future__ import annotations

from typing import Any



def init_team(
    team_name: str,
    cwd: str,
    task: str,
    worker_count: int = 2,
    agent_type: str = "executor",
    worker_cli: str = "codex",
) -> dict[str, Any]:
    """Initialize a new team with configuration and state directories.

    Args:
        team_name: Name for the team.
        cwd: Working directory.
        task: Top-level task description.
        worker_count: Number of workers.
        agent_type: Default agent role.
        worker_cli: CLI tool for workers.

    Returns:
        Team configuration dict.
    """
    from omx.team.state.io import write_team_config

    config = {
        "name": team_name,
        "task": task,
        "agent_type": agent_type,
        "worker_launch_mode": "interactive",
        "worker_count": worker_count,
        "worker_cli": worker_cli,
        "workers": [],
        "tmux_session": f"omx-{team_name}",
        "next_task_id": 1,
    }
    write_team_config(cwd, config, team_name)
    return config


def is_team_active(team_name: str, cwd: str) -> bool:
    """Check if a team has active (non-terminal) tasks.

    Args:
        team_name: Team name.
        cwd: Working directory.

    Returns:
        True if there are pending or in-progress tasks.
    """
    from omx.team.runtime import check_team_completion

    return not check_team_completion(cwd, team_name)
