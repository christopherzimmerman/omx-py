"""Tests for omx.team.runtime_types — dataclass contract round-trips."""

from __future__ import annotations

import unittest

from omx.team.contracts import TaskStatus, TeamTask
from omx.team.runtime_types import (
    ShutdownOptions,
    StaleTeamSummary,
    TeamRuntime,
    TeamShutdownSummary,
    TeamSnapshot,
    TeamSnapshotPerformance,
    TeamSnapshotTasks,
    TeamSnapshotWorker,
    TeamStartOptions,
)


class TestTeamSnapshotWorker(unittest.TestCase):
    def test_round_trip_basic(self) -> None:
        w = TeamSnapshotWorker(
            name="alpha",
            alive=True,
            status={"state": "idle"},
            heartbeat={"timestamp": "2025-01-01T00:00:00Z"},
            assigned_tasks=["t1", "t2"],
            turns_without_progress=3,
        )
        d = w.to_dict()
        self.assertEqual(d["name"], "alpha")
        self.assertEqual(d["status"], {"state": "idle"})
        self.assertEqual(d["assigned_tasks"], ["t1", "t2"])
        self.assertEqual(d["turns_without_progress"], 3)

        restored = TeamSnapshotWorker.from_dict(d)
        self.assertEqual(restored, w)

    def test_from_dict_defaults_for_missing_fields(self) -> None:
        w = TeamSnapshotWorker.from_dict({"name": "beta", "alive": False})
        self.assertEqual(w.name, "beta")
        self.assertFalse(w.alive)
        self.assertEqual(w.status, {})
        self.assertIsNone(w.heartbeat)
        self.assertEqual(w.assigned_tasks, [])
        self.assertEqual(w.turns_without_progress, 0)


class TestTeamSnapshotTasks(unittest.TestCase):
    def test_round_trip_with_items(self) -> None:
        task = TeamTask(task_id="t1", description="do thing", status=TaskStatus.PENDING)
        tasks = TeamSnapshotTasks(
            total=1,
            pending=1,
            blocked=0,
            in_progress=0,
            completed=0,
            failed=0,
            items=[task],
        )
        d = tasks.to_dict()
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["items"][0]["task_id"], "t1")

        restored = TeamSnapshotTasks.from_dict(d)
        self.assertEqual(restored.total, 1)
        self.assertEqual(len(restored.items), 1)
        self.assertEqual(restored.items[0].task_id, "t1")

    def test_from_dict_handles_empty(self) -> None:
        tasks = TeamSnapshotTasks.from_dict({})
        self.assertEqual(tasks.total, 0)
        self.assertEqual(tasks.items, [])

    def test_from_dict_accepts_teamtask_instances(self) -> None:
        task = TeamTask(task_id="x", description="d")
        tasks = TeamSnapshotTasks.from_dict({"total": 1, "items": [task]})
        self.assertEqual(tasks.items[0].task_id, "x")


class TestTeamSnapshotPerformance(unittest.TestCase):
    def test_round_trip(self) -> None:
        perf = TeamSnapshotPerformance(
            list_tasks_ms=1.5,
            worker_scan_ms=2.5,
            mailbox_delivery_ms=3.5,
            total_ms=10.0,
            updated_at="2025-01-01T00:00:00Z",
        )
        d = perf.to_dict()
        restored = TeamSnapshotPerformance.from_dict(d)
        self.assertEqual(restored, perf)


