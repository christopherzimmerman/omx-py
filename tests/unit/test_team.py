"""Tests for omx.team — orchestration, allocation, state."""

import tempfile
import unittest

from omx.team.allocation_policy import choose_task_owner
from omx.team.contracts import TaskStatus, TeamEvent, TeamTask, TeamWorker
from omx.team.idle_nudge import NudgeConfig, should_nudge
from omx.team.model_contract import resolve_worker_cli
from omx.team.runtime import (
    assign_pending_tasks,
    check_team_completion,
    mark_task_completed,
)
from omx.team.state.io import (
    append_team_event,
    read_tasks,
    read_team_events,
    read_workers,
    write_tasks,
    write_workers,
)


class TestTeamContracts(unittest.TestCase):
    def test_task_serialization(self):
        task = TeamTask(
            task_id="t1", description="Do something", status=TaskStatus.PENDING
        )
        d = task.to_dict()
        self.assertEqual(d["task_id"], "t1")
        restored = TeamTask.from_dict(d)
        self.assertEqual(restored.task_id, "t1")
        self.assertEqual(restored.status, TaskStatus.PENDING)

    def test_worker_serialization(self):
        worker = TeamWorker(worker_id="w1", pane_id="sess:0.1", role="executor")
        d = worker.to_dict()
        restored = TeamWorker.from_dict(d)
        self.assertEqual(restored.worker_id, "w1")
        self.assertEqual(restored.role, "executor")


class TestTeamStateIO(unittest.TestCase):
    def test_read_write_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks = [
                TeamTask(task_id="t1", description="test", created_at="2026-01-01")
            ]
            write_tasks(tmpdir, tasks)
            loaded = read_tasks(tmpdir)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].task_id, "t1")

    def test_read_write_workers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            workers = [TeamWorker(worker_id="w1", pane_id="s:0.0")]
            write_workers(tmpdir, workers)
            loaded = read_workers(tmpdir)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].worker_id, "w1")

    def test_append_and_read_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            event = TeamEvent(
                event_type="task_completed",
                timestamp="2026-01-01T00:00:00Z",
                task_id="t1",
            )
            append_team_event(tmpdir, event)
            append_team_event(tmpdir, event)
            events = read_team_events(tmpdir)
            self.assertEqual(len(events), 2)


class TestAllocationPolicy(unittest.TestCase):
    def test_allocate_to_available_worker(self):
        task = TeamTask(task_id="t1", description="build feature", role="executor")
        workers = [TeamWorker(worker_id="w1", pane_id="s:0.0", role="executor")]
        decision = choose_task_owner(task, workers, [task])
        self.assertIsNotNone(decision)
        self.assertEqual(decision.owner, "w1")

    def test_allocate_load_balancing(self):
        tasks = [
            TeamTask(
                task_id="t1", description="a", owner="w1", status=TaskStatus.IN_PROGRESS
            ),
            TeamTask(task_id="t2", description="b"),
        ]
        workers = [
            TeamWorker(worker_id="w1", pane_id="s:0.0"),
            TeamWorker(worker_id="w2", pane_id="s:0.1"),
        ]
        decision = choose_task_owner(tasks[1], workers, tasks)
        self.assertEqual(decision.owner, "w2")

    def test_no_workers_returns_none(self):
        task = TeamTask(task_id="t1", description="test")
        self.assertIsNone(choose_task_owner(task, [], [task]))


class TestTeamRuntime(unittest.TestCase):
    @unittest.skipUnless(
        __import__("shutil").which("tmux"),
        "tmux not available",
    )
    def test_assign_pending_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks = [
                TeamTask(task_id="t1", description="do it", created_at="2026-01-01")
            ]
            workers = [TeamWorker(worker_id="w1", pane_id="s:0.0")]
            write_tasks(tmpdir, tasks)
            write_workers(tmpdir, workers)

            assigned = assign_pending_tasks(tmpdir)
            self.assertEqual(assigned, ["t1"])

            reloaded = read_tasks(tmpdir)
            self.assertEqual(reloaded[0].status, TaskStatus.IN_PROGRESS)
            self.assertEqual(reloaded[0].owner, "w1")

    def test_mark_task_completed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks = [
                TeamTask(task_id="t1", description="x", status=TaskStatus.IN_PROGRESS)
            ]
            write_tasks(tmpdir, tasks)
            mark_task_completed(tmpdir, "t1")
            reloaded = read_tasks(tmpdir)
            self.assertEqual(reloaded[0].status, TaskStatus.COMPLETED)

    def test_check_completion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks = [
                TeamTask(task_id="t1", description="x", status=TaskStatus.COMPLETED)
            ]
            write_tasks(tmpdir, tasks)
            self.assertTrue(check_team_completion(tmpdir))


class TestIdleNudge(unittest.TestCase):
    def test_should_nudge_when_idle(self):
        self.assertTrue(should_nudge(35000, 0))
        self.assertFalse(should_nudge(10000, 0))

    def test_max_nudge_count(self):
        self.assertFalse(should_nudge(35000, 3))

    def test_custom_config(self):
        cfg = NudgeConfig(delay_ms=5000, max_count=1)
        self.assertTrue(should_nudge(6000, 0, cfg))
        self.assertFalse(should_nudge(6000, 1, cfg))


class TestModelContract(unittest.TestCase):
    def test_default_cli(self):
        self.assertEqual(resolve_worker_cli(), "codex")

    def test_explicit_cli(self):
        self.assertEqual(resolve_worker_cli("claude"), "claude")


if __name__ == "__main__":
    unittest.main()
