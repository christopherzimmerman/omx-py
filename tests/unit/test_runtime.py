"""Tests for omx.runtime — run loop, outcome, bridge."""

import tempfile
import unittest
from pathlib import Path

from omx.core.types import RuntimeCommand
from omx.runtime.bridge import RuntimeBridge
from omx.runtime.run_loop import RunLoopIteration, run_until_terminal
from omx.runtime.run_outcome import (
    apply_run_outcome_contract,
    classify_run_outcome,
    infer_run_outcome,
    is_terminal_run_outcome,
)
from omx.runtime.run_state import build_run_state
from omx.runtime.terminal_lifecycle import infer_terminal_lifecycle_outcome


class TestRunOutcome(unittest.TestCase):
    def test_classify_terminal_outcomes(self):
        self.assertEqual(classify_run_outcome("finish"), "finish")
        self.assertEqual(classify_run_outcome("blocked_on_user"), "blocked_on_user")
        self.assertEqual(classify_run_outcome("failed"), "failed")
        self.assertEqual(classify_run_outcome("cancelled"), "cancelled")

    def test_classify_aliases(self):
        self.assertEqual(classify_run_outcome("finished"), "finish")
        self.assertEqual(classify_run_outcome("complete"), "finish")
        self.assertEqual(classify_run_outcome("blocked"), "blocked_on_user")
        self.assertEqual(classify_run_outcome("canceled"), "cancelled")

    def test_classify_non_terminal(self):
        self.assertEqual(classify_run_outcome("continue"), "continue")
        self.assertEqual(classify_run_outcome("progress"), "progress")
        self.assertEqual(classify_run_outcome(""), "progress")
        self.assertEqual(classify_run_outcome(None), "progress")
        self.assertEqual(classify_run_outcome("unknown_value"), "progress")

    def test_is_terminal(self):
        self.assertTrue(is_terminal_run_outcome("finish"))
        self.assertTrue(is_terminal_run_outcome("failed"))
        self.assertFalse(is_terminal_run_outcome("progress"))
        self.assertFalse(is_terminal_run_outcome("continue"))

    def test_infer_from_explicit_run_outcome(self):
        self.assertEqual(infer_run_outcome({"run_outcome": "finish"}), "finish")

    def test_infer_from_lifecycle_outcome(self):
        self.assertEqual(infer_run_outcome({"lifecycle_outcome": "finished"}), "finish")
        self.assertEqual(
            infer_run_outcome({"lifecycle_outcome": "blocked"}), "blocked_on_user"
        )

    def test_infer_from_active_false(self):
        self.assertEqual(infer_run_outcome({"active": False}), "finish")
        self.assertEqual(
            infer_run_outcome({"active": False, "error": "oops"}), "failed"
        )

    def test_infer_from_phase(self):
        self.assertEqual(infer_run_outcome({"current_phase": "completed"}), "finish")
        self.assertEqual(infer_run_outcome({"current_phase": "running"}), "progress")

    def test_apply_contract_terminal(self):
        state = {"run_outcome": "finish", "active": True}
        result = apply_run_outcome_contract(state)
        self.assertTrue(result["ok"])
        self.assertFalse(result["state"]["active"])
        self.assertIn("completed_at", result["state"])

    def test_apply_contract_non_terminal(self):
        state = {"current_phase": "running"}
        result = apply_run_outcome_contract(state)
        self.assertTrue(result["ok"])
        self.assertTrue(result["state"]["active"])


class TestRunLoop(unittest.TestCase):
    def test_reaches_terminal_in_one_step(self):
        def step(state):
            return RunLoopIteration(outcome="finish", state={"done": True})

        result = run_until_terminal(step)
        self.assertEqual(result.terminal_outcome, "finish")
        self.assertEqual(result.iteration_count, 1)

    def test_iterates_until_terminal(self):
        counter = {"n": 0}

        def step(state):
            counter["n"] += 1
            if counter["n"] >= 3:
                return RunLoopIteration(outcome="finish", state=state)
            return RunLoopIteration(outcome="continue", state=state)

        result = run_until_terminal(step)
        self.assertEqual(result.terminal_outcome, "finish")
        self.assertEqual(result.iteration_count, 3)
        self.assertEqual(len(result.history), 3)

    def test_raises_on_max_iterations(self):
        def step(state):
            return RunLoopIteration(outcome="continue", state=state)

        with self.assertRaises(RuntimeError):
            run_until_terminal(step, max_iterations=5)

    def test_on_iteration_callback(self):
        calls = []

        def step(state):
            return RunLoopIteration(outcome="finish", state={})

        run_until_terminal(step, on_iteration=lambda n: calls.append(n.iteration))
        self.assertEqual(calls, [1])


class TestRuntimeBridge(unittest.TestCase):
    def test_bridge_exec_and_read(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = RuntimeBridge(Path(tmpdir))
            event = bridge.exec_command(
                RuntimeCommand.acquire_authority("w1", "l1", "2026-01-01T00:00:00Z")
            )
            self.assertEqual(event.event, "AuthorityAcquired")

            snap = bridge.read_snapshot()
            self.assertTrue(snap.is_ready())
            self.assertEqual(snap.authority.owner, "w1")

    def test_bridge_dispatch_cycle(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = RuntimeBridge(Path(tmpdir))
            bridge.exec_command(
                RuntimeCommand.acquire_authority("w1", "l1", "2026-01-01T00:00:00Z")
            )
            bridge.exec_command(RuntimeCommand.queue_dispatch("r1", "w2"))
            bridge.exec_command(RuntimeCommand.mark_notified("r1", "tmux"))
            bridge.exec_command(RuntimeCommand.mark_delivered("r1"))

            backlog = bridge.read_backlog()
            self.assertEqual(backlog["delivered"], 1)


class TestRunState(unittest.TestCase):
    def test_build_run_state_active(self):
        state = build_run_state(
            {"mode": "autopilot", "active": True, "current_phase": "running"}
        )
        self.assertTrue(state["active"])
        self.assertEqual(state["outcome"], "progress")
        self.assertIn("updated_at", state)

    def test_build_run_state_terminal(self):
        state = build_run_state({"mode": "autopilot", "run_outcome": "finish"})
        self.assertFalse(state["active"])
        self.assertEqual(state["outcome"], "finish")
        self.assertIn("completed_at", state)


class TestTerminalLifecycle(unittest.TestCase):
    def test_infer_from_lifecycle_field(self):
        self.assertEqual(
            infer_terminal_lifecycle_outcome({"lifecycle_outcome": "finished"}),
            "finished",
        )
        self.assertEqual(
            infer_terminal_lifecycle_outcome({"lifecycle_outcome": "blocked"}),
            "blocked",
        )

    def test_infer_from_run_outcome(self):
        self.assertEqual(
            infer_terminal_lifecycle_outcome({"run_outcome": "finish"}), "finished"
        )
        self.assertEqual(
            infer_terminal_lifecycle_outcome({"run_outcome": "failed"}), "failed"
        )

    def test_infer_none_for_non_terminal(self):
        self.assertIsNone(infer_terminal_lifecycle_outcome({"run_outcome": "continue"}))
        self.assertIsNone(infer_terminal_lifecycle_outcome({}))


if __name__ == "__main__":
    unittest.main()
