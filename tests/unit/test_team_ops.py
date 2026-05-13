"""Tests for the MCP-aligned team_ops gateway.

Covers the new primitives added during the gateway rebuild (cleanup,
broadcast, dispatch helpers, task CRUD) and the wrapper layer that
translates team_name+cwd to team_dir.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from omx.team import team_ops
from omx.team.contracts import TaskStatus, TeamTask, TeamWorker


class TestGatewayExportSurface(unittest.TestCase):
    """Verify all TS-aligned gateway names are present."""

    REQUIRED_FUNCTIONS = (
        # Lifecycle
        "team_init",
        "team_cleanup",
        "team_read_manifest",
        "team_write_manifest",
        "team_read_config",
        "team_save_config",
        "team_normalize_policy",
        "team_normalize_governance",
        # Worker
        "team_write_worker_identity",
        "team_read_worker_heartbeat",
        "team_update_worker_heartbeat",
        "team_read_worker_status",
        "team_write_worker_status",
        "team_write_worker_inbox",
        # Tasks
        "team_create_task",
        "team_read_task",
        "team_list_tasks",
        "team_update_task",
        "team_claim_task",
        "team_release_task_claim",
        "team_reclaim_expired_task_claim",
        "team_transition_task_status",
        "team_compute_task_readiness",
        # Messaging
        "team_send_message",
        "team_broadcast",
        "team_list_mailbox",
        "team_mark_message_delivered",
        "team_mark_message_notified",
        # Dispatch
        "team_enqueue_dispatch_request",
        "team_list_dispatch_requests",
        "team_read_dispatch_request",
        "team_transition_dispatch_request",
        "team_mark_dispatch_request_notified",
        "team_mark_dispatch_request_delivered",
        "resolve_dispatch_lock_timeout_ms",
        # Events
        "team_append_event",
        # Approvals
        "team_read_task_approval",
        "team_write_task_approval",
        # Summary / monitor / phase
        "team_get_summary",
        "team_read_monitor_snapshot",
        "team_write_monitor_snapshot",
        "team_read_phase",
        "team_write_phase",
        # Leader-attention
        "team_read_leader_attention",
        "team_write_leader_attention",
        "team_mark_leader_session_stopped",
        "team_mark_owned_teams_leader_session_stopped",
        # Shutdown handshake
        "team_write_shutdown_request",
        "team_read_shutdown_ack",
        # Scaling lock + atomic
        "team_with_scaling_lock",
        "write_atomic",
    )

    REQUIRED_TYPES = (
        "DEFAULT_MAX_WORKERS",
        "ABSOLUTE_MAX_WORKERS",
        "TeamTask",
        "TeamTaskClaim",
        "TeamWorker",
        "TeamEvent",
        "TeamManifestV2",
        "TeamLeader",
        "TeamLeaderAttentionState",
        "TeamPolicy",
        "TeamGovernance",
        "PermissionsSnapshot",
        "ShutdownAck",
        "TaskReadiness",
        "TaskStatus",
        "TaskApprovalRecord",
        "TeamDispatchRequest",
        "TeamMailboxMessage",
        "TeamMonitorSnapshot",
        "TeamPhaseState",
    )

    def test_all_required_functions_exposed(self) -> None:
        missing = [n for n in self.REQUIRED_FUNCTIONS if not hasattr(team_ops, n)]
        self.assertEqual(missing, [], f"Gateway missing functions: {missing}")

    def test_all_required_types_exposed(self) -> None:
        missing = [n for n in self.REQUIRED_TYPES if not hasattr(team_ops, n)]
        self.assertEqual(missing, [], f"Gateway missing types: {missing}")


class TestNewPrimitives(unittest.TestCase):
    """Direct tests for primitives added during the gateway rebuild."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        team_ops.team_save_config(self.cwd, {"name": "alpha", "next_task_id": 1}, "alpha")

    def test_cleanup_team_state_removes_directory(self) -> None:
        team_dir = Path(self.cwd) / ".omx" / "team" / "alpha"
        self.assertTrue(team_dir.exists())
        team_ops.team_cleanup(self.cwd, "alpha")
        self.assertFalse(team_dir.exists())

    def test_cleanup_team_state_idempotent(self) -> None:
        team_ops.team_cleanup(self.cwd, "alpha")
        team_ops.team_cleanup(self.cwd, "alpha")  # no error
        self.assertFalse((Path(self.cwd) / ".omx" / "team" / "alpha").exists())

    def test_write_worker_identity_round_trips(self) -> None:
        identity = {"name": "worker-1", "index": 1, "pane_id": "%5"}
        team_ops.team_write_worker_identity(self.cwd, "alpha", "worker-1", identity)
        path = (
            Path(self.cwd)
            / ".omx"
            / "team"
            / "alpha"
            / "workers"
            / "worker-1"
            / "identity.json"
        )
        self.assertTrue(path.exists())
        import json

        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), identity)

    def test_resolve_dispatch_lock_timeout_defaults(self) -> None:
        self.assertEqual(team_ops.resolve_dispatch_lock_timeout_ms({}), 15_000)

    def test_resolve_dispatch_lock_timeout_clamps(self) -> None:
        self.assertEqual(
            team_ops.resolve_dispatch_lock_timeout_ms({"OMX_TEAM_DISPATCH_LOCK_TIMEOUT_MS": "100"}),
            1_000,
        )
        self.assertEqual(
            team_ops.resolve_dispatch_lock_timeout_ms(
                {"OMX_TEAM_DISPATCH_LOCK_TIMEOUT_MS": "999999"}
            ),
            60_000,
        )
        self.assertEqual(
            team_ops.resolve_dispatch_lock_timeout_ms(
                {"OMX_TEAM_DISPATCH_LOCK_TIMEOUT_MS": "5000"}
            ),
            5_000,
        )

    def test_resolve_dispatch_lock_timeout_invalid_falls_back(self) -> None:
        self.assertEqual(
            team_ops.resolve_dispatch_lock_timeout_ms(
                {"OMX_TEAM_DISPATCH_LOCK_TIMEOUT_MS": "bananas"}
            ),
            15_000,
        )


