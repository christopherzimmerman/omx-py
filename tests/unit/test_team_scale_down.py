"""Tests for ``omx.team.scaling_down`` — Phase 3b port of ``scaleDown``.

Strategy:
- Filesystem state is rooted under a per-test ``tempfile.TemporaryDirectory``.
- The tmux surface (``is_worker_alive``, ``kill_worker``) and the worktree
  rollback / AGENTS.md helpers are patched via ``unittest.mock.patch.object``
  so tests do not require tmux or ``git``.
- Each test enables ``OMX_TEAM_SCALING_ENABLED`` via the ``env`` parameter
  rather than mutating ``os.environ`` so the suite stays hermetic.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from omx.team import scaling_down
from omx.team.scaling_down import (
    ScaleDownOptions,
    ScaleDownResult,
    ScaleError,
    _collect_detached_worktrees,
    _read_worker_state,
    _resolve_target_workers,
    _wait_for_drain,
    assert_scaling_enabled,
    is_scaling_enabled,
    scale_down,
)
from omx.team.state.io import (
    read_team_config,
    read_worker_status,
    write_team_config,
)
from omx.team.state_root import team_dir as _team_dir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ENABLED_ENV: dict[str, str] = {"OMX_TEAM_SCALING_ENABLED": "1"}


def _make_worker(
    name: str,
    index: int,
    *,
    pane_id: str | None = None,
    worktree_path: str | None = None,
    worktree_repo_root: str | None = None,
    worktree_detached: bool | None = None,
    worktree_created: bool | None = None,
    team_state_root: str | None = None,
) -> dict[str, Any]:
    worker: dict[str, Any] = {"name": name, "index": index}
    if pane_id is not None:
        worker["pane_id"] = pane_id
    if worktree_path is not None:
        worker["worktree_path"] = worktree_path
    if worktree_repo_root is not None:
        worker["worktree_repo_root"] = worktree_repo_root
    if worktree_detached is not None:
        worker["worktree_detached"] = worktree_detached
    if worktree_created is not None:
        worker["worktree_created"] = worktree_created
    if team_state_root is not None:
        worker["team_state_root"] = team_state_root
    return worker


def _seed_team(
    cwd: str,
    *,
    team_name: str = "t1",
    workers: list[dict[str, Any]] | None = None,
    leader_pane_id: str | None = "%0",
    tmux_session: str | None = None,
) -> str:
    """Seed a minimal team config on disk and return ``team_name``."""
    base = Path(cwd) / ".omx" / "team" / team_name
    base.mkdir(parents=True, exist_ok=True)
    if workers is None:
        workers = [
            _make_worker("w1", 1, pane_id="%2"),
            _make_worker("w2", 2, pane_id="%3"),
        ]
    config: dict[str, Any] = {
        "name": team_name,
        "workers": workers,
        "worker_count": len(workers),
        "tmux_session": tmux_session
        if tmux_session is not None
        else f"omx-team-{team_name}",
        "leader_pane_id": leader_pane_id,
    }
    write_team_config(cwd, config, team_name)
    return team_name


def _write_status(cwd: str, team_name: str, worker_name: str, state: str) -> None:
    """Write a status file directly (bypassing the helper to control timing)."""
    d = _team_dir(team_name, cwd) / "workers" / worker_name
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(
        json.dumps({"state": state, "updated_at": "2099-01-01T00:00:00Z"}),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Environment gate
# ---------------------------------------------------------------------------


class TestScalingEnabledGate(unittest.TestCase):
    def test_disabled_when_unset(self) -> None:
        self.assertFalse(is_scaling_enabled({}))

    def test_disabled_for_falsey_values(self) -> None:
        for val in ("0", "false", "no", "off", "disabled", "", "  "):
            self.assertFalse(
                is_scaling_enabled({"OMX_TEAM_SCALING_ENABLED": val}),
                f"unexpectedly enabled for {val!r}",
            )

    def test_enabled_for_truthy_values(self) -> None:
        for val in ("1", "true", "TRUE", "Yes", "on", "enabled"):
            self.assertTrue(
                is_scaling_enabled({"OMX_TEAM_SCALING_ENABLED": val}),
                f"unexpectedly disabled for {val!r}",
            )

    def test_assert_raises_when_disabled(self) -> None:
        with self.assertRaises(RuntimeError):
            assert_scaling_enabled({})

    def test_assert_silent_when_enabled(self) -> None:
        # No raise.
        assert_scaling_enabled(_ENABLED_ENV)


# ---------------------------------------------------------------------------
# _read_worker_state
# ---------------------------------------------------------------------------


class TestReadWorkerState(unittest.TestCase):
    def test_missing_status_returns_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            self.assertEqual(_read_worker_state(team, "w1", cwd), "unknown")

    def test_existing_status_returns_state(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "idle")
            self.assertEqual(_read_worker_state(team, "w1", cwd), "idle")


# ---------------------------------------------------------------------------
# _resolve_target_workers
# ---------------------------------------------------------------------------


class TestResolveTargetWorkers(unittest.TestCase):
    def test_explicit_names_returns_matches_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            config = read_team_config(cwd, team)
            result = _resolve_target_workers(
                team, cwd, config, ScaleDownOptions(worker_names=["w2", "w1"])
            )
            self.assertIsInstance(result, list)
            names = [w["name"] for w in result]  # type: ignore[index]
            self.assertEqual(names, ["w2", "w1"])

    def test_explicit_unknown_name_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            config = read_team_config(cwd, team)
            result = _resolve_target_workers(
                team, cwd, config, ScaleDownOptions(worker_names=["w-missing"])
            )
            self.assertIsInstance(result, ScaleError)
            self.assertIn("not found", result.error)  # type: ignore[union-attr]

    def test_count_picks_idle_workers(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "idle")
            _write_status(cwd, team, "w2", "working")
            config = read_team_config(cwd, team)
            result = _resolve_target_workers(
                team, cwd, config, ScaleDownOptions(count=1)
            )
            self.assertEqual([w["name"] for w in result], ["w1"])  # type: ignore[union-attr]

    def test_count_picks_done_and_unknown_as_idle(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "done")
            # w2 has no status -> unknown
            config = read_team_config(cwd, team)
            result = _resolve_target_workers(
                team, cwd, config, ScaleDownOptions(count=2)
            )
            self.assertEqual({w["name"] for w in result}, {"w1", "w2"})  # type: ignore[union-attr]

    def test_not_enough_idle_without_force_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "working")
            _write_status(cwd, team, "w2", "working")
            config = read_team_config(cwd, team)
            result = _resolve_target_workers(
                team, cwd, config, ScaleDownOptions(count=1)
            )
            self.assertIsInstance(result, ScaleError)
            self.assertIn("Not enough idle", result.error)  # type: ignore[union-attr]

    def test_force_fills_with_non_idle(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "working")
            _write_status(cwd, team, "w2", "working")
            config = read_team_config(cwd, team)
            result = _resolve_target_workers(
                team, cwd, config, ScaleDownOptions(count=1, force=True)
            )
            self.assertEqual(len(result), 1)  # type: ignore[arg-type]
            self.assertIn(result[0]["name"], {"w1", "w2"})  # type: ignore[index]

    def test_count_invalid_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            config = read_team_config(cwd, team)
            for bad in (0, -1, True):
                result = _resolve_target_workers(
                    team,
                    cwd,
                    config,
                    ScaleDownOptions(count=bad),  # type: ignore[arg-type]
                )
                self.assertIsInstance(result, ScaleError)
                self.assertIn("positive integer", result.error)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# _wait_for_drain
# ---------------------------------------------------------------------------


class TestWaitForDrain(unittest.TestCase):
    def test_no_targets_returns_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _wait_for_drain("t1", cwd, [], "sess", 5_000, poll_interval_s=0.0)
            # nothing to assert beyond not raising

    def test_returns_when_all_drained_states(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "idle")
            _write_status(cwd, team, "w2", "done")
            config = read_team_config(cwd, team)
            with patch.object(scaling_down, "is_worker_alive", return_value=True):
                _wait_for_drain(
                    team,
                    cwd,
                    list(config["workers"]),
                    "omx-team-t1",
                    5_000,
                    poll_interval_s=0.0,
                )

    def test_returns_when_panes_dead(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            # No status -> would block, but panes dead trips the bypass.
            config = read_team_config(cwd, team)
            with patch.object(scaling_down, "is_worker_alive", return_value=False):
                _wait_for_drain(
                    team,
                    cwd,
                    list(config["workers"]),
                    "omx-team-t1",
                    5_000,
                    poll_interval_s=0.0,
                )

    def test_returns_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "working")
            config = read_team_config(cwd, team)
            with patch.object(scaling_down, "is_worker_alive", return_value=True):
                _wait_for_drain(
                    team,
                    cwd,
                    list(config["workers"]),
                    "omx-team-t1",
                    0,  # immediate deadline
                    poll_interval_s=0.0,
                )


# ---------------------------------------------------------------------------
# _collect_detached_worktrees
# ---------------------------------------------------------------------------


class TestCollectDetachedWorktrees(unittest.TestCase):
    def test_skips_workers_without_detached_metadata(self) -> None:
        workers = [
            _make_worker("w1", 1, worktree_path="/tmp/wt", worktree_created=False),
            _make_worker(
                "w2",
                2,
                worktree_path="/tmp/wt",
                worktree_created=True,
                worktree_detached=False,
                worktree_repo_root="/tmp/repo",
            ),
            _make_worker("w3", 3),  # no worktree at all
        ]
        self.assertEqual(_collect_detached_worktrees(workers), [])

    def test_collects_detached_workers(self) -> None:
        workers = [
            _make_worker(
                "w1",
                1,
                worktree_path="/tmp/wt-1",
                worktree_repo_root="/tmp/repo",
                worktree_created=True,
                worktree_detached=True,
            )
        ]
        result = _collect_detached_worktrees(workers)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].worktree_path, "/tmp/wt-1")
        self.assertEqual(result[0].repo_root, "/tmp/repo")
        self.assertTrue(result[0].detached)
        self.assertIsNone(result[0].branch_name)


# ---------------------------------------------------------------------------
# scale_down end-to-end
# ---------------------------------------------------------------------------


class TestScaleDown(unittest.TestCase):
    def setUp(self) -> None:
        # Patch all tmux/worktree side-effects across every scale_down test.
        self._is_alive = patch.object(
            scaling_down, "is_worker_alive", return_value=False
        )
        self._kill = patch.object(scaling_down, "kill_worker", return_value=None)
        self._rollback = patch.object(
            scaling_down, "rollback_provisioned_worktrees", return_value=None
        )
        self._remove_agents = patch.object(
            scaling_down,
            "remove_worker_worktree_root_agents_file",
            return_value=None,
        )
        self._is_alive.start()
        self._kill.start()
        self._rollback.start()
        self._remove_agents.start()
        self.addCleanup(self._is_alive.stop)
        self.addCleanup(self._kill.stop)
        self.addCleanup(self._rollback.stop)
        self.addCleanup(self._remove_agents.stop)

    def test_raises_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            with self.assertRaises(RuntimeError):
                scale_down("t1", cwd, options=None, env={})

    def test_team_not_found_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            result = scale_down(
                "ghost",
                cwd,
                options=ScaleDownOptions(count=1, force=True),
                env=_ENABLED_ENV,
            )
            self.assertIsInstance(result, ScaleError)
            self.assertIn("not found", result.error)  # type: ignore[union-attr]

    def test_explicit_worker_names_removes_them(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    _make_worker("w1", 1, pane_id="%2"),
                    _make_worker("w2", 2, pane_id="%3"),
                    _make_worker("w3", 3, pane_id="%4"),
                ],
            )
            result = scale_down(
                team,
                cwd,
                options=ScaleDownOptions(worker_names=["w2"], force=True),
                env=_ENABLED_ENV,
                poll_interval_s=0.0,
            )
            self.assertIsInstance(result, ScaleDownResult)
            assert isinstance(result, ScaleDownResult)
            self.assertEqual(result.removed_workers, ["w2"])
            self.assertEqual(result.new_worker_count, 2)
            cfg = read_team_config(cwd, team)
            names = [w["name"] for w in cfg["workers"]]
            self.assertEqual(names, ["w1", "w3"])
            self.assertEqual(cfg["worker_count"], 2)

    def test_unknown_explicit_worker_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            result = scale_down(
                team,
                cwd,
                options=ScaleDownOptions(worker_names=["ghost"], force=True),
                env=_ENABLED_ENV,
            )
            self.assertIsInstance(result, ScaleError)

    def test_idle_count_selection_writes_draining_status(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    _make_worker("w1", 1),
                    _make_worker("w2", 2),
                    _make_worker("w3", 3),
                ],
            )
            _write_status(cwd, team, "w1", "idle")
            _write_status(cwd, team, "w2", "working")
            _write_status(cwd, team, "w3", "done")
            result = scale_down(
                team,
                cwd,
                options=ScaleDownOptions(count=1),
                env=_ENABLED_ENV,
                poll_interval_s=0.0,
            )
            self.assertIsInstance(result, ScaleDownResult)
            assert isinstance(result, ScaleDownResult)
            # w1 or w3 — both idle-like; the first match in config order is w1.
            self.assertEqual(result.removed_workers, ["w1"])
            # Draining status was written before kill.
            status = read_worker_status(cwd, team, "w1")
            assert status is not None
            self.assertIn(status["state"], {"draining"})  # final state at write time

    def test_minimum_worker_guard_blocks_full_drain(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    _make_worker("w1", 1),
                    _make_worker("w2", 2),
                ],
            )
            result = scale_down(
                team,
                cwd,
                options=ScaleDownOptions(worker_names=["w1", "w2"], force=True),
                env=_ENABLED_ENV,
                poll_interval_s=0.0,
            )
            self.assertIsInstance(result, ScaleError)
            self.assertIn("at least 1", result.error)  # type: ignore[union-attr]
            # Config untouched.
            cfg = read_team_config(cwd, team)
            self.assertEqual(len(cfg["workers"]), 2)

    def test_not_enough_idle_without_force(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "working")
            _write_status(cwd, team, "w2", "working")
            result = scale_down(
                team,
                cwd,
                options=ScaleDownOptions(count=1, force=False),
                env=_ENABLED_ENV,
                poll_interval_s=0.0,
            )
            self.assertIsInstance(result, ScaleError)
            self.assertIn("Not enough idle", result.error)  # type: ignore[union-attr]

    def test_force_kill_busy_workers(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "working")
            _write_status(cwd, team, "w2", "working")
            result = scale_down(
                team,
                cwd,
                options=ScaleDownOptions(count=1, force=True),
                env=_ENABLED_ENV,
                poll_interval_s=0.0,
            )
            self.assertIsInstance(result, ScaleDownResult)
            assert isinstance(result, ScaleDownResult)
            self.assertEqual(len(result.removed_workers), 1)

    def test_drain_timeout_then_continues(self) -> None:
        """force=False + alive worker + worker keeps working until deadline.

        We stub ``time.sleep`` so the test runs instantly, but we leave the
        worker in ``working`` for the full poll. Once the deadline trips,
        scale_down should still complete (the pane is then killed).
        """
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "idle")
            _write_status(cwd, team, "w2", "working")
            with patch.object(scaling_down, "is_worker_alive", return_value=True):
                result = scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"], drain_timeout_ms=0),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
            self.assertIsInstance(result, ScaleDownResult)
            assert isinstance(result, ScaleDownResult)
            self.assertEqual(result.removed_workers, ["w2"])

    def test_partial_dead_workers_drain_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            # No status files at all; alive=False is the setUp default.
            result = scale_down(
                team,
                cwd,
                options=ScaleDownOptions(worker_names=["w2"], drain_timeout_ms=60_000),
                env=_ENABLED_ENV,
                poll_interval_s=0.0,
            )
            self.assertIsInstance(result, ScaleDownResult)
            assert isinstance(result, ScaleDownResult)
            self.assertEqual(result.removed_workers, ["w2"])

    def test_kill_worker_called_for_each_target(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with patch.object(scaling_down, "kill_worker") as kw:
                scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"], force=True),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
                self.assertEqual(kw.call_count, 1)
                call = kw.call_args
                # (session, index, pane_id, leader_pane_id)
                self.assertEqual(call.args[1], 2)
                self.assertEqual(call.args[2], "%3")
                self.assertEqual(call.args[3], "%0")

    def test_kill_worker_failure_does_not_block_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with patch.object(
                scaling_down, "kill_worker", side_effect=RuntimeError("boom")
            ):
                result = scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"], force=True),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
            self.assertIsInstance(result, ScaleDownResult)
            assert isinstance(result, ScaleDownResult)
            self.assertEqual(result.removed_workers, ["w2"])

    def test_detached_worktree_rollback_invoked(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    _make_worker("w1", 1),
                    _make_worker(
                        "w2",
                        2,
                        pane_id="%3",
                        worktree_path="/tmp/wt-2",
                        worktree_repo_root="/tmp/repo",
                        worktree_detached=True,
                        worktree_created=True,
                    ),
                ],
            )
            with patch.object(scaling_down, "rollback_provisioned_worktrees") as rb:
                scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"], force=True),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
                rb.assert_called_once()
                args, _ = rb.call_args
                results = args[0]
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].worktree_path, "/tmp/wt-2")

    def test_detached_worktree_rollback_failure_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    _make_worker("w1", 1),
                    _make_worker(
                        "w2",
                        2,
                        pane_id="%3",
                        worktree_path="/tmp/wt-2",
                        worktree_repo_root="/tmp/repo",
                        worktree_detached=True,
                        worktree_created=True,
                    ),
                ],
            )
            with patch.object(
                scaling_down,
                "rollback_provisioned_worktrees",
                side_effect=RuntimeError("git failed"),
            ):
                result = scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"], force=True),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
            self.assertIsInstance(result, ScaleError)
            self.assertIn("scale_down_worktree_cleanup_failed", result.error)  # type: ignore[union-attr]
            # Config was not updated because rollback failed.
            cfg = read_team_config(cwd, team)
            self.assertEqual(len(cfg["workers"]), 2)

    def test_no_worktree_skips_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with patch.object(scaling_down, "rollback_provisioned_worktrees") as rb:
                scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"], force=True),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
                rb.assert_not_called()

    def test_branch_mode_worktree_not_rolled_back(self) -> None:
        """Only detached worktrees are rolled back here; branch-mode is owned
        by the team-level shutdown flow."""
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    _make_worker("w1", 1),
                    _make_worker(
                        "w2",
                        2,
                        pane_id="%3",
                        worktree_path="/tmp/wt-2",
                        worktree_repo_root="/tmp/repo",
                        worktree_detached=False,
                        worktree_created=True,
                    ),
                ],
            )
            with patch.object(scaling_down, "rollback_provisioned_worktrees") as rb:
                scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"], force=True),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
                rb.assert_not_called()

    def test_remove_worktree_agents_file_invoked(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                workers=[
                    _make_worker("w1", 1),
                    _make_worker(
                        "w2",
                        2,
                        pane_id="%3",
                        worktree_path="/tmp/wt-2",
                        worktree_repo_root="/tmp/repo",
                        worktree_detached=True,
                        worktree_created=True,
                        team_state_root="/tmp/state",
                    ),
                ],
            )
            with patch.object(
                scaling_down, "remove_worker_worktree_root_agents_file"
            ) as rm:
                scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"], force=True),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
                rm.assert_called_once()
                args, _ = rm.call_args
                self.assertEqual(args[1], "w2")  # worker_name
                self.assertEqual(args[2], "/tmp/state")  # team_state_root
                self.assertEqual(args[3], "/tmp/wt-2")  # worktree_path

    def test_event_emitted_on_success(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            scale_down(
                team,
                cwd,
                options=ScaleDownOptions(worker_names=["w2"], force=True),
                env=_ENABLED_ENV,
                poll_interval_s=0.0,
            )
            events_path = _team_dir(team, cwd) / "events.jsonl"
            self.assertTrue(events_path.exists())
            lines = events_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 1)
            event = json.loads(lines[0])
            self.assertEqual(event["event_type"], "team_leader_nudge")
            self.assertEqual(event["worker_id"], "leader-fixed")
            self.assertIn("scale_down: removed 1 worker(s)", event["detail"]["reason"])

    def test_force_skips_drain_wait(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with patch.object(scaling_down, "_wait_for_drain") as wfd:
                scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"], force=True),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
                wfd.assert_not_called()

    def test_non_force_invokes_drain_wait(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with patch.object(scaling_down, "_wait_for_drain") as wfd:
                scale_down(
                    team,
                    cwd,
                    options=ScaleDownOptions(worker_names=["w2"]),
                    env=_ENABLED_ENV,
                    poll_interval_s=0.0,
                )
                wfd.assert_called_once()

    def test_default_options_remove_one_idle_worker(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "idle")
            _write_status(cwd, team, "w2", "idle")
            result = scale_down(
                team, cwd, options=None, env=_ENABLED_ENV, poll_interval_s=0.0
            )
            self.assertIsInstance(result, ScaleDownResult)
            assert isinstance(result, ScaleDownResult)
            self.assertEqual(len(result.removed_workers), 1)
            self.assertEqual(result.new_worker_count, 1)

    def test_empty_worker_names_falls_back_to_count(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            _write_status(cwd, team, "w1", "idle")
            _write_status(cwd, team, "w2", "idle")
            result = scale_down(
                team,
                cwd,
                options=ScaleDownOptions(worker_names=[], count=1),
                env=_ENABLED_ENV,
                poll_interval_s=0.0,
            )
            self.assertIsInstance(result, ScaleDownResult)
            assert isinstance(result, ScaleDownResult)
            self.assertEqual(len(result.removed_workers), 1)


if __name__ == "__main__":
    unittest.main()
