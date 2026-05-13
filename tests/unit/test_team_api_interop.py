"""Tests for the team API interop envelope (port of api-interop.ts)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from omx.team import api_interop, team_ops
from omx.team.api_interop import (
    LEGACY_TEAM_MCP_TOOLS,
    TEAM_API_OPERATIONS,
    TEAM_TASK_APPROVAL_STATUSES,
    TEAM_TASK_STATUSES,
    build_legacy_team_deprecation_hint,
    execute_team_api_operation,
    resolve_team_api_operation,
)


_TS_TEAM_API_OPERATIONS = (
    "send-message",
    "broadcast",
    "mailbox-list",
    "mailbox-mark-delivered",
    "mailbox-mark-notified",
    "create-task",
    "read-task",
    "list-tasks",
    "update-task",
    "claim-task",
    "transition-task-status",
    "release-task-claim",
    "read-config",
    "read-manifest",
    "read-worker-status",
    "read-worker-heartbeat",
    "update-worker-heartbeat",
    "write-worker-inbox",
    "write-worker-identity",
    "append-event",
    "read-events",
    "await-event",
    "read-idle-state",
    "read-stall-state",
    "get-summary",
    "cleanup",
    "orphan-cleanup",
    "write-shutdown-request",
    "read-shutdown-ack",
    "read-monitor-snapshot",
    "write-monitor-snapshot",
    "read-task-approval",
    "write-task-approval",
)


class TestSurfaceConstants(unittest.TestCase):
    """Constants match the TS tuple verbatim."""

    def test_team_api_operations_matches_ts(self) -> None:
        self.assertEqual(TEAM_API_OPERATIONS, _TS_TEAM_API_OPERATIONS)

    def test_team_api_operations_is_tuple(self) -> None:
        self.assertIsInstance(TEAM_API_OPERATIONS, tuple)

    def test_team_api_operations_has_33_entries(self) -> None:
        self.assertEqual(len(TEAM_API_OPERATIONS), 33)

    def test_task_statuses_constant(self) -> None:
        self.assertEqual(
            TEAM_TASK_STATUSES,
            ("pending", "blocked", "in_progress", "completed", "failed"),
        )

    def test_approval_statuses_constant(self) -> None:
        self.assertEqual(
            TEAM_TASK_APPROVAL_STATUSES, ("pending", "approved", "rejected")
        )

    def test_legacy_tools_constant_is_tuple(self) -> None:
        self.assertIsInstance(LEGACY_TEAM_MCP_TOOLS, tuple)
        self.assertIn("team_send_message", LEGACY_TEAM_MCP_TOOLS)


class TestResolveTeamApiOperation(unittest.TestCase):
    """Name normalization + lookup parity with TS ``resolveTeamApiOperation``."""

    def test_canonical_name_resolves(self) -> None:
        self.assertEqual(resolve_team_api_operation("send-message"), "send-message")

    def test_legacy_underscored_name_resolves(self) -> None:
        self.assertEqual(
            resolve_team_api_operation("team_send_message"), "send-message"
        )

    def test_legacy_uppercased_name_resolves(self) -> None:
        self.assertEqual(resolve_team_api_operation("TEAM_BROADCAST"), "broadcast")

    def test_legacy_with_surrounding_whitespace(self) -> None:
        self.assertEqual(
            resolve_team_api_operation("  team_create_task  "), "create-task"
        )

    def test_unknown_returns_none(self) -> None:
        self.assertIsNone(resolve_team_api_operation("not-a-real-op"))

    def test_empty_string_returns_none(self) -> None:
        self.assertIsNone(resolve_team_api_operation(""))

    def test_non_string_returns_none(self) -> None:
        self.assertIsNone(resolve_team_api_operation(None))  # type: ignore[arg-type]


class TestBuildLegacyDeprecationHint(unittest.TestCase):
    """Deprecation hint includes the canonical op when known."""

    def test_known_tool_includes_operation(self) -> None:
        hint = build_legacy_team_deprecation_hint("team_send_message", {"a": 1})
        self.assertIn("send-message", hint)
        self.assertIn("'{\"a\": 1}'", hint)

    def test_unknown_tool_falls_back_to_placeholder(self) -> None:
        hint = build_legacy_team_deprecation_hint("team_does_not_exist", {})
        self.assertIn("<operation>", hint)
        self.assertIn("'{}'", hint)

    def test_default_args_is_empty_object(self) -> None:
        hint = build_legacy_team_deprecation_hint("team_broadcast")
        self.assertIn("broadcast", hint)
        self.assertIn("'{}'", hint)


# --- Execute fixtures ------------------------------------------------------


class _ExecuteCase(unittest.TestCase):
    """Common fixture: a team with one worker and a baseline config."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        self.team = "alpha"
        # Save a minimal team config — required by read-config + create-task.
        team_ops.team_save_config(
            self.cwd, {"name": self.team, "next_task_id": 1}, self.team
        )

    def _exec(
        self, operation: str, args: dict[str, object] | None = None
    ) -> dict[str, object]:
        return execute_team_api_operation(operation, dict(args or {}), self.cwd)


