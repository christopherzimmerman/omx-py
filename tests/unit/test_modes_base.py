"""Tests for omx.modes.base (start_mode / update_mode_state + helpers).

Port-of: src/modes/__tests__/base-*.test.ts and the start/update behavior
described in the team-port handoff. Sync stdlib-only; no asyncio.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from omx.modes.base import (
    assert_mode_start_allowed,
    cancel_mode,
    read_mode_state_dict,
    start_mode,
    update_mode_state,
)
from omx.state.paths import get_base_state_dir, get_state_path
from omx.state.workflow_transition import (
    assert_workflow_transition_allowed,
    pick_primary_workflow_mode,
    read_active_workflow_modes,
)


def _state_file(root: str, mode: str, session_id: str | None = None) -> Path:
    return get_state_path(mode, root, session_id)


def _write_state(
    root: str, mode: str, payload: dict, session_id: str | None = None
) -> Path:
    path = _state_file(root, mode, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


class StartModeBasicsTest(unittest.TestCase):
    """Lifecycle: start_mode persists the canonical initial document."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_mode_writes_initial_state_with_required_fields(self) -> None:
        state = start_mode("autopilot", "build a thing", 25, self.root)
        self.assertTrue(state["active"])
        self.assertEqual(state["mode"], "autopilot")
        self.assertEqual(state["iteration"], 0)
        self.assertEqual(state["max_iterations"], 25)
        self.assertEqual(state["current_phase"], "starting")
        self.assertEqual(state["task_description"], "build a thing")
        self.assertTrue(state["started_at"])

    def test_start_mode_persists_to_disk(self) -> None:
        start_mode("autopilot", "task", 10, self.root)
        path = _state_file(self.root, "autopilot")
        self.assertTrue(path.exists())
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["mode"], "autopilot")
        self.assertEqual(loaded["task_description"], "task")

    def test_start_mode_default_max_iterations_is_50(self) -> None:
        state = start_mode("ralph", "default", project_root=self.root)
        self.assertEqual(state["max_iterations"], 50)

    def test_start_mode_merges_initial_state(self) -> None:
        state = start_mode(
            "autoresearch",
            "research",
            5,
            self.root,
            initial_state={"goal": "find truth", "iteration": 3, "max_iterations": 99},
        )
        self.assertEqual(state["goal"], "find truth")
        self.assertEqual(state["iteration"], 3)
        # initial_state overrides defaults
        self.assertEqual(state["max_iterations"], 99)

    def test_start_mode_creates_state_dir(self) -> None:
        self.assertFalse(get_base_state_dir(self.root).exists())
        start_mode("team", "spin up", project_root=self.root)
        self.assertTrue(get_base_state_dir(self.root).exists())


class StartModeRalphContractTest(unittest.TestCase):
    """ralph mode must round-trip through validate_and_normalize_ralph_state."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_mode_ralph_uses_canonical_initial_phase(self) -> None:
        # Ralph phases are aligned with TS (starting/executing/verifying/
        # fixing/blocked_on_user/complete/failed/cancelled), so
        # start_mode("ralph") emits "starting" as the canonical initial phase.
        state = start_mode("ralph", "ralph task", project_root=self.root)
        self.assertEqual(state["current_phase"], "starting")

    def test_start_mode_ralph_records_owner_session_when_scoped(self) -> None:
        state = start_mode(
            "ralph",
            "ralph",
            project_root=self.root,
            session_id="sess001",
        )
        self.assertEqual(state.get("owner_omx_session_id"), "sess001")


class StartModeWorkflowMutexTest(unittest.TestCase):
    """Workflow mutex: rejects starting conflicting tracked modes."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_mode_denies_incompatible_overlap(self) -> None:
        start_mode("autopilot", "first", project_root=self.root)
        with self.assertRaises(RuntimeError):
            start_mode("autoresearch", "second", project_root=self.root)

    def test_start_mode_denies_execution_to_planning_rollback(self) -> None:
        start_mode("autopilot", "first", project_root=self.root)
        with self.assertRaises(RuntimeError) as ctx:
            start_mode("deep-interview", "rollback", project_root=self.root)
        self.assertIn("rollback", str(ctx.exception).lower())

    def test_start_mode_allows_ralph_team_overlap(self) -> None:
        start_mode("ralph", "ralph", project_root=self.root)
        team_state = start_mode("team", "team", project_root=self.root)
        self.assertTrue(team_state["active"])
        # ralph remains active (allowed overlap, not auto-complete)
        ralph_state = json.loads(
            _state_file(self.root, "ralph").read_text(encoding="utf-8")
        )
        self.assertTrue(ralph_state["active"])

    def test_start_mode_ultrawork_overlaps_with_anything(self) -> None:
        start_mode("autopilot", "auto", project_root=self.root)
        uw = start_mode("ultrawork", "uw", project_root=self.root)
        self.assertTrue(uw["active"])

    def test_start_mode_auto_completes_deep_interview_to_ralplan(self) -> None:
        start_mode("deep-interview", "di", project_root=self.root)
        ralplan = start_mode("ralplan", "rp", project_root=self.root)
        self.assertTrue(ralplan["active"])
        # deep-interview should be auto-completed
        di_path = _state_file(self.root, "deep-interview")
        di = json.loads(di_path.read_text(encoding="utf-8"))
        self.assertFalse(di["active"])
        self.assertEqual(di["current_phase"], "completed")
        self.assertIn("auto_completed_reason", di)
        # transition_message should be on the ralplan state
        self.assertIn("transition_message", ralplan)

    def test_start_mode_auto_completes_ralplan_to_team(self) -> None:
        start_mode("ralplan", "plan", project_root=self.root)
        team = start_mode("team", "team", project_root=self.root)
        self.assertTrue(team["active"])
        rp = json.loads(_state_file(self.root, "ralplan").read_text(encoding="utf-8"))
        self.assertFalse(rp["active"])
        self.assertEqual(rp["current_phase"], "completed")

    def test_start_mode_non_tracked_mode_skips_mutex(self) -> None:
        # Non-tracked mode names just write state without consulting mutex.
        state = start_mode("custom-not-tracked", "x", project_root=self.root)
        self.assertEqual(state["mode"], "custom-not-tracked")
        # And does NOT raise even when another mode is active.
        start_mode("autopilot", "auto", project_root=self.root)
        again = start_mode("custom-not-tracked", "y", project_root=self.root)
        self.assertEqual(again["task_description"], "y")


