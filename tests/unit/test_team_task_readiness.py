"""Tests for compute_task_readiness and TeamTask.depends_on round-tripping.

Port-parity tests for src/team/state/tasks.ts::computeTaskReadiness.
"""

from __future__ import annotations

import tempfile
import unittest

from omx.team.contracts import TaskStatus, TeamTask
from omx.team.state.io import write_tasks
from omx.team.state.tasks import (
    TaskReadiness,
    TaskReadinessReason,
    compute_task_readiness,
)


def _make_task(
    task_id: str,
    status: TaskStatus = TaskStatus.PENDING,
    depends_on: list[str] | None = None,
    blocked_by: list[str] | None = None,
) -> TeamTask:
    return TeamTask(
        task_id=task_id,
        description=f"task {task_id}",
        status=status,
        created_at="2026-01-01T00:00:00Z",
        depends_on=depends_on,
        blocked_by=blocked_by,
    )


class TestTeamTaskDependsOnSerialization(unittest.TestCase):
    """TeamTask.depends_on must round-trip through to_dict/from_dict."""

    def test_depends_on_round_trip(self):
        t = _make_task("t1", depends_on=["a", "b"])
        d = t.to_dict()
        self.assertEqual(d["depends_on"], ["a", "b"])
        restored = TeamTask.from_dict(d)
        self.assertEqual(restored.depends_on, ["a", "b"])

    def test_blocked_by_round_trip(self):
        t = _make_task("t1", blocked_by=["a"])
        d = t.to_dict()
        self.assertEqual(d["blocked_by"], ["a"])
        restored = TeamTask.from_dict(d)
        self.assertEqual(restored.blocked_by, ["a"])

    def test_depends_on_absent_when_unset(self):
        t = _make_task("t1")
        d = t.to_dict()
        self.assertNotIn("depends_on", d)
        self.assertNotIn("blocked_by", d)
        restored = TeamTask.from_dict(d)
        self.assertIsNone(restored.depends_on)
        self.assertIsNone(restored.blocked_by)

    def test_empty_depends_on_round_trips(self):
        # Empty list should survive round trip (it's distinct from None).
        t = _make_task("t1", depends_on=[])
        d = t.to_dict()
        self.assertEqual(d["depends_on"], [])
        restored = TeamTask.from_dict(d)
        self.assertEqual(restored.depends_on, [])


class TestComputeTaskReadiness(unittest.TestCase):
    """compute_task_readiness mirrors TS computeTaskReadiness semantics."""

    def test_no_deps_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tasks(tmp, [_make_task("t1")])
            r = compute_task_readiness(tmp, "default", "t1")
            self.assertIsInstance(r, TaskReadiness)
            self.assertTrue(r.ready)
            self.assertIsNone(r.reason)
            self.assertEqual(r.dependencies, [])

    def test_empty_depends_on_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_tasks(tmp, [_make_task("t1", depends_on=[])])
            r = compute_task_readiness(tmp, "default", "t1")
            self.assertTrue(r.ready)

    def test_one_pending_dep_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            dep = _make_task("dep1", status=TaskStatus.PENDING)
            main = _make_task("main", depends_on=["dep1"])
            write_tasks(tmp, [dep, main])
            r = compute_task_readiness(tmp, "default", "main")
            self.assertFalse(r.ready)
            self.assertEqual(r.reason, TaskReadinessReason.BLOCKED_DEPENDENCY)
            self.assertEqual(r.dependencies, ["dep1"])

    def test_all_deps_completed_is_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            d1 = _make_task("d1", status=TaskStatus.COMPLETED)
            d2 = _make_task("d2", status=TaskStatus.COMPLETED)
            main = _make_task("main", depends_on=["d1", "d2"])
            write_tasks(tmp, [d1, d2, main])
            r = compute_task_readiness(tmp, "default", "main")
            self.assertTrue(r.ready)
            self.assertEqual(r.dependencies, [])

    def test_mixed_deps_returns_only_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            d1 = _make_task("d1", status=TaskStatus.COMPLETED)
            d2 = _make_task("d2", status=TaskStatus.IN_PROGRESS)
            d3 = _make_task("d3", status=TaskStatus.COMPLETED)
            d4 = _make_task("d4", status=TaskStatus.FAILED)
            main = _make_task("main", depends_on=["d1", "d2", "d3", "d4"])
            write_tasks(tmp, [d1, d2, d3, d4, main])
            r = compute_task_readiness(tmp, "default", "main")
            self.assertFalse(r.ready)
            # Only incomplete deps reported, in original order.
            self.assertEqual(r.dependencies, ["d2", "d4"])

    def test_missing_dep_id_blocks(self):
        # TS treats missing dep (depTasks[idx] === undefined) as not completed,
        # so it ends up in the incomplete list.
        with tempfile.TemporaryDirectory() as tmp:
            main = _make_task("main", depends_on=["ghost"])
            write_tasks(tmp, [main])
            r = compute_task_readiness(tmp, "default", "main")
            self.assertFalse(r.ready)
            self.assertEqual(r.reason, TaskReadinessReason.BLOCKED_DEPENDENCY)
            self.assertEqual(r.dependencies, ["ghost"])

    def test_missing_task_id_blocks_with_empty_deps(self):
        # TS: if task itself doesn't exist, ready=false with dependencies=[].
        with tempfile.TemporaryDirectory() as tmp:
            write_tasks(tmp, [])
            r = compute_task_readiness(tmp, "default", "nonexistent")
            self.assertFalse(r.ready)
            self.assertEqual(r.reason, TaskReadinessReason.BLOCKED_DEPENDENCY)
            self.assertEqual(r.dependencies, [])

    def test_blocked_by_fallback_when_depends_on_unset(self):
        # TS: const depIds = task.depends_on ?? task.blocked_by ?? [];
        # When depends_on is None, blocked_by is used.
        with tempfile.TemporaryDirectory() as tmp:
            dep = _make_task("dep1", status=TaskStatus.PENDING)
            main = _make_task("main", blocked_by=["dep1"])
            write_tasks(tmp, [dep, main])
            r = compute_task_readiness(tmp, "default", "main")
            self.assertFalse(r.ready)
            self.assertEqual(r.dependencies, ["dep1"])

    def test_depends_on_takes_precedence_over_blocked_by(self):
        # TS uses ?? — depends_on (even if empty list) overrides blocked_by.
        # Python parity: an explicitly-set empty depends_on means "no deps".
        with tempfile.TemporaryDirectory() as tmp:
            dep = _make_task("dep1", status=TaskStatus.PENDING)
            main = _make_task("main", depends_on=[], blocked_by=["dep1"])
            write_tasks(tmp, [dep, main])
            r = compute_task_readiness(tmp, "default", "main")
            self.assertTrue(r.ready)


class TestTaskReadinessSerialization(unittest.TestCase):
    """TaskReadiness.to_dict shape parity with the TS union."""

    def test_ready_to_dict(self):
        r = TaskReadiness(ready=True)
        self.assertEqual(r.to_dict(), {"ready": True})

    def test_blocked_to_dict(self):
        r = TaskReadiness(
            ready=False,
            reason=TaskReadinessReason.BLOCKED_DEPENDENCY,
            dependencies=["a", "b"],
        )
        self.assertEqual(
            r.to_dict(),
            {
                "ready": False,
                "reason": "blocked_dependency",
                "dependencies": ["a", "b"],
            },
        )


if __name__ == "__main__":
    unittest.main()
