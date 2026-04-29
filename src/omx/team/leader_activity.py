"""Leader activity monitoring and git history tracking.

Port of src/team/leader-activity.ts.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_leader_activity(team_dir: Path) -> dict[str, Any] | None:
    """Read the leader activity state file.

    Args:
        team_dir: Path to team state directory.

    Returns:
        Leader activity dict or None.
    """
    path = team_dir / "leader-activity.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def write_leader_activity(team_dir: Path, activity: dict[str, Any]) -> None:
    """Write the leader activity state file.

    Args:
        team_dir: Path to team state directory.
        activity: Activity state to write.
    """
    path = team_dir / "leader-activity.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    activity["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(activity, indent=2), encoding="utf-8")


def get_leader_git_head(cwd: str) -> str | None:
    """Get the current git HEAD commit hash for the leader.

    Args:
        cwd: Working directory.

    Returns:
        Commit hash string or None.
    """
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def has_leader_diverged(cwd: str, last_known_head: str) -> bool:
    """Check if the leader's branch has diverged from a known commit.

    Args:
        cwd: Working directory.
        last_known_head: Previously recorded HEAD commit.

    Returns:
        True if HEAD has changed.
    """
    current = get_leader_git_head(cwd)
    return current is not None and current != last_known_head