class UpdateModeStateTest(unittest.TestCase):
    """update_mode_state merges, persists, and syncs."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_update_mode_state_raises_when_no_state_exists(self) -> None:
        with self.assertRaises(RuntimeError):
            update_mode_state("autopilot", {"iteration": 1}, self.root)

    def test_update_mode_state_merges_fields(self) -> None:
        start_mode("autopilot", "task", project_root=self.root)
        updated = update_mode_state(
            "autopilot",
            {"iteration": 5, "current_phase": "running"},
            self.root,
        )
        self.assertEqual(updated["iteration"], 5)
        self.assertEqual(updated["current_phase"], "running")
        # original task_description survives
        self.assertEqual(updated["task_description"], "task")

    def test_update_mode_state_persists_to_disk(self) -> None:
        start_mode("autopilot", "task", project_root=self.root)
        update_mode_state("autopilot", {"iteration": 7}, self.root)
        loaded = json.loads(
            _state_file(self.root, "autopilot").read_text(encoding="utf-8")
        )
        self.assertEqual(loaded["iteration"], 7)

    def test_update_mode_state_strips_run_outcome_when_not_provided(self) -> None:
        # Seed a state file directly with a stale run_outcome field.
        _write_state(
            self.root,
            "autopilot",
            {
                "active": True,
                "mode": "autopilot",
                "iteration": 0,
                "max_iterations": 50,
                "current_phase": "running",
                "task_description": "t",
                "started_at": "2025-01-01T00:00:00Z",
                "run_outcome": "stale",
            },
        )
        updated = update_mode_state("autopilot", {"iteration": 1}, self.root)
        # run_outcome was not in updates -> dropped (TS parity).
        self.assertNotIn("run_outcome", updated)

    def test_update_mode_state_preserves_explicit_run_outcome(self) -> None:
        start_mode("autopilot", "task", project_root=self.root)
        updated = update_mode_state(
            "autopilot",
            {"run_outcome": "finish", "active": False, "current_phase": "complete"},
            self.root,
        )
        self.assertEqual(updated["run_outcome"], "finish")

    def test_update_mode_state_session_scoped(self) -> None:
        start_mode(
            "autopilot",
            "task",
            project_root=self.root,
            session_id="sess123",
        )
        update_mode_state(
            "autopilot",
            {"iteration": 9},
            self.root,
            session_id="sess123",
        )
        scoped = json.loads(
            _state_file(self.root, "autopilot", "sess123").read_text(encoding="utf-8")
        )
        self.assertEqual(scoped["iteration"], 9)

    def test_update_mode_state_ralph_adds_owner_session_when_missing(self) -> None:
        # Seed a session-scoped ralph state without owner_omx_session_id.
        _write_state(
            self.root,
            "ralph",
            {
                "active": True,
                "mode": "ralph",
                "iteration": 0,
                "max_iterations": 50,
                "current_phase": "starting",
                "task_description": "t",
                "started_at": "2025-01-01T00:00:00Z",
            },
            session_id="sess-r",
        )
        updated = update_mode_state(
            "ralph",
            {"iteration": 1},
            self.root,
            session_id="sess-r",
        )
        self.assertEqual(updated.get("owner_omx_session_id"), "sess-r")


class WorkflowTransitionExportsTest(unittest.TestCase):
    """assert_workflow_transition_allowed / read_active_workflow_modes /
    pick_primary_workflow_mode behave per TS parity."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_assert_workflow_transition_allowed_passes_for_empty(self) -> None:
        # No active modes -> no raise.
        assert_workflow_transition_allowed([], "autopilot")

    def test_assert_workflow_transition_allowed_raises_for_deny(self) -> None:
        with self.assertRaises(RuntimeError):
            assert_workflow_transition_allowed(["autopilot"], "autoresearch")

    def test_read_active_workflow_modes_empty_when_no_state(self) -> None:
        self.assertEqual(read_active_workflow_modes(self.root), [])

    def test_read_active_workflow_modes_finds_active_state(self) -> None:
        start_mode("autopilot", "t", project_root=self.root)
        modes = read_active_workflow_modes(self.root)
        self.assertEqual(modes, ["autopilot"])

    def test_read_active_workflow_modes_skips_inactive(self) -> None:
        start_mode("autopilot", "t", project_root=self.root)
        cancel_mode("autopilot", self.root)
        modes = read_active_workflow_modes(self.root)
        self.assertEqual(modes, [])

    def test_read_active_workflow_modes_raises_on_corrupt(self) -> None:
        path = _state_file(self.root, "ralph")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            read_active_workflow_modes(self.root)

    def test_pick_primary_workflow_mode_keeps_current_when_in_set(self) -> None:
        self.assertEqual(
            pick_primary_workflow_mode("ralph", ["ralph", "team"], "team"),
            "ralph",
        )

    def test_pick_primary_workflow_mode_falls_back_to_first(self) -> None:
        self.assertEqual(
            pick_primary_workflow_mode(None, ["ralph", "team"], "x"),
            "ralph",
        )

    def test_pick_primary_workflow_mode_uses_fallback_when_empty(self) -> None:
        self.assertEqual(pick_primary_workflow_mode("", [], "ultrawork"), "ultrawork")


