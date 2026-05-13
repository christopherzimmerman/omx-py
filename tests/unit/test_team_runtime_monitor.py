"""Tests for omx.team.runtime_monitor — TS-parity monitor_team_ts."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from typing import Any
from unittest import mock

from omx.team import runtime_monitor
from omx.team.contracts import TaskStatus, TeamTask
from omx.team.runtime_monitor import monitor_team_ts
from omx.team.runtime_types import (
    TeamSnapshot,
    TeamSnapshotPerformance,
    TeamSnapshotTasks,
    TeamSnapshotWorker,
)
from omx.team.state.io import (
    write_team_config,
    write_tasks,
    write_worker_heartbeat,
    write_worker_status,
)
from omx.team.state.monitor import write_monitor_snapshot, write_phase_state
from omx.team.state.types import TeamMonitorSnapshot, TeamPhaseState
from omx.team.state_root import team_dir as _team_dir


def _make_team_config(
    *,
    team_name: str = "alpha",
    workers: list[dict[str, Any]] | None = None,
    worker_launch_mode: str = "interactive",
    tmux_session: str | None = None,
) -> dict[str, Any]:
    """Build a minimal team config compatible with monitor_team_ts."""
    if workers is None:
        workers = []
    return {
        "name": team_name,
        "task": "demo",
        "agent_type": "executor",
        "worker_launch_mode": worker_launch_mode,
        "worker_count": len(workers),
        "worker_cli": "codex",
        "workers": workers,
        "tmux_session": tmux_session
        if tmux_session is not None
        else f"omx-team-{team_name}",
        "next_task_id": 1,
    }


def _make_worker_entry(
    *,
    name: str,
    index: int,
    pane_id: str = "%0",
    role: str = "executor",
    assigned_tasks: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "index": index,
        "pane_id": pane_id,
        "role": role,
        "worker_cli": "codex",
        "assigned_tasks": assigned_tasks or [],
    }


class _MonitorTestBase(unittest.TestCase):
    """Shared setup: per-test tmpdir + patched is_worker_alive."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        self.team_name = "alpha"

        # Default: every worker is alive. Individual tests override via
        # self.alive_map keyed by (session_name, worker_index).
        self.alive_map: dict[tuple[str, int], bool] = {}

        def fake_is_worker_alive(
            session_name: str, worker_index: int, pane_id: str | None = None
        ) -> bool:
            return self.alive_map.get(
                (session_name, worker_index),
                self.alive_map.get(("__default__", 0), True),
            )

        patcher = mock.patch.object(
            runtime_monitor, "is_worker_alive", side_effect=fake_is_worker_alive
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_config(self, config: dict[str, Any]) -> None:
        write_team_config(self.cwd, config, self.team_name)

    def _write_tasks(self, tasks: list[TeamTask]) -> None:
        write_tasks(self.cwd, tasks, self.team_name)

    def _set_default_alive(self, alive: bool) -> None:
        self.alive_map[("__default__", 0)] = alive


# -------------------- absent / empty cases -------------------- #


class TestMonitorTeamAbsent(_MonitorTestBase):
    def test_returns_none_when_team_config_missing(self) -> None:
        result = monitor_team_ts("ghost-team", self.cwd)
        self.assertIsNone(result)

    def test_empty_team_no_workers_no_tasks(self) -> None:
        self._write_config(_make_team_config(team_name=self.team_name, workers=[]))
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertIsInstance(snapshot, TeamSnapshot)
        self.assertEqual(snapshot.team_name, self.team_name)
        self.assertEqual(snapshot.workers, [])
        self.assertEqual(snapshot.tasks.total, 0)
        # all_tasks_terminal is True for empty task list (per Phase 1 contract).
        self.assertTrue(snapshot.all_tasks_terminal)
        self.assertEqual(snapshot.dead_workers, [])
        self.assertEqual(snapshot.non_reporting_workers, [])


# -------------------- task counting -------------------- #


class TestTaskCounting(_MonitorTestBase):
    def setUp(self) -> None:
        super().setUp()
        self._write_config(_make_team_config(workers=[]))

    def test_task_counts_by_status(self) -> None:
        tasks = [
            TeamTask(task_id="1", description="p", status=TaskStatus.PENDING),
            TeamTask(task_id="2", description="b", status=TaskStatus.BLOCKED),
            TeamTask(task_id="3", description="i", status=TaskStatus.IN_PROGRESS),
            TeamTask(task_id="4", description="c", status=TaskStatus.COMPLETED),
            TeamTask(task_id="5", description="c2", status=TaskStatus.COMPLETED),
            TeamTask(task_id="6", description="f", status=TaskStatus.FAILED),
        ]
        self._write_tasks(tasks)
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        counts = snapshot.tasks
        self.assertEqual(counts.total, 6)
        self.assertEqual(counts.pending, 1)
        self.assertEqual(counts.blocked, 1)
        self.assertEqual(counts.in_progress, 1)
        self.assertEqual(counts.completed, 2)
        self.assertEqual(counts.failed, 1)
        self.assertEqual(len(counts.items), 6)
        self.assertFalse(snapshot.all_tasks_terminal)

    def test_all_completed_marks_terminal(self) -> None:
        tasks = [
            TeamTask(task_id="1", description="c", status=TaskStatus.COMPLETED),
            TeamTask(task_id="2", description="c", status=TaskStatus.COMPLETED),
        ]
        self._write_tasks(tasks)
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertTrue(snapshot.all_tasks_terminal)


# -------------------- worker liveness -------------------- #


class TestWorkerLiveness(_MonitorTestBase):
    def test_idle_alive_workers_no_dead(self) -> None:
        workers = [
            _make_worker_entry(name="w1", index=1, pane_id="%1"),
            _make_worker_entry(name="w2", index=2, pane_id="%2"),
        ]
        cfg = _make_team_config(workers=workers)
        self._write_config(cfg)
        self.alive_map[(cfg["tmux_session"], 1)] = True
        self.alive_map[(cfg["tmux_session"], 2)] = True
        write_worker_status(self.cwd, self.team_name, "w1", "idle")
        write_worker_status(self.cwd, self.team_name, "w2", "idle")

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(len(snapshot.workers), 2)
        self.assertEqual(snapshot.dead_workers, [])
        self.assertEqual(snapshot.non_reporting_workers, [])
        self.assertTrue(all(w.alive for w in snapshot.workers))

    def test_dead_worker_detected_and_in_progress_task_reassign_rec(self) -> None:
        workers = [_make_worker_entry(name="w1", index=1, pane_id="%1")]
        cfg = _make_team_config(workers=workers)
        self._write_config(cfg)
        self.alive_map[(cfg["tmux_session"], 1)] = False
        write_worker_status(self.cwd, self.team_name, "w1", "working", "t-1")
        self._write_tasks(
            [
                TeamTask(
                    task_id="t-1",
                    description="x",
                    status=TaskStatus.IN_PROGRESS,
                    owner="w1",
                )
            ]
        )

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.dead_workers, ["w1"])
        self.assertIn("Reassign task-t-1 from dead w1", snapshot.recommendations)

    def test_busy_worker_with_heartbeat_no_progress_yet(self) -> None:
        workers = [_make_worker_entry(name="w1", index=1, pane_id="%1")]
        cfg = _make_team_config(workers=workers)
        self._write_config(cfg)
        self.alive_map[(cfg["tmux_session"], 1)] = True
        write_worker_status(self.cwd, self.team_name, "w1", "working", "t-1")
        write_worker_heartbeat(self.cwd, self.team_name, "w1", pid=123, turn_count=3)
        self._write_tasks(
            [TeamTask(task_id="t-1", description="x", status=TaskStatus.IN_PROGRESS)]
        )

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        # No previous snapshot -> turns_without_progress is 0.
        self.assertEqual(snapshot.workers[0].turns_without_progress, 0)
        self.assertEqual(snapshot.non_reporting_workers, [])


