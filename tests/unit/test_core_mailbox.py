"""Tests for omx.core.mailbox — port of Rust mailbox tests."""

import json
import unittest

from omx.core.mailbox import (
    MailboxAlreadyDelivered,
    MailboxLog,
    MailboxNotFound,
)


class TestMailboxLog(unittest.TestCase):
    def test_create_adds_record_with_timestamp(self):
        log = MailboxLog()
        log.create("msg-1", "worker-a", "worker-b", "hello")
        self.assertEqual(len(log.records), 1)
        r = log.records[0]
        self.assertEqual(r.message_id, "msg-1")
        self.assertEqual(r.from_worker, "worker-a")
        self.assertEqual(r.to_worker, "worker-b")
        self.assertEqual(r.body, "hello")
        self.assertTrue(r.created_at)
        self.assertIsNone(r.notified_at)
        self.assertIsNone(r.delivered_at)

    def test_mark_notified_sets_timestamp(self):
        log = MailboxLog()
        log.create("msg-1", "a", "b", "body")
        log.mark_notified("msg-1")
        self.assertIsNotNone(log.records[0].notified_at)

    def test_mark_delivered_sets_timestamp(self):
        log = MailboxLog()
        log.create("msg-1", "a", "b", "body")
        log.mark_delivered("msg-1")
        self.assertIsNotNone(log.records[0].delivered_at)

    def test_mark_notified_not_found(self):
        log = MailboxLog()
        with self.assertRaises(MailboxNotFound):
            log.mark_notified("nonexistent")

    def test_mark_delivered_not_found(self):
        log = MailboxLog()
        with self.assertRaises(MailboxNotFound):
            log.mark_delivered("nonexistent")

    def test_mark_notified_already_delivered_errors(self):
        log = MailboxLog()
        log.create("msg-1", "a", "b", "body")
        log.mark_delivered("msg-1")
        with self.assertRaises(MailboxAlreadyDelivered):
            log.mark_notified("msg-1")

    def test_mark_delivered_twice_errors(self):
        log = MailboxLog()
        log.create("msg-1", "a", "b", "body")
        log.mark_delivered("msg-1")
        with self.assertRaises(MailboxAlreadyDelivered):
            log.mark_delivered("msg-1")

    def test_full_lifecycle(self):
        log = MailboxLog()
        log.create("msg-1", "worker-a", "worker-b", "task payload")
        log.mark_notified("msg-1")
        log.mark_delivered("msg-1")
        r = log.records[0]
        self.assertIsNotNone(r.notified_at)
        self.assertIsNotNone(r.delivered_at)

    def test_multiple_messages(self):
        log = MailboxLog()
        log.create("msg-1", "a", "b", "first")
        log.create("msg-2", "b", "a", "second")
        self.assertEqual(len(log.records), 2)
        log.mark_delivered("msg-2")
        self.assertIsNotNone(log.records[1].delivered_at)
        self.assertIsNone(log.records[0].delivered_at)

    def test_serialization_round_trip(self):
        log = MailboxLog()
        log.create("msg-1", "a", "b", "payload")
        log.mark_notified("msg-1")
        data = log.to_dict()
        json_str = json.dumps(data)
        deserialized = MailboxLog.from_dict(json.loads(json_str))
        self.assertEqual(len(log.records), len(deserialized.records))
        self.assertEqual(log.records[0].message_id, deserialized.records[0].message_id)


if __name__ == "__main__":
    unittest.main()
