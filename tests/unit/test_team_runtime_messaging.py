"""Tests for ``omx.team.runtime_messaging``.

Covers :func:`send_worker_message` and :func:`broadcast_worker_message`
plus their dispatch-policy / transport-preference helpers.

All filesystem state is rooted under a tempdir; tmux is patched away via
``unittest.mock``. The notifier surface is patched at the
``omx.team.runtime_messaging`` import boundary so the runtime path is
exercised without touching real tmux/subprocess.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from omx.team.mcp_comm import DispatchOutcome
from omx.team.runtime_messaging import (
    _build_broadcast_notifier,
    _build_leader_notifier,
    _build_worker_notifier,
    _find_worker,
    _is_existing_mailbox_notification,
    _notify_leader_outcome,
    _notify_worker_outcome,
    _resolve_dispatch_policy,
    _resolve_instruction_state_root,
    _resolve_leader_transport_preference,
    _resolve_worker_transport_preference,
    _worker_index,
    broadcast_worker_message,
    send_worker_message,
)
from omx.team.state.policy import (
    TeamDispatchMode,
    TeamPolicy,
    TeamWorkerLaunchMode,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _init_team_with_config(
    cwd: str,
    team_name: str = "t1",
    *,
    workers: list[dict[str, Any]] | None = None,
    worker_launch_mode: str = "interactive",
    tmux_session: str = "test-session",
    leader_pane_id: str | None = "%99",
    manifest_policy: dict[str, Any] | None = None,
) -> None:
    """Initialize a team state dir, config.json, and (optionally) manifest."""
    base = Path(cwd) / ".omx" / "team" / team_name
    base.mkdir(parents=True, exist_ok=True)
    config: dict[str, Any] = {
        "name": team_name,
        "worker_launch_mode": worker_launch_mode,
        "tmux_session": tmux_session,
        "leader_pane_id": leader_pane_id,
        "workers": workers or [],
    }
    (base / "config.json").write_text(json.dumps(config), encoding="utf-8")
    if manifest_policy is not None:
        manifest = {
            "schema_version": 2,
            "name": team_name,
            "task": "",
            "tmux_session": tmux_session,
            "worker_count": len(workers or []),
            "next_task_id": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "workers": [],
            "leader_pane_id": leader_pane_id,
            "hud_pane_id": None,
            "resize_hook_name": None,
            "resize_hook_target": None,
            "leader": {
                "session_id": "s",
                "started_at": "2026-01-01T00:00:00Z",
                "leader_cli": "codex",
            },
            "permissions_snapshot": {
                "sandbox": "workspace-write",
                "approval_policy": "on-failure",
            },
            "lifecycle_profile": "default",
            "policy": manifest_policy,
            "governance": {},
        }
        (base / "manifest.v2.json").write_text(json.dumps(manifest), encoding="utf-8")


def _make_worker(
    name: str,
    index: int,
    pane_id: str = "",
    worker_cli: str = "codex",
    worktree_path: str | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "index": index,
        "pane_id": pane_id,
        "worker_cli": worker_cli,
        "worktree_path": worktree_path,
    }


# ---------------------------------------------------------------------------
# Pure-helper tests
# ---------------------------------------------------------------------------


class TestResolveDispatchPolicy(unittest.TestCase):
    def test_no_manifest_uses_defaults(self) -> None:
        policy = _resolve_dispatch_policy(None, "interactive")
        self.assertEqual(policy.worker_launch_mode, TeamWorkerLaunchMode.INTERACTIVE)
        self.assertEqual(
            policy.dispatch_mode, TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK
        )

    def test_manifest_split_pane_preserved(self) -> None:
        policy = _resolve_dispatch_policy({"display_mode": "split_pane"}, "interactive")
        self.assertEqual(policy.display_mode.value, "split_pane")

    def test_manifest_transport_direct(self) -> None:
        policy = _resolve_dispatch_policy(
            {"dispatch_mode": "transport_direct"}, "interactive"
        )
        self.assertEqual(policy.dispatch_mode, TeamDispatchMode.TRANSPORT_DIRECT)

    def test_prompt_worker_launch_mode_threaded_through(self) -> None:
        policy = _resolve_dispatch_policy(None, "prompt")
        self.assertEqual(policy.worker_launch_mode, TeamWorkerLaunchMode.PROMPT)


class TestResolveTransportPreference(unittest.TestCase):
    def test_prompt_launch_mode_forces_prompt_stdin(self) -> None:
        policy = TeamPolicy(
            dispatch_mode=TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK,
        )
        self.assertEqual(
            _resolve_worker_transport_preference("prompt", policy), "prompt_stdin"
        )

    def test_transport_direct(self) -> None:
        policy = TeamPolicy(dispatch_mode=TeamDispatchMode.TRANSPORT_DIRECT)
        self.assertEqual(
            _resolve_worker_transport_preference("interactive", policy),
            "transport_direct",
        )

    def test_default_is_hook_preferred(self) -> None:
        policy = TeamPolicy(dispatch_mode=TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK)
        self.assertEqual(
            _resolve_worker_transport_preference("interactive", policy),
            "hook_preferred_with_fallback",
        )

    def test_leader_transport_never_prompt_stdin(self) -> None:
        policy = TeamPolicy(dispatch_mode=TeamDispatchMode.TRANSPORT_DIRECT)
        self.assertEqual(
            _resolve_leader_transport_preference(policy), "transport_direct"
        )
        policy_hook = TeamPolicy(
            dispatch_mode=TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK
        )
        self.assertEqual(
            _resolve_leader_transport_preference(policy_hook),
            "hook_preferred_with_fallback",
        )


class TestResolveInstructionStateRoot(unittest.TestCase):
    def test_no_worktree(self) -> None:
        self.assertEqual(_resolve_instruction_state_root(None), ".omx/state")

    def test_with_worktree(self) -> None:
        self.assertEqual(
            _resolve_instruction_state_root("/tmp/wt"), ".omx-worker/state"
        )


class TestFindWorker(unittest.TestCase):
    def test_found(self) -> None:
        config = {"workers": [_make_worker("alice", 0), _make_worker("bob", 1)]}
        self.assertEqual(_find_worker(config, "bob")["index"], 1)

    def test_missing(self) -> None:
        self.assertIsNone(_find_worker({"workers": []}, "alice"))

    def test_workers_field_missing(self) -> None:
        self.assertIsNone(_find_worker({}, "alice"))


class TestWorkerIndex(unittest.TestCase):
    def test_int(self) -> None:
        self.assertEqual(_worker_index({"index": 3}), 3)

    def test_string(self) -> None:
        self.assertEqual(_worker_index({"index": "2"}), 2)

    def test_invalid_string(self) -> None:
        self.assertIsNone(_worker_index({"index": "x"}))

    def test_bool_rejected(self) -> None:
        self.assertIsNone(_worker_index({"index": True}))

    def test_missing(self) -> None:
        self.assertIsNone(_worker_index({}))


class TestIsExistingMailboxNotification(unittest.TestCase):
    def test_matches(self) -> None:
        outcome = DispatchOutcome(
            ok=True, transport="mailbox", reason="existing_message_already_notified"
        )
        self.assertTrue(_is_existing_mailbox_notification(outcome))

    def test_other_reason(self) -> None:
        outcome = DispatchOutcome(ok=True, transport="mailbox", reason="ok")
        self.assertFalse(_is_existing_mailbox_notification(outcome))

    def test_failed(self) -> None:
        outcome = DispatchOutcome(
            ok=False, transport="mailbox", reason="existing_message_already_notified"
        )
        self.assertFalse(_is_existing_mailbox_notification(outcome))


# ---------------------------------------------------------------------------
# Notifier construction tests
# ---------------------------------------------------------------------------


class TestBuildWorkerNotifier(unittest.TestCase):
    def test_hook_preferred_short_circuits(self) -> None:
        config = {"workers": []}
        notifier = _build_worker_notifier(
            config, "hook_preferred_with_fallback", 1, "%5"
        )
        from omx.team.mcp_comm import TeamNotifierTarget

        outcome = notifier(
            TeamNotifierTarget(worker_name="x", worker_index=1), "msg", {}
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "hook")
        self.assertEqual(outcome.reason, "queued_for_hook_dispatch")

    def test_missing_worker_index(self) -> None:
        config = {"workers": []}
        notifier = _build_worker_notifier(config, "transport_direct", None, None)
        from omx.team.mcp_comm import TeamNotifierTarget

        outcome = notifier(TeamNotifierTarget(worker_name="x"), "msg", {})
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "missing_worker_index")

    def test_direct_path_invokes_notify_worker(self) -> None:
        config = {
            "tmux_session": "sess",
            "worker_launch_mode": "interactive",
            "workers": [_make_worker("alice", 0, pane_id="%1")],
        }
        notifier = _build_worker_notifier(config, "transport_direct", 0, "%1")
        from omx.team.mcp_comm import TeamNotifierTarget

        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch("omx.team.runtime_messaging.send_to_worker") as send_mock,
        ):
            outcome = notifier(
                TeamNotifierTarget(worker_name="alice", worker_index=0, pane_id="%1"),
                "msg",
                {},
            )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "tmux_send_keys")
        send_mock.assert_called_once()


class TestBuildLeaderNotifier(unittest.TestCase):
    def test_hook_preferred_short_circuits(self) -> None:
        notifier = _build_leader_notifier({}, "hook_preferred_with_fallback")
        from omx.team.mcp_comm import TeamNotifierTarget

        outcome = notifier(TeamNotifierTarget(worker_name="leader-fixed"), "msg", {})
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.reason, "queued_for_hook_dispatch")

    def test_direct_path_invokes_notify_leader(self) -> None:
        config = {"tmux_session": "sess"}
        notifier = _build_leader_notifier(config, "transport_direct")
        from omx.team.mcp_comm import TeamNotifierTarget

        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch("omx.team.runtime_messaging.notify_leader_status", return_value=True),
        ):
            outcome = notifier(
                TeamNotifierTarget(worker_name="leader-fixed"), "msg", {}
            )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "tmux_send_keys")


class TestBuildBroadcastNotifier(unittest.TestCase):
    def test_hook_preferred_short_circuits(self) -> None:
        notifier = _build_broadcast_notifier({}, "hook_preferred_with_fallback")
        from omx.team.mcp_comm import TeamNotifierTarget

        outcome = notifier(
            TeamNotifierTarget(worker_name="alice", worker_index=0), "msg", {}
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.reason, "queued_for_hook_dispatch")

    def test_missing_index(self) -> None:
        notifier = _build_broadcast_notifier({}, "transport_direct")
        from omx.team.mcp_comm import TeamNotifierTarget

        outcome = notifier(TeamNotifierTarget(worker_name="alice"), "msg", {})
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "missing_worker_index")


class TestNotifyWorkerOutcome(unittest.TestCase):
    def test_worker_not_found(self) -> None:
        outcome = _notify_worker_outcome({"workers": []}, 5, "msg", None)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "worker_not_found")

    def test_prompt_mode_returns_handle_missing(self) -> None:
        config = {
            "worker_launch_mode": "prompt",
            "workers": [_make_worker("alice", 0)],
        }
        outcome = _notify_worker_outcome(config, 0, "msg", None)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.transport, "prompt_stdin")
        self.assertEqual(outcome.reason, "prompt_worker_handle_missing")

    def test_no_tmux_session(self) -> None:
        config = {
            "worker_launch_mode": "interactive",
            "tmux_session": "",
            "workers": [_make_worker("alice", 0)],
        }
        outcome = _notify_worker_outcome(config, 0, "msg", None)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "tmux_unavailable")

    def test_send_failure(self) -> None:
        config = {
            "worker_launch_mode": "interactive",
            "tmux_session": "sess",
            "workers": [_make_worker("alice", 0, pane_id="%1")],
        }
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch(
                "omx.team.runtime_messaging.send_to_worker",
                side_effect=RuntimeError("boom"),
            ),
        ):
            outcome = _notify_worker_outcome(config, 0, "msg", "%1")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.reason.startswith("tmux_send_keys_failed:"))

    def test_send_success(self) -> None:
        config = {
            "worker_launch_mode": "interactive",
            "tmux_session": "sess",
            "workers": [_make_worker("alice", 0, pane_id="%1")],
        }
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch("omx.team.runtime_messaging.send_to_worker") as send_mock,
        ):
            outcome = _notify_worker_outcome(config, 0, "msg", "%1")
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "tmux_send_keys")
        self.assertEqual(outcome.reason, "tmux_send_keys_sent")
        send_mock.assert_called_once()


class TestNotifyLeaderOutcome(unittest.TestCase):
    def test_no_session(self) -> None:
        outcome = _notify_leader_outcome({"tmux_session": ""}, "msg")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "tmux_unavailable")

    def test_display_message_failure(self) -> None:
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch(
                "omx.team.runtime_messaging.notify_leader_status", return_value=False
            ),
        ):
            outcome = _notify_leader_outcome({"tmux_session": "sess"}, "msg")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "tmux_send_keys_failed")

    def test_display_message_exception(self) -> None:
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch(
                "omx.team.runtime_messaging.notify_leader_status",
                side_effect=RuntimeError("boom"),
            ),
        ):
            outcome = _notify_leader_outcome({"tmux_session": "sess"}, "msg")
        self.assertFalse(outcome.ok)
        self.assertTrue(outcome.reason.startswith("tmux_send_keys_failed:"))

    def test_success(self) -> None:
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch("omx.team.runtime_messaging.notify_leader_status", return_value=True),
        ):
            outcome = _notify_leader_outcome({"tmux_session": "sess"}, "msg")
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.reason, "tmux_send_keys_sent")


# ---------------------------------------------------------------------------
# send_worker_message integration tests
# ---------------------------------------------------------------------------


class _TmpTeamCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name
        self.team_name = "t1"

    def tearDown(self) -> None:
        self._tmp.cleanup()


class TestSendWorkerMessageMissingTeam(_TmpTeamCase):
    def test_no_config_raises(self) -> None:
        with self.assertRaises(ValueError):
            send_worker_message(self.team_name, "alice", "bob", "body", self.cwd)


class TestSendWorkerMessageDirect(_TmpTeamCase):
    def _setup_transport_direct(self) -> None:
        _init_team_with_config(
            self.cwd,
            self.team_name,
            workers=[
                _make_worker("alice", 0, pane_id="%1"),
                _make_worker("bob", 1, pane_id="%2"),
            ],
            manifest_policy={"dispatch_mode": "transport_direct"},
        )

    def _setup_hook_preferred(self) -> None:
        _init_team_with_config(
            self.cwd,
            self.team_name,
            workers=[
                _make_worker("alice", 0, pane_id="%1"),
                _make_worker("bob", 1, pane_id="%2"),
            ],
        )

    def test_hook_preferred_returns_queued_outcome(self) -> None:
        # Default policy (no manifest) is hook_preferred_with_fallback.
        self._setup_hook_preferred()
        outcome = send_worker_message(
            self.team_name, "alice", "bob", "hello bob", self.cwd
        )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "hook")
        self.assertEqual(outcome.reason, "queued_for_hook_dispatch")
        self.assertEqual(outcome.to_worker, "bob")

    def test_transport_direct_invokes_tmux(self) -> None:
        self._setup_transport_direct()
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch("omx.team.runtime_messaging.send_to_worker") as send_mock,
        ):
            outcome = send_worker_message(
                self.team_name, "alice", "bob", "hello", self.cwd
            )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "tmux_send_keys")
        send_mock.assert_called_once()

    def test_unknown_worker_raises(self) -> None:
        self._setup_hook_preferred()
        with self.assertRaises(ValueError):
            send_worker_message(self.team_name, "alice", "ghost", "hello", self.cwd)

    def test_direct_tmux_failure_raises_runtime_error(self) -> None:
        self._setup_transport_direct()
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch(
                "omx.team.runtime_messaging.send_to_worker",
                side_effect=RuntimeError("boom"),
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                send_worker_message(self.team_name, "alice", "bob", "msg", self.cwd)
        self.assertIn("mailbox_notify_failed:", str(ctx.exception))


class TestSendWorkerMessageLeaderFixed(_TmpTeamCase):
    def test_leader_fixed_hook_with_pane_id(self) -> None:
        _init_team_with_config(
            self.cwd,
            self.team_name,
            workers=[_make_worker("alice", 0, pane_id="%1")],
            leader_pane_id="%99",
        )
        outcome = send_worker_message(
            self.team_name, "alice", "leader-fixed", "for leader", self.cwd
        )
        # Hook preferred with leader pane present → queued_for_hook_dispatch
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "hook")
        self.assertEqual(outcome.reason, "queued_for_hook_dispatch")

    def test_leader_fixed_hook_without_pane_persists(self) -> None:
        _init_team_with_config(
            self.cwd,
            self.team_name,
            workers=[_make_worker("alice", 0, pane_id="%1")],
            leader_pane_id=None,
        )
        outcome = send_worker_message(
            self.team_name, "alice", "leader-fixed", "for leader", self.cwd
        )
        # Should be restamped as soft-persisted success.
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "mailbox")
        self.assertEqual(outcome.reason, "leader_pane_missing_mailbox_persisted")
        self.assertEqual(outcome.to_worker, "leader-fixed")

    def test_leader_fixed_transport_direct_invokes_notify_leader(self) -> None:
        _init_team_with_config(
            self.cwd,
            self.team_name,
            workers=[_make_worker("alice", 0, pane_id="%1")],
            manifest_policy={"dispatch_mode": "transport_direct"},
        )
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch(
                "omx.team.runtime_messaging.notify_leader_status", return_value=True
            ) as notify_mock,
        ):
            outcome = send_worker_message(
                self.team_name, "alice", "leader-fixed", "msg", self.cwd
            )
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.transport, "tmux_send_keys")
        notify_mock.assert_called_once()


# ---------------------------------------------------------------------------
# broadcast_worker_message integration tests
# ---------------------------------------------------------------------------


class TestBroadcastWorkerMessage(_TmpTeamCase):
    def test_no_config_raises(self) -> None:
        with self.assertRaises(ValueError):
            broadcast_worker_message(self.team_name, "alice", "ping", self.cwd)

    def test_no_workers_is_noop(self) -> None:
        _init_team_with_config(self.cwd, self.team_name, workers=[])
        # Should not raise and should not require a notifier.
        broadcast_worker_message(self.team_name, "alice", "ping", self.cwd)

    def test_broadcast_skips_sender(self) -> None:
        _init_team_with_config(
            self.cwd,
            self.team_name,
            workers=[
                _make_worker("alice", 0, pane_id="%1"),
                _make_worker("bob", 1, pane_id="%2"),
                _make_worker("carol", 2, pane_id="%3"),
            ],
        )
        broadcast_worker_message(self.team_name, "alice", "team ping", self.cwd)
        # alice's mailbox must not have the broadcast body.
        alice_box = (
            Path(self.cwd)
            / ".omx"
            / "team"
            / self.team_name
            / "workers"
            / "alice"
            / "mailbox.json"
        )
        if alice_box.exists():
            data = json.loads(alice_box.read_text(encoding="utf-8"))
            for m in data.get("messages", []):
                self.assertNotEqual(m.get("body"), "team ping")

    def test_broadcast_hook_preferred(self) -> None:
        _init_team_with_config(
            self.cwd,
            self.team_name,
            workers=[
                _make_worker("alice", 0, pane_id="%1"),
                _make_worker("bob", 1, pane_id="%2"),
            ],
        )
        # Hook preferred path short-circuits via the notifier → no tmux call.
        broadcast_worker_message(self.team_name, "alice", "ping", self.cwd)

    def test_broadcast_transport_direct_calls_tmux(self) -> None:
        _init_team_with_config(
            self.cwd,
            self.team_name,
            workers=[
                _make_worker("alice", 0, pane_id="%1"),
                _make_worker("bob", 1, pane_id="%2"),
                _make_worker("carol", 2, pane_id="%3"),
            ],
            manifest_policy={"dispatch_mode": "transport_direct"},
        )
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch("omx.team.runtime_messaging.send_to_worker") as send_mock,
        ):
            broadcast_worker_message(self.team_name, "alice", "ping", self.cwd)
        # bob + carol → 2 calls (alice is the sender and is skipped).
        self.assertEqual(send_mock.call_count, 2)

    def test_broadcast_failure_raises_first_failure(self) -> None:
        _init_team_with_config(
            self.cwd,
            self.team_name,
            workers=[
                _make_worker("alice", 0, pane_id="%1"),
                _make_worker("bob", 1, pane_id="%2"),
            ],
            manifest_policy={"dispatch_mode": "transport_direct"},
        )
        with (
            patch("omx.team.runtime_messaging.is_tmux_available", return_value=True),
            patch(
                "omx.team.runtime_messaging.send_to_worker",
                side_effect=RuntimeError("nope"),
            ),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                broadcast_worker_message(self.team_name, "alice", "ping", self.cwd)
        self.assertIn("mailbox_notify_failed:", str(ctx.exception))


# ---------------------------------------------------------------------------
# Team name sanitization
# ---------------------------------------------------------------------------


class TestTeamNameSanitization(_TmpTeamCase):
    def test_send_sanitizes_team_name(self) -> None:
        # Config is stored under "my-team"; calling with "My_Team" must
        # still resolve via sanitize_team_name.
        _init_team_with_config(
            self.cwd,
            "my-team",
            workers=[
                _make_worker("alice", 0, pane_id="%1"),
                _make_worker("bob", 1, pane_id="%2"),
            ],
        )
        outcome = send_worker_message("My_Team", "alice", "bob", "hi", self.cwd)
        self.assertTrue(outcome.ok)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
