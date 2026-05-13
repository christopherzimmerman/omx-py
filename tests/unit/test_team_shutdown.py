"""Tests for omx.team.state.shutdown — worker shutdown request/ack handshake.

Covers:
  * ShutdownAck dataclass round-trip + invalid-status rejection.
  * write_shutdown_request writes the on-disk path the TS port expects.
  * read_shutdown_ack returns None on missing file / corrupt JSON /
    unknown status.
  * read_shutdown_ack honors the min_updated_at threshold.
  * request → ack round-trip ordering: request appears first, ack appears
    only after the worker writes it.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omx.team.state.shutdown import (
    ShutdownAck,
    read_shutdown_ack,
    write_shutdown_request,
)


def _ack_path(cwd: str, team_name: str, worker_name: str) -> Path:
    return (
        Path(cwd)
        / ".omx"
        / "team"
        / team_name
        / "workers"
        / worker_name
        / "shutdown-ack.json"
    )


def _request_path(cwd: str, team_name: str, worker_name: str) -> Path:
    return (
        Path(cwd)
        / ".omx"
        / "team"
        / team_name
        / "workers"
        / worker_name
        / "shutdown-request.json"
    )


def _write_ack(cwd: str, team_name: str, worker_name: str, payload: dict) -> None:
    p = _ack_path(cwd, team_name, worker_name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class TestShutdownAckDataclass(unittest.TestCase):
    def test_accept_round_trip(self) -> None:
        ack = ShutdownAck(
            status="accept",
            reason="ok",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        d = ack.to_dict()
        self.assertEqual(d["status"], "accept")
        self.assertEqual(d["reason"], "ok")
        restored = ShutdownAck.from_dict(d)
        self.assertEqual(restored, ack)

    def test_reject_round_trip(self) -> None:
        ack = ShutdownAck(status="reject", reason="busy")
        d = ack.to_dict()
        self.assertNotIn("updated_at", d)
        restored = ShutdownAck.from_dict(d)
        self.assertEqual(restored, ack)

    def test_from_dict_unknown_status_returns_none(self) -> None:
        self.assertIsNone(ShutdownAck.from_dict({"status": "maybe"}))

    def test_from_dict_missing_status_returns_none(self) -> None:
        self.assertIsNone(ShutdownAck.from_dict({}))

    def test_from_dict_non_dict_returns_none(self) -> None:
        # type: ignore[arg-type] — intentionally probing the defensive guard.
        self.assertIsNone(ShutdownAck.from_dict("nope"))  # type: ignore[arg-type]

    def test_omits_optional_fields_when_none(self) -> None:
        ack = ShutdownAck(status="accept")
        d = ack.to_dict()
        self.assertEqual(d, {"status": "accept"})


class TestWriteShutdownRequest(unittest.TestCase):
    def test_writes_expected_path(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            write_shutdown_request(
                "alpha",
                "worker-1",
                cwd,
                requested_by="leader-session-xyz",
                requested_at="2026-01-01T00:00:00+00:00",
            )
            p = _request_path(cwd, "alpha", "worker-1")
            self.assertTrue(p.is_file())
            payload = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(payload["requested_by"], "leader-session-xyz")
            self.assertEqual(payload["requested_at"], "2026-01-01T00:00:00+00:00")

    def test_defaults_requested_at_to_now(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            write_shutdown_request("alpha", "worker-1", cwd, requested_by="leader")
            payload = json.loads(
                _request_path(cwd, "alpha", "worker-1").read_text(encoding="utf-8")
            )
            # Default timestamp should be a non-empty ISO 8601 string.
            self.assertIsInstance(payload["requested_at"], str)
            self.assertGreater(len(payload["requested_at"]), 0)

    def test_creates_parent_directories(self) -> None:
        """The request writer is responsible for materializing the worker
        directory under .omx/team/{team}/workers/{worker}/."""
        with tempfile.TemporaryDirectory() as cwd:
            self.assertFalse((Path(cwd) / ".omx").exists())
            write_shutdown_request("alpha", "worker-1", cwd, requested_by="leader")
            self.assertTrue(_request_path(cwd, "alpha", "worker-1").exists())


class TestReadShutdownAck(unittest.TestCase):
    def test_missing_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(read_shutdown_ack("alpha", "worker-1", cwd))

    def test_corrupt_json_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            p = _ack_path(cwd, "alpha", "worker-1")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("{not valid", encoding="utf-8")
            self.assertIsNone(read_shutdown_ack("alpha", "worker-1", cwd))

    def test_unknown_status_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _write_ack(cwd, "alpha", "worker-1", {"status": "maybe"})
            self.assertIsNone(read_shutdown_ack("alpha", "worker-1", cwd))

    def test_accept_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _write_ack(
                cwd,
                "alpha",
                "worker-1",
                {
                    "status": "accept",
                    "reason": "ok",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                },
            )
            ack = read_shutdown_ack("alpha", "worker-1", cwd)
            self.assertIsNotNone(ack)
            assert ack is not None
            self.assertEqual(ack.status, "accept")
            self.assertEqual(ack.reason, "ok")

    def test_min_updated_at_rejects_stale_ack(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _write_ack(
                cwd,
                "alpha",
                "worker-1",
                {"status": "accept", "updated_at": "2026-01-01T00:00:00+00:00"},
            )
            # Ack is older than the threshold → must be filtered out.
            self.assertIsNone(
                read_shutdown_ack(
                    "alpha",
                    "worker-1",
                    cwd,
                    min_updated_at="2026-01-02T00:00:00+00:00",
                )
            )

    def test_min_updated_at_accepts_fresh_ack(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _write_ack(
                cwd,
                "alpha",
                "worker-1",
                {"status": "reject", "updated_at": "2026-01-02T00:00:00+00:00"},
            )
            ack = read_shutdown_ack(
                "alpha",
                "worker-1",
                cwd,
                min_updated_at="2026-01-01T00:00:00+00:00",
            )
            self.assertIsNotNone(ack)
            assert ack is not None
            self.assertEqual(ack.status, "reject")

    def test_min_updated_at_with_z_suffix_normalizes(self) -> None:
        """ISO timestamps written with the TS-style ``Z`` suffix must parse."""
        with tempfile.TemporaryDirectory() as cwd:
            _write_ack(
                cwd,
                "alpha",
                "worker-1",
                {"status": "accept", "updated_at": "2026-01-02T00:00:00Z"},
            )
            ack = read_shutdown_ack(
                "alpha",
                "worker-1",
                cwd,
                min_updated_at="2026-01-01T00:00:00Z",
            )
            self.assertIsNotNone(ack)

    def test_min_updated_at_with_missing_ack_timestamp(self) -> None:
        """If the ack omits updated_at and a threshold is set, the threshold
        gate must reject — there's no way to prove freshness."""
        with tempfile.TemporaryDirectory() as cwd:
            _write_ack(cwd, "alpha", "worker-1", {"status": "accept"})
            self.assertIsNone(
                read_shutdown_ack(
                    "alpha",
                    "worker-1",
                    cwd,
                    min_updated_at="2026-01-01T00:00:00+00:00",
                )
            )
            # But without a threshold, the ack is fine.
            ack = read_shutdown_ack("alpha", "worker-1", cwd)
            self.assertIsNotNone(ack)


