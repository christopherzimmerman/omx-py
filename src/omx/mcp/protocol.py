"""JSON-RPC 2.0 over stdio transport for MCP servers.

Implements the MCP protocol without any external SDK dependency.
Reads Content-Length framed JSON from stdin, dispatches to handlers,
writes responses to stdout.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable


ToolHandler = Callable[[str, dict[str, Any]], dict[str, Any]]
ToolLister = Callable[[], list[dict[str, Any]]]


class McpServer:
    """Minimal MCP server using JSON-RPC 2.0 over stdio."""

    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        self._tool_handler: ToolHandler | None = None
        self._tool_lister: ToolLister | None = None

    def set_tool_handler(self, handler: ToolHandler) -> None:
        """Register the handler called when tools/call is invoked."""
        self._tool_handler = handler

    def set_tool_lister(self, lister: ToolLister) -> None:
        """Register the handler called when tools/list is invoked."""
        self._tool_lister = lister

    def run(self) -> None:
        """Run the server, reading from stdin and writing to stdout."""
        while True:
            message = _read_message()
            if message is None:
                break
            response = self._handle_message(message)
            if response is not None:
                _write_message(response)

    def _handle_message(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method", "")
        msg_id = message.get("id")
        params = message.get("params", {})

        # Notifications (no id) don't get responses
        if msg_id is None:
            return None

        try:
            result = self._dispatch(method, params)
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": str(exc)},
            }

    def _dispatch(self, method: str, params: dict[str, Any]) -> Any:
        match method:
            case "initialize":
                return {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.name, "version": self.version},
                }
            case "notifications/initialized":
                return None
            case "tools/list":
                if self._tool_lister:
                    return {"tools": self._tool_lister()}
                return {"tools": []}
            case "tools/call":
                if self._tool_handler is None:
                    raise ValueError("no tool handler registered")
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {})
                return self._tool_handler(tool_name, tool_args)
            case _:
                raise ValueError(f"unknown method: {method}")


def _read_message() -> dict[str, Any] | None:
    """Read a Content-Length framed JSON-RPC message from stdin."""
    stdin = sys.stdin.buffer
    content_length = -1

    while True:
        line = stdin.readline()
        if not line:
            return None
        header = line.decode("utf-8").strip()
        if not header:
            break
        if header.lower().startswith("content-length:"):
            content_length = int(header.split(":", 1)[1].strip())

    if content_length < 0:
        return None

    body = stdin.read(content_length)
    if not body:
        return None

    return json.loads(body.decode("utf-8"))


def _write_message(message: dict[str, Any]) -> None:
    """Write a Content-Length framed JSON-RPC message to stdout."""
    body = json.dumps(message).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8")
    sys.stdout.buffer.write(header + body)
    sys.stdout.buffer.flush()
