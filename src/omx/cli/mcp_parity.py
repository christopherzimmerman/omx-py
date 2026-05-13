"""``omx mcp-parity`` — CLI surface that mirrors MCP tool calls.

Port of ``src/cli/mcp-parity.ts``. Sync, stdlib-only.

Usage:
    omx mcp-parity <server> <tool-name> [--input <json>] [--json]

Supported servers: ``state``, ``notepad``, ``project-memory``, ``trace``,
``code-intel``, ``wiki`` (when the wiki server is available).

The handler imports the appropriate MCP server module and invokes its
``handle_tool_call`` (the matching MCP entry point in this Python port).
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

McpServerName = str

SUPPORTED_SERVERS = {
    "state",
    "notepad",
    "project-memory",
    "trace",
    "code-intel",
    "wiki",
}


def _load_handler(server: str) -> tuple[Callable[..., Any], dict[str, str]]:
    """Import an MCP server's ``handle_tool_call`` and per-server alias map.

    Returns:
        ``(handle_tool_call, aliases)`` where ``aliases`` maps user-friendly
        short names (``"read"``) to the canonical MCP tool name
        (``"state_read"``).
    """
    if server == "state":
        from omx.mcp.state_server import handle_tool_call

        return handle_tool_call, {
            "read": "state_read",
            "write": "state_write",
            "clear": "state_clear",
            "list-active": "state_list_active",
            "get-status": "state_get_status",
        }
    if server == "notepad":
        from omx.mcp.memory_server import handle_tool_call

        return handle_tool_call, {
            "read": "notepad_read",
            "write-priority": "notepad_write_priority",
            "write-working": "notepad_write_working",
            "write-manual": "notepad_write_manual",
            "prune": "notepad_prune",
            "stats": "notepad_stats",
        }
    if server == "project-memory":
        from omx.mcp.memory_server import handle_tool_call

        return handle_tool_call, {
            "read": "project_memory_read",
            "write": "project_memory_write",
            "add-note": "project_memory_add_note",
            "add-directive": "project_memory_add_directive",
        }
    if server == "trace":
        from omx.mcp.trace_server import handle_tool_call

        return handle_tool_call, {
            "timeline": "trace_timeline",
            "summary": "trace_summary",
        }
    if server == "code-intel":
        from omx.mcp.code_intel_server import handle_tool_call

        return handle_tool_call, {}
    if server == "wiki":
        from omx.mcp.wiki_server import handle_tool_call

        return handle_tool_call, {
            "ingest": "wiki_ingest",
            "query": "wiki_query",
            "lint": "wiki_lint",
            "add": "wiki_add",
            "list": "wiki_list",
            "read": "wiki_read",
            "delete": "wiki_delete",
            "refresh": "wiki_refresh",
        }
    raise ValueError(f"Unknown mcp-parity server: {server}")


def parse_mcp_parity_args(
    args: list[str],
) -> tuple[str | None, dict[str, Any], bool, bool]:
    """Parse ``omx mcp-parity`` args.

    Returns:
        ``(tool_name, input_dict, json_flag, help_flag)``. ``tool_name`` is
        ``None`` when help is requested or no tool was supplied.
    """
    if not args or args[0] in ("--help", "-h", "help"):
        return None, {}, False, True

    tool_name = args[0]
    input_obj: dict[str, Any] = {}
    json_flag = False

    i = 1
    while i < len(args):
        token = args[i]
        if token == "--json":
            json_flag = True
            i += 1
            continue
        if token == "--input":
            if i + 1 >= len(args):
                raise ValueError("Missing value for --input")
            raw = args[i + 1]
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("--input must be a JSON object")
            input_obj = parsed
            i += 2
            continue
        if token.startswith("--input="):
            parsed = json.loads(token[len("--input=") :])
            if not isinstance(parsed, dict):
                raise ValueError("--input must be a JSON object")
            input_obj = parsed
            i += 1
            continue
        if token in ("--help", "-h", "help"):
            return None, {}, json_flag, True
        raise ValueError(f"Unknown argument: {token}")

    return tool_name, input_obj, json_flag, False


def _extract_payload(result: Any) -> Any:
    """Extract a JSON-ish payload from an MCP tool result envelope."""
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            texts = [
                entry.get("text", "")
                for entry in content
                if isinstance(entry, dict)
                and entry.get("type") == "text"
                and isinstance(entry.get("text"), str)
            ]
            joined = "\n".join(t for t in texts).strip()
            if joined:
                try:
                    return json.loads(joined)
                except json.JSONDecodeError:
                    return joined
    return result


def handle_mcp_parity(args: list[str]) -> None:
    """Top-level handler for ``omx mcp-parity <server> [...]``."""
    if not args or args[0] in ("--help", "-h", "help"):
        print(
            "Usage: omx mcp-parity <server> <tool-name> [--input <json>] [--json]\n"
            f"Supported servers: {', '.join(sorted(SUPPORTED_SERVERS))}"
        )
        return

    server = args[0]
    if server not in SUPPORTED_SERVERS:
        print(
            f"Error: unknown server '{server}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_SERVERS))}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        handler, aliases = _load_handler(server)
    except (ImportError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        tool_name, tool_input, json_flag, help_flag = parse_mcp_parity_args(args[1:])
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if help_flag or tool_name is None:
        print(f"Available {server} tools/aliases:")
        for alias, canonical in sorted(aliases.items()):
            print(f"  {alias} -> {canonical}")
        return

    canonical = aliases.get(tool_name, tool_name)

    if "workingDirectory" not in tool_input:
        tool_input["workingDirectory"] = os.getcwd()

    try:
        result = handler(canonical, tool_input)
    except Exception as exc:  # noqa: BLE001
        print(f"Error invoking {canonical}: {exc}", file=sys.stderr)
        sys.exit(1)

    payload = _extract_payload(result)
    is_error = isinstance(result, dict) and result.get("isError") is True

    if json_flag:
        print(json.dumps(payload))
    elif isinstance(payload, str):
        print(payload)
    else:
        print(json.dumps(payload, indent=2))

    if is_error:
        sys.exit(1)