class TestShutdownHandshakeOrdering(unittest.TestCase):
    def test_request_then_ack_round_trip(self) -> None:
        """Full handshake: leader writes request, worker writes ack, leader
        reads ack. Verifies the two files live in the same worker dir and do
        not interfere with each other."""
        with tempfile.TemporaryDirectory() as cwd:
            # Step 1: leader writes the request.
            write_shutdown_request(
                "alpha",
                "worker-1",
                cwd,
                requested_by="leader",
                requested_at="2026-01-01T00:00:00+00:00",
            )
            self.assertTrue(_request_path(cwd, "alpha", "worker-1").exists())
            # Ack should not yet exist.
            self.assertIsNone(read_shutdown_ack("alpha", "worker-1", cwd))

            # Step 2: worker writes the ack.
            _write_ack(
                cwd,
                "alpha",
                "worker-1",
                {
                    "status": "accept",
                    "reason": "drain_complete",
                    "updated_at": "2026-01-01T00:00:10+00:00",
                },
            )

            # Step 3: leader reads the ack, gated to be at least as new as the
            # request — proving the ack arrived after the request.
            ack = read_shutdown_ack(
                "alpha",
                "worker-1",
                cwd,
                min_updated_at="2026-01-01T00:00:00+00:00",
            )
            self.assertIsNotNone(ack)
            assert ack is not None
            self.assertEqual(ack.status, "accept")
            self.assertEqual(ack.reason, "drain_complete")

    def test_request_and_ack_do_not_collide(self) -> None:
        """Writing the request must never overwrite an existing ack and
        vice-versa — they are distinct file names by design."""
        with tempfile.TemporaryDirectory() as cwd:
            _write_ack(
                cwd,
                "alpha",
                "worker-1",
                {"status": "reject", "updated_at": "2026-01-01T00:00:00+00:00"},
            )
            write_shutdown_request(
                "alpha",
                "worker-1",
                cwd,
                requested_by="leader",
                requested_at="2026-01-01T00:00:05+00:00",
            )
            self.assertTrue(_request_path(cwd, "alpha", "worker-1").exists())
            self.assertTrue(_ack_path(cwd, "alpha", "worker-1").exists())
            ack = read_shutdown_ack("alpha", "worker-1", cwd)
            self.assertIsNotNone(ack)
            assert ack is not None
            self.assertEqual(ack.status, "reject")


if __name__ == "__main__":
    unittest.main()
