"""Tests for omx.core.replay — port of Rust replay tests."""

import unittest

from omx.core.replay import ReplayState


class TestReplayState(unittest.TestCase):
    def test_new_state_is_empty(self):
        state = ReplayState()
        self.assertIsNone(state.cursor)
        self.assertEqual(state.seen_count, 0)
        self.assertFalse(state.is_deferred)

    def test_request_replay_sets_cursor(self):
        state = ReplayState()
        state.request_replay("cursor-1")
        self.assertEqual(state.cursor, "cursor-1")

    def test_record_event_deduplicates(self):
        state = ReplayState()
        self.assertTrue(state.record_event("evt-1"))
        self.assertFalse(state.record_event("evt-1"))
        self.assertTrue(state.record_event("evt-2"))
        self.assertEqual(state.seen_count, 2)

    def test_deferred_notification(self):
        state = ReplayState()
        state.defer_leader_notification()
        self.assertTrue(state.is_deferred)
        state.clear_deferred()
        self.assertFalse(state.is_deferred)

    def test_snapshot_reflects_state(self):
        state = ReplayState()
        state.request_replay("cur-1")
        state.defer_leader_notification()
        snap = state.to_snapshot()
        self.assertEqual(snap.cursor, "cur-1")
        self.assertTrue(snap.deferred_leader_notification)


if __name__ == "__main__":
    unittest.main()