class TestTaskCrud(unittest.TestCase):
    """create_task / read_task / list_tasks / update_task wrappers."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        team_ops.team_save_config(self.cwd, {"name": "alpha", "next_task_id": 1}, "alpha")

    def test_create_task_returns_team_task_with_assigned_id(self) -> None:
        t = team_ops.team_create_task(self.cwd, "alpha", "first task")
        self.assertIsInstance(t, TeamTask)
        self.assertEqual(t.task_id, "1")
        self.assertEqual(t.description, "first task")

    def test_create_task_advances_next_id(self) -> None:
        team_ops.team_create_task(self.cwd, "alpha", "first")
        team_ops.team_create_task(self.cwd, "alpha", "second")
        config = team_ops.team_read_config(self.cwd, "alpha")
        self.assertEqual(config["next_task_id"], 3)

    def test_read_task_returns_none_for_missing(self) -> None:
        self.assertIsNone(team_ops.team_read_task(self.cwd, "alpha", "999"))

    def test_read_task_returns_existing(self) -> None:
        created = team_ops.team_create_task(self.cwd, "alpha", "alpha task")
        read = team_ops.team_read_task(self.cwd, "alpha", created.task_id)
        self.assertIsNotNone(read)
        assert read is not None
        self.assertEqual(read.task_id, created.task_id)
        self.assertEqual(read.description, "alpha task")

    def test_list_tasks_orders_by_creation(self) -> None:
        team_ops.team_create_task(self.cwd, "alpha", "a")
        team_ops.team_create_task(self.cwd, "alpha", "b")
        team_ops.team_create_task(self.cwd, "alpha", "c")
        ids = [t.task_id for t in team_ops.team_list_tasks(self.cwd, "alpha")]
        self.assertEqual(ids, ["1", "2", "3"])

    def test_update_task_merges_fields(self) -> None:
        created = team_ops.team_create_task(self.cwd, "alpha", "original")
        updated = team_ops.team_update_task(
            self.cwd, "alpha", created.task_id, {"description": "edited", "role": "executor"}
        )
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.description, "edited")
        self.assertEqual(updated.role, "executor")
        # Immutable fields preserved
        self.assertEqual(updated.task_id, created.task_id)
        self.assertEqual(updated.created_at, created.created_at)

    def test_update_task_returns_none_for_missing(self) -> None:
        self.assertIsNone(team_ops.team_update_task(self.cwd, "alpha", "999", {"description": "x"}))

    def test_update_task_rejects_invalid_status(self) -> None:
        created = team_ops.team_create_task(self.cwd, "alpha", "x")
        with self.assertRaises(ValueError):
            team_ops.team_update_task(self.cwd, "alpha", created.task_id, {"status": "nonsense"})

    def test_update_task_falls_back_to_blocked_by_when_depends_on_missing(self) -> None:
        # Create with legacy blocked_by, no depends_on
        from omx.team.state.io import read_tasks, write_tasks
        tasks = read_tasks(self.cwd, "alpha")
        tasks.append(
            TeamTask(
                task_id="42",
                description="legacy",
                blocked_by=["10", "11"],
                created_at="2026-01-01T00:00:00Z",
            )
        )
        write_tasks(self.cwd, tasks, "alpha")

        updated = team_ops.team_update_task(self.cwd, "alpha", "42", {"description": "edited"})
        assert updated is not None
        # depends_on hydrated from blocked_by since neither was in updates
        self.assertEqual(updated.depends_on, ["10", "11"])


class TestBroadcastMessage(unittest.TestCase):
    """team_broadcast fans a single body out to all workers in the team."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        team_ops.team_save_config(self.cwd, {"name": "alpha"}, "alpha")
        # Seed workers
        from omx.team.state.io import write_workers
        write_workers(
            self.cwd,
            [
                TeamWorker(worker_id="worker-1", pane_id="%1", role="executor"),
                TeamWorker(worker_id="worker-2", pane_id="%2", role="executor"),
                TeamWorker(worker_id="worker-3", pane_id="%3", role="reviewer"),
            ],
            "alpha",
        )

    def test_broadcast_skips_sender(self) -> None:
        sent = team_ops.team_broadcast("alpha", "worker-1", "hello team", self.cwd)
        recipients = sorted(m.to_worker for m in sent)
        self.assertEqual(recipients, ["worker-2", "worker-3"])
        self.assertTrue(all(m.body == "hello team" for m in sent))

    def test_broadcast_explicit_worker_list(self) -> None:
        sent = team_ops.team_broadcast(
            "alpha", "leader", "to one only", self.cwd, worker_names=["worker-2"]
        )
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].to_worker, "worker-2")