class TestEnvelopeShape(_ExecuteCase):
    """Envelope wrapper invariants."""

    def test_unknown_operation_returns_unknown_envelope(self) -> None:
        envelope = self._exec("totally-fake-op")
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["operation"], "unknown")
        self.assertEqual(envelope["error"]["code"], "unknown_operation")

    def test_invalid_team_name_returns_invalid_input(self) -> None:
        envelope = self._exec("list-tasks", {"team_name": "BAD NAME"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")
        self.assertIn("team_name", envelope["error"]["message"])

    def test_invalid_worker_name_returns_invalid_input(self) -> None:
        envelope = self._exec(
            "read-worker-status",
            {"team_name": self.team, "worker": "BAD"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_invalid_task_id_returns_invalid_input(self) -> None:
        envelope = self._exec(
            "read-task",
            {"team_name": self.team, "task_id": "abc"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_missing_team_name_returns_invalid_input(self) -> None:
        envelope = self._exec("list-tasks", {})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")


class TestCreateTask(_ExecuteCase):
    def test_create_task_happy_path(self) -> None:
        envelope = self._exec(
            "create-task",
            {
                "team_name": self.team,
                "subject": "Title",
                "description": "Body",
            },
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["operation"], "create-task")
        self.assertIn("task", envelope["data"])
        self.assertEqual(envelope["data"]["task"]["task_id"], "1")

    def test_create_task_missing_required_fields(self) -> None:
        envelope = self._exec(
            "create-task",
            {"team_name": self.team, "subject": "x"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")


class TestReadAndListTasks(_ExecuteCase):
    def test_read_task_missing(self) -> None:
        envelope = self._exec("read-task", {"team_name": self.team, "task_id": "999"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "task_not_found")

    def test_list_tasks_empty(self) -> None:
        envelope = self._exec("list-tasks", {"team_name": self.team})
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"]["count"], 0)
        self.assertEqual(envelope["data"]["tasks"], [])

    def test_list_tasks_after_create(self) -> None:
        self._exec(
            "create-task",
            {"team_name": self.team, "subject": "s", "description": "d"},
        )
        self._exec(
            "create-task",
            {"team_name": self.team, "subject": "s2", "description": "d2"},
        )
        envelope = self._exec("list-tasks", {"team_name": self.team})
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"]["count"], 2)

    def test_read_task_after_create(self) -> None:
        created = self._exec(
            "create-task",
            {"team_name": self.team, "subject": "s", "description": "d"},
        )
        task_id = created["data"]["task"]["task_id"]
        envelope = self._exec("read-task", {"team_name": self.team, "task_id": task_id})
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"]["task"]["task_id"], task_id)


class TestUpdateTask(_ExecuteCase):
    def test_update_task_rejects_lifecycle_fields(self) -> None:
        envelope = self._exec(
            "update-task",
            {
                "team_name": self.team,
                "task_id": "1",
                "status": "completed",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")
        self.assertIn("status", envelope["error"]["message"])

    def test_update_task_rejects_unexpected_fields(self) -> None:
        envelope = self._exec(
            "update-task",
            {
                "team_name": self.team,
                "task_id": "1",
                "unsupported_field": "x",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_update_task_validates_blocked_by_array(self) -> None:
        envelope = self._exec(
            "update-task",
            {
                "team_name": self.team,
                "task_id": "1",
                "blocked_by": ["abc"],
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_update_task_missing_returns_task_not_found(self) -> None:
        envelope = self._exec(
            "update-task",
            {
                "team_name": self.team,
                "task_id": "99",
                "description": "x",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "task_not_found")

    def test_update_task_description_happy_path(self) -> None:
        created = self._exec(
            "create-task",
            {"team_name": self.team, "subject": "s", "description": "d"},
        )
        task_id = created["data"]["task"]["task_id"]
        envelope = self._exec(
            "update-task",
            {
                "team_name": self.team,
                "task_id": task_id,
                "description": "updated body",
            },
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertIn("updated body", envelope["data"]["task"]["description"])


class TestReadConfig(_ExecuteCase):
    def test_read_config_returns_config(self) -> None:
        envelope = self._exec("read-config", {"team_name": self.team})
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"]["config"]["name"], self.team)

    def test_read_config_missing_team(self) -> None:
        envelope = self._exec("read-config", {"team_name": "ghost"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "team_not_found")


class TestWorkerOps(_ExecuteCase):
    def test_write_worker_inbox_happy_path(self) -> None:
        envelope = self._exec(
            "write-worker-inbox",
            {
                "team_name": self.team,
                "worker": "worker-1",
                "content": "hello",
            },
        )
        self.assertTrue(envelope["ok"])
        inbox = (
            Path(self.cwd)
            / ".omx"
            / "team"
            / self.team
            / "workers"
            / "worker-1"
            / "inbox.md"
        )
        self.assertTrue(inbox.exists())
        self.assertEqual(inbox.read_text(encoding="utf-8"), "hello")

    def test_write_worker_inbox_missing_required(self) -> None:
        envelope = self._exec(
            "write-worker-inbox",
            {"team_name": self.team, "worker": "worker-1"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_read_worker_status_returns_none_when_absent(self) -> None:
        envelope = self._exec(
            "read-worker-status",
            {"team_name": self.team, "worker": "worker-1"},
        )
        self.assertTrue(envelope["ok"])
        self.assertIsNone(envelope["data"]["status"])

    def test_update_worker_heartbeat_happy_path(self) -> None:
        envelope = self._exec(
            "update-worker-heartbeat",
            {
                "team_name": self.team,
                "worker": "worker-1",
                "pid": 12345,
                "turn_count": 7,
                "alive": True,
            },
        )
        self.assertTrue(envelope["ok"], envelope)
        read = self._exec(
            "read-worker-heartbeat",
            {"team_name": self.team, "worker": "worker-1"},
        )
        self.assertTrue(read["ok"])
        self.assertEqual(read["data"]["heartbeat"]["pid"], 12345)
        self.assertEqual(read["data"]["heartbeat"]["turn_count"], 7)

    def test_update_worker_heartbeat_rejects_non_numeric_pid(self) -> None:
        envelope = self._exec(
            "update-worker-heartbeat",
            {
                "team_name": self.team,
                "worker": "worker-1",
                "pid": "not-a-number",
                "turn_count": 1,
                "alive": True,
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_write_worker_identity_happy_path(self) -> None:
        envelope = self._exec(
            "write-worker-identity",
            {
                "team_name": self.team,
                "worker": "worker-1",
                "index": 0,
                "role": "executor",
                "pane_id": "%5",
            },
        )
        self.assertTrue(envelope["ok"], envelope)
        identity_path = (
            Path(self.cwd)
            / ".omx"
            / "team"
            / self.team
            / "workers"
            / "worker-1"
            / "identity.json"
        )
        self.assertTrue(identity_path.exists())
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
        self.assertEqual(identity["role"], "executor")
        self.assertEqual(identity["pane_id"], "%5")

    def test_write_worker_identity_rejects_missing_role(self) -> None:
        envelope = self._exec(
            "write-worker-identity",
            {
                "team_name": self.team,
                "worker": "worker-1",
                "index": 0,
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")


class TestEvents(_ExecuteCase):
    def test_append_event_happy_path(self) -> None:
        envelope = self._exec(
            "append-event",
            {
                "team_name": self.team,
                "type": "task_completed",
                "worker": "worker-1",
                "task_id": "1",
            },
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["data"]["event"]["event_type"], "task_completed")

    def test_append_event_rejects_unknown_type(self) -> None:
        envelope = self._exec(
            "append-event",
            {
                "team_name": self.team,
                "type": "not-a-real-event-type",
                "worker": "worker-1",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_read_events_empty_team(self) -> None:
        envelope = self._exec("read-events", {"team_name": self.team})
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"]["count"], 0)
        self.assertEqual(envelope["data"]["events"], [])

    def test_read_events_rejects_unknown_type(self) -> None:
        envelope = self._exec(
            "read-events",
            {"team_name": self.team, "type": "not-real"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_await_event_times_out(self) -> None:
        envelope = self._exec(
            "await-event",
            {"team_name": self.team, "timeout_ms": 10, "poll_ms": 5},
        )
        self.assertTrue(envelope["ok"])
        self.assertEqual(envelope["data"]["status"], "timeout")

    def test_await_event_rejects_bad_timeout(self) -> None:
        envelope = self._exec(
            "await-event",
            {"team_name": self.team, "timeout_ms": -5},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")


class TestMailboxAndBroadcast(_ExecuteCase):
    def test_mailbox_list_empty(self) -> None:
        envelope = self._exec(
            "mailbox-list",
            {"team_name": self.team, "worker": "worker-1"},
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["data"]["count"], 0)

    def test_mailbox_list_missing_worker(self) -> None:
        envelope = self._exec(
            "mailbox-list",
            {"team_name": self.team},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_mailbox_mark_delivered_missing_required(self) -> None:
        envelope = self._exec(
            "mailbox-mark-delivered",
            {"team_name": self.team, "worker": "worker-1"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_mailbox_mark_notified_missing_required(self) -> None:
        envelope = self._exec(
            "mailbox-mark-notified",
            {"team_name": self.team, "worker": "worker-1"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_broadcast_missing_required(self) -> None:
        envelope = self._exec(
            "broadcast",
            {"team_name": self.team, "from_worker": "leader-fixed"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")


class TestShutdownAndApproval(_ExecuteCase):
    def test_write_shutdown_request_happy_path(self) -> None:
        envelope = self._exec(
            "write-shutdown-request",
            {
                "team_name": self.team,
                "worker": "worker-1",
                "requested_by": "leader",
            },
        )
        self.assertTrue(envelope["ok"], envelope)
        path = (
            Path(self.cwd)
            / ".omx"
            / "team"
            / self.team
            / "workers"
            / "worker-1"
            / "shutdown-request.json"
        )
        self.assertTrue(path.exists())

    def test_write_shutdown_request_missing_required(self) -> None:
        envelope = self._exec(
            "write-shutdown-request",
            {"team_name": self.team},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_read_shutdown_ack_missing_returns_null(self) -> None:
        envelope = self._exec(
            "read-shutdown-ack",
            {"team_name": self.team, "worker": "worker-1"},
        )
        self.assertTrue(envelope["ok"])
        self.assertIsNone(envelope["data"]["ack"])

    def test_write_task_approval_happy_path(self) -> None:
        envelope = self._exec(
            "write-task-approval",
            {
                "team_name": self.team,
                "task_id": "1",
                "status": "approved",
                "reviewer": "leader-fixed",
                "decision_reason": "looks good",
            },
        )
        self.assertTrue(envelope["ok"], envelope)
        self.assertEqual(envelope["data"]["status"], "approved")

    def test_write_task_approval_rejects_invalid_status(self) -> None:
        envelope = self._exec(
            "write-task-approval",
            {
                "team_name": self.team,
                "task_id": "1",
                "status": "fake",
                "reviewer": "leader-fixed",
                "decision_reason": "?",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_read_task_approval_returns_null_when_absent(self) -> None:
        envelope = self._exec(
            "read-task-approval",
            {"team_name": self.team, "task_id": "1"},
        )
        self.assertTrue(envelope["ok"])
        self.assertIsNone(envelope["data"]["approval"])


class TestMonitorAndManifest(_ExecuteCase):
    def test_read_monitor_snapshot_returns_null_when_absent(self) -> None:
        envelope = self._exec("read-monitor-snapshot", {"team_name": self.team})
        self.assertTrue(envelope["ok"])
        self.assertIsNone(envelope["data"]["snapshot"])

    def test_write_monitor_snapshot_round_trips(self) -> None:
        envelope = self._exec(
            "write-monitor-snapshot",
            {
                "team_name": self.team,
                "snapshot": {
                    "taskStatusById": {"1": "pending"},
                    "workerAliveByName": {"worker-1": True},
                    "workerStateByName": {"worker-1": "idle"},
                },
            },
        )
        self.assertTrue(envelope["ok"], envelope)
        read = self._exec("read-monitor-snapshot", {"team_name": self.team})
        self.assertTrue(read["ok"])
        self.assertEqual(read["data"]["snapshot"]["taskStatusById"], {"1": "pending"})

    def test_write_monitor_snapshot_missing_snapshot(self) -> None:
        envelope = self._exec("write-monitor-snapshot", {"team_name": self.team})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_read_manifest_missing_returns_not_found(self) -> None:
        envelope = self._exec("read-manifest", {"team_name": self.team})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "manifest_not_found")


class TestNotImplemented(_ExecuteCase):
    """Operations that depend on Phase 2.9 runtime are gracefully gated."""

    def test_send_message_not_implemented(self) -> None:
        envelope = self._exec(
            "send-message",
            {
                "team_name": self.team,
                "from_worker": "worker-1",
                "to_worker": "worker-2",
                "body": "hello",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "not_implemented_yet")

    def test_send_message_validates_from_worker(self) -> None:
        envelope = self._exec(
            "send-message",
            {
                "team_name": self.team,
                "to_worker": "worker-2",
                "body": "hello",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")
        self.assertIn("from_worker", envelope["error"]["message"])

    def test_cleanup_routes_to_not_implemented(self) -> None:
        envelope = self._exec("cleanup", {"team_name": self.team})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "not_implemented_yet")

    def test_read_stall_state_not_implemented(self) -> None:
        envelope = self._exec("read-stall-state", {"team_name": self.team})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "not_implemented_yet")


class TestCleanupAndIdle(_ExecuteCase):
    def test_orphan_cleanup_removes_team_state(self) -> None:
        team_dir = Path(self.cwd) / ".omx" / "team" / self.team
        self.assertTrue(team_dir.exists())
        envelope = self._exec("orphan-cleanup", {"team_name": self.team})
        self.assertTrue(envelope["ok"], envelope)
        self.assertFalse(team_dir.exists())

    def test_orphan_cleanup_idempotent(self) -> None:
        self._exec("orphan-cleanup", {"team_name": self.team})
        envelope = self._exec("orphan-cleanup", {"team_name": self.team})
        # Even when the team is gone the envelope should remain ok=True.
        self.assertTrue(envelope["ok"], envelope)

    def test_read_idle_state_team_not_found(self) -> None:
        envelope = self._exec(
            "read-idle-state",
            {"team_name": "ghost"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "team_not_found")

    def test_get_summary_team_not_found(self) -> None:
        envelope = self._exec("get-summary", {"team_name": "ghost"})
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "team_not_found")

    def test_read_idle_state_happy_path_returns_shape(self) -> None:
        envelope = self._exec("read-idle-state", {"team_name": self.team})
        self.assertTrue(envelope["ok"], envelope)
        data = envelope["data"]
        for key in (
            "team_name",
            "worker_count",
            "idle_worker_count",
            "idle_workers",
            "non_idle_workers",
            "all_workers_idle",
            "last_idle_transition_by_worker",
            "last_all_workers_idle_event",
            "source",
        ):
            self.assertIn(key, data)


class TestClaimAndTransition(_ExecuteCase):
    def test_claim_task_missing_required(self) -> None:
        envelope = self._exec(
            "claim-task",
            {"team_name": self.team, "task_id": "1"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_claim_task_validates_expected_version(self) -> None:
        envelope = self._exec(
            "claim-task",
            {
                "team_name": self.team,
                "task_id": "1",
                "worker": "worker-1",
                "expected_version": 0,
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_claim_task_unknown_task(self) -> None:
        envelope = self._exec(
            "claim-task",
            {
                "team_name": self.team,
                "task_id": "9999",
                "worker": "worker-1",
            },
        )
        # Gateway returns the dict from claim_task; "ok" inside the envelope is
        # True at the envelope layer (operation succeeded structurally), but the
        # inner data carries an error key from team_ops.
        self.assertTrue(envelope["ok"], envelope)
        self.assertIn("error", envelope["data"])

    def test_transition_task_status_rejects_bad_statuses(self) -> None:
        envelope = self._exec(
            "transition-task-status",
            {
                "team_name": self.team,
                "task_id": "1",
                "from": "bogus",
                "to": "completed",
                "claim_token": "tok",
            },
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_transition_task_status_missing_required(self) -> None:
        envelope = self._exec(
            "transition-task-status",
            {"team_name": self.team, "task_id": "1"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")

    def test_release_task_claim_missing_required(self) -> None:
        envelope = self._exec(
            "release-task-claim",
            {"team_name": self.team, "task_id": "1"},
        )
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_input")


class TestHandleTeamToolCall(_ExecuteCase):
    """Legacy MCP-tool-call entrypoint still works."""

    def test_unknown_tool_returns_deprecation_envelope(self) -> None:
        result = api_interop.handle_team_tool_call("team_does_not_exist", {})
        self.assertTrue(result["isError"])
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["code"], "deprecated_cli_only")
        self.assertIn("hint", payload)

    def test_known_tool_routes_through_gateway(self) -> None:
        prev_cwd = os.getcwd()
        os.chdir(self.cwd)
        try:
            result = api_interop.handle_team_tool_call(
                "team_list_tasks", {"team_name": self.team}
            )
        finally:
            os.chdir(prev_cwd)
        self.assertFalse(result["isError"], result)
        payload = json.loads(result["content"][0]["text"])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["operation"], "list-tasks")


class TestOperationCoverage(unittest.TestCase):
    """Every canonical operation is reachable without raising."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = self.tmp.name
        self.team = "alpha"
        team_ops.team_save_config(
            self.cwd, {"name": self.team, "next_task_id": 1}, self.team
        )

    def test_every_operation_returns_envelope_shape(self) -> None:
        """Smoke test: call every op with team_name only; never raises."""
        for op in TEAM_API_OPERATIONS:
            envelope = execute_team_api_operation(
                op, {"team_name": self.team}, self.cwd
            )
            self.assertIn("operation", envelope, op)
            self.assertIn("ok", envelope, op)
            if envelope["ok"]:
                self.assertIn("data", envelope, op)
            else:
                self.assertIn("error", envelope, op)
                self.assertIn("code", envelope["error"], op)


if __name__ == "__main__":
    unittest.main()