class AssertModeStartAllowedTest(unittest.TestCase):
    """assert_mode_start_allowed is a public guard used by Phase 2.9/Phase 4."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_assert_passes_for_non_tracked_mode(self) -> None:
        assert_mode_start_allowed("not-tracked", self.root)

    def test_assert_passes_when_empty(self) -> None:
        assert_mode_start_allowed("autopilot", self.root)

    def test_assert_raises_on_conflict(self) -> None:
        start_mode("autopilot", "first", project_root=self.root)
        with self.assertRaises(RuntimeError):
            assert_mode_start_allowed("autoresearch", self.root)


class ModeRuntimeContextTest(unittest.TestCase):
    """with_mode_runtime_context is invoked on start/update transitions."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_mode_captures_tmux_pane_from_env(self) -> None:
        with mock.patch.dict(os.environ, {"TMUX_PANE": "%17"}, clear=False):
            state = start_mode("autopilot", "task", project_root=self.root)
        self.assertEqual(state.get("tmux_pane_id"), "%17")
        self.assertTrue(state.get("tmux_pane_set_at"))

    def test_start_mode_no_pane_when_env_empty(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "TMUX_PANE"}
        with mock.patch.dict(os.environ, env, clear=True):
            state = start_mode("autopilot", "task", project_root=self.root)
        self.assertNotIn("tmux_pane_id", state)


class SkillActiveSyncTest(unittest.TestCase):
    """start_mode / update_mode_state must sync the skill-active state for
    tracked workflow modes."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_start_mode_writes_skill_active_entry(self) -> None:
        start_mode("autopilot", "task", project_root=self.root)
        skill_path = _state_file(self.root, "skill-active")
        self.assertTrue(skill_path.exists())
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        skills = data.get("active_skills", [])
        self.assertTrue(any(s.get("skill") == "autopilot" for s in skills))

    def test_update_mode_state_marks_skill_inactive_on_completion(self) -> None:
        start_mode("autopilot", "task", project_root=self.root)
        update_mode_state(
            "autopilot",
            {"active": False, "current_phase": "complete"},
            self.root,
        )
        skill_path = _state_file(self.root, "skill-active")
        data = json.loads(skill_path.read_text(encoding="utf-8"))
        skills = data.get("active_skills", [])
        for entry in skills:
            if entry.get("skill") == "autopilot":
                self.assertFalse(entry.get("active"))


class ReadModeStateDictTest(unittest.TestCase):
    """read_mode_state_dict preserves the raw extensible JSON shape."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_returns_none_when_missing(self) -> None:
        self.assertIsNone(read_mode_state_dict("autopilot", self.root))

    def test_returns_dict_with_extension_fields(self) -> None:
        _write_state(
            self.root,
            "autoresearch",
            {
                "active": True,
                "mode": "autoresearch",
                "goal": "x",
                "custom_field": [1, 2, 3],
            },
        )
        state = read_mode_state_dict("autoresearch", self.root)
        assert state is not None
        self.assertEqual(state["goal"], "x")
        self.assertEqual(state["custom_field"], [1, 2, 3])

    def test_session_scoped_takes_precedence(self) -> None:
        _write_state(self.root, "autopilot", {"active": True, "iteration": 1})
        _write_state(
            self.root,
            "autopilot",
            {"active": True, "iteration": 99},
            session_id="sessA",
        )
        scoped = read_mode_state_dict("autopilot", self.root, "sessA")
        assert scoped is not None
        self.assertEqual(scoped["iteration"], 99)


if __name__ == "__main__":
    unittest.main()
