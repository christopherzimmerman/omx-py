"""Tests for omx.mcp.state_server — tool call handling."""

import json
import tempfile
import unittest

from omx.mcp.state_server import build_state_server_tools, handle_tool_call


class TestStateServer(unittest.TestCase):
    def test_build_tools_returns_all_five(self):
        tools = build_state_server_tools()
        names = [t["name"] for t in tools]
        self.assertEqual(len(names), 5)
        self.assertIn("state_read", names)
        self.assertIn("state_write", names)
        self.assertIn("state_clear", names)
        self.assertIn("state_list_active", names)
        self.assertIn("state_get_status", names)

    def test_state_read_nonexistent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call(
                "state_read",
                {
                    "mode": "autopilot",
                    "workingDirectory": tmpdir,
                },
            )
            text = json.loads(result["content"][0]["text"])
            self.assertFalse(text.get("exists", True))

    def test_state_write_then_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "state_write",
                {
                    "mode": "autopilot",
                    "active": True,
                    "current_phase": "running",
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "state_read",
                {
                    "mode": "autopilot",
                    "workingDirectory": tmpdir,
                },
            )
            text = json.loads(result["content"][0]["text"])
            self.assertTrue(text.get("active"))

    def test_state_clear(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "state_write",
                {
                    "mode": "autopilot",
                    "active": True,
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "state_clear",
                {
                    "mode": "autopilot",
                    "workingDirectory": tmpdir,
                },
            )
            text = json.loads(result["content"][0]["text"])
            self.assertTrue(text["cleared"])

    def test_state_list_active(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "state_write",
                {
                    "mode": "autopilot",
                    "active": True,
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "state_list_active",
                {
                    "workingDirectory": tmpdir,
                },
            )
            text = json.loads(result["content"][0]["text"])
            self.assertIn("autopilot", text["active_modes"])

    def test_state_get_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "state_write",
                {
                    "mode": "ralph",
                    "active": True,
                    "current_phase": "investigate",
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "state_get_status",
                {
                    "mode": "ralph",
                    "workingDirectory": tmpdir,
                },
            )
            text = json.loads(result["content"][0]["text"])
            self.assertIn("ralph", text["statuses"])

    def test_invalid_mode_returns_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call(
                "state_read",
                {
                    "mode": "nonexistent_mode",
                    "workingDirectory": tmpdir,
                },
            )
            text = json.loads(result["content"][0]["text"])
            self.assertIn("error", text)

    def test_unknown_tool_returns_error(self):
        result = handle_tool_call("nonexistent_tool", {})
        self.assertTrue(result.get("isError"))


if __name__ == "__main__":
    unittest.main()
