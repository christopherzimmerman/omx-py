"""Tests for ``omx.team.delivery_log``.

Covers the widened ``append_delivery_event`` schema introduced to match
``appendTeamDeliveryLogForCwd`` in ``src/team/delivery-log.ts``: TS-contract
fields (``request_id``, ``message_id``, ``dispatch_kind``, ``intent``,
``transport_preference``, ``reason``) appear as top-level JSONL keys, the
``detail`` slot still accepts free-form extras, and the transport string
is normalized to TS shorthand (``send-keys`` / ``prompt-stdin``).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from omx.team.delivery_log import _normalize_transport, append_delivery_event


def _read_today_log(cwd: Path) -> list[dict[str, object]]:
    """Read and JSON-decode every line of today's delivery JSONL file."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = cwd / ".omx" / "logs" / f"team-delivery-{date}.jsonl"
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]


class TestNormalizeTransport(unittest.TestCase):
    def test_tmux_send_keys_to_shorthand(self) -> None:
        self.assertEqual(_normalize_transport("tmux_send_keys"), "send-keys")

    def test_prompt_stdin_to_shorthand(self) -> None:
        self.assertEqual(_normalize_transport("prompt_stdin"), "prompt-stdin")

    def test_passthrough(self) -> None:
        self.assertEqual(_normalize_transport("hook"), "hook")
        self.assertEqual(_normalize_transport("mailbox"), "mailbox")
        self.assertEqual(_normalize_transport("none"), "none")
        self.assertEqual(_normalize_transport("send-keys"), "send-keys")

    def test_none_passthrough(self) -> None:
        self.assertIsNone(_normalize_transport(None))


class TestAppendDeliveryEvent(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_minimal_event_has_core_keys_only(self) -> None:
        append_delivery_event(str(self.cwd), "mailbox_created")
        entries = _read_today_log(self.cwd)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["kind"], "team_delivery")
        self.assertEqual(entry["event"], "mailbox_created")
        self.assertEqual(entry["source"], "")
        self.assertEqual(entry["team"], "")
        # Default transport "send-keys" + default result "ok" are kept.
        self.assertEqual(entry["transport"], "send-keys")
        self.assertEqual(entry["result"], "ok")
        # No top-level extras when not supplied.
        for absent in (
            "request_id",
            "message_id",
            "dispatch_kind",
            "intent",
            "transport_preference",
            "reason",
            "detail",
        ):
            self.assertNotIn(absent, entry)

    def test_top_level_fields_match_ts_contract(self) -> None:
        append_delivery_event(
            str(self.cwd),
            "dispatch_result",
            source="team.mcp-comm",
            team="t1",
            transport="tmux_send_keys",
            result="ok",
            request_id="req-123",
            message_id="msg-456",
            dispatch_kind="mailbox",
            intent={"kind": "nudge", "reason": "test"},
            transport_preference="transport_direct",
            reason="delivered",
            detail={"to_worker": "alice"},
        )
        entries = _read_today_log(self.cwd)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        # TS-contract top-level keys.
        self.assertEqual(entry["event"], "dispatch_result")
        self.assertEqual(entry["source"], "team.mcp-comm")
        self.assertEqual(entry["team"], "t1")
        self.assertEqual(entry["transport"], "send-keys")  # normalized
        self.assertEqual(entry["result"], "ok")
        self.assertEqual(entry["request_id"], "req-123")
        self.assertEqual(entry["message_id"], "msg-456")
        self.assertEqual(entry["dispatch_kind"], "mailbox")
        self.assertEqual(entry["intent"], {"kind": "nudge", "reason": "test"})
        self.assertEqual(entry["transport_preference"], "transport_direct")
        self.assertEqual(entry["reason"], "delivered")
        # detail is still passed through for caller-supplied extras.
        self.assertEqual(entry["detail"], {"to_worker": "alice"})

    def test_prompt_stdin_transport_normalized(self) -> None:
        append_delivery_event(
            str(self.cwd),
            "dispatch_result",
            transport="prompt_stdin",
        )
        entries = _read_today_log(self.cwd)
        self.assertEqual(entries[0]["transport"], "prompt-stdin")

    def test_omit_transport_and_result_when_none(self) -> None:
        append_delivery_event(
            str(self.cwd),
            "nudge_triggered",
            transport=None,
            result=None,
        )
        entry = _read_today_log(self.cwd)[0]
        self.assertNotIn("transport", entry)
        self.assertNotIn("result", entry)

    def test_empty_detail_is_omitted(self) -> None:
        append_delivery_event(str(self.cwd), "x", detail={})
        entry = _read_today_log(self.cwd)[0]
        self.assertNotIn("detail", entry)

    def test_multiple_events_append_sequentially(self) -> None:
        append_delivery_event(str(self.cwd), "a", request_id="r1")
        append_delivery_event(str(self.cwd), "b", request_id="r2")
        entries = _read_today_log(self.cwd)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["request_id"], "r1")
        self.assertEqual(entries[1]["request_id"], "r2")

    def test_log_path_uses_omx_logs_dir(self) -> None:
        append_delivery_event(str(self.cwd), "ping")
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        expected = self.cwd / ".omx" / "logs" / f"team-delivery-{date}.jsonl"
        self.assertTrue(expected.exists())


if __name__ == "__main__":
    unittest.main()