# -------------------- turns_without_progress heuristic -------------------- #


class TestTurnsWithoutProgress(_MonitorTestBase):
    def _setup_worker(self, alive: bool = True) -> str:
        workers = [_make_worker_entry(name="w1", index=1, pane_id="%1")]
        cfg = _make_team_config(workers=workers)
        self._write_config(cfg)
        self.alive_map[(cfg["tmux_session"], 1)] = alive
        return cfg["tmux_session"]

    def _seed_previous(self, *, turn_count: int, task_id: str = "t-1") -> None:
        snap = TeamMonitorSnapshot(
            worker_turn_count_by_name={"w1": turn_count},
            worker_task_id_by_name={"w1": task_id},
        )
        write_monitor_snapshot(_team_dir(self.team_name, self.cwd), snap)

    def test_same_task_progress_delta_computed(self) -> None:
        self._setup_worker()
        write_worker_status(self.cwd, self.team_name, "w1", "working", "t-1")
        write_worker_heartbeat(self.cwd, self.team_name, "w1", pid=1, turn_count=10)
        self._write_tasks(
            [TeamTask(task_id="t-1", description="x", status=TaskStatus.IN_PROGRESS)]
        )
        self._seed_previous(turn_count=3, task_id="t-1")

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.workers[0].turns_without_progress, 7)

    def test_task_changed_resets_turn_delta(self) -> None:
        self._setup_worker()
        write_worker_status(self.cwd, self.team_name, "w1", "working", "t-2")
        write_worker_heartbeat(self.cwd, self.team_name, "w1", pid=1, turn_count=10)
        self._write_tasks(
            [TeamTask(task_id="t-2", description="x", status=TaskStatus.IN_PROGRESS)]
        )
        self._seed_previous(turn_count=3, task_id="t-1")

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.workers[0].turns_without_progress, 0)

    def test_idle_state_resets_turn_delta(self) -> None:
        self._setup_worker()
        write_worker_status(self.cwd, self.team_name, "w1", "idle")
        write_worker_heartbeat(self.cwd, self.team_name, "w1", pid=1, turn_count=10)
        self._seed_previous(turn_count=3, task_id="")

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.workers[0].turns_without_progress, 0)

    def test_terminal_current_task_resets_turn_delta(self) -> None:
        self._setup_worker()
        write_worker_status(self.cwd, self.team_name, "w1", "working", "t-1")
        write_worker_heartbeat(self.cwd, self.team_name, "w1", pid=1, turn_count=10)
        self._write_tasks(
            [TeamTask(task_id="t-1", description="x", status=TaskStatus.COMPLETED)]
        )
        self._seed_previous(turn_count=3, task_id="t-1")

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.workers[0].turns_without_progress, 0)

    def test_non_reporting_when_delta_above_threshold(self) -> None:
        self._setup_worker()
        write_worker_status(self.cwd, self.team_name, "w1", "working", "t-1")
        write_worker_heartbeat(self.cwd, self.team_name, "w1", pid=1, turn_count=20)
        self._write_tasks(
            [TeamTask(task_id="t-1", description="x", status=TaskStatus.IN_PROGRESS)]
        )
        # delta = 20 - 5 = 15 (> threshold 5).
        self._seed_previous(turn_count=5, task_id="t-1")

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.workers[0].turns_without_progress, 15)
        self.assertEqual(snapshot.non_reporting_workers, ["w1"])
        self.assertIn("Send reminder to non-reporting w1", snapshot.recommendations)

    def test_negative_delta_clamped_to_zero(self) -> None:
        self._setup_worker()
        write_worker_status(self.cwd, self.team_name, "w1", "working", "t-1")
        # Heartbeat regression (turn_count smaller than previously seen).
        write_worker_heartbeat(self.cwd, self.team_name, "w1", pid=1, turn_count=2)
        self._write_tasks(
            [TeamTask(task_id="t-1", description="x", status=TaskStatus.IN_PROGRESS)]
        )
        self._seed_previous(turn_count=10, task_id="t-1")

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.workers[0].turns_without_progress, 0)


