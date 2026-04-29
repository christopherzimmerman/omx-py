"""Regression tests for MCP protocol: NDJSON, Content-Length, ping, version negotiation."""

import json
import unittest

from omx.mcp.protocol import (
    LATEST_PROTOCOL_VERSION,
    McpServer,
)


class TestProtocolVersionNegotiation(unittest.TestCase):
    def _make_server(self) -> McpServer:
        server = McpServer("test", "0.1.0")
        server.set_tool_lister(
            lambda: [{"name": "echo", "inputSchema": {"type": "object"}}]
        )
        server.set_tool_handler(
            lambda name, args: {"content": [{"type": "text", "text": "ok"}]}
        )
        return server

    def test_initialize_returns_latest_version_by_default(self):
        server = self._make_server()
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            }
        )
        self.assertEqual(resp["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION)

    def test_initialize_negotiates_requested_version(self):
        server = self._make_server()
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        self.assertEqual(resp["result"]["protocolVersion"], "2024-11-05")

    def test_initialize_falls_back_on_unknown_version(self):
        server = self._make_server()
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "9999-01-01"},
            }
        )
        self.assertEqual(resp["result"]["protocolVersion"], LATEST_PROTOCOL_VERSION)

    def test_initialize_includes_server_info(self):
        server = self._make_server()
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            }
        )
        self.assertEqual(resp["result"]["serverInfo"]["name"], "test")
        self.assertIn("capabilities", resp["result"])


class TestPing(unittest.TestCase):
    def test_ping_returns_empty_result(self):
        server = McpServer("test", "0.1.0")
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 42,
                "method": "ping",
                "params": {},
            }
        )
        self.assertEqual(resp["id"], 42)
        self.assertEqual(resp["result"], {})

    def test_ping_without_params(self):
        server = McpServer("test", "0.1.0")
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "ping",
            }
        )
        self.assertEqual(resp["result"], {})


class TestToolsListUnchanged(unittest.TestCase):
    def test_tools_list_returns_registered_tools(self):
        server = McpServer("test", "0.1.0")
        tools = [
            {"name": "state_read", "inputSchema": {"type": "object"}},
            {"name": "state_write", "inputSchema": {"type": "object"}},
        ]
        server.set_tool_lister(lambda: tools)
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        )
        self.assertEqual(len(resp["result"]["tools"]), 2)
        self.assertEqual(resp["result"]["tools"][0]["name"], "state_read")

    def test_tools_list_empty_when_no_lister(self):
        server = McpServer("test", "0.1.0")
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {},
            }
        )
        self.assertEqual(resp["result"]["tools"], [])


class TestNotifications(unittest.TestCase):
    def test_notification_no_response(self):
        server = McpServer("test", "0.1.0")
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        self.assertIsNone(resp)

    def test_notification_without_id_returns_none(self):
        server = McpServer("test", "0.1.0")
        resp = server._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "ping",
                "params": {},
                # no "id" field
            }
        )
        self.assertIsNone(resp)


class TestNDJSONOutput(unittest.TestCase):
    def test_write_message_is_single_line(self):
        """Verify output is compact single-line JSON (NDJSON)."""
        import io

        from omx.mcp import protocol

        buf = io.BytesIO()
        original_stdout = protocol.sys.stdout
        try:
            mock_stdout = type(
                "MockStdout", (), {"buffer": buf, "flush": lambda self: None}
            )()
            protocol.sys.stdout = mock_stdout
            protocol._write_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
        finally:
            protocol.sys.stdout = original_stdout

        output = buf.getvalue().decode("utf-8")
        # Must be a single line ending with newline
        self.assertTrue(output.endswith("\n"))
        lines = output.strip().split("\n")
        self.assertEqual(len(lines), 1)
        # Must be valid JSON
        parsed = json.loads(lines[0])
        self.assertEqual(parsed["id"], 1)
        self.assertTrue(parsed["result"]["ok"])

    def test_output_has_no_content_length_header(self):
        """NDJSON output must NOT have Content-Length headers."""
        import io

        from omx.mcp import protocol

        buf = io.BytesIO()
        original_stdout = protocol.sys.stdout
        try:
            mock_stdout = type(
                "MockStdout", (), {"buffer": buf, "flush": lambda self: None}
            )()
            protocol.sys.stdout = mock_stdout
            protocol._write_message({"jsonrpc": "2.0", "id": 1, "result": {}})
        finally:
            protocol.sys.stdout = original_stdout

        output = buf.getvalue().decode("utf-8")
        self.assertNotIn("Content-Length", output)


class TestNDJSONInput(unittest.TestCase):
    def test_read_ndjson_message(self):
        """Verify NDJSON input is parsed correctly."""
        import io

        from omx.mcp import protocol

        msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        data = (json.dumps(msg) + "\n").encode("utf-8")

        original_stdin = protocol.sys.stdin
        try:
            mock_stdin = type("MockStdin", (), {"buffer": io.BytesIO(data)})()
            protocol.sys.stdin = mock_stdin
            result = protocol._read_message()
        finally:
            protocol.sys.stdin = original_stdin

        self.assertIsNotNone(result)
        self.assertEqual(result["method"], "ping")

    def test_read_content_length_message(self):
        """Verify legacy Content-Length input still works."""
        import io

        from omx.mcp import protocol

        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        framed = f"Content-Length: {len(body)}\r\n\r\n{body}".encode("utf-8")

        original_stdin = protocol.sys.stdin
        try:
            mock_stdin = type("MockStdin", (), {"buffer": io.BytesIO(framed)})()
            protocol.sys.stdin = mock_stdin
            result = protocol._read_message()
        finally:
            protocol.sys.stdin = original_stdin

        self.assertIsNotNone(result)
        self.assertEqual(result["method"], "initialize")

    def test_read_skips_blank_lines(self):
        """NDJSON reader should skip blank lines between messages."""
        import io

        from omx.mcp import protocol

        msg = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        data = ("\n\n" + json.dumps(msg) + "\n").encode("utf-8")

        original_stdin = protocol.sys.stdin
        try:
            mock_stdin = type("MockStdin", (), {"buffer": io.BytesIO(data)})()
            protocol.sys.stdin = mock_stdin
            result = protocol._read_message()
        finally:
            protocol.sys.stdin = original_stdin

        self.assertIsNotNone(result)
        self.assertEqual(result["method"], "ping")


if __name__ == "__main__":
    unittest.main()
