"""Team delivery event logging.

Port of src/team/delivery-log.ts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.utils.paths import omx_logs_dir


def append_delivery_event(
    cwd: str,
    event: str,
    *,
    source: str = "",
    team: str = "",
    transport: str = "send-keys",
    result: str = "ok",
    detail: dict[str, Any] | None = None,
) -> None:
    """Append a delivery event to the daily team delivery JSONL log.

    Args:
        cwd: Working directory for log resolution.
        event: Event name (e.g. "dispatch", "confirm").
        source: Source identifier for the event.
        team: Team session name.
        transport: Delivery transport used (default "send-keys").
        result: Outcome string (default "ok").
        detail: Optional additional metadata.
    """
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = omx_logs_dir(Path(cwd)) / f"team-delivery-{date}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": "team_delivery",
        "event": event,
        "source": source,
        "team": team,
        "transport": transport,
        "result": result,
    }
    if detail:
        entry["detail"] = detail

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