# -------------------- recommendations -------------------- #


class TestRecommendations(_MonitorTestBase):
    def test_pending_with_no_dead_workers(self) -> None:
        cfg = _make_team_config(workers=[])
        self._write_config(cfg)
        self._write_tasks(
            [TeamTask(task_id="1", description="x", status=TaskStatus.PENDING)]
        )
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertTrue(
            any("pending tasks ready" in r for r in snapshot.recommendations)
        )

    def test_blocked_tasks_hint(self) -> None:
        cfg = _make_team_config(workers=[])
        self._write_config(cfg)
        self._write_tasks(
            [TeamTask(task_id="1", description="x", status=TaskStatus.BLOCKED)]
        )
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertTrue(any("blocked tasks" in r for r in snapshot.recommendations))

    def test_dead_worker_stall_in_prompt_mode(self) -> None:
        workers = [_make_worker_entry(name="w1", index=1, pane_id=None)]
        cfg = _make_team_config(workers=workers, worker_launch_mode="prompt")
        self._write_config(cfg)
        self.alive_map[(cfg["tmux_session"], 1)] = False
        self._write_tasks(
            [TeamTask(task_id="1", description="x", status=TaskStatus.PENDING)]
        )
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.dead_workers, ["w1"])
        self.assertEqual(snapshot.phase, "failed")
        self.assertTrue(
            any("All workers are dead" in r for r in snapshot.recommendations)
        )


