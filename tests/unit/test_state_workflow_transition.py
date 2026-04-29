"""Tests for omx.state.workflow_transition."""

import unittest

from omx.state.workflow_transition import (
    evaluate_workflow_transition,
    is_tracked_workflow_mode,
)


class TestWorkflowTransition(unittest.TestCase):
    def test_is_tracked_workflow_mode(self):
        self.assertTrue(is_tracked_workflow_mode("autopilot"))
        self.assertTrue(is_tracked_workflow_mode("ralph"))
        self.assertFalse(is_tracked_workflow_mode("nonexistent"))
        self.assertFalse(is_tracked_workflow_mode("skill-active"))

    def test_allow_when_no_active_modes(self):
        decision = evaluate_workflow_transition([], "autopilot")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, "allow")
        self.assertEqual(decision.resulting_modes, ["autopilot"])

    def test_allow_when_already_active(self):
        decision = evaluate_workflow_transition(["autopilot"], "autopilot")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, "allow")

    def test_deny_incompatible_overlap(self):
        decision = evaluate_workflow_transition(["autopilot"], "autoresearch")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.kind, "deny")

    def test_allow_ralph_team_overlap(self):
        decision = evaluate_workflow_transition(["ralph"], "team")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, "overlap")
        self.assertIn("ralph", decision.resulting_modes)
        self.assertIn("team", decision.resulting_modes)

    def test_auto_complete_deep_interview_to_ralplan(self):
        decision = evaluate_workflow_transition(["deep-interview"], "ralplan")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, "auto-complete")
        self.assertEqual(decision.auto_complete_modes, ["deep-interview"])
        self.assertIn("ralplan", decision.resulting_modes)
        self.assertNotIn("deep-interview", decision.resulting_modes)

    def test_auto_complete_ralplan_to_team(self):
        decision = evaluate_workflow_transition(["ralplan"], "team")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, "auto-complete")

    def test_deny_rollback_execution_to_planning(self):
        decision = evaluate_workflow_transition(["autopilot"], "deep-interview")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.denial_reason, "rollback")

    def test_ultrawork_overlaps_with_anything(self):
        decision = evaluate_workflow_transition(["autopilot"], "ultrawork")
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.kind, "overlap")


if __name__ == "__main__":
    unittest.main()
