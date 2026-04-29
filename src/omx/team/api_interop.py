"""MCP-aligned gateway for team operations.

Port of src/team/api-interop.ts.
Provides the interface between MCP tools and team state operations.
"""

from __future__ import annotations

from typing import Any

LEGACY_TEAM_MCP_TOOLS = [
    "team_create_task",
    "team_list_tasks",
    "team_complete_task",
    "team_fail_task",
    "team_send_message",
    "team_read_mailbox",
    "team_get_status",
    "team_list_workers",
]


def build_legacy_team_deprecation_hint(tool_name: str, args: dict[str, Any]) -> str:
    """Build a deprecation hint for legacy team MCP tools.

    Args:
        tool_name: The deprecated tool name.
        args: Arguments that were passed.

    Returns:
        Hint string explaining how to accomplish the task via CLI.
    """
    hints: dict[str, str] = {
        "team_create_task": "Use 'omx team' CLI to create tasks",
        "team_list_tasks": "Read .omx/team/{name}/tasks.json directly",
        "team_complete_task": "Write to worker status.json to report completion",
        "team_fail_task": "Write to worker status.json to report failure",
        "team_send_message": "Write to .omx/team/{name}/mailbox/{worker}.json",
        "team_read_mailbox": "Read .omx/team/{name}/mailbox/{worker}.json",
        "team_get_status": "Read .omx/team/{name}/workers/{worker}/status.json",
        "team_list_workers": "Read .omx/team/{name}/workers.json",
    }
    return hints.get(
        tool_name, f"Tool '{tool_name}' is deprecated. Use CLI interop instead."
    )


def handle_team_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Handle a team MCP tool call.

    For legacy tools, returns deprecation notice.
    For supported operations, delegates to team_ops.

    Args:
        name: Tool name.
        args: Tool arguments.

    Returns:
        Response dict with content.
    """
    import json

    if name in LEGACY_TEAM_MCP_TOOLS:
        hint = build_legacy_team_deprecation_hint(name, args)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "error": f'MCP tool "{name}" is hard-deprecated. Team mutations now require CLI interop.',
                            "code": "deprecated_cli_only",
                            "hint": hint,
                        }
                    ),
                }
            ],
            "isError": True,
        }

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"error": f"Unknown team tool: {name}"}),
            }
        ],
        "isError": True,
    }
