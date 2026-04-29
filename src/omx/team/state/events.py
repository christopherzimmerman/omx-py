"""Team event logging and querying.

Port of src/team/state/events.ts.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _events_path(team_dir: Path) -> Path:
    return team_dir / "events" / "events.ndjson"


def append_team_event(team_dir: Path, event: dict[str, Any]) -> None:
    """Append an event to the team event log (NDJSON).

    Args:
        team_dir: Path to team state directory.
        event: Event dict to append.
    """
    path = _events_path(team_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def read_team_events(
    team_dir: Path,
    *,
    after_event_id: str | None = None,
    event_type: str | None = None,
    worker: str | None = None,
    task_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read and filter team events from the NDJSON log.

    Args:
        team_dir: Path to team state directory.
        after_event_id: Only return events after this cursor.
        event_type: Filter by event type.
        worker: Filter by worker name.
        task_id: Filter by task ID.

    Returns:
        List of matching event dicts.
    """
    path = _events_path(team_dir)
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    found_cursor = after_event_id is None

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        if not found_cursor:
            if event.get("event_id") == after_event_id:
                found_cursor = True
            continue

        if event_type and event.get("type") != event_type:
            continue
        if worker and event.get("worker") != worker:
            continue
        if task_id and event.get("task_id") != task_id:
            continue

        events.append(event)

    return events


def get_latest_event_cursor(team_dir: Path) -> str | None:
    """Get the event_id of the most recent event.

    Args:
        team_dir: Path to team state directory.

    Returns:
        The latest event_id, or None if no events.
    """
    path = _events_path(team_dir)
    if not path.exists():
        return None

    last_line = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last_line = line.strip()

    if not last_line:
        return None
    try:
        return json.loads(last_line).get("event_id")
    except json.JSONDecodeError:
        return None


def wait_for_team_event(
    team_dir: Path,
    *,
    after_event_id: str | None = None,
    timeout_ms: int = 30_000,
    poll_ms: int = 100,
    event_type: str | None = None,
    worker: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Poll for a matching team event with timeout.

    Args:
        team_dir: Path to team state directory.
        after_event_id: Only check events after this cursor.
        timeout_ms: Maximum wait time.
        poll_ms: Initial polling interval.
        event_type: Filter by event type.
        worker: Filter by worker.
        task_id: Filter by task ID.

    Returns:
        Dict with "status" ("event" or "timeout"), optional "event", and "cursor".
    """
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    delay = poll_ms / 1000.0

    while time.monotonic() < deadline:
        events = read_team_events(
            team_dir,
            after_event_id=after_event_id,
            event_type=event_type,
            worker=worker,
            task_id=task_id,
        )
        if events:
            return {
                "status": "event",
                "event": events[0],
                "cursor": events[-1].get("event_id", ""),
            }
        time.sleep(delay)
        delay = min(delay * 2, 0.5)

    cursor = get_latest_event_cursor(team_dir) or ""
    return {"status": "timeout", "cursor": cursor}
