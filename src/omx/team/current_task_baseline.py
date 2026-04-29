"""Current task baseline tracking.

Port of src/team/current-task-baseline.ts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_task_baseline(team_dir: Path, worker_name: str) -> dict[str, Any] | None:
    """Read a worker's current task baseline.

    Args:
        team_dir: Path to team state directory.
        worker_name: Worker name.

    Returns:
        Baseline dict or None.
    """
    path = team_dir / "workers" / worker_name / "task-baseline.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_task_baseline(
    team_dir: Path,
    worker_name: str,
    task_id: str,
    commit_ref: str | None = None,
) -> None:
    """Write a worker's current task baseline.

    Args:
        team_dir: Path to team state directory.
        worker_name: Worker name.
        task_id: Current task ID.
        commit_ref: Git commit reference at task start.
    """
    path = team_dir / "workers" / worker_name / "task-baseline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"task_id": task_id}
    if commit_ref:
        data["commit_ref"] = commit_ref
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
