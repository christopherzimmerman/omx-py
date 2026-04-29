"""Tests for omx.core.dispatch — port of Rust dispatch tests."""

import json
import unittest

from omx.core.dispatch import (
    DispatchLog,
    DispatchNotFound,
    DispatchStatus,
    InvalidTransition,
)


class TestDispatchLog(unittest.TestCase):
    def test_queue_and_transition_happy_path(self):
        log = DispatchLog()
        log.queue("req-1", "worker-1")
        self.assertEqual(len(log.records), 1)
        self.assertEqual(log.records[0].status, DispatchStatus.PENDING)

        log.mark_notified("req-1", "tmux")
        self.assertEqual(log.records[0].status, DispatchStatus.NOTIFIED)

        log.mark_delivered("req-1")
        self.assertEqual(log.records[0].status, DispatchStatus.DELIVERED)

    def test_mark_failed_from_notified(self):
        log = DispatchLog()
        log.queue("req-1", "worker-1")
        log.mark_notified("req-1", "tmux")
        log.mark_failed("req-1", "send_error")
        self.assertEqual(log.records[0].status, DispatchStatus.FAILED)

    def test_invalid_transition_errors(self):
        log = DispatchLog()
        log.queue("req-1", "worker-1")
        with self.assertRaises(InvalidTransition):
            log.mark_delivered("req-1")

    def test_mark_failed_from_pending(self):
        log = DispatchLog()
        log.queue("req-1", "worker-1")
        log.mark_failed("req-1", "target_resolution_failed")
        self.assertEqual(log.records[0].status, DispatchStatus.FAILED)

    def test_not_found_errors(self):
        log = DispatchLog()
        with self.assertRaises(DispatchNotFound):
            log.mark_notified("nonexistent", "tmux")

    def test_backlog_snapshot_counts(self):
        log = DispatchLog()
        log.queue("req-1", "w1")
        log.queue("req-2", "w2")
        log.queue("req-3", "w3")
        log.mark_notified("req-2", "tmux")
        log.mark_notified("req-3", "tmux")
        log.mark_delivered("req-2")
        log.mark_failed("req-3", "error")

        snap = log.to_backlog_snapshot()
        self.assertEqual(snap.pending, 1)
        self.assertEqual(snap.notified, 0)
        self.assertEqual(snap.delivered, 1)
        self.assertEqual(snap.failed, 1)

    def test_queue_with_metadata_round_trips(self):
        log = DispatchLog()
        meta = {"priority": "high", "tags": ["urgent"]}
        log.queue("req-meta", "worker-1", meta)
        self.assertEqual(log.records[0].metadata, meta)

        data = log.to_dict()
        json_str = json.dumps(data)
        loaded = DispatchLog.from_dict(json.loads(json_str))
        self.assertEqual(loaded.records[0].metadata, meta)
        self.assertEqual(loaded.records[0].request_id, "req-meta")


if __name__ == "__main__":
    unittest.main()
