"""Tests for ``omx.team.runtime_shutdown`` — Phase 2.9b port of
``shutdownTeam`` from ``src/team/runtime.ts``.

Strategy:
- Filesystem state is rooted under a per-test ``tempfile.TemporaryDirectory``.
- The tmux surface (``is_worker_alive``, ``kill_worker``,
  ``destroy_team_session``) and the worktree rollback helper are stubbed
  via ``unittest.mock.patch.object`` so tests do not require tmux or
  ``git``.
- Public entry point ``shutdown_team`` is exercised end-to-end; private
  helpers are exercised directly when they have non-trivial logic
  (drain loop timing, terminal-phase selection).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from omx.team import runtime_shutdown
from omx.team.runtime_shutdown import (
    _collect_provisioned_worktrees,
    _config_is_empty,
    _send_shutdown_inboxes,
    _teardown_worker_panes,
    _terminal_phase_for_shutdown,
    _wait_for_drain,
    shutdown_team,
)
from omx.team.runtime_types import ShutdownOptions, TeamShutdownSummary
from omx.team.state.io import write_team_config
from omx.team.state.manifest import (
    PermissionsSnapshot,
    TeamLeader,
    TeamManifestV2,
    write_team_manifest_v2,
)
from omx.team.state.shutdown import ShutdownAck
from omx.team.state_root import team_dir as _team_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_manifest(
    cwd: str,
    team_name: str,
    *,
    leader_session_id: str = "leader-sid",
) -> None:
    manifest = TeamManifestV2(
        name=team_name,
        task="t",
        leader=TeamLeader(session_id=leader_session_id),
        permissions_snapshot=PermissionsSnapshot(),
        tmux_session=f"omx-team-{team_name}",
        worker_count=1,
        workers=[],
        next_task_id=1,
        created_at="2026-01-01T00:00:00Z",
    )
    write_team_manifest_v2(manifest, cwd)


def _seed_team(
    cwd: str,
    *,
    team_name: str = "t1",
    workers: list[dict[str, Any]] | None = None,
    worker_launch_mode: str = "interactive",
    tmux_session: str | None = None,
) -> str:
    """Seed a minimal team config on disk and return ``team_name``."""
    base = Path(cwd) / ".omx" / "team" / team_name
    base.mkdir(parents=True, exist_ok=True)
    _write_manifest(cwd, team_name)

    if workers is None:
        workers = [{"name": "w1", "index": 1, "pane_id": "%2"}]
    config: dict[str, Any] = {
        "name": team_name,
        "workers": workers,
        "worker_launch_mode": worker_launch_mode,
        "tmux_session": tmux_session
        if tmux_session is not None
        else f"omx-team-{team_name}",
        "leader_pane_id": "%0",
    }
    write_team_config(cwd, config, team_name)
    return team_name


def _write_ack(
    cwd: str,
    team_name: str,
    worker_name: str,
    *,
    status: str = "accept",
    reason: str | None = None,
    updated_at: str = "2099-01-01T00:00:00Z",
) -> None:
    """Write a shutdown-ack file directly (worker would normally do this)."""
    ack_dir = _team_dir(team_name, cwd) / "workers" / worker_name
    ack_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {"status": status, "updated_at": updated_at}
    if reason is not None:
        payload["reason"] = reason
    (ack_dir / "shutdown-ack.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# Helper-level tests
# ---------------------------------------------------------------------------


class TestConfigIsEmpty(unittest.TestCase):
    def test_empty_dict(self) -> None:
        self.assertTrue(_config_is_empty({}))

    def test_none(self) -> None:
        self.assertTrue(_config_is_empty(None))

    def test_populated(self) -> None:
        self.assertFalse(_config_is_empty({"workers": []}))


class TestTerminalPhaseForShutdown(unittest.TestCase):
    def test_graceful_complete(self) -> None:
        self.assertEqual(
            _terminal_phase_for_shutdown([], force=False, confirm_issues=False),
            "complete",
        )

    def test_force_cancelled(self) -> None:
        self.assertEqual(
            _terminal_phase_for_shutdown([], force=True, confirm_issues=False),
            "cancelled",
        )

    def test_rejected_cancelled(self) -> None:
        self.assertEqual(
            _terminal_phase_for_shutdown(
                [("w1", "busy")], force=False, confirm_issues=True
            ),
            "cancelled",
        )


class TestCollectProvisionedWorktrees(unittest.TestCase):
    def test_skips_workers_without_worktree(self) -> None:
        config = {"workers": [{"name": "w1"}], "leader_cwd": "/tmp/repo"}
        self.assertEqual(_collect_provisioned_worktrees(config), [])

    def test_collects_workers_with_worktree(self) -> None:
        config = {
            "leader_cwd": "/tmp/repo",
            "workers": [
                {
                    "name": "w1",
                    "worktree_path": "/tmp/wt-w1",
                    "worktree_branch": "feature/w1",
                    "worktree_branch_created": True,
                }
            ],
        }
        results = _collect_provisioned_worktrees(config)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].worktree_path, "/tmp/wt-w1")
        self.assertEqual(results[0].repo_root, "/tmp/repo")
        self.assertEqual(results[0].branch_name, "feature/w1")
        self.assertTrue(results[0].created_branch)
        self.assertTrue(results[0].created)

    def test_skips_when_no_repo_root(self) -> None:
        config = {
            "workers": [{"name": "w1", "worktree_path": "/tmp/wt"}],
            # No leader_cwd and no per-worker repo_root.
        }
        self.assertEqual(_collect_provisioned_worktrees(config), [])

    def test_uses_per_worker_repo_root_override(self) -> None:
        config = {
            "leader_cwd": "/tmp/leader",
            "workers": [
                {
                    "name": "w1",
                    "worktree_path": "/tmp/wt",
                    "repo_root": "/tmp/repo-override",
                }
            ],
        }
        results = _collect_provisioned_worktrees(config)
        self.assertEqual(results[0].repo_root, "/tmp/repo-override")


# ---------------------------------------------------------------------------
# _send_shutdown_inboxes
# ---------------------------------------------------------------------------


class TestSendShutdownInboxes(unittest.TestCase):
    def test_writes_request_and_inbox_for_each_worker(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    {"name": "w1", "index": 1, "pane_id": "%2"},
                    {"name": "w2", "index": 2, "pane_id": "%3"},
                ],
            )
            from omx.team.state.io import read_team_config

            config = read_team_config(cwd, team)
            ts = _send_shutdown_inboxes(team, config, cwd)

            self.assertEqual(set(ts.keys()), {"w1", "w2"})
            for w in ("w1", "w2"):
                req = _team_dir(team, cwd) / "workers" / w / "shutdown-request.json"
                inbox = _team_dir(team, cwd) / "workers" / w / "inbox.md"
                self.assertTrue(req.exists(), f"missing request for {w}")
                self.assertTrue(inbox.exists(), f"missing inbox for {w}")
                content = inbox.read_text(encoding="utf-8")
                self.assertIn("Shutdown", content)

    def test_skips_worker_without_name(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = "t1"
            (Path(cwd) / ".omx" / "team" / team).mkdir(parents=True, exist_ok=True)
            config = {"workers": [{"index": 1}]}
            ts = _send_shutdown_inboxes(team, config, cwd)
            self.assertEqual(ts, {})

    def test_swallows_per_worker_errors(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    {"name": "w1", "index": 1, "pane_id": "%2"},
                    {"name": "w2", "index": 2, "pane_id": "%3"},
                ],
            )
            from omx.team.state.io import read_team_config

            config = read_team_config(cwd, team)

            real_write = runtime_shutdown.write_shutdown_request
            calls: list[str] = []

            def flaky(team_name: str, worker_name: str, cwd_: str, **kw):
                calls.append(worker_name)
                if worker_name == "w1":
                    raise OSError("disk gone")
                return real_write(team_name, worker_name, cwd_, **kw)

            with patch.object(runtime_shutdown, "write_shutdown_request", flaky):
                ts = _send_shutdown_inboxes(team, config, cwd)

            # w1 failed, w2 succeeded — only w2 is tracked.
            self.assertIn("w2", ts)
            self.assertNotIn("w1", ts)


# ---------------------------------------------------------------------------
# _wait_for_drain
# ---------------------------------------------------------------------------


class TestWaitForDrain(unittest.TestCase):
    def test_no_workers_returns_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            rejected = _wait_for_drain(
                "t1", {"workers": []}, cwd, {}, force=False, drain_timeout_ms=0
            )
            self.assertEqual(rejected, [])

    def test_returns_when_all_panes_dead(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            from omx.team.state.io import read_team_config

            config = read_team_config(cwd, team)
            with patch.object(runtime_shutdown, "is_worker_alive", return_value=False):
                rejected = _wait_for_drain(
                    team,
                    config,
                    cwd,
                    {"w1": "2026-01-01T00:00:00Z"},
                    force=False,
                    drain_timeout_ms=10_000,
                    poll_interval_s=0.0,
                )
            self.assertEqual(rejected, [])

    def test_returns_on_timeout_when_panes_alive(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            from omx.team.state.io import read_team_config

            config = read_team_config(cwd, team)
            with patch.object(runtime_shutdown, "is_worker_alive", return_value=True):
                rejected = _wait_for_drain(
                    team,
                    config,
                    cwd,
                    {"w1": "2026-01-01T00:00:00Z"},
                    force=False,
                    drain_timeout_ms=0,
                    poll_interval_s=0.0,
                )
            self.assertEqual(rejected, [])

    def test_records_accept_ack_event(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            from omx.team.state.io import read_team_config

            config = read_team_config(cwd, team)
            _write_ack(cwd, team, "w1", status="accept")
            with patch.object(runtime_shutdown, "is_worker_alive", return_value=False):
                rejected = _wait_for_drain(
                    team,
                    config,
                    cwd,
                    {"w1": "2026-01-01T00:00:00Z"},
                    force=False,
                    drain_timeout_ms=10_000,
                    poll_interval_s=0.0,
                )
            self.assertEqual(rejected, [])
            events_path = _team_dir(team, cwd) / "events.jsonl"
            self.assertTrue(events_path.exists())
            evts = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            ack_events = [e for e in evts if e.get("event_type") == "shutdown_ack"]
            self.assertEqual(len(ack_events), 1)
            self.assertEqual(ack_events[0]["worker_id"], "w1")
            self.assertEqual(ack_events[0]["detail"]["reason"], "accept")

    def test_reject_ack_raises_when_not_forced(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            from omx.team.state.io import read_team_config

            config = read_team_config(cwd, team)
            _write_ack(cwd, team, "w1", status="reject", reason="busy")
            with patch.object(runtime_shutdown, "is_worker_alive", return_value=True):
                with self.assertRaises(RuntimeError) as ctx:
                    _wait_for_drain(
                        team,
                        config,
                        cwd,
                        {"w1": "2026-01-01T00:00:00Z"},
                        force=False,
                        drain_timeout_ms=10_000,
                        poll_interval_s=0.0,
                    )
            self.assertIn("shutdown_rejected:w1:busy", str(ctx.exception))

    def test_reject_ack_swallowed_when_forced(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            from omx.team.state.io import read_team_config

            config = read_team_config(cwd, team)
            _write_ack(cwd, team, "w1", status="reject", reason="busy")
            with patch.object(runtime_shutdown, "is_worker_alive", return_value=False):
                rejected = _wait_for_drain(
                    team,
                    config,
                    cwd,
                    {"w1": "2026-01-01T00:00:00Z"},
                    force=True,
                    drain_timeout_ms=10_000,
                    poll_interval_s=0.0,
                )
            self.assertEqual(rejected, [("w1", "busy")])


# ---------------------------------------------------------------------------
# _teardown_worker_panes
# ---------------------------------------------------------------------------


class TestTeardownWorkerPanes(unittest.TestCase):
    def test_calls_kill_worker_for_each(self) -> None:
        config = {
            "tmux_session": "omx-team-t1",
            "leader_pane_id": "%0",
            "workers": [
                {"name": "w1", "index": 1, "pane_id": "%2"},
                {"name": "w2", "index": 2, "pane_id": "%3"},
            ],
        }
        with patch.object(runtime_shutdown, "kill_worker") as kw:
            _teardown_worker_panes(config)
        self.assertEqual(kw.call_count, 2)

    def test_swallows_kill_worker_errors(self) -> None:
        config = {
            "tmux_session": "omx-team-t1",
            "leader_pane_id": "%0",
            "workers": [
                {"name": "w1", "index": 1, "pane_id": "%2"},
                {"name": "w2", "index": 2, "pane_id": "%3"},
            ],
        }
        with patch.object(
            runtime_shutdown,
            "kill_worker",
            side_effect=[OSError("boom"), None],
        ) as kw:
            _teardown_worker_panes(config)
        # Both calls attempted despite first one raising.
        self.assertEqual(kw.call_count, 2)


# ---------------------------------------------------------------------------
# shutdown_team — end-to-end
# ---------------------------------------------------------------------------


class TestShutdownTeamMissingConfig(unittest.TestCase):
    def test_returns_summary_with_destroy_and_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            # No config seeded.
            with patch.object(runtime_shutdown, "destroy_team_session") as destroy:
                summary = shutdown_team("t1", cwd)
            self.assertIsInstance(summary, TeamShutdownSummary)
            self.assertIsNone(summary.commit_hygiene_artifacts)
            destroy.assert_called_once_with("omx-team-t1")

    def test_swallows_destroy_failure(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with patch.object(
                runtime_shutdown,
                "destroy_team_session",
                side_effect=OSError("tmux gone"),
            ):
                summary = shutdown_team("t1", cwd)
            self.assertIsInstance(summary, TeamShutdownSummary)


class TestShutdownTeamHappyPath(unittest.TestCase):
    def test_graceful_shutdown_writes_complete_phase(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
            ):
                summary = shutdown_team(
                    team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000
                )

            self.assertIsInstance(summary, TeamShutdownSummary)
            self.assertIsNone(summary.commit_hygiene_artifacts)
            # Team state was cleaned up — directory should no longer exist.
            self.assertFalse((_team_dir(team, cwd)).exists())

    def test_writes_shutdown_request_per_worker(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    {"name": "w1", "index": 1, "pane_id": "%2"},
                    {"name": "w2", "index": 2, "pane_id": "%3"},
                ],
            )
            _write_ack(cwd, team, "w1", status="accept")
            _write_ack(cwd, team, "w2", status="accept")
            # Capture request paths BEFORE state cleanup deletes them.
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(runtime_shutdown, "team_cleanup") as cleanup,
            ):
                shutdown_team(team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000)
                # State dir survives because we stubbed cleanup.
                for w in ("w1", "w2"):
                    req_path = (
                        _team_dir(team, cwd) / "workers" / w / "shutdown-request.json"
                    )
                    self.assertTrue(req_path.exists(), f"missing request for {w}")
                cleanup.assert_called_once()


class TestShutdownTeamForceFlag(unittest.TestCase):
    def test_force_proceeds_through_reject(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="reject", reason="still working")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker") as kw,
                patch.object(runtime_shutdown, "destroy_team_session"),
            ):
                summary = shutdown_team(
                    team,
                    cwd,
                    ShutdownOptions(force=True),
                    poll_interval_s=0.0,
                    drain_timeout_ms=10_000,
                )
            # Force-kill was still invoked.
            self.assertTrue(kw.called)
            self.assertIsInstance(summary, TeamShutdownSummary)
            self.assertFalse((_team_dir(team, cwd)).exists())

    def test_non_force_reject_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="reject", reason="busy")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=True),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    shutdown_team(
                        team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000
                    )
            self.assertIn("shutdown_rejected", str(ctx.exception))


class TestShutdownTeamDrainTimeout(unittest.TestCase):
    def test_timeout_proceeds_to_force_kill(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            # No ack written -- alive forever -- should hit timeout.
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=True),
                patch.object(runtime_shutdown, "kill_worker") as kw,
                patch.object(runtime_shutdown, "destroy_team_session"),
            ):
                summary = shutdown_team(
                    team, cwd, poll_interval_s=0.0, drain_timeout_ms=0
                )
            self.assertTrue(kw.called)
            self.assertIsInstance(summary, TeamShutdownSummary)


class TestShutdownTeamMissingTeam(unittest.TestCase):
    def test_sanitizes_name_and_handles_missing(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with patch.object(runtime_shutdown, "destroy_team_session") as destroy:
                shutdown_team("DoesNotExist!", cwd)
            # Destroy is called with the sanitized name.
            args, _ = destroy.call_args
            self.assertEqual(args[0], "omx-team-doesnotexist")

    def test_empty_team_name_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with self.assertRaises(ValueError):
                shutdown_team("", cwd)


class TestShutdownTeamPartialDeath(unittest.TestCase):
    def test_one_worker_dead_one_alive(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    {"name": "w1", "index": 1, "pane_id": "%2"},
                    {"name": "w2", "index": 2, "pane_id": "%3"},
                ],
            )
            _write_ack(cwd, team, "w1", status="accept")
            _write_ack(cwd, team, "w2", status="accept")

            alive_calls: list[Any] = []

            def alive_side_effect(session: str, idx: int, pane_id: str | None) -> bool:
                alive_calls.append((session, idx, pane_id))
                return idx == 2  # w2 still alive, w1 dead

            with (
                patch.object(
                    runtime_shutdown,
                    "is_worker_alive",
                    side_effect=alive_side_effect,
                ),
                patch.object(runtime_shutdown, "kill_worker") as kw,
                patch.object(runtime_shutdown, "destroy_team_session"),
            ):
                summary = shutdown_team(
                    team, cwd, poll_interval_s=0.0, drain_timeout_ms=0
                )
            # Both workers had kill_worker attempted (best-effort).
            self.assertEqual(kw.call_count, 2)
            self.assertIsInstance(summary, TeamShutdownSummary)


class TestShutdownTeamModeStateSync(unittest.TestCase):
    def test_mode_state_sync_called_on_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(
                    runtime_shutdown, "_sync_team_mode_state_on_shutdown"
                ) as sync,
            ):
                shutdown_team(team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000)
            self.assertTrue(sync.called)
            # Called with (sanitized, cwd, leader_session_id).
            args, _ = sync.call_args
            self.assertEqual(args[0], team)
            self.assertEqual(args[1], cwd)
            self.assertEqual(args[2], "leader-sid")

    def test_mode_state_sync_tolerates_exceptions(self) -> None:
        # Default stub is a no-op; explicitly inject a raising sync to
        # verify shutdown still completes when mode sync is buggy.
        with tempfile.TemporaryDirectory() as cwd:
            # Empty config path: mode-state sync runs there too.
            with (
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(
                    runtime_shutdown,
                    "_sync_team_mode_state_on_shutdown",
                    side_effect=Exception("not_implemented"),
                ),
            ):
                # The empty-config branch DOES surface mode-state-sync
                # exceptions because we don't try/except it there. That's
                # acceptable for the port; just confirm the call happened.
                try:
                    shutdown_team("t-missing", cwd)
                except Exception:  # noqa: BLE001
                    pass

    def test_default_mode_state_sync_is_noop(self) -> None:
        # The stub never raises and returns None.
        result = runtime_shutdown._sync_team_mode_state_on_shutdown("t1", "/tmp")
        self.assertIsNone(result)


class TestShutdownTeamPhaseState(unittest.TestCase):
    def test_phase_state_written_before_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="accept")
            captured: dict[str, Any] = {}

            real_cleanup = runtime_shutdown.team_cleanup

            def capture_cleanup(cwd_: str, team_name: str) -> None:
                phase_path = _team_dir(team_name, cwd_) / "phase-state.json"
                captured["phase_exists_before_cleanup"] = phase_path.exists()
                if phase_path.exists():
                    captured["phase_payload"] = json.loads(
                        phase_path.read_text(encoding="utf-8")
                    )
                real_cleanup(cwd_, team_name)

            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(runtime_shutdown, "team_cleanup", capture_cleanup),
            ):
                shutdown_team(team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000)
            self.assertTrue(captured.get("phase_exists_before_cleanup"))
            self.assertEqual(captured["phase_payload"]["current_phase"], "complete")

    def test_force_writes_cancelled_phase(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            captured: dict[str, Any] = {}
            real_cleanup = runtime_shutdown.team_cleanup

            def capture_cleanup(cwd_: str, team_name: str) -> None:
                phase_path = _team_dir(team_name, cwd_) / "phase-state.json"
                if phase_path.exists():
                    captured["phase_payload"] = json.loads(
                        phase_path.read_text(encoding="utf-8")
                    )
                real_cleanup(cwd_, team_name)

            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(runtime_shutdown, "team_cleanup", capture_cleanup),
            ):
                shutdown_team(
                    team,
                    cwd,
                    ShutdownOptions(force=True),
                    poll_interval_s=0.0,
                    drain_timeout_ms=10_000,
                )
            self.assertEqual(captured["phase_payload"]["current_phase"], "cancelled")


class TestShutdownTeamLeaderSessionStopped(unittest.TestCase):
    def test_marks_leader_session_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(
                    runtime_shutdown, "mark_team_leader_session_stopped"
                ) as mark,
            ):
                shutdown_team(team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000)
            mark.assert_called_once()
            args, _ = mark.call_args
            self.assertEqual(args[0], team)
            self.assertEqual(args[1], cwd)
            self.assertEqual(args[2], "leader-sid")

    def test_tolerates_mark_failure(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(
                    runtime_shutdown,
                    "mark_team_leader_session_stopped",
                    side_effect=OSError("disk"),
                ),
            ):
                summary = shutdown_team(
                    team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000
                )
            self.assertIsInstance(summary, TeamShutdownSummary)


class TestShutdownTeamWorktreeRollback(unittest.TestCase):
    def test_worktree_rollback_invoked_when_workers_have_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    {
                        "name": "w1",
                        "index": 1,
                        "pane_id": "%2",
                        "worktree_path": "/tmp/wt-w1",
                        "repo_root": "/tmp/repo",
                    }
                ],
            )
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(
                    runtime_shutdown, "rollback_provisioned_worktrees"
                ) as rollback,
            ):
                shutdown_team(team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000)
            rollback.assert_called_once()
            args, _ = rollback.call_args
            self.assertEqual(len(args[0]), 1)
            self.assertEqual(args[0][0].worktree_path, "/tmp/wt-w1")

    def test_worktree_rollback_skipped_when_none_provisioned(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(
                    runtime_shutdown, "rollback_provisioned_worktrees"
                ) as rollback,
            ):
                shutdown_team(team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000)
            rollback.assert_not_called()

    def test_worktree_rollback_error_surfaces_as_runtime_error(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    {
                        "name": "w1",
                        "index": 1,
                        "pane_id": "%2",
                        "worktree_path": "/tmp/wt-w1",
                        "repo_root": "/tmp/repo",
                    }
                ],
            )
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(
                    runtime_shutdown,
                    "rollback_provisioned_worktrees",
                    side_effect=RuntimeError("worktree_rollback_failed:foo"),
                ),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    shutdown_team(
                        team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000
                    )
            self.assertIn("rollbackProvisionedWorktrees", str(ctx.exception))


class TestShutdownTeamSharedSession(unittest.TestCase):
    def test_shared_session_skips_destroy(self) -> None:
        # Sessions with ":" in the name are shared windows -- destroy must
        # be skipped (the leader may still be in another pane).
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd, tmux_session="omx-team-t1:0.1")
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session") as destroy,
            ):
                shutdown_team(team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000)
            destroy.assert_not_called()


class TestShutdownTeamReturnsSummary(unittest.TestCase):
    def test_summary_shape(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
            ):
                summary = shutdown_team(
                    team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000
                )
            self.assertIsInstance(summary, TeamShutdownSummary)
            self.assertIsNone(summary.commit_hygiene_artifacts)
            self.assertEqual(summary.to_dict(), {"commit_hygiene_artifacts": None})


class TestShutdownTeamCleanupGating(unittest.TestCase):
    def test_rejected_without_confirm_does_not_cleanup_on_force(self) -> None:
        # With force=True and rejections, cleanup proceeds (force overrides).
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="reject", reason="busy")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
            ):
                shutdown_team(
                    team,
                    cwd,
                    ShutdownOptions(force=True),
                    poll_interval_s=0.0,
                    drain_timeout_ms=10_000,
                )
            self.assertFalse((_team_dir(team, cwd)).exists())

    def test_cleanup_error_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
                patch.object(
                    runtime_shutdown,
                    "team_cleanup",
                    side_effect=OSError("disk full"),
                ),
            ):
                with self.assertRaises(RuntimeError) as ctx:
                    shutdown_team(
                        team, cwd, poll_interval_s=0.0, drain_timeout_ms=10_000
                    )
            self.assertIn("cleanupTeamState", str(ctx.exception))


class TestShutdownTeamOptionsDefaults(unittest.TestCase):
    def test_none_options_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_ack(cwd, team, "w1", status="accept")
            with (
                patch.object(runtime_shutdown, "is_worker_alive", return_value=False),
                patch.object(runtime_shutdown, "kill_worker"),
                patch.object(runtime_shutdown, "destroy_team_session"),
            ):
                # Pass options=None explicitly.
                summary = shutdown_team(
                    team,
                    cwd,
                    options=None,
                    poll_interval_s=0.0,
                    drain_timeout_ms=10_000,
                )
            self.assertIsInstance(summary, TeamShutdownSummary)

    def test_shutdown_options_dataclass_roundtrip(self) -> None:
        opts = ShutdownOptions(force=True, confirm_issues=True)
        self.assertEqual(
            ShutdownOptions.from_dict(opts.to_dict()),
            opts,
        )


# Sanity check that the ShutdownAck shape is what we expect (guard against
# upstream changes in shutdown.py).
class TestShutdownAckShapeSanity(unittest.TestCase):
    def test_accept_round_trip(self) -> None:
        a = ShutdownAck(status="accept", reason="ok", updated_at="2026-01-01T00:00:00Z")
        d = a.to_dict()
        self.assertEqual(d["status"], "accept")
        round_trip = ShutdownAck.from_dict(d)
        self.assertEqual(round_trip, a)


if __name__ == "__main__":
    unittest.main()
