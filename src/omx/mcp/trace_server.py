"""OMX Trace MCP Server.

Provides trace timeline and summary tools for debugging agent flows.
Reads .omx/logs/ turn JSONL files produced by the notify hook.

Port of src/mcp/trace-server.ts.
Can be run as: python -m omx.mcp.trace_server
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omx.mcp.bootstrap import auto_start_stdio_server
from omx.mcp.protocol import McpServer
from omx.state.paths import resolve_working_directory


def _text_response(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


def _error_response(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps({"error": message})}],
        "isError": True,
    }


def _iterate_log_entries(logs_dir: Path) -> list[dict[str, Any]]:
    """Read all trace entries from JSONL turn files.

    Args:
        logs_dir: Path to the logs directory.

    Returns:
        List of parsed trace entries.
    """
    if not logs_dir.exists():
        return []

    entries: list[dict[str, Any]] = []
    files = sorted(
        f
        for f in logs_dir.iterdir()
        if f.name.startswith("turns-") and f.name.endswith(".jsonl")
    )

    for file_path in files:
        try:
            for line in file_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass

    return entries


def read_log_files(logs_dir: Path, last: int | None = None) -> list[dict[str, Any]]:
    """Read trace log files, optionally keeping only the last N entries.

    Args:
        logs_dir: Path to the logs directory.
        last: If set, return only the last N entries.

    Returns:
        Sorted list of trace entries.
    """
    entries = _iterate_log_entries(logs_dir)
    entries.sort(key=lambda e: e.get("timestamp", ""))

    if last and last > 0:
        return entries[-last:]
    return entries


def summarize_log_files(logs_dir: Path) -> dict[str, Any]:
    """Summarize trace log files.

    Args:
        logs_dir: Path to the logs directory.

    Returns:
        Summary dict with counts and timestamps.
    """
    turns_by_type: dict[str, int] = {}
    total_turns = 0
    first_timestamp: str | None = None
    last_timestamp: str | None = None

    for entry in _iterate_log_entries(logs_dir):
        total_turns += 1
        entry_type = entry.get("type", "unknown")
        turns_by_type[entry_type] = turns_by_type.get(entry_type, 0) + 1

        timestamp = entry.get("timestamp", "")
        if not timestamp:
            continue
        if first_timestamp is None or timestamp < first_timestamp:
            first_timestamp = timestamp
        if last_timestamp is None or timestamp > last_timestamp:
            last_timestamp = timestamp

    return {
        "totalTurns": total_turns,
        "turnsByType": turns_by_type,
        "firstTimestamp": first_timestamp,
        "lastTimestamp": last_timestamp,
    }


def build_trace_server_tools() -> list[dict[str, Any]]:
    """Build the tool definitions for the trace server."""
    return [
        {
            "name": "trace_timeline",
            "description": "Show chronological agent flow trace timeline.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "last": {
                        "type": "number",
                        "description": "Show only the last N entries",
                    },
                    "filter": {
                        "type": "string",
                        "enum": ["all", "turns", "modes"],
                        "description": "Filter: all (default), turns, modes",
                    },
                    "workingDirectory": {"type": "string"},
                },
            },
        },
        {
            "name": "trace_summary",
            "description": "Show aggregate statistics for agent flow trace.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workingDirectory": {"type": "string"},
                },
            },
        },
    ]


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Handle a trace server tool call.

    Args:
        name: Tool name.
        args: Tool arguments.

    Returns:
        MCP tool response dict.
    """
    try:
        wd = str(resolve_working_directory(args.get("workingDirectory")))
    except ValueError as exc:
        return _error_response(str(exc))

    omx_dir = Path(wd) / ".omx"
    logs_dir = omx_dir / "logs"

    match name:
        case "trace_timeline":
            last = args.get("last")
            if isinstance(last, (int, float)):
                last = int(last)
            else:
                last = None
            filter_type = args.get("filter", "all")

            turns = read_log_files(logs_dir, last) if filter_type != "modes" else []

            timeline: list[dict[str, Any]] = []
            for t in turns:
                timeline.append(
                    {
                        "timestamp": t.get("timestamp", ""),
                        "type": "turn",
                        "turn_type": t.get("type"),
                        "thread_id": t.get("thread_id"),
                        "input_preview": t.get("input_preview"),
                        "output_preview": t.get("output_preview"),
                    }
                )

            timeline.sort(key=lambda e: e.get("timestamp", ""))
            result = timeline[-last:] if last else timeline

            return _text_response(
                {
                    "entryCount": len(result),
                    "totalAvailable": len(timeline),
                    "filter": filter_type,
                    "timeline": result,
                }
            )

        case "trace_summary":
            log_summary = summarize_log_files(logs_dir)

            # Read metrics
            metrics: dict[str, Any] | None = None
            metrics_path = omx_dir / "metrics.json"
            if metrics_path.exists():
                try:
                    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

            first_turn = log_summary.get("firstTimestamp")
            last_turn = log_summary.get("lastTimestamp")
            duration_ms = 0
            if first_turn and last_turn:
                from datetime import datetime

                try:
                    t1 = datetime.fromisoformat(first_turn.replace("Z", "+00:00"))
                    t2 = datetime.fromisoformat(last_turn.replace("Z", "+00:00"))
                    duration_ms = int((t2 - t1).total_seconds() * 1000)
                except (ValueError, AttributeError):
                    pass

            duration_formatted = "N/A"
            if duration_ms > 0:
                minutes = duration_ms // 60000
                seconds = (duration_ms % 60000) // 1000
                duration_formatted = f"{minutes}m {seconds}s"

            return _text_response(
                {
                    "turns": {
                        "total": log_summary["totalTurns"],
                        "byType": log_summary["turnsByType"],
                        "firstAt": first_turn,
                        "lastAt": last_turn,
                        "durationMs": duration_ms,
                        "durationFormatted": duration_formatted,
                    },
                    "metrics": metrics or {"note": "No metrics file found"},
                }
            )

        case _:
            return _error_response(f"Unknown tool: {name}")


def main() -> None:
    """Entry point for running as a standalone MCP server."""
    server = McpServer("omx-trace", "0.1.0")
    server.set_tool_lister(build_trace_server_tools)
    server.set_tool_handler(handle_tool_call)
    auto_start_stdio_server("trace", server)


if __name__ == "__main__":
    main()
