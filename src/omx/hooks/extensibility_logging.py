"""Hook extensibility logging.

Port of src/hooks/extensibility/logging.ts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def hook_log_path(cwd: str, timestamp: datetime | None = None) -> str:
    """Compute the log file path for a given date.

    Args:
        cwd: Working directory.
        timestamp: Timestamp for the log filename (defaults to now).

    Returns:
        Absolute path to the JSONL log file.
    """
    ts = timestamp or datetime.now(timezone.utc)
    date_str = ts.strftime("%Y-%m-%d")
    return str(Path(cwd) / ".omx" / "logs" / f"hooks-{date_str}.jsonl")


def append_hook_plugin_log(cwd: str, entry: dict[str, Any]) -> None:
    """Append a hook plugin log entry to the daily JSONL file.

    Args:
        cwd: Working directory.
        entry: Log entry dict (should contain ``timestamp`` key).
    """
    ts_str = entry.get("timestamp")
    if isinstance(ts_str, str):
        try:
            ts = datetime.fromisoformat(ts_str)
        except ValueError:
            ts = datetime.now(timezone.utc)
    else:
        ts = datetime.now(timezone.utc)

    path = hook_log_path(cwd, ts)
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": entry.get("timestamp")
            or datetime.now(timezone.utc).isoformat(),
            **entry,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload) + "\n")
    except OSError as exc:
        import sys

        print(
            f"[omx] warning: failed to append hook plugin log entry path={path} error={exc}",
            file=sys.stderr,
        )
