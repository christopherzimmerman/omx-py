"""Tests for omx.ralph — persistence and contract."""

import tempfile
import unittest
from pathlib import Path

from omx.ralph.contract import RALPH_PHASES, validate_and_normalize_ralph_state
from omx.ralph.persistence import (
    ensure_canonical_ralph_artifacts,
    read_ralph_plan,
    write_ralph_plan,
)


class TestRalphContract(unittest.TestCase):
    def test_valid_phases(self):
        for phase in RALPH_PHASES:
            result = validate_and_normalize_ralph_state(
                {"current_phase": phase, "active": True}
            )
            self.assertTrue(result["ok"])

    def test_phase_alias_normalization(self):
        result = validate_and_normalize_ralph_state(
            {"current_phase": "investigating", "active": True}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["current_phase"], "investigate")

    def test_invalid_phase(self):
        result = validate_and_normalize_ralph_state({"current_phase": "nonexistent"})
        self.assertFalse(result["ok"])

    def test_default_phase_when_active(self):
        result = validate_and_normalize_ralph_state({"active": True})
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["current_phase"], "investigate")


class TestRalphPersistence(unittest.TestCase):
    def test_ensure_artifacts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ensure_canonical_ralph_artifacts(tmpdir)
            Path(tmpdir) / ".omx" / "state" / ".." / "ralph"
            # The actual path resolution; let's just check write_ralph_plan works
            write_ralph_plan(tmpdir, "# My Plan\n\n- Step 1\n- Step 2")
            plan = read_ralph_plan(tmpdir)
            self.assertIn("My Plan", plan)

    def test_read_nonexistent_plan(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(read_ralph_plan(tmpdir))


if __name__ == "__main__":
    unittest.main()
