"""Team task approval records.

Port of src/team/state/approvals.ts.
"""

from __future__ import annotations

import json
from pathlib import Path

from omx.team.state.types import TaskApprovalRecord


def _approval_path(team_dir: Path, task_id: str) -> Path:
    return team_dir / "approvals" / f"{task_id}.json"


def write_task_approval(team_dir: Path, approval: TaskApprovalRecord) -> None:
    """Write a task approval record to disk.

    Args:
        team_dir: Path to team state directory.
        approval: The approval record to write.
    """
    path = _approval_path(team_dir, approval.task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(approval.to_dict(), indent=2), encoding="utf-8")


def read_task_approval(team_dir: Path, task_id: str) -> TaskApprovalRecord | None:
    """Read a task approval record from disk.

    Args:
        team_dir: Path to team state directory.
        task_id: Task to read approval for.

    Returns:
        The approval record, or None if not found.
    """
    path = _approval_path(team_dir, task_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("task_id") != task_id:
            return None
        return TaskApprovalRecord.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return None
