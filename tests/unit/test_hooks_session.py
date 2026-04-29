"""Tests for omx.hooks.session — session lifecycle."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from omx.hooks.session import (
    SessionState,
    read_session_state,
    read_usable_session_state,
    reset_session_metrics,
    write_session_end,
    write_session_start,
)


class TestSession(unittest.TestCase):
    def test_write_and_read_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = write_session_start(tmpdir, session_id="test-sess-1")
            self.assertEqual(state.session_id, "test-sess-1")
            self.assertEqual(state.pid, os.getpid())

            read_back = read_session_state(tmpdir)
            self.assertIsNotNone(read_back)
            self.assertEqual(read_back.session_id, "test-sess-1")

    def test_read_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(read_session_state(tmpdir))

    def test_usable_session_for_current_pid(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_session_start(tmpdir, session_id="test-sess-2")
            state = read_usable_session_state(tmpdir)
            self.assertIsNotNone(state)
            self.assertEqual(state.session_id, "test-sess-2")

    def test_session_end_removes_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_session_start(tmpdir, session_id="test-sess-3")
            session_path = Path(tmpdir) / ".omx" / "session.json"
            self.assertTrue(session_path.exists())

            write_session_end(tmpdir, "test-sess-3")
            self.assertFalse(session_path.exists())

    def test_session_history_appended(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_session_start(tmpdir, session_id="test-sess-4")
            write_session_end(tmpdir, "test-sess-4")

            history_path = Path(tmpdir) / ".omx" / "logs" / "session-history.jsonl"
            self.assertTrue(history_path.exists())
            lines = history_path.read_text().strip().splitlines()
            self.assertEqual(len(lines), 2)  # start + end

            start_entry = json.loads(lines[0])
            self.assertEqual(start_entry["event"], "session_start")
            end_entry = json.loads(lines[1])
            self.assertEqual(end_entry["event"], "session_end")

    def test_reset_session_metrics(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            reset_session_metrics(tmpdir)
            metrics_path = Path(tmpdir) / ".omx" / "state" / "metrics.json"
            self.assertTrue(metrics_path.exists())
            data = json.loads(metrics_path.read_text())
            self.assertEqual(data["tool_calls"], 0)
            self.assertEqual(data["errors"], 0)

    def test_session_state_serialization(self):
        state = SessionState(
            session_id="s1",
            started_at="2026-01-01T00:00:00Z",
            cwd="/tmp/test",
            pid=12345,
            platform="linux",
        )
        d = state.to_dict()
        self.assertEqual(d["session_id"], "s1")
        self.assertEqual(d["pid"], 12345)
        self.assertNotIn("native_session_id", d)  # None values excluded

        restored = SessionState.from_dict(d)
        self.assertEqual(restored.session_id, "s1")
        self.assertIsNone(restored.native_session_id)

    def test_auto_generates_session_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = write_session_start(tmpdir)
            self.assertIsNotNone(state.session_id)
            self.assertGreater(len(state.session_id), 0)


if __name__ == "__main__":
    unittest.main()
