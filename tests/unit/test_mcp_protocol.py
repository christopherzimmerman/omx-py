"""Tests for omx.mcp.protocol — JSON-RPC 2.0 over stdio."""

import json
import unittest

from omx.mcp.protocol import McpServer


class TestMcpServer(unittest.TestCase):
    def _make_server(self):
        server = McpServer("test-server", "0.1.0")
        server.set_tool_lister(
            lambda: [
                {
                    "name": "echo",
                    "description": "Echo tool",
                    "inputSchema": {"type": "object"},
                },
            ]
        )
        server.set_tool_handler(
            lambda name, args: {
                "content": [{"type": "text", "text": json.dumps({"echoed": args})}]
            }
        )
        return server

    def test_handle_initialize(self):
        server = self._make_server()
        response = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {},
            }
        )
        self.assertEqual(response["id"], 1)
        result = response["result"]
        self.assertEqual(result["serverInfo"]["name"], "test-server")
        self.assertIn("capabilities", result)

    def test_handle_tools_list(self):
        server = self._make_server()
        response = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
                "params": {},
            }
        )
        tools = response["result"]["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "echo")

    def test_handle_tools_call(self):
        server = self._make_server()
        response = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"msg": "hello"}},
            }
        )
        result = response["result"]
        text = json.loads(result["content"][0]["text"])
        self.assertEqual(text["echoed"]["msg"], "hello")

    def test_unknown_method_returns_error(self):
        server = self._make_server()
        response = server._handle_message(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "nonexistent",
                "params": {},
            }
        )
        self.assertIn("error", response)

    def test_notification_returns_none(self):
        server = self._make_server()
        response = server._handle_message(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
        )
        self.assertIsNone(response)


if __name__ == "__main__":
    unittest.main()
