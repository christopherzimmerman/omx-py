"""Tests for omx.core.engine — port of Rust engine tests."""

import json
import tempfile
import unittest
from pathlib import Path

from omx.core.authority import AuthorityLease
from omx.core.dispatch import DispatchLog
from omx.core.engine import EngineError, RuntimeEngine, derive_readiness
from omx.core.replay import ReplayState
from omx.core.types import RuntimeCommand, RuntimeEvent


class TestRuntimeEngine(unittest.TestCase):
    def test_process_acquire_authority(self):
        engine = RuntimeEngine()
        event = engine.process(
            RuntimeCommand.acquire_authority("w1", "l1", "2026-03-19T02:00:00Z")
        )
        self.assertEqual(event.event, "AuthorityAcquired")
        snap = engine.snapshot()
        self.assertEqual(snap.authority.owner, "w1")
        self.assertTrue(snap.is_ready())

    def test_process_full_dispatch_cycle(self):
        engine = RuntimeEngine()
        engine.process(
            RuntimeCommand.acquire_authority("w1", "l1", "2026-03-19T02:00:00Z")
        )
        engine.process(RuntimeCommand.queue_dispatch("req-1", "worker-2"))
        engine.process(RuntimeCommand.mark_notified("req-1", "tmux"))
        engine.process(RuntimeCommand.mark_delivered("req-1"))

        snap = engine.snapshot()
        self.assertEqual(snap.backlog.delivered, 1)
        self.assertEqual(snap.backlog.pending, 0)

    def test_snapshot_shows_blocked_without_authority(self):
        engine = RuntimeEngine()
        snap = engine.snapshot()
        self.assertFalse(snap.is_ready())
        self.assertEqual(snap.readiness.reasons, ["authority lease not acquired"])

    def test_process_replay_request(self):
        engine = RuntimeEngine()
        engine.process(RuntimeCommand.request_replay("cur-1"))
        snap = engine.snapshot()
        self.assertEqual(snap.replay.cursor, "cur-1")

    def test_event_log_accumulates(self):
        engine = RuntimeEngine()
        engine.process(RuntimeCommand.capture_snapshot())
        engine.process(RuntimeCommand.capture_snapshot())
        self.assertEqual(len(engine.event_log), 2)

    def test_persist_and_load_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            engine = RuntimeEngine().with_state_dir(state_dir)
            engine.process(
                RuntimeCommand.acquire_authority("w1", "l1", "2026-03-19T02:00:00Z")
            )
            engine.process(RuntimeCommand.queue_dispatch("req-1", "worker-2"))
            engine.persist()

            loaded = RuntimeEngine.load(state_dir)
            original_snap = engine.snapshot()
            loaded_snap = loaded.snapshot()
            self.assertEqual(original_snap.authority.owner, loaded_snap.authority.owner)
            self.assertEqual(original_snap.backlog.pending, loaded_snap.backlog.pending)
            self.assertEqual(len(loaded.event_log), 2)

    def test_persist_and_load_preserves_mailbox_body(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            engine = RuntimeEngine().with_state_dir(state_dir)
            engine.process(
                RuntimeCommand.create_mailbox_message(
                    "msg-1",
                    "worker-1",
                    "leader-fixed",
                    "ACK: worker-1 initialized",
                )
            )
            engine.persist()

            loaded = RuntimeEngine.load(state_dir)
            loaded.write_compatibility_view()

            mailbox_data = json.loads((state_dir / "mailbox.json").read_text())
            self.assertEqual(
                mailbox_data["records"][0]["body"], "ACK: worker-1 initialized"
            )

    def test_load_backfills_legacy_mailbox_event_body(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)

            events = [
                RuntimeEvent(
                    event="MailboxMessageCreated",
                    message_id="msg-legacy",
                    from_worker="worker-1",
                    to_worker="leader-fixed",
                    body=None,
                ).to_dict()
            ]
            (state_dir / "events.json").write_text(json.dumps(events))

            mailbox = {
                "records": [
                    {
                        "message_id": "msg-legacy",
                        "from_worker": "worker-1",
                        "to_worker": "leader-fixed",
                        "body": "recovered body",
                        "created_at": "2026-04-04T00:00:00.000Z",
                        "notified_at": None,
                        "delivered_at": None,
                    }
                ]
            }
            (state_dir / "mailbox.json").write_text(json.dumps(mailbox))
            (state_dir / "engine.lock").touch()

            loaded = RuntimeEngine.load(state_dir)
            loaded.persist()

            persisted_events = json.loads((state_dir / "events.json").read_text())
            self.assertEqual(persisted_events[0]["body"], "recovered body")

    def test_derive_readiness_stale_authority(self):
        authority = AuthorityLease()
        authority.acquire("w1", "l1", "2026-03-19T02:00:00Z")
        authority.mark_stale("expired")
        dispatch = DispatchLog()
        replay = ReplayState()

        readiness = derive_readiness(authority, dispatch, replay)
        self.assertFalse(readiness.ready)
        self.assertIn("stale", readiness.reasons[0])

    def test_renew_authority_via_engine(self):
        engine = RuntimeEngine()
        engine.process(
            RuntimeCommand.acquire_authority("w1", "l1", "2026-03-19T02:00:00Z")
        )
        event = engine.process(
            RuntimeCommand.renew_authority("w1", "l2", "2026-03-19T03:00:00Z")
        )
        self.assertEqual(event.event, "AuthorityRenewed")

    def test_acquire_authority_wrong_owner_fails(self):
        engine = RuntimeEngine()
        engine.process(
            RuntimeCommand.acquire_authority("w1", "l1", "2026-03-19T02:00:00Z")
        )
        with self.assertRaises(EngineError):
            engine.process(
                RuntimeCommand.acquire_authority("w2", "l2", "2026-03-19T03:00:00Z")
            )

    def test_compatibility_view_writes_section_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            engine = RuntimeEngine().with_state_dir(state_dir)
            engine.process(
                RuntimeCommand.acquire_authority("w1", "l1", "2026-03-19T02:00:00Z")
            )
            engine.process(RuntimeCommand.queue_dispatch("req-1", "worker-2"))
            engine.write_compatibility_view()

            authority = json.loads((state_dir / "authority.json").read_text())
            self.assertEqual(authority["owner"], "w1")

            backlog = json.loads((state_dir / "backlog.json").read_text())
            self.assertEqual(backlog["pending"], 1)

            readiness = json.loads((state_dir / "readiness.json").read_text())
            self.assertTrue(readiness["ready"])

            replay = json.loads((state_dir / "replay.json").read_text())
            self.assertFalse(replay["deferred_leader_notification"])

            self.assertTrue((state_dir / "dispatch.json").exists())

    def test_mark_failed_dispatch_via_engine(self):
        engine = RuntimeEngine()
        engine.process(RuntimeCommand.queue_dispatch("req-1", "worker-2"))
        engine.process(RuntimeCommand.mark_notified("req-1", "tmux"))
        event = engine.process(RuntimeCommand.mark_failed("req-1", "timeout"))
        self.assertEqual(event.event, "DispatchFailed")
        self.assertEqual(engine.snapshot().backlog.failed, 1)

    def test_compact_removes_delivered_and_failed_events(self):
        engine = RuntimeEngine()
        engine.process(RuntimeCommand.queue_dispatch("req-pending", "w1"))
        engine.process(RuntimeCommand.queue_dispatch("req-delivered", "w2"))
        engine.process(RuntimeCommand.queue_dispatch("req-failed", "w3"))

        engine.process(RuntimeCommand.mark_notified("req-delivered", "tmux"))
        engine.process(RuntimeCommand.mark_delivered("req-delivered"))

        engine.process(RuntimeCommand.mark_notified("req-failed", "tmux"))
        engine.process(RuntimeCommand.mark_failed("req-failed", "timeout"))

        self.assertEqual(len(engine.event_log), 7)
        engine.compact()

        remaining = engine.event_log
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].request_id, "req-pending")


if __name__ == "__main__":
    unittest.main()
