"""Tests for omx.core.authority — port of Rust authority tests."""

import unittest

from omx.core.authority import (
    AlreadyHeldByOther,
    AuthorityLease,
    NotHeld,
    OwnerMismatch,
)


class TestAuthorityLease(unittest.TestCase):
    def test_acquire_and_renew_happy_path(self):
        lease = AuthorityLease()
        self.assertFalse(lease.is_held())
        lease.acquire("worker-1", "lease-1", "2026-03-19T02:00:00Z")
        self.assertTrue(lease.is_held())
        self.assertEqual(lease.current_owner(), "worker-1")
        lease.renew("worker-1", "lease-2", "2026-03-19T03:00:00Z")
        self.assertTrue(lease.is_held())

    def test_acquire_fails_if_held_by_other(self):
        lease = AuthorityLease()
        lease.acquire("worker-1", "lease-1", "2026-03-19T02:00:00Z")
        with self.assertRaises(AlreadyHeldByOther):
            lease.acquire("worker-2", "lease-2", "2026-03-19T03:00:00Z")

    def test_acquire_succeeds_for_same_owner(self):
        lease = AuthorityLease()
        lease.acquire("worker-1", "lease-1", "2026-03-19T02:00:00Z")
        lease.acquire("worker-1", "lease-2", "2026-03-19T03:00:00Z")

    def test_renew_fails_if_not_held(self):
        lease = AuthorityLease()
        with self.assertRaises(NotHeld):
            lease.renew("worker-1", "lease-1", "2026-03-19T02:00:00Z")

    def test_renew_fails_if_owner_mismatch(self):
        lease = AuthorityLease()
        lease.acquire("worker-1", "lease-1", "2026-03-19T02:00:00Z")
        with self.assertRaises(OwnerMismatch):
            lease.renew("worker-2", "lease-2", "2026-03-19T03:00:00Z")

    def test_force_release_clears_everything(self):
        lease = AuthorityLease()
        lease.acquire("worker-1", "lease-1", "2026-03-19T02:00:00Z")
        lease.mark_stale("expired")
        lease.force_release()
        self.assertFalse(lease.is_held())
        self.assertFalse(lease.is_stale())
        self.assertIsNone(lease.current_owner())

    def test_stale_marking_and_clearing(self):
        lease = AuthorityLease()
        lease.acquire("worker-1", "lease-1", "2026-03-19T02:00:00Z")
        lease.mark_stale("network timeout")
        self.assertTrue(lease.is_stale())
        lease.clear_stale()
        self.assertFalse(lease.is_stale())

    def test_snapshot_reflects_current_state(self):
        lease = AuthorityLease()
        lease.acquire("worker-1", "lease-1", "2026-03-19T02:00:00Z")
        snap = lease.to_snapshot()
        self.assertEqual(snap.owner, "worker-1")
        self.assertEqual(snap.lease_id, "lease-1")
        self.assertFalse(snap.stale)


if __name__ == "__main__":
    unittest.main()
