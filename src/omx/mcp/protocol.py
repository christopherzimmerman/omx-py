"""JSON-RPC 2.0 over stdio transport for MCP servers.

Supports two framing modes:
- Newline-delimited JSON (NDJSON) — primary, one JSON object per line
- Content-Length framing — legacy, accepted for backward compatibility

Output always uses NDJSON (one JSON line per message).

Also supports protocol version negotiation and ping/pong.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

# Protocol versions we support, newest first
SUPPORTED_PROTOCOL_VERSIONS = ["2025-03-26", "2024-11-05"]
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]

ToolHandler = Callable[[str, dict[str, Any]], dict[str, Any]]
ToolLister = Callable[[], list[dict[str, Any]]]


class McpServer:
    """Minimal MCP server using JSON-RPC 2.0 over stdio.

    Reads NDJSON or Content-Length framed messages from stdin.
    Writes NDJSON (one JSON object per line) to stdout.
    Supports protocol version negotiation and ping keepalives.
    """

    def __init__(self, name: str, version: str) -> None:
        self.name = name
        self.version = version
        self._tool_handler: ToolHandler | None = None
        self._tool_lister: ToolLister | None = None
        self._negotiated_version: str = LATEST_PROTOCOL_VERSION

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
        """Process a single JSON-RPC message and return the response.

        Args:
            message: Parsed JSON-RPC message dict.

        Returns:
            Response dict, or None for notifications.
        """
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
        """Dispatch a JSON-RPC method to the appropriate handler.

        Args:
            method: JSON-RPC method name.
            params: Method parameters.

        Returns:
            Result value for the response.

        Raises:
            ValueError: If the method is unknown and not a notification.
        """
        match method:
            case "initialize":
                return self._handle_initialize(params)
            case "notifications/initialized":
                return None
            case "ping":
                return {}
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

    def _handle_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        """Handle the initialize request with protocol version negotiation.

        Selects the best mutually supported protocol version. If the client
        requests a version we support, use it. Otherwise fall back to our
        latest supported version.

        Args:
            params: Initialize parameters, may contain "protocolVersion".

        Returns:
            Initialize result with negotiated version and capabilities.
        """
        requested = params.get("protocolVersion", "")
        if requested in SUPPORTED_PROTOCOL_VERSIONS:
            self._negotiated_version = requested
        else:
            self._negotiated_version = LATEST_PROTOCOL_VERSION

        return {
            "protocolVersion": self._negotiated_version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": self.name, "version": self.version},
        }


def _read_message() -> dict[str, Any] | None:
    """Read a JSON-RPC message from stdin.

    Supports two framing modes:
    - NDJSON: a single line containing a complete JSON object
    - Content-Length: HTTP-style headers followed by a JSON body

    Legacy Content-Length framing is auto-detected when the first line
    starts with "Content-Length:".

    Returns:
        Parsed message dict, or None on EOF.
    """
    stdin = sys.stdin.buffer

    while True:
        line = stdin.readline()
        if not line:
            return None

        decoded = line.decode("utf-8", errors="replace").strip()
        if not decoded:
            continue  # skip blank lines between messages

        # Content-Length framing (legacy)
        if decoded.lower().startswith("content-length:"):
            return _read_content_length_body(stdin, decoded)

        # NDJSON — the line itself is the JSON message
        try:
            return json.loads(decoded)
        except json.JSONDecodeError:
            continue  # skip malformed lines


def _read_content_length_body(
    stdin: Any,
    first_header: str,
) -> dict[str, Any] | None:
    """Read the body of a Content-Length framed message.

    Args:
        stdin: Binary stdin stream.
        first_header: The already-read first header line (decoded, stripped).

    Returns:
        Parsed message dict, or None on error.
    """
    content_length = int(first_header.split(":", 1)[1].strip())

    # Read remaining headers until empty line
    while True:
        line = stdin.readline()
        if not line:
            return None
        if not line.decode("utf-8", errors="replace").strip():
            break

    if content_length <= 0:
        return None

    body = stdin.read(content_length)
    if not body:
        return None

    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def _write_message(message: dict[str, Any]) -> None:
    """Write a JSON-RPC message to stdout using NDJSON framing.

    Each message is a single line of compact JSON followed by a newline.

    Args:
        message: JSON-RPC response dict.
    """
    line = json.dumps(message, separators=(",", ":")) + "\n"
    sys.stdout.buffer.write(line.encode("utf-8"))
    sys.stdout.buffer.flush()