class TestTeamSnapshot(unittest.TestCase):
    def test_round_trip_full(self) -> None:
        snap = TeamSnapshot(
            team_name="myteam",
            phase="team-exec",
            workers=[
                TeamSnapshotWorker(name="w1", alive=True, status={}, heartbeat=None)
            ],
            tasks=TeamSnapshotTasks(total=2, pending=1, in_progress=1),
            all_tasks_terminal=False,
            dead_workers=["w2"],
            non_reporting_workers=["w3"],
            recommendations=["restart w2"],
            performance=TeamSnapshotPerformance(total_ms=12.0),
        )
        d = snap.to_dict()
        self.assertEqual(d["team_name"], "myteam")
        self.assertEqual(d["phase"], "team-exec")
        self.assertEqual(d["workers"][0]["name"], "w1")
        self.assertEqual(d["dead_workers"], ["w2"])
        self.assertEqual(d["recommendations"], ["restart w2"])
        self.assertIn("performance", d)

        restored = TeamSnapshot.from_dict(d)
        self.assertEqual(restored.team_name, "myteam")
        self.assertEqual(restored.phase, "team-exec")
        self.assertEqual(len(restored.workers), 1)
        self.assertEqual(restored.workers[0].name, "w1")
        self.assertEqual(restored.dead_workers, ["w2"])
        self.assertIsNotNone(restored.performance)
        assert restored.performance is not None
        self.assertEqual(restored.performance.total_ms, 12.0)

    def test_to_dict_omits_performance_when_none(self) -> None:
        snap = TeamSnapshot(team_name="t", phase="team-plan")
        d = snap.to_dict()
        self.assertNotIn("performance", d)

    def test_terminal_phase_string_round_trips(self) -> None:
        for phase in ("complete", "failed", "cancelled"):
            snap = TeamSnapshot(team_name="t", phase=phase, all_tasks_terminal=True)
            restored = TeamSnapshot.from_dict(snap.to_dict())
            self.assertEqual(restored.phase, phase)
            self.assertTrue(restored.all_tasks_terminal)

    def test_from_dict_minimal(self) -> None:
        snap = TeamSnapshot.from_dict({"team_name": "x"})
        self.assertEqual(snap.team_name, "x")
        self.assertEqual(snap.phase, "")
        self.assertEqual(snap.workers, [])
        self.assertIsNone(snap.performance)


class TestTeamRuntime(unittest.TestCase):
    def test_round_trip(self) -> None:
        rt = TeamRuntime(
            team_name="my team",
            sanitized_name="my-team",
            session_name="omx-team-my-team",
            config={"workers": []},
            cwd="/repo",
        )
        d = rt.to_dict()
        restored = TeamRuntime.from_dict(d)
        self.assertEqual(restored, rt)

    def test_config_defaults_to_empty_dict(self) -> None:
        rt = TeamRuntime.from_dict(
            {
                "team_name": "x",
                "sanitized_name": "x",
                "session_name": "s",
            }
        )
        self.assertEqual(rt.config, {})
        self.assertEqual(rt.cwd, "")


class TestShutdownOptions(unittest.TestCase):
    def test_defaults_false(self) -> None:
        opts = ShutdownOptions()
        self.assertFalse(opts.force)
        self.assertFalse(opts.confirm_issues)

    def test_round_trip(self) -> None:
        opts = ShutdownOptions(force=True, confirm_issues=True)
        restored = ShutdownOptions.from_dict(opts.to_dict())
        self.assertEqual(restored, opts)

    def test_from_dict_coerces_truthy(self) -> None:
        opts = ShutdownOptions.from_dict({"force": 1, "confirm_issues": "yes"})
        self.assertTrue(opts.force)
        self.assertTrue(opts.confirm_issues)


class TestTeamShutdownSummary(unittest.TestCase):
    def test_round_trip_with_artifacts(self) -> None:
        summary = TeamShutdownSummary(
            commit_hygiene_artifacts={
                "json_path": "/x/team.context.json",
                "markdown_path": "/x/team.md",
            }
        )
        d = summary.to_dict()
        restored = TeamShutdownSummary.from_dict(d)
        self.assertEqual(restored, summary)

    def test_round_trip_with_none(self) -> None:
        summary = TeamShutdownSummary()
        self.assertIsNone(summary.commit_hygiene_artifacts)
        d = summary.to_dict()
        restored = TeamShutdownSummary.from_dict(d)
        self.assertIsNone(restored.commit_hygiene_artifacts)

    def test_from_dict_accepts_camel_case_keys(self) -> None:
        summary = TeamShutdownSummary.from_dict(
            {
                "commit_hygiene_artifacts": {
                    "jsonPath": "/a.json",
                    "markdownPath": "/a.md",
                }
            }
        )
        self.assertEqual(
            summary.commit_hygiene_artifacts,
            {"json_path": "/a.json", "markdown_path": "/a.md"},
        )

    def test_from_dict_garbage_collapses_to_none(self) -> None:
        summary = TeamShutdownSummary.from_dict(
            {"commit_hygiene_artifacts": "not-a-dict"}
        )
        self.assertIsNone(summary.commit_hygiene_artifacts)


