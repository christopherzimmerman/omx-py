"""Team state I/O — file-based persistence for team orchestration.

Port of src/team/state/*.ts.
State layout:
  .omx/team/{team_name}/
    config.json
    tasks.json
    workers.json
    events.jsonl
    workers/{worker_name}/
      inbox.md
      status.json
      heartbeat.json
    mailbox/{worker_name}.json
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.team.contracts import TeamEvent, TeamTask, TeamWorker


def _team_dir(cwd: str, team_name: str = "default") -> Path:
    return Path(cwd) / ".omx" / "team" / team_name


def _ensure_team_dir(cwd: str, team_name: str = "default") -> Path:
    d = _team_dir(cwd, team_name)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _worker_dir(cwd: str, team_name: str, worker_name: str) -> Path:
    d = _team_dir(cwd, team_name) / "workers" / worker_name
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- Config ---


def read_team_config(cwd: str, team_name: str = "default") -> dict[str, Any]:
    """Read the team config.json, returning empty dict if absent."""
    path = _team_dir(cwd, team_name) / "config.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_team_config(
    cwd: str, config: dict[str, Any], team_name: str = "default"
) -> None:
    """Write the team config.json."""
    d = _ensure_team_dir(cwd, team_name)
    (d / "config.json").write_text(json.dumps(config, indent=2), encoding="utf-8")


# --- Tasks ---


def read_tasks(cwd: str, team_name: str = "default") -> list[TeamTask]:
    """Read all team tasks from tasks.json."""
    path = _team_dir(cwd, team_name) / "tasks.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [TeamTask.from_dict(t) for t in data.get("tasks", [])]


def write_tasks(cwd: str, tasks: list[TeamTask], team_name: str = "default") -> None:
    """Write the complete task list to tasks.json."""
    d = _ensure_team_dir(cwd, team_name)
    data = {"tasks": [t.to_dict() for t in tasks]}
    (d / "tasks.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


# --- Workers ---


def read_workers(cwd: str, team_name: str = "default") -> list[TeamWorker]:
    """Read all team workers from workers.json."""
    path = _team_dir(cwd, team_name) / "workers.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [TeamWorker.from_dict(w) for w in data.get("workers", [])]


def write_workers(
    cwd: str, workers: list[TeamWorker], team_name: str = "default"
) -> None:
    """Write the complete worker list to workers.json."""
    d = _ensure_team_dir(cwd, team_name)
    data = {"workers": [w.to_dict() for w in workers]}
    (d / "workers.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


# --- Worker Inbox ---


def write_worker_inbox(cwd: str, team_name: str, worker_name: str, prompt: str) -> Path:
    """Write the worker's inbox.md file with the task prompt.

    Args:
        cwd: Working directory.
        team_name: Team name.
        worker_name: Worker name.
        prompt: Markdown content for the worker's inbox.

    Returns:
        Path to the written inbox file.
    """
    d = _worker_dir(cwd, team_name, worker_name)
    inbox_path = d / "inbox.md"
    inbox_path.write_text(prompt, encoding="utf-8")
    return inbox_path


def read_worker_inbox(cwd: str, team_name: str, worker_name: str) -> str | None:
    """Read the worker's inbox.md file."""
    path = _worker_dir(cwd, team_name, worker_name) / "inbox.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


# --- Worker Status ---


def write_worker_status(
    cwd: str,
    team_name: str,
    worker_name: str,
    state: str,
    current_task_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Write the worker's status.json."""
    d = _worker_dir(cwd, team_name, worker_name)
    status = {
        "state": state,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if current_task_id:
        status["current_task_id"] = current_task_id
    if reason:
        status["reason"] = reason
    (d / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")


def read_worker_status(
    cwd: str, team_name: str, worker_name: str
) -> dict[str, Any] | None:
    """Read the worker's status.json."""
    path = _worker_dir(cwd, team_name, worker_name) / "status.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# --- Worker Heartbeat ---


def write_worker_heartbeat(
    cwd: str,
    team_name: str,
    worker_name: str,
    pid: int,
    turn_count: int = 0,
) -> None:
    """Write the worker's heartbeat.json."""
    d = _worker_dir(cwd, team_name, worker_name)
    heartbeat = {
        "pid": pid,
        "last_turn_at": datetime.now(timezone.utc).isoformat(),
        "turn_count": turn_count,
        "alive": True,
    }
    (d / "heartbeat.json").write_text(json.dumps(heartbeat, indent=2), encoding="utf-8")


def read_worker_heartbeat(
    cwd: str, team_name: str, worker_name: str
) -> dict[str, Any] | None:
    """Read the worker's heartbeat.json."""
    path = _worker_dir(cwd, team_name, worker_name) / "heartbeat.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# --- Events ---


def append_team_event(cwd: str, event: TeamEvent, team_name: str = "default") -> None:
    """Append a team event to the JSONL event log."""
    d = _ensure_team_dir(cwd, team_name)
    events_path = d / "events.jsonl"
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event.to_dict()) + "\n")


def read_team_events(cwd: str, team_name: str = "default") -> list[TeamEvent]:
    """Read all team events from the JSONL event log."""
    path = _team_dir(cwd, team_name) / "events.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                d = json.loads(line)
                events.append(
                    TeamEvent(
                        event_type=d["event_type"],
                        timestamp=d["timestamp"],
                        worker_id=d.get("worker_id"),
                        task_id=d.get("task_id"),
                        detail=d.get("detail", {}),
                    )
                )
            except (json.JSONDecodeError, KeyError):
                pass
    return events
