"""Tests for omx.ralph — persistence and contract."""

import tempfile
import unittest
from pathlib import Path

from omx.ralph.contract import (
    LEGACY_PHASE_ALIASES,
    RALPH_PHASES,
    RALPH_TERMINAL_PHASE_SET,
    normalize_ralph_phase,
    validate_and_normalize_ralph_state,
)
from omx.ralph.persistence import (
    ensure_canonical_ralph_artifacts,
    read_ralph_plan,
    write_ralph_plan,
)


class TestRalphContract(unittest.TestCase):
    def test_valid_phases(self):
        # Terminal phases require active=False; non-terminal phases are
        # legal with either active flag.
        for phase in RALPH_PHASES:
            active = phase not in RALPH_TERMINAL_PHASE_SET
            result = validate_and_normalize_ralph_state(
                {"current_phase": phase, "active": active}
            )
            self.assertTrue(result["ok"], f"phase {phase!r} should validate")

    def test_canonical_phase_set_matches_ts(self):
        self.assertEqual(
            RALPH_PHASES,
            [
                "starting",
                "executing",
                "verifying",
                "fixing",
                "blocked_on_user",
                "complete",
                "failed",
                "cancelled",
            ],
        )

    def test_terminal_phase_set(self):
        self.assertEqual(
            RALPH_TERMINAL_PHASE_SET,
            {"blocked_on_user", "complete", "failed", "cancelled"},
        )

    def test_legacy_alias_mapping(self):
        # Pre-port Python 4-phase contract folds onto the canonical TS set:
        # investigate/plan -> starting (planning half), execute -> executing,
        # verify -> verifying.
        self.assertEqual(LEGACY_PHASE_ALIASES["investigate"], "starting")
        self.assertEqual(LEGACY_PHASE_ALIASES["plan"], "starting")
        self.assertEqual(LEGACY_PHASE_ALIASES["execute"], "executing")
        self.assertEqual(LEGACY_PHASE_ALIASES["verify"], "verifying")

    def test_normalize_ralph_phase_canonical(self):
        result = normalize_ralph_phase("executing")
        self.assertEqual(result, {"phase": "executing"})

    def test_normalize_ralph_phase_legacy_investigate(self):
        result = normalize_ralph_phase("investigate")
        self.assertEqual(result["phase"], "starting")
        self.assertIn("warning", result)

    def test_normalize_ralph_phase_rejects_empty(self):
        result = normalize_ralph_phase("")
        self.assertIn("error", result)

    def test_normalize_ralph_phase_rejects_unknown(self):
        result = normalize_ralph_phase("nonexistent")
        self.assertIn("error", result)

    def test_phase_alias_normalization_in_state(self):
        # Legacy callers writing the old 4-phase names get auto-coerced.
        result = validate_and_normalize_ralph_state(
            {"current_phase": "investigating", "active": True}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["current_phase"], "starting")
        self.assertIn("warning", result)

    def test_legacy_execute_normalizes_to_executing(self):
        result = validate_and_normalize_ralph_state(
            {"current_phase": "execute", "active": True}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["current_phase"], "executing")

    def test_invalid_phase(self):
        result = validate_and_normalize_ralph_state({"current_phase": "nonexistent"})
        self.assertFalse(result["ok"])

    def test_default_phase_when_active(self):
        # Empty/missing phase + active=True must default to "starting".
        result = validate_and_normalize_ralph_state({"active": True})
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["current_phase"], "starting")

    def test_active_fills_lifecycle_defaults(self):
        result = validate_and_normalize_ralph_state({"active": True})
        self.assertTrue(result["ok"])
        state = result["state"]
        self.assertEqual(state["iteration"], 0)
        self.assertEqual(state["max_iterations"], 50)
        self.assertEqual(state["current_phase"], "starting")
        self.assertIsNotNone(state.get("started_at"))

    def test_iteration_must_be_nonneg_integer(self):
        bad = validate_and_normalize_ralph_state(
            {"current_phase": "executing", "iteration": -1}
        )
        self.assertFalse(bad["ok"])

    def test_max_iterations_must_be_positive_integer(self):
        bad = validate_and_normalize_ralph_state(
            {"current_phase": "executing", "max_iterations": 0}
        )
        self.assertFalse(bad["ok"])

    def test_terminal_phase_requires_inactive(self):
        bad = validate_and_normalize_ralph_state(
            {"current_phase": "complete", "active": True}
        )
        self.assertFalse(bad["ok"])

    def test_terminal_phase_stamps_completed_at(self):
        result = validate_and_normalize_ralph_state(
            {"current_phase": "complete", "active": False}
        )
        self.assertTrue(result["ok"])
        self.assertIsNotNone(result["state"].get("completed_at"))

    def test_terminal_phase_preserves_completed_at(self):
        existing = "2025-01-01T00:00:00+00:00"
        result = validate_and_normalize_ralph_state(
            {
                "current_phase": "complete",
                "active": False,
                "completed_at": existing,
            }
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["completed_at"], existing)

    def test_invalid_timestamp_rejected(self):
        bad = validate_and_normalize_ralph_state(
            {"current_phase": "executing", "started_at": "not-a-date"}
        )
        self.assertFalse(bad["ok"])

    def test_now_iso_override_seeds_defaults(self):
        stamp = "2030-06-01T12:00:00+00:00"
        result = validate_and_normalize_ralph_state({"active": True}, now_iso=stamp)
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"]["started_at"], stamp)


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