class TestStaleTeamSummary(unittest.TestCase):
    def test_round_trip(self) -> None:
        summary = StaleTeamSummary(
            team_name="abandoned",
            worktree_paths=["/x/w1", "/x/w2"],
            state_path="/x/.omx/team/abandoned",
            has_dirty_worktrees=True,
        )
        restored = StaleTeamSummary.from_dict(summary.to_dict())
        self.assertEqual(restored, summary)

    def test_defaults(self) -> None:
        summary = StaleTeamSummary(team_name="t")
        self.assertEqual(summary.worktree_paths, [])
        self.assertEqual(summary.state_path, "")
        self.assertFalse(summary.has_dirty_worktrees)


class TestTeamStartOptions(unittest.TestCase):
    def test_defaults(self) -> None:
        opts = TeamStartOptions()
        self.assertIsNone(opts.worktree_mode)
        self.assertIsNone(opts.confirm_stale_cleanup)
        self.assertIsNone(opts.cleanup_launch_orphaned_mcp_processes)
        self.assertIsNone(opts.write_cleanup_warning)

    def test_callbacks_stored_and_callable(self) -> None:
        def confirm(summary: StaleTeamSummary) -> bool:
            return True

        def cleanup() -> dict:
            return {"failed_pids": []}

        warnings: list[str] = []

        opts = TeamStartOptions(
            worktree_mode={"enabled": False},
            confirm_stale_cleanup=confirm,
            cleanup_launch_orphaned_mcp_processes=cleanup,
            write_cleanup_warning=warnings.append,
        )
        self.assertEqual(opts.worktree_mode, {"enabled": False})
        assert opts.confirm_stale_cleanup is not None
        self.assertTrue(opts.confirm_stale_cleanup(StaleTeamSummary(team_name="x")))
        assert opts.cleanup_launch_orphaned_mcp_processes is not None
        self.assertEqual(
            opts.cleanup_launch_orphaned_mcp_processes(), {"failed_pids": []}
        )
        assert opts.write_cleanup_warning is not None
        opts.write_cleanup_warning("hi")
        self.assertEqual(warnings, ["hi"])

    def test_to_dict_records_callback_presence(self) -> None:
        opts = TeamStartOptions(
            worktree_mode={"enabled": True, "detached": True, "name": None},
            confirm_stale_cleanup=lambda s: True,
        )
        d = opts.to_dict()
        self.assertEqual(
            d["worktree_mode"], {"enabled": True, "detached": True, "name": None}
        )
        self.assertTrue(d["has_confirm_stale_cleanup"])
        self.assertFalse(d["has_cleanup_launch_orphaned_mcp_processes"])
        self.assertFalse(d["has_write_cleanup_warning"])

    def test_from_dict_drops_callback_markers(self) -> None:
        opts = TeamStartOptions.from_dict(
            {
                "worktree_mode": {"enabled": False},
                "has_confirm_stale_cleanup": True,
                "has_cleanup_launch_orphaned_mcp_processes": True,
            }
        )
        self.assertEqual(opts.worktree_mode, {"enabled": False})
        self.assertIsNone(opts.confirm_stale_cleanup)
        self.assertIsNone(opts.cleanup_launch_orphaned_mcp_processes)


if __name__ == "__main__":
    unittest.main()
