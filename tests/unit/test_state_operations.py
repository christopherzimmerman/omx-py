"""Tests for omx.state.operations."""

import tempfile
import unittest

from omx.state.operations import (
    state_clear,
    state_get_status,
    state_list_active,
    state_read,
    state_write,
)


class TestStateOperations(unittest.TestCase):
    def test_read_nonexistent_returns_exists_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = state_read("autopilot", tmpdir)
            self.assertFalse(result.get("exists", True))

    def test_write_then_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_write(
                "autopilot", tmpdir, {"active": True, "current_phase": "running"}
            )
            result = state_read("autopilot", tmpdir)
            self.assertTrue(result.get("active"))
            self.assertEqual(result.get("current_phase"), "running")

    def test_write_merges_with_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_write("autopilot", tmpdir, {"active": True, "iteration": 1})
            state_write("autopilot", tmpdir, {"iteration": 2})
            result = state_read("autopilot", tmpdir)
            self.assertTrue(result.get("active"))
            self.assertEqual(result.get("iteration"), 2)

    def test_clear_removes_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_write("autopilot", tmpdir, {"active": True})
            state_clear("autopilot", tmpdir)
            result = state_read("autopilot", tmpdir)
            self.assertFalse(result.get("exists", True))

    def test_list_active_modes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_write("autopilot", tmpdir, {"active": True})
            state_write("ralph", tmpdir, {"active": False})
            result = state_list_active(tmpdir)
            self.assertIn("autopilot", result["active_modes"])
            self.assertNotIn("ralph", result["active_modes"])

    def test_get_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_write("autopilot", tmpdir, {"active": True, "current_phase": "init"})
            result = state_get_status(tmpdir)
            self.assertIn("autopilot", result["statuses"])
            status = result["statuses"]["autopilot"]
            self.assertTrue(status["active"])
            self.assertEqual(status["phase"], "init")

    def test_get_status_for_specific_mode(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_write("autopilot", tmpdir, {"active": True})
            state_write("ralph", tmpdir, {"active": True})
            result = state_get_status(tmpdir, mode="ralph")
            self.assertIn("ralph", result["statuses"])
            self.assertNotIn("autopilot", result["statuses"])

    def test_write_returns_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = state_write("autopilot", tmpdir, {"active": True})
            self.assertTrue(result["success"])
            self.assertEqual(result["mode"], "autopilot")

    def test_clear_all_sessions(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write to root and a session
            state_write("autopilot", tmpdir, {"active": True})
            state_write("autopilot", tmpdir, {"active": True}, session_id="sess-1")
            result = state_clear("autopilot", tmpdir, all_sessions=True)
            self.assertTrue(result["cleared"])
            self.assertGreaterEqual(result["removed"], 1)


if __name__ == "__main__":
    unittest.main()