# -------------------- phase resolution -------------------- #


class TestPhaseResolution(_MonitorTestBase):
    def test_persisted_phase_returned_when_no_stall(self) -> None:
        cfg = _make_team_config(workers=[])
        self._write_config(cfg)
        write_phase_state(
            _team_dir(self.team_name, self.cwd),
            TeamPhaseState(current_phase="team-exec"),
        )
        self._write_tasks(
            [TeamTask(task_id="1", description="x", status=TaskStatus.IN_PROGRESS)]
        )
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.phase, "team-exec")

    def test_phase_complete_when_all_tasks_completed(self) -> None:
        cfg = _make_team_config(workers=[])
        self._write_config(cfg)
        self._write_tasks(
            [TeamTask(task_id="1", description="x", status=TaskStatus.COMPLETED)]
        )
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.phase, "complete")

    def test_phase_failed_when_only_failed_tasks(self) -> None:
        cfg = _make_team_config(workers=[])
        self._write_config(cfg)
        self._write_tasks(
            [TeamTask(task_id="1", description="x", status=TaskStatus.FAILED)]
        )
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.phase, "failed")

    def test_phase_default_when_no_tasks_and_no_persisted(self) -> None:
        cfg = _make_team_config(workers=[])
        self._write_config(cfg)
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        # No tasks at all -> "team-plan".
        self.assertEqual(snapshot.phase, "team-plan")


# -------------------- performance section -------------------- #


class TestPerformance(_MonitorTestBase):
    def test_performance_present_by_default(self) -> None:
        cfg = _make_team_config(workers=[])
        self._write_config(cfg)
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertIsInstance(snapshot.performance, TeamSnapshotPerformance)
        assert snapshot.performance is not None
        self.assertGreaterEqual(snapshot.performance.total_ms, 0.0)
        self.assertGreaterEqual(snapshot.performance.list_tasks_ms, 0.0)
        self.assertGreaterEqual(snapshot.performance.worker_scan_ms, 0.0)
        self.assertNotEqual(snapshot.performance.updated_at, "")

    def test_performance_omitted_when_disabled(self) -> None:
        cfg = _make_team_config(workers=[])
        self._write_config(cfg)
        snapshot = monitor_team_ts(self.team_name, self.cwd, measure_performance=False)
        assert snapshot is not None
        self.assertIsNone(snapshot.performance)


# -------------------- parallel worker scan -------------------- #


class TestParallelWorkerScan(_MonitorTestBase):
    def test_thread_pool_used_for_five_or_more_workers(self) -> None:
        """A team with 5+ workers should fan out parallel reads."""
        worker_count = 5
        workers = [
            _make_worker_entry(name=f"w{i}", index=i, pane_id=f"%{i}")
            for i in range(1, worker_count + 1)
        ]
        cfg = _make_team_config(workers=workers)
        self._write_config(cfg)
        for w in workers:
            self.alive_map[(cfg["tmux_session"], w["index"])] = True
            write_worker_status(self.cwd, self.team_name, w["name"], "idle")

        # Track concurrent thread-id observations inside the worker scan.
        observed_threads: set[int] = set()
        scan_lock = threading.Lock()
        concurrent_max = [0]
        active = [0]
        active_lock = threading.Lock()

        original = runtime_monitor._scan_one_worker

        def instrumented(
            cwd: str, sanitized: str, session_name: str, worker: dict[str, Any]
        ) -> dict[str, Any]:
            tid = threading.get_ident()
            with scan_lock:
                observed_threads.add(tid)
            with active_lock:
                active[0] += 1
                if active[0] > concurrent_max[0]:
                    concurrent_max[0] = active[0]
            # Force overlap so threads collide.
            time.sleep(0.02)
            try:
                return original(cwd, sanitized, session_name, worker)
            finally:
                with active_lock:
                    active[0] -= 1

        with mock.patch.object(
            runtime_monitor, "_scan_one_worker", side_effect=instrumented
        ):
            snapshot = monitor_team_ts(self.team_name, self.cwd)

        assert snapshot is not None
        self.assertEqual(len(snapshot.workers), worker_count)
        # Multiple worker threads were used (ThreadPoolExecutor really ran in parallel).
        self.assertGreater(len(observed_threads), 1)
        # And we observed at least 2 worker scans running at the same time.
        self.assertGreaterEqual(concurrent_max[0], 2)

    def test_max_parallel_workers_clamps_pool_size(self) -> None:
        """Setting max_parallel_workers=1 forces sequential execution."""
        workers = [
            _make_worker_entry(name=f"w{i}", index=i, pane_id=f"%{i}")
            for i in range(1, 6)
        ]
        cfg = _make_team_config(workers=workers)
        self._write_config(cfg)
        for w in workers:
            self.alive_map[(cfg["tmux_session"], w["index"])] = True

        concurrent_max = [0]
        active = [0]
        active_lock = threading.Lock()
        original = runtime_monitor._scan_one_worker

        def instrumented(
            cwd: str, sanitized: str, session_name: str, worker: dict[str, Any]
        ) -> dict[str, Any]:
            with active_lock:
                active[0] += 1
                if active[0] > concurrent_max[0]:
                    concurrent_max[0] = active[0]
            time.sleep(0.01)
            try:
                return original(cwd, sanitized, session_name, worker)
            finally:
                with active_lock:
                    active[0] -= 1

        with mock.patch.object(
            runtime_monitor, "_scan_one_worker", side_effect=instrumented
        ):
            snapshot = monitor_team_ts(self.team_name, self.cwd, max_parallel_workers=1)

        assert snapshot is not None
        self.assertEqual(len(snapshot.workers), 5)
        self.assertEqual(concurrent_max[0], 1)


