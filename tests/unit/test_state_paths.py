"""Tests for omx.state.paths."""

import tempfile
import unittest
from pathlib import Path

from omx.state.paths import (
    get_state_dir,
    get_state_path,
    validate_session_id,
    validate_state_file_name,
    validate_state_mode_segment,
)


class TestStatePaths(unittest.TestCase):
    def test_validate_session_id_none(self):
        self.assertIsNone(validate_session_id(None))

    def test_validate_session_id_valid(self):
        self.assertEqual(validate_session_id("abc-123"), "abc-123")

    def test_validate_session_id_invalid(self):
        with self.assertRaises(ValueError):
            validate_session_id("invalid id with spaces")

    def test_validate_mode_segment_valid(self):
        self.assertEqual(validate_state_mode_segment("autopilot"), "autopilot")

    def test_validate_mode_segment_path_traversal(self):
        with self.assertRaises(ValueError):
            validate_state_mode_segment("../etc")

    def test_validate_mode_segment_separators(self):
        with self.assertRaises(ValueError):
            validate_state_mode_segment("foo/bar")

    def test_validate_mode_segment_empty(self):
        with self.assertRaises(ValueError):
            validate_state_mode_segment("")

    def test_validate_state_file_name_valid(self):
        self.assertEqual(validate_state_file_name("session.json"), "session.json")

    def test_validate_state_file_name_traversal(self):
        with self.assertRaises(ValueError):
            validate_state_file_name("../../etc/passwd")

    def test_get_state_dir_no_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_state_dir(tmpdir)
            self.assertEqual(result, Path(tmpdir).resolve() / ".omx" / "state")

    def test_get_state_dir_with_session(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_state_dir(tmpdir, "sess-1")
            self.assertEqual(
                result,
                Path(tmpdir).resolve() / ".omx" / "state" / "sessions" / "sess-1",
            )

    def test_get_state_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = get_state_path("ralph", tmpdir)
            self.assertEqual(result.name, "ralph-state.json")


if __name__ == "__main__":
    unittest.main()