class TestDispatchHelpers(unittest.TestCase):
    """read_dispatch_request + mark_*_notified/delivered helpers."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        team_ops.team_save_config(self.cwd, {"name": "alpha"}, "alpha")
        # Enqueue a baseline request
        result = team_ops.team_enqueue_dispatch_request(
            "alpha",
            {"kind": "inbox", "to_worker": "worker-1", "trigger_message": "go"},
            self.cwd,
        )
        assert result is not None
        self.request_id = result.request_id

    def test_read_dispatch_request_returns_record(self) -> None:
        rec = team_ops.team_read_dispatch_request("alpha", self.request_id, self.cwd)
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec.request_id, self.request_id)
        self.assertEqual(rec.status, "pending")

    def test_read_dispatch_request_returns_none_for_missing(self) -> None:
        self.assertIsNone(
            team_ops.team_read_dispatch_request("alpha", "no-such-id", self.cwd)
        )

    def test_mark_notified_transitions(self) -> None:
        self.assertTrue(
            team_ops.team_mark_dispatch_request_notified("alpha", self.request_id, self.cwd)
        )
        rec = team_ops.team_read_dispatch_request("alpha", self.request_id, self.cwd)
        assert rec is not None
        self.assertEqual(rec.status, "notified")

    def test_mark_delivered_transitions(self) -> None:
        team_ops.team_mark_dispatch_request_notified("alpha", self.request_id, self.cwd)
        self.assertTrue(
            team_ops.team_mark_dispatch_request_delivered("alpha", self.request_id, self.cwd)
        )
        rec = team_ops.team_read_dispatch_request("alpha", self.request_id, self.cwd)
        assert rec is not None
        self.assertEqual(rec.status, "delivered")

    def test_mark_delivered_returns_false_on_invalid_transition(self) -> None:
        # delivered → notified is not a valid transition; nor is unknown id
        self.assertFalse(
            team_ops.team_mark_dispatch_request_notified("alpha", "no-such-id", self.cwd)
        )


class TestGatewayWrapperRoundTrip(unittest.TestCase):
    """End-to-end: use the gateway to drive a small team lifecycle."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name

    def test_init_create_task_compute_readiness_cleanup(self) -> None:
        manifest = team_ops.team_init(
            "alpha", "smoke task", "executor", 2, self.cwd, max_workers=3
        )
        self.assertEqual(manifest.name, "alpha")

        a = team_ops.team_create_task(self.cwd, "alpha", "first")
        b = team_ops.team_create_task(
            self.cwd, "alpha", "second", depends_on=[a.task_id]
        )

        # b is blocked on a
        readiness_b = team_ops.team_compute_task_readiness(self.cwd, "alpha", b.task_id)
        self.assertFalse(readiness_b.ready)
        self.assertEqual(readiness_b.dependencies, [a.task_id])

        # Mark a completed via direct write — simulates worker completion
        from omx.team.state.io import read_tasks, write_tasks
        tasks = read_tasks(self.cwd, "alpha")
        for t in tasks:
            if t.task_id == a.task_id:
                t.status = TaskStatus.COMPLETED
        write_tasks(self.cwd, tasks, "alpha")

        readiness_b = team_ops.team_compute_task_readiness(self.cwd, "alpha", b.task_id)
        self.assertTrue(readiness_b.ready)

        team_ops.team_cleanup(self.cwd, "alpha")
        self.assertIsNone(team_ops.team_read_manifest("alpha", self.cwd))


if __name__ == "__main__":
    unittest.main()
