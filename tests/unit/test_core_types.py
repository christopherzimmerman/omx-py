"""Tests for omx.core.types — port of Rust lib.rs tests."""

import json
import unittest

from omx.core.types import (
    AuthoritySnapshot,
    BacklogSnapshot,
    DispatchOutcomeReason,
    RuntimeCommand,
    RuntimeEvent,
    RuntimeSnapshot,
    WorkerCli,
    classify_dispatch_outcome,
    submit_presses_for_worker_cli,
)


class TestCoreTypes(unittest.TestCase):
    def test_snapshot_defaults_to_blocked_state(self):
        snapshot = RuntimeSnapshot()
        self.assertFalse(snapshot.is_ready())
        self.assertEqual(snapshot.backlog, BacklogSnapshot())
        self.assertIsNone(snapshot.authority.owner)
        self.assertEqual(snapshot.readiness.reasons, ["authority lease not acquired"])

    def test_backlog_transitions(self):
        backlog = BacklogSnapshot()
        backlog.pending += 1
        self.assertEqual(backlog.pending, 1)

    def test_authority_state_can_be_marked_stale(self):
        authority = AuthoritySnapshot.acquire(
            "worker-1", "lease-1", "2026-03-19T01:40:37Z"
        )
        authority.mark_stale("lease expired")
        self.assertTrue(authority.stale)
        self.assertEqual(authority.stale_reason, "lease expired")
        authority.clear_stale()
        self.assertFalse(authority.stale)
        self.assertIsNone(authority.stale_reason)

    def test_worker_cli_submit_policy(self):
        self.assertEqual(submit_presses_for_worker_cli(WorkerCli.CLAUDE), 1)
        self.assertEqual(submit_presses_for_worker_cli(WorkerCli.CODEX), 2)
        self.assertEqual(
            submit_presses_for_worker_cli(WorkerCli.from_label("other")), 2
        )

    def test_dispatch_outcome_classification(self):
        confirmed = classify_dispatch_outcome(
            True, True, True, True, True, False, False
        )
        self.assertEqual(confirmed.status, "notified")
        self.assertEqual(confirmed.reason, DispatchOutcomeReason.DELIVERED_CONFIRMED)

        active_task = classify_dispatch_outcome(
            True, True, True, True, True, True, False
        )
        self.assertEqual(
            active_task.reason, DispatchOutcomeReason.DELIVERED_CONFIRMED_ACTIVE_TASK
        )

        unconfirmed_retry = classify_dispatch_outcome(
            True, True, True, True, False, False, True
        )
        self.assertEqual(unconfirmed_retry.status, "pending")
        self.assertEqual(
            unconfirmed_retry.reason, DispatchOutcomeReason.DELIVERED_UNCONFIRMED
        )

        unconfirmed_failed = classify_dispatch_outcome(
            True, True, True, True, False, False, False
        )
        self.assertEqual(unconfirmed_failed.status, "failed")

    def test_snapshot_serializes_to_json(self):
        snapshot = RuntimeSnapshot()
        json_str = json.dumps(snapshot.to_dict())
        deserialized = RuntimeSnapshot.from_dict(json.loads(json_str))
        self.assertEqual(snapshot.schema_version, deserialized.schema_version)
        self.assertEqual(snapshot.authority.owner, deserialized.authority.owner)

    def test_runtime_command_serializes_to_json(self):
        cmd = RuntimeCommand.acquire_authority("w1", "l1", "2026-03-19T02:00:00Z")
        json_str = json.dumps(cmd.to_dict())
        data = json.loads(json_str)
        self.assertEqual(data["command"], "AcquireAuthority")
        self.assertEqual(data["owner"], "w1")

    def test_runtime_event_serializes_to_json(self):
        event = RuntimeEvent(event="SnapshotCaptured")
        json_str = json.dumps(event.to_dict())
        deserialized = RuntimeEvent.from_dict(json.loads(json_str))
        self.assertEqual(event.event, deserialized.event)

    def test_mailbox_event_deserializes_without_body(self):
        data = {
            "event": "MailboxMessageCreated",
            "message_id": "msg-1",
            "from_worker": "worker-1",
            "to_worker": "leader-fixed",
        }
        event = RuntimeEvent.from_dict(data)
        self.assertEqual(event.event, "MailboxMessageCreated")
        self.assertIsNone(event.body)


if __name__ == "__main__":
    unittest.main()
