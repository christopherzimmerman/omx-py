"""OMX State Management MCP Server.

Provides state read/write/clear/list tools for workflow modes.
Storage: .omx/state/{mode}-state.json

Port of src/mcp/state-server.ts.
Can be run as: python -m omx.mcp.state_server
"""

from __future__ import annotations

from typing import Any

from omx.mcp.bootstrap import auto_start_stdio_server
from omx.mcp.protocol import McpServer
from omx.state.operations import (
    SUPPORTED_MODES,
    state_clear,
    state_get_status,
    state_list_active,
    state_read,
    state_write,
)
from omx.state.paths import resolve_working_directory, validate_session_id


def build_state_server_tools() -> list[dict[str, Any]]:
    """Build the tool definitions for the state server."""
    return [
        {
            "name": "state_read",
            "description": "Read state for a specific mode. Returns JSON state data or indicates no state exists.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": SUPPORTED_MODES},
                    "workingDirectory": {"type": "string"},
                    "session_id": {"type": "string"},
                },
                "required": ["mode"],
            },
        },
        {
            "name": "state_write",
            "description": "Write/update state for a specific mode. Creates directories if needed.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": SUPPORTED_MODES},
                    "active": {"type": "boolean"},
                    "iteration": {"type": "number"},
                    "max_iterations": {"type": "number"},
                    "current_phase": {"type": "string"},
                    "task_description": {"type": "string"},
                    "started_at": {"type": "string"},
                    "completed_at": {"type": "string"},
                    "run_outcome": {
                        "type": "string",
                        "enum": [
                            "continue",
                            "finish",
                            "blocked_on_user",
                            "failed",
                            "cancelled",
                        ],
                    },
                    "error": {"type": "string"},
                    "state": {"type": "object"},
                    "workingDirectory": {"type": "string"},
                    "session_id": {"type": "string"},
                },
                "required": ["mode"],
            },
        },
        {
            "name": "state_clear",
            "description": "Clear/delete state for a specific mode.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": SUPPORTED_MODES},
                    "workingDirectory": {"type": "string"},
                    "session_id": {"type": "string"},
                    "all_sessions": {"type": "boolean"},
                },
                "required": ["mode"],
            },
        },
        {
            "name": "state_list_active",
            "description": "List all currently active modes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workingDirectory": {"type": "string"},
                    "session_id": {"type": "string"},
                },
            },
        },
        {
            "name": "state_get_status",
            "description": "Get detailed status for a specific mode or all modes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "mode": {"type": "string", "enum": SUPPORTED_MODES},
                    "workingDirectory": {"type": "string"},
                    "session_id": {"type": "string"},
                },
            },
        },
    ]


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Handle a state server tool call."""
    try:
        cwd = str(resolve_working_directory(args.get("workingDirectory")))
        session_id = validate_session_id(args.get("session_id"))
    except ValueError as exc:
        return _error_response(str(exc))

    try:
        match name:
            case "state_read":
                mode = args.get("mode", "")
                if mode not in SUPPORTED_MODES:
                    return _error_response(
                        f"mode must be one of: {', '.join(SUPPORTED_MODES)}"
                    )
                result = state_read(mode, cwd, session_id)
                return _text_response(result)

            case "state_write":
                mode = args.get("mode", "")
                # Extract known control keys, pass rest as fields
                fields = {
                    k: v
                    for k, v in args.items()
                    if k not in ("mode", "workingDirectory", "session_id")
                }
                # Merge custom 'state' dict into fields
                custom_state = fields.pop("state", None)
                if isinstance(custom_state, dict):
                    fields.update(custom_state)
                result = state_write(mode, cwd, fields, session_id)
                return _text_response(result)

            case "state_clear":
                mode = args.get("mode", "")
                all_sessions = args.get("all_sessions", False) is True
                result = state_clear(mode, cwd, session_id, all_sessions)
                return _text_response(result)

            case "state_list_active":
                result = state_list_active(cwd, session_id)
                return _text_response(result)

            case "state_get_status":
                mode = args.get("mode")
                result = state_get_status(cwd, session_id, mode)
                return _text_response(result)

            case _:
                return _error_response(f"Unknown tool: {name}")
    except Exception as exc:
        return _error_response(str(exc))


def _text_response(data: Any) -> dict[str, Any]:
    import json

    return {"content": [{"type": "text", "text": json.dumps(data)}]}


def _error_response(message: str) -> dict[str, Any]:
    import json

    return {
        "content": [{"type": "text", "text": json.dumps({"error": message})}],
        "isError": True,
    }


def main() -> None:
    """Entry point for running as a standalone MCP server."""
    server = McpServer("omx-state", "0.1.0")
    server.set_tool_lister(build_state_server_tools)
    server.set_tool_handler(handle_tool_call)
    auto_start_stdio_server("state", server)


if __name__ == "__main__":
    main()
