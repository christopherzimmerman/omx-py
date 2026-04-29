"""Team worker monitoring and summary snapshots.

Port of src/team/state/monitor.ts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omx.team.state.types import TeamMonitorSnapshot, TeamPhaseState


def read_monitor_snapshot(team_dir: Path) -> TeamMonitorSnapshot | None:
    """Read the cached monitor snapshot."""
    path = team_dir / "monitor-snapshot.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return TeamMonitorSnapshot.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return None


def write_monitor_snapshot(team_dir: Path, snapshot: TeamMonitorSnapshot) -> None:
    """Write the monitor snapshot atomically."""
    path = team_dir / "monitor-snapshot.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")


def read_phase_state(team_dir: Path) -> TeamPhaseState | None:
    """Read the team phase state."""
    path = team_dir / "phase-state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return TeamPhaseState.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return None


def write_phase_state(team_dir: Path, phase: TeamPhaseState) -> None:
    """Write the team phase state."""
    path = team_dir / "phase-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(phase.to_dict(), indent=2), encoding="utf-8")


def get_team_summary(
    team_dir: Path,
    workers: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a team health summary from current state.

    Args:
        team_dir: Path to team state directory.
        workers: List of worker info dicts.
        tasks: List of task dicts.

    Returns:
        Summary dict with worker/task counts and health indicators.
    """
    task_counts = {
        "total": len(tasks),
        "pending": sum(1 for t in tasks if t.get("status") == "pending"),
        "blocked": sum(1 for t in tasks if t.get("status") == "blocked"),
        "in_progress": sum(1 for t in tasks if t.get("status") == "in_progress"),
        "completed": sum(1 for t in tasks if t.get("status") == "completed"),
        "failed": sum(1 for t in tasks if t.get("status") == "failed"),
    }

    non_reporting: list[str] = []
    worker_summaries: list[dict[str, Any]] = []

    for w in workers:
        name = w.get("name", "")
        alive = w.get("alive", True)
        if not alive:
            non_reporting.append(name)
        worker_summaries.append(
            {
                "name": name,
                "alive": alive,
                "lastTurnAt": w.get("last_turn_at", ""),
                "turnsWithoutProgress": w.get("turns_without_progress", 0),
            }
        )

    return {
        "workerCount": len(workers),
        "tasks": task_counts,
        "workers": worker_summaries,
        "nonReportingWorkers": non_reporting,
    }