# -------------------- assigned_tasks preserved -------------------- #


class TestAssignedTasksPreserved(_MonitorTestBase):
    def test_assigned_tasks_copied_from_config(self) -> None:
        workers = [
            _make_worker_entry(
                name="w1",
                index=1,
                pane_id="%1",
                assigned_tasks=["t-1", "t-2"],
            ),
        ]
        cfg = _make_team_config(workers=workers)
        self._write_config(cfg)
        self.alive_map[(cfg["tmux_session"], 1)] = True

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.workers[0].assigned_tasks, ["t-1", "t-2"])

    def test_assigned_tasks_defaults_to_empty_list(self) -> None:
        workers = [_make_worker_entry(name="w1", index=1, pane_id="%1")]
        cfg = _make_team_config(workers=workers)
        # Drop assigned_tasks entirely to simulate older configs.
        cfg["workers"][0].pop("assigned_tasks", None)
        self._write_config(cfg)
        self.alive_map[(cfg["tmux_session"], 1)] = True

        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.workers[0].assigned_tasks, [])


# -------------------- snapshot round-trip -------------------- #


class TestSnapshotDictRoundTrip(_MonitorTestBase):
    def test_snapshot_to_dict_and_back(self) -> None:
        cfg = _make_team_config(workers=[])
        self._write_config(cfg)
        self._write_tasks(
            [TeamTask(task_id="1", description="x", status=TaskStatus.PENDING)]
        )
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        as_dict = snapshot.to_dict()
        # Ensure to_dict is JSON-serializable.
        rendered = json.dumps(as_dict)
        round_tripped = TeamSnapshot.from_dict(json.loads(rendered))
        self.assertEqual(round_tripped.team_name, snapshot.team_name)
        self.assertEqual(round_tripped.tasks.pending, snapshot.tasks.pending)
        self.assertEqual(round_tripped.phase, snapshot.phase)


# -------------------- structural sanity -------------------- #


class TestSnapshotStructure(_MonitorTestBase):
    def test_workers_are_team_snapshot_worker_instances(self) -> None:
        workers = [_make_worker_entry(name="w1", index=1, pane_id="%1")]
        cfg = _make_team_config(workers=workers)
        self._write_config(cfg)
        self.alive_map[(cfg["tmux_session"], 1)] = True
        snapshot = monitor_team_ts(self.team_name, self.cwd)
        assert snapshot is not None
        self.assertIsInstance(snapshot.workers[0], TeamSnapshotWorker)
        self.assertIsInstance(snapshot.tasks, TeamSnapshotTasks)

    def test_team_name_is_sanitized(self) -> None:
        # Use a name that survives sanitize_team_name unchanged but exercise
        # the call path; the snapshot must echo the sanitized value.
        self._write_config(_make_team_config(team_name="alpha", workers=[]))
        snapshot = monitor_team_ts("alpha", self.cwd)
        assert snapshot is not None
        self.assertEqual(snapshot.team_name, "alpha")


if __name__ == "__main__":
    unittest.main()
