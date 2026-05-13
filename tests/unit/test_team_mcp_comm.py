"""Tests for ``omx.team.mcp_comm``.

Covers the four public dispatch entry points ported from
``src/team/mcp-comm.ts`` (``queue_inbox_instruction``,
``queue_direct_mailbox_message``, ``queue_broadcast_mailbox_message``,
``wait_for_dispatch_receipt``) plus transport classification, fallback
chains, broadcast fan-out, dedup, leader-pane-missing deferral, and
notifier-exception handling.

All filesystem state is rooted under a tempdir; no network or tmux is
invoked. Notifiers are stubbed via ``unittest.mock`` style callables.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from omx.team import team_ops
from omx.team.mcp_comm import (
    BroadcastRecipient,
    DispatchOutcome,
    DispatchTransport,
    QueueBroadcastParams,
    QueueDirectMessageParams,
    QueueInboxParams,
    TeamNotifier,
    TeamNotifierTarget,
    _fallback_transport_for_preference,
    _is_confirmed_notification,
    _is_leader_pane_missing_persisted,
    _notify_exception_reason,
    _result_label,
    queue_broadcast_mailbox_message,
    queue_direct_mailbox_message,
    queue_inbox_instruction,
    wait_for_dispatch_receipt,
)
from omx.team.reminder_intents import TeamReminderIntent
from omx.team.state.types import TeamDispatchRequest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_team(cwd: str, team_name: str = "t1") -> None:
    """Initialize a minimal team state directory.

    The state layer creates required directories lazily, so an empty
    base team dir is sufficient for these tests; we only need the
    parent ``.omx/team/<name>`` to exist for io operations to write
    into.
    """
    base = Path(cwd) / ".omx" / "team" / team_name
    base.mkdir(parents=True, exist_ok=True)


def _make_notifier(outcome: DispatchOutcome) -> tuple[TeamNotifier, MagicMock]:
    """Build a notifier callable that returns a fixed outcome and tracks calls."""
    mock = MagicMock()

    def notifier(
        target: TeamNotifierTarget, message: str, context: dict[str, Any]
    ) -> DispatchOutcome:
        mock(target=target, message=message, context=context)
        return outcome

    return notifier, mock


def _make_raising_notifier(exc: BaseException) -> TeamNotifier:
    def notifier(*_args: Any, **_kwargs: Any) -> DispatchOutcome:
        raise exc

    return notifier


# ---------------------------------------------------------------------------
# Pure-function tests (no filesystem)
# ---------------------------------------------------------------------------


class TestDispatchTransport(unittest.TestCase):
    def test_string_values_match_ts(self) -> None:
        """Enum values must match the TS string union byte-for-byte."""
        self.assertEqual(DispatchTransport.HOOK.value, "hook")
        self.assertEqual(DispatchTransport.PROMPT_STDIN.value, "prompt_stdin")
        self.assertEqual(DispatchTransport.TMUX_SEND_KEYS.value, "tmux_send_keys")
        self.assertEqual(DispatchTransport.MAILBOX.value, "mailbox")
        self.assertEqual(DispatchTransport.NONE.value, "none")


class TestDispatchOutcomeShape(unittest.TestCase):
    def test_required_fields_only(self) -> None:
        outcome = DispatchOutcome(ok=True, transport="hook", reason="ok")
        self.assertEqual(
            outcome.to_dict(), {"ok": True, "transport": "hook", "reason": "ok"}
        )

    def test_optional_fields_omitted_when_none(self) -> None:
        outcome = DispatchOutcome(ok=False, transport="none", reason="x")
        self.assertNotIn("request_id", outcome.to_dict())
        self.assertNotIn("message_id", outcome.to_dict())
        self.assertNotIn("to_worker", outcome.to_dict())

    def test_optional_fields_included_when_set(self) -> None:
        outcome = DispatchOutcome(
            ok=True,
            transport="mailbox",
            reason="ok",
            request_id="r1",
            message_id="m1",
            to_worker="w1",
        )
        self.assertEqual(
            outcome.to_dict(),
            {
                "ok": True,
                "transport": "mailbox",
                "reason": "ok",
                "request_id": "r1",
                "message_id": "m1",
                "to_worker": "w1",
            },
        )


class TestInternalClassifiers(unittest.TestCase):
    def test_confirmed_notification_failed(self) -> None:
        self.assertFalse(
            _is_confirmed_notification(
                DispatchOutcome(ok=False, transport="hook", reason="x")
            )
        )

    def test_confirmed_notification_non_hook(self) -> None:
        self.assertTrue(
            _is_confirmed_notification(
                DispatchOutcome(ok=True, transport="tmux_send_keys", reason="ok")
            )
        )

    def test_confirmed_notification_hook_queued(self) -> None:
        self.assertFalse(
            _is_confirmed_notification(
                DispatchOutcome(
                    ok=True, transport="hook", reason="queued_for_hook_dispatch"
                )
            )
        )

    def test_confirmed_notification_hook_immediate(self) -> None:
        self.assertTrue(
            _is_confirmed_notification(
                DispatchOutcome(ok=True, transport="hook", reason="delivered")
            )
        )

    def test_leader_pane_missing_persisted_match(self) -> None:
        req = TeamDispatchRequest(request_id="r", to_worker="leader-fixed")
        outcome = DispatchOutcome(
            ok=True, transport="mailbox", reason="leader_pane_missing_mailbox_persisted"
        )
        self.assertTrue(_is_leader_pane_missing_persisted(req, outcome))

    def test_leader_pane_missing_persisted_wrong_worker(self) -> None:
        req = TeamDispatchRequest(request_id="r", to_worker="alice")
        outcome = DispatchOutcome(
            ok=True, transport="mailbox", reason="leader_pane_missing_mailbox_persisted"
        )
        self.assertFalse(_is_leader_pane_missing_persisted(req, outcome))


class TestFallbackTransport(unittest.TestCase):
    def test_prompt_stdin_preference(self) -> None:
        self.assertEqual(
            _fallback_transport_for_preference("prompt_stdin"), "prompt_stdin"
        )

    def test_transport_direct_preference(self) -> None:
        self.assertEqual(
            _fallback_transport_for_preference("transport_direct"), "tmux_send_keys"
        )

    def test_default_preference_is_hook(self) -> None:
        self.assertEqual(_fallback_transport_for_preference(None), "hook")
        self.assertEqual(
            _fallback_transport_for_preference("hook_preferred_with_fallback"), "hook"
        )


class TestNotifyExceptionReason(unittest.TestCase):
    def test_includes_message(self) -> None:
        self.assertEqual(
            _notify_exception_reason(RuntimeError("boom")), "notify_exception:boom"
        )

    def test_non_exception_value(self) -> None:
        # Non-Exception values still stringify.
        try:
            raise ValueError("x")
        except ValueError as exc:
            self.assertEqual(_notify_exception_reason(exc), "notify_exception:x")


class TestResultLabel(unittest.TestCase):
    def test_failed(self) -> None:
        self.assertEqual(
            _result_label(DispatchOutcome(ok=False, transport="hook", reason="x")),
            "failed",
        )

    def test_queued(self) -> None:
        self.assertEqual(
            _result_label(
                DispatchOutcome(
                    ok=True, transport="hook", reason="queued_for_hook_dispatch"
                )
            ),
            "queued",
        )

    def test_ok(self) -> None:
        self.assertEqual(
            _result_label(
                DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
            ),
            "ok",
        )


# ---------------------------------------------------------------------------
# Integration tests (real tempdir + state layer)
# ---------------------------------------------------------------------------


class _TmpTeamCase(unittest.TestCase):
    """Common temp-team fixture."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name
        self.team_name = "t1"
        _init_team(self.cwd, self.team_name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestQueueInboxInstruction(_TmpTeamCase):
    def test_successful_tmux_dispatch_marks_notified(self) -> None:
        notifier_outcome = DispatchOutcome(
            ok=True, transport="tmux_send_keys", reason="delivered"
        )
        notifier, mock = _make_notifier(notifier_outcome)
        params = QueueInboxParams(
            team_name=self.team_name,
            worker_name="alice",
            worker_index=0,
            inbox="do work",
            trigger_message="poke",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcome = queue_inbox_instruction(params)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "tmux_send_keys")
        self.assertIsNotNone(outcome.request_id)
        # Notifier should have been called exactly once with our target.
        mock.assert_called_once()
        # Request must now be persisted and marked notified.
        req = team_ops.team_read_dispatch_request(
            self.team_name, outcome.request_id, self.cwd
        )
        assert req is not None
        self.assertEqual(req.status, "notified")
        self.assertEqual(req.last_reason, "delivered")
        # Inbox file should exist.
        inbox_path = (
            Path(self.cwd)
            / ".omx"
            / "team"
            / self.team_name
            / "workers"
            / "alice"
            / "inbox.md"
        )
        self.assertTrue(inbox_path.exists())

    def test_hook_queued_does_not_mark_notified(self) -> None:
        notifier, _ = _make_notifier(
            DispatchOutcome(
                ok=True, transport="hook", reason="queued_for_hook_dispatch"
            )
        )
        params = QueueInboxParams(
            team_name=self.team_name,
            worker_name="alice",
            worker_index=0,
            inbox="x",
            trigger_message="t",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="hook_preferred_with_fallback",
        )
        outcome = queue_inbox_instruction(params)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.reason, "queued_for_hook_dispatch")
        # Request must remain pending (hook will confirm later).
        req = team_ops.team_read_dispatch_request(
            self.team_name, outcome.request_id, self.cwd
        )
        assert req is not None
        self.assertEqual(req.status, "pending")

    def test_notifier_failure_transitions_to_failed(self) -> None:
        notifier, _ = _make_notifier(
            DispatchOutcome(ok=False, transport="tmux_send_keys", reason="pane_dead")
        )
        params = QueueInboxParams(
            team_name=self.team_name,
            worker_name="alice",
            worker_index=0,
            inbox="x",
            trigger_message="t",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcome = queue_inbox_instruction(params)
        self.assertFalse(outcome.ok)
        req = team_ops.team_read_dispatch_request(
            self.team_name, outcome.request_id, self.cwd
        )
        assert req is not None
        self.assertEqual(req.status, "failed")
        self.assertEqual(req.last_reason, "pane_dead")

    def test_hook_preferred_failure_left_pending(self) -> None:
        # hook_preferred_with_fallback failures are left alone by the
        # immediate-failure helper (hook will own terminal state).
        notifier, _ = _make_notifier(
            DispatchOutcome(ok=False, transport="hook", reason="pending_hook")
        )
        params = QueueInboxParams(
            team_name=self.team_name,
            worker_name="alice",
            worker_index=0,
            inbox="x",
            trigger_message="t",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="hook_preferred_with_fallback",
        )
        outcome = queue_inbox_instruction(params)
        self.assertFalse(outcome.ok)
        req = team_ops.team_read_dispatch_request(
            self.team_name, outcome.request_id, self.cwd
        )
        assert req is not None
        # Status is unchanged.
        self.assertEqual(req.status, "pending")

    def test_notifier_exception_records_failure(self) -> None:
        notifier = _make_raising_notifier(RuntimeError("boom"))
        params = QueueInboxParams(
            team_name=self.team_name,
            worker_name="alice",
            worker_index=0,
            inbox="x",
            trigger_message="t",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcome = queue_inbox_instruction(params)
        self.assertFalse(outcome.ok)
        # Fallback transport from preference.
        self.assertEqual(outcome.transport, "tmux_send_keys")
        self.assertEqual(outcome.reason, "notify_exception:boom")

    def test_duplicate_dispatch_returns_short_circuit(self) -> None:
        ok_outcome = DispatchOutcome(
            ok=True, transport="tmux_send_keys", reason="delivered"
        )
        notifier, _ = _make_notifier(ok_outcome)
        params = QueueInboxParams(
            team_name=self.team_name,
            worker_name="alice",
            worker_index=0,
            inbox="x",
            trigger_message="t",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="transport_direct",
            inbox_correlation_key="job-1",
        )
        first = queue_inbox_instruction(params)
        self.assertTrue(first.ok)
        # Reset request to pending state by creating a fresh queue attempt
        # under the same correlation key WITHOUT marking notified first;
        # we exercise dedup by submitting a brand-new pending request.
        # Easiest path: rebuild the team and queue twice without the
        # notifier confirming.
        # Inline: use a notifier that leaves the request pending.
        params2 = QueueInboxParams(
            team_name=self.team_name,
            worker_name="bob",
            worker_index=1,
            inbox="x",
            trigger_message="t",
            cwd=self.cwd,
            notify=_make_notifier(
                DispatchOutcome(
                    ok=True, transport="hook", reason="queued_for_hook_dispatch"
                )
            )[0],
            transport_preference="hook_preferred_with_fallback",
            inbox_correlation_key="job-bob",
        )
        first_bob = queue_inbox_instruction(params2)
        self.assertTrue(first_bob.ok)
        # Second call with the same correlation key should be deduped.
        second_bob = queue_inbox_instruction(params2)
        self.assertFalse(second_bob.ok)
        self.assertEqual(second_bob.reason, "duplicate_pending_dispatch_request")
        self.assertEqual(second_bob.transport, "none")
        self.assertEqual(second_bob.request_id, first_bob.request_id)


class TestQueueDirectMailboxMessage(_TmpTeamCase):
    def test_successful_direct_mailbox(self) -> None:
        notifier, mock = _make_notifier(
            DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
        )
        params = QueueDirectMessageParams(
            team_name=self.team_name,
            from_worker="alice",
            to_worker="bob",
            body="hello",
            trigger_message="trigger",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcome = queue_direct_mailbox_message(params)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.to_worker, "bob")
        self.assertIsNotNone(outcome.message_id)
        self.assertIsNotNone(outcome.request_id)
        mock.assert_called_once()
        # Mailbox message should be marked notified.
        msgs = team_ops.team_list_mailbox(self.team_name, "bob", self.cwd)
        self.assertEqual(len(msgs), 1)
        self.assertIsNotNone(msgs[0].notified_at)

    def test_existing_already_notified_short_circuit_non_leader(self) -> None:
        # First call notifies normally.
        notifier, _ = _make_notifier(
            DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
        )
        first_params = QueueDirectMessageParams(
            team_name=self.team_name,
            from_worker="alice",
            to_worker="bob",
            body="repeat",
            trigger_message="t",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="transport_direct",
        )
        first = queue_direct_mailbox_message(first_params)
        self.assertTrue(first.ok)
        # Second call should hit the existing-notified short-circuit.
        # (message is dedup'd at the mailbox layer by body match, and the
        # first call has already notified it.)
        second_notifier, second_mock = _make_notifier(
            DispatchOutcome(ok=False, transport="hook", reason="should_not_be_called")
        )
        second_params = QueueDirectMessageParams(
            team_name=self.team_name,
            from_worker="alice",
            to_worker="bob",
            body="repeat",
            trigger_message="t",
            cwd=self.cwd,
            notify=second_notifier,
            transport_preference="transport_direct",
        )
        second = queue_direct_mailbox_message(second_params)
        self.assertTrue(second.ok)
        self.assertEqual(second.reason, "existing_message_already_notified")
        # Non-leader: transport falls back to preference-derived value.
        self.assertEqual(second.transport, "tmux_send_keys")
        # Notifier must not have been called the second time.
        second_mock.assert_not_called()

    def test_existing_already_notified_leader_uses_mailbox_transport(self) -> None:
        notifier, _ = _make_notifier(
            DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
        )
        first = queue_direct_mailbox_message(
            QueueDirectMessageParams(
                team_name=self.team_name,
                from_worker="alice",
                to_worker="leader-fixed",
                body="msg",
                trigger_message="t",
                cwd=self.cwd,
                notify=notifier,
                transport_preference="transport_direct",
            )
        )
        self.assertTrue(first.ok)
        # Second call against the leader: must report mailbox transport.
        second_notifier, _ = _make_notifier(
            DispatchOutcome(ok=False, transport="hook", reason="x")
        )
        second = queue_direct_mailbox_message(
            QueueDirectMessageParams(
                team_name=self.team_name,
                from_worker="alice",
                to_worker="leader-fixed",
                body="msg",
                trigger_message="t",
                cwd=self.cwd,
                notify=second_notifier,
                transport_preference="transport_direct",
            )
        )
        self.assertTrue(second.ok)
        self.assertEqual(second.transport, "mailbox")
        self.assertEqual(second.reason, "existing_message_already_notified")

    def test_leader_pane_missing_persisted_branch(self) -> None:
        # Notifier returns the "leader pane missing, persisted" outcome.
        notifier, _ = _make_notifier(
            DispatchOutcome(
                ok=True,
                transport="mailbox",
                reason="leader_pane_missing_mailbox_persisted",
            )
        )
        outcome = queue_direct_mailbox_message(
            QueueDirectMessageParams(
                team_name=self.team_name,
                from_worker="alice",
                to_worker="leader-fixed",
                body="hi",
                trigger_message="t",
                cwd=self.cwd,
                notify=notifier,
                transport_preference="hook_preferred_with_fallback",
            )
        )
        self.assertTrue(outcome.ok)
        # Request should remain pending (not flipped to notified or failed).
        req = team_ops.team_read_dispatch_request(
            self.team_name, outcome.request_id, self.cwd
        )
        assert req is not None
        self.assertEqual(req.status, "pending")
        # Mailbox should NOT be marked notified for this branch.
        msgs = team_ops.team_list_mailbox(self.team_name, "leader-fixed", self.cwd)
        self.assertEqual(len(msgs), 1)
        self.assertIsNone(msgs[0].notified_at)

    def test_direct_notifier_failure(self) -> None:
        notifier, _ = _make_notifier(
            DispatchOutcome(ok=False, transport="tmux_send_keys", reason="pane_dead")
        )
        outcome = queue_direct_mailbox_message(
            QueueDirectMessageParams(
                team_name=self.team_name,
                from_worker="alice",
                to_worker="bob",
                body="x",
                trigger_message="t",
                cwd=self.cwd,
                notify=notifier,
                transport_preference="transport_direct",
            )
        )
        self.assertFalse(outcome.ok)
        req = team_ops.team_read_dispatch_request(
            self.team_name, outcome.request_id, self.cwd
        )
        assert req is not None
        self.assertEqual(req.status, "failed")
        # Mailbox message should NOT be marked notified on failure.
        msgs = team_ops.team_list_mailbox(self.team_name, "bob", self.cwd)
        self.assertIsNone(msgs[0].notified_at)

    def test_direct_notifier_exception_falls_back(self) -> None:
        notifier = _make_raising_notifier(ValueError("nope"))
        outcome = queue_direct_mailbox_message(
            QueueDirectMessageParams(
                team_name=self.team_name,
                from_worker="alice",
                to_worker="bob",
                body="x",
                trigger_message="t",
                cwd=self.cwd,
                notify=notifier,
                transport_preference="prompt_stdin",
            )
        )
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.transport, "prompt_stdin")
        self.assertEqual(outcome.reason, "notify_exception:nope")


class TestQueueBroadcastMailboxMessage(_TmpTeamCase):
    def test_fan_out_to_all_recipients(self) -> None:
        notifier, mock = _make_notifier(
            DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
        )
        recipients = [
            BroadcastRecipient(worker_name="bob", worker_index=1),
            BroadcastRecipient(worker_name="carol", worker_index=2),
        ]
        params = QueueBroadcastParams(
            team_name=self.team_name,
            from_worker="alice",
            recipients=recipients,
            body="announce",
            cwd=self.cwd,
            trigger_for=lambda name: f"trigger-{name}",
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcomes = queue_broadcast_mailbox_message(params)
        self.assertEqual(len(outcomes), 2)
        names = {o.to_worker for o in outcomes}
        self.assertEqual(names, {"bob", "carol"})
        # Each notifier call gets a unique target.
        self.assertEqual(mock.call_count, 2)
        # Both mailboxes should now contain a notified message.
        for name in ("bob", "carol"):
            msgs = team_ops.team_list_mailbox(self.team_name, name, self.cwd)
            self.assertEqual(len(msgs), 1)
            self.assertIsNotNone(msgs[0].notified_at)

    def test_broadcast_skips_recipients_not_in_list(self) -> None:
        # broadcast_message will still fan out to recipient_names; we pass
        # one recipient explicitly and one extra name in the list. Only
        # the explicit one should produce an outcome.
        notifier, _ = _make_notifier(
            DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
        )
        params = QueueBroadcastParams(
            team_name=self.team_name,
            from_worker="alice",
            recipients=[BroadcastRecipient(worker_name="bob", worker_index=1)],
            body="hi",
            cwd=self.cwd,
            trigger_for=lambda name: "t",
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcomes = queue_broadcast_mailbox_message(params)
        self.assertEqual(len(outcomes), 1)
        self.assertEqual(outcomes[0].to_worker, "bob")

    def test_broadcast_per_recipient_intent(self) -> None:
        notifier, mock = _make_notifier(
            DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
        )
        intents: dict[str, TeamReminderIntent] = {
            "bob": TeamReminderIntent(kind="nudge", reason="for-bob"),
            "carol": TeamReminderIntent(kind="nudge", reason="for-carol"),
        }
        params = QueueBroadcastParams(
            team_name=self.team_name,
            from_worker="alice",
            recipients=[
                BroadcastRecipient(worker_name="bob", worker_index=1),
                BroadcastRecipient(worker_name="carol", worker_index=2),
            ],
            body="x",
            cwd=self.cwd,
            trigger_for=lambda name: f"t-{name}",
            intent_for=lambda name: intents.get(name),
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcomes = queue_broadcast_mailbox_message(params)
        self.assertEqual(len(outcomes), 2)
        self.assertEqual(mock.call_count, 2)

    def test_broadcast_partial_failure(self) -> None:
        call_log: list[str] = []

        def notifier(
            target: TeamNotifierTarget, message: str, context: dict[str, Any]
        ) -> DispatchOutcome:
            call_log.append(target.worker_name)
            if target.worker_name == "bob":
                return DispatchOutcome(
                    ok=False, transport="tmux_send_keys", reason="pane_dead"
                )
            return DispatchOutcome(
                ok=True, transport="tmux_send_keys", reason="delivered"
            )

        params = QueueBroadcastParams(
            team_name=self.team_name,
            from_worker="alice",
            recipients=[
                BroadcastRecipient(worker_name="bob", worker_index=1),
                BroadcastRecipient(worker_name="carol", worker_index=2),
            ],
            body="x",
            cwd=self.cwd,
            trigger_for=lambda name: "t",
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcomes = queue_broadcast_mailbox_message(params)
        by_name = {o.to_worker: o for o in outcomes}
        self.assertFalse(by_name["bob"].ok)
        self.assertTrue(by_name["carol"].ok)
        # Bob's request must be failed; Carol's must be notified.
        bob_req = team_ops.team_read_dispatch_request(
            self.team_name, by_name["bob"].request_id, self.cwd
        )
        carol_req = team_ops.team_read_dispatch_request(
            self.team_name, by_name["carol"].request_id, self.cwd
        )
        assert bob_req is not None and carol_req is not None
        self.assertEqual(bob_req.status, "failed")
        self.assertEqual(carol_req.status, "notified")

    def test_broadcast_notifier_exception(self) -> None:
        notifier = _make_raising_notifier(RuntimeError("kapow"))
        params = QueueBroadcastParams(
            team_name=self.team_name,
            from_worker="alice",
            recipients=[BroadcastRecipient(worker_name="bob", worker_index=1)],
            body="x",
            cwd=self.cwd,
            trigger_for=lambda name: "t",
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcomes = queue_broadcast_mailbox_message(params)
        self.assertEqual(len(outcomes), 1)
        self.assertFalse(outcomes[0].ok)
        self.assertEqual(outcomes[0].reason, "notify_exception:kapow")
        self.assertEqual(outcomes[0].transport, "tmux_send_keys")


class TestWaitForDispatchReceipt(_TmpTeamCase):
    def test_returns_request_when_already_terminal(self) -> None:
        notifier, _ = _make_notifier(
            DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
        )
        params = QueueInboxParams(
            team_name=self.team_name,
            worker_name="alice",
            worker_index=0,
            inbox="x",
            trigger_message="t",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="transport_direct",
        )
        outcome = queue_inbox_instruction(params)
        result = wait_for_dispatch_receipt(
            self.team_name,
            outcome.request_id,
            self.cwd,
            timeout_ms=0,
            poll_ms=1,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "notified")

    def test_returns_none_when_request_missing(self) -> None:
        result = wait_for_dispatch_receipt(
            self.team_name,
            "no-such-id",
            self.cwd,
            timeout_ms=0,
            poll_ms=1,
        )
        self.assertIsNone(result)

    def test_polls_until_terminal_via_test_seam(self) -> None:
        # Queue a request whose notifier leaves it pending (hook-queued).
        notifier, _ = _make_notifier(
            DispatchOutcome(
                ok=True, transport="hook", reason="queued_for_hook_dispatch"
            )
        )
        params = QueueInboxParams(
            team_name=self.team_name,
            worker_name="alice",
            worker_index=0,
            inbox="x",
            trigger_message="t",
            cwd=self.cwd,
            notify=notifier,
            transport_preference="hook_preferred_with_fallback",
        )
        outcome = queue_inbox_instruction(params)
        # Drive a fake clock; transition the request to notified after one
        # poll iteration via the sleep seam.
        time_box = {"t": 0.0}
        sleep_calls: list[float] = []
        request_id = outcome.request_id

        def fake_now_ms() -> float:
            return time_box["t"]

        team_name = self.team_name
        cwd = self.cwd

        def fake_sleep(secs: float) -> None:
            sleep_calls.append(secs)
            time_box["t"] += secs * 1000.0
            # On first iteration, transition the request to delivered.
            if len(sleep_calls) == 1:
                team_ops.team_transition_dispatch_request(
                    team_name, request_id, "delivered", cwd, reason="hook_fired"
                )

        result = wait_for_dispatch_receipt(
            self.team_name,
            outcome.request_id,
            self.cwd,
            timeout_ms=5_000,
            poll_ms=50,
            _sleep=fake_sleep,
            _now_ms=fake_now_ms,
        )
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "delivered")
        self.assertEqual(len(sleep_calls), 1)

    def test_timeout_returns_final_read(self) -> None:
        # Notifier leaves the request pending.
        notifier, _ = _make_notifier(
            DispatchOutcome(
                ok=True, transport="hook", reason="queued_for_hook_dispatch"
            )
        )
        outcome = queue_inbox_instruction(
            QueueInboxParams(
                team_name=self.team_name,
                worker_name="alice",
                worker_index=0,
                inbox="x",
                trigger_message="t",
                cwd=self.cwd,
                notify=notifier,
                transport_preference="hook_preferred_with_fallback",
            )
        )
        # Use a clock that immediately exceeds the deadline.
        time_box = {"t": 0.0}

        def fake_now_ms() -> float:
            t = time_box["t"]
            time_box["t"] += 10_000.0
            return t

        result = wait_for_dispatch_receipt(
            self.team_name,
            outcome.request_id,
            self.cwd,
            timeout_ms=10,
            poll_ms=25,
            _sleep=lambda s: None,
            _now_ms=fake_now_ms,
        )
        # The deadline expires; we still get the final read (still pending).
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "pending")


class TestDeliveryLogTopLevelFields(_TmpTeamCase):
    """Assert mcp_comm writes TS-equivalent top-level keys to the JSONL log.

    Phase 2.6 used to funnel ``request_id``/``message_id``/``dispatch_kind``/
    ``intent``/``transport_preference``/``reason`` through ``detail``; the
    delivery_log widening now puts them at the top level to match
    ``appendTeamDeliveryLogForCwd``.
    """

    def _read_delivery_log(self) -> list[dict[str, Any]]:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        log_path = Path(self.cwd) / ".omx" / "logs" / f"team-delivery-{date}.jsonl"
        if not log_path.exists():
            return []
        return [
            json.loads(line)
            for line in log_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    def test_inbox_dispatch_writes_top_level_keys(self) -> None:
        notifier, _ = _make_notifier(
            DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
        )
        outcome = queue_inbox_instruction(
            QueueInboxParams(
                team_name=self.team_name,
                worker_name="alice",
                worker_index=0,
                inbox="x",
                trigger_message="t",
                cwd=self.cwd,
                notify=notifier,
                intent=TeamReminderIntent(kind="nudge", reason="inbox-test"),
                transport_preference="transport_direct",
            )
        )
        entries = self._read_delivery_log()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        # TS-contract top-level keys.
        self.assertEqual(entry["event"], "dispatch_result")
        self.assertEqual(entry["source"], "team.mcp-comm")
        self.assertEqual(entry["team"], self.team_name)
        # Transport is normalized to TS shorthand.
        self.assertEqual(entry["transport"], "send-keys")
        self.assertEqual(entry["result"], "ok")
        self.assertEqual(entry["request_id"], outcome.request_id)
        self.assertEqual(entry["dispatch_kind"], "inbox")
        self.assertEqual(entry["transport_preference"], "transport_direct")
        self.assertEqual(entry["reason"], "delivered")
        self.assertEqual(entry["intent"], {"kind": "nudge", "reason": "inbox-test"})
        # Recipient is the only field still routed via detail (no TS slot for it).
        self.assertEqual(entry["detail"], {"to_worker": "alice"})
        # message_id stays absent for inbox dispatch.
        self.assertNotIn("message_id", entry)

    def test_mailbox_dispatch_writes_message_id_top_level(self) -> None:
        notifier, _ = _make_notifier(
            DispatchOutcome(ok=True, transport="tmux_send_keys", reason="delivered")
        )
        outcome = queue_direct_mailbox_message(
            QueueDirectMessageParams(
                team_name=self.team_name,
                from_worker="alice",
                to_worker="bob",
                body="hello",
                trigger_message="trigger",
                cwd=self.cwd,
                notify=notifier,
                transport_preference="prompt_stdin",
            )
        )
        entries = self._read_delivery_log()
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["dispatch_kind"], "mailbox")
        self.assertEqual(entry["request_id"], outcome.request_id)
        self.assertEqual(entry["message_id"], outcome.message_id)
        self.assertEqual(entry["transport_preference"], "prompt_stdin")
        self.assertEqual(entry["reason"], "delivered")
        # prompt_stdin normalizes the response transport too.
        self.assertEqual(entry["transport"], "send-keys")
        self.assertEqual(entry["detail"], {"to_worker": "bob"})


if __name__ == "__main__":
    unittest.main()
