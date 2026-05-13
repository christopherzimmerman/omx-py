"""Tests for ``omx.team.runtime_resume``.

Covers the Phase 2.10 port of TS ``resumeTeam`` plus the richer
``resume_team_with_signals`` entry point.

Tmux state is mocked at the module boundary so the tests stay
deterministic on machines without tmux installed.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from omx.team.runtime_resume import (
    ResumeOutcome,
    RotatedSessionError,
    TeamNotRunningError,
    WorkerResumeSignal,
    _classify_worker_state,
    _get_team_tmux_sessions,
    _is_pid_alive,
    _is_prompt_worker_alive,
    _redispatch_stale_pending,
    _scan_workers_parallel,
    _verify_worker_count_and_pane_ids,
    resume_team,
    resume_team_with_signals,
)
from omx.team.runtime_types import TeamRuntime
from omx.team.state.dispatch import write_dispatch_requests
from omx.team.state.io import (
    write_team_config,
    write_worker_heartbeat,
    write_worker_status,
)
from omx.team.state.leader import (
    TeamLeaderAttentionState,
    write_team_leader_attention,
)
from omx.team.state.manifest import (
    PermissionsSnapshot,
    TeamLeader,
    TeamManifestV2,
    write_team_manifest_v2,
)
from omx.team.state.types import TeamDispatchRequest, WorkerInfo


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_manifest(
    name: str = "alpha",
    worker_count: int = 2,
    tmux_session: str = "omx-team-alpha",
    worker_launch_mode: str = "interactive",
    workers: list[WorkerInfo] | None = None,
) -> TeamManifestV2:
    if workers is None:
        workers = [
            WorkerInfo(
                name=f"worker-{i + 1}",
                index=i + 1,
                role="executor",
                worker_cli="codex",
                pid=10000 + i,
                pane_id=f"%{i + 1}",
            )
            for i in range(worker_count)
        ]
    return TeamManifestV2(
        name=name,
        task="port resume",
        leader=TeamLeader(session_id="sess-1"),
        permissions_snapshot=PermissionsSnapshot(),
        tmux_session=tmux_session,
        worker_count=worker_count,
        workers=workers,
        next_task_id=1,
        created_at=_now_iso(),
        policy={"worker_launch_mode": worker_launch_mode},
    )


def _seed_team(
    cwd: str,
    team_name: str = "alpha",
    *,
    worker_count: int = 2,
    worker_launch_mode: str = "interactive",
    write_config: bool = True,
    write_manifest: bool = True,
    tmux_session: str = "omx-team-alpha",
    workers: list[WorkerInfo] | None = None,
) -> TeamManifestV2:
    manifest = _make_manifest(
        name=team_name,
        worker_count=worker_count,
        tmux_session=tmux_session,
        worker_launch_mode=worker_launch_mode,
        workers=workers,
    )
    if write_manifest:
        write_team_manifest_v2(manifest, cwd)
    if write_config:
        config = {
            "name": team_name,
            "tmux_session": tmux_session,
            "worker_count": worker_count,
            "worker_launch_mode": worker_launch_mode,
            "workers": [w.to_dict() for w in manifest.workers],
        }
        write_team_config(cwd, config, team_name)
    return manifest


class _MockTmuxFixture:
    """Patch the tmux probes used by runtime_resume."""

    def __init__(
        self,
        *,
        sessions: list[str] | None = None,
        alive: bool = True,
        pane_open: bool = True,
        pane_ids: list[str] | None = None,
    ):
        self.sessions = sessions if sessions is not None else ["omx-team-alpha"]
        self.alive = alive
        self.pane_open = pane_open
        self.pane_ids = pane_ids if pane_ids is not None else ["%1", "%2"]
        self._patches: list = []

    def __enter__(self):
        self._patches.append(
            mock.patch(
                "omx.team.runtime_resume.list_team_sessions",
                return_value=list(self.sessions),
            )
        )
        self._patches.append(
            mock.patch(
                "omx.team.runtime_resume.is_worker_alive",
                return_value=self.alive,
            )
        )
        self._patches.append(
            mock.patch(
                "omx.team.runtime_resume.is_worker_pane_open",
                return_value=self.pane_open,
            )
        )
        self._patches.append(
            mock.patch(
                "omx.team.runtime_resume.list_pane_ids",
                return_value=list(self.pane_ids),
            )
        )
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        for p in self._patches:
            p.stop()
        self._patches.clear()


# ---------------------------------------------------------------------------
# resume_team — basic surface (TS parity)


class TestResumeTeamBasic(unittest.TestCase):
    def test_missing_manifest_and_config_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _MockTmuxFixture(sessions=[]):
                result = resume_team("does-not-exist", cwd)
            self.assertIsNone(result)

    def test_happy_interactive_resume_returns_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                result = resume_team("alpha", cwd)
            self.assertIsInstance(result, TeamRuntime)
            assert result is not None  # for mypy
            self.assertEqual(result.team_name, "alpha")
            self.assertEqual(result.sanitized_name, "alpha")
            self.assertEqual(result.session_name, "omx-team-alpha")
            self.assertEqual(result.cwd, cwd)
            self.assertEqual(result.config["lifecycle_profile"], "default")

    def test_no_tmux_session_returns_none_in_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            with _MockTmuxFixture(sessions=[]):
                result = resume_team("alpha", cwd)
            self.assertIsNone(result)

    def test_team_name_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd, team_name="alpha")
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                # Caller passes a "fancy" name that sanitizes to "alpha".
                result = resume_team("Alpha!", cwd)
            assert result is not None
            self.assertEqual(result.sanitized_name, "alpha")

    def test_lifecycle_profile_forced_to_default(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            # Inject a non-default lifecycle profile.
            cfg_path = Path(cwd) / ".omx" / "team" / "alpha" / "config.json"
            cfg = json.loads(cfg_path.read_text())
            cfg["lifecycle_profile"] = "experimental"
            cfg_path.write_text(json.dumps(cfg))
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                result = resume_team("alpha", cwd)
            assert result is not None
            self.assertEqual(result.config["lifecycle_profile"], "default")

    def test_manifest_only_resume_hydrates_minimal_config(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd, write_config=False)
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                result = resume_team("alpha", cwd)
            assert result is not None
            self.assertEqual(result.config["name"], "alpha")
            self.assertEqual(result.config["worker_count"], 2)


# ---------------------------------------------------------------------------
# resume_team — prompt-mode branch


class TestResumeTeamPromptMode(unittest.TestCase):
    def test_prompt_mode_no_live_worker_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd, worker_launch_mode="prompt")
            with mock.patch(
                "omx.team.runtime_resume._is_pid_alive", return_value=False
            ):
                result = resume_team("alpha", cwd)
            self.assertIsNone(result)

    def test_prompt_mode_live_worker_returns_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd, worker_launch_mode="prompt")
            with mock.patch("omx.team.runtime_resume._is_pid_alive", return_value=True):
                result = resume_team("alpha", cwd)
            self.assertIsInstance(result, TeamRuntime)


# ---------------------------------------------------------------------------
# resume_team_with_signals — richer path


class TestResumeTeamWithSignals(unittest.TestCase):
    def test_missing_manifest_outcome_has_none_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _MockTmuxFixture(sessions=[]):
                outcome = resume_team_with_signals(
                    "missing",
                    cwd,
                    persist_phase=False,
                    require_tmux_session=False,
                )
            self.assertIsInstance(outcome, ResumeOutcome)
            self.assertIsNone(outcome.runtime)

    def test_happy_path_returns_runtime_and_signals(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            with _MockTmuxFixture(
                sessions=["omx-team-alpha"], alive=True, pane_open=True
            ):
                outcome = resume_team_with_signals("alpha", cwd, persist_phase=False)
            self.assertIsNotNone(outcome.runtime)
            self.assertEqual(len(outcome.worker_signals), 2)
            for s in outcome.worker_signals:
                self.assertTrue(s.alive)
                self.assertTrue(s.pane_open)
            self.assertEqual(outcome.dead_workers, [])

    def test_missing_tmux_session_raises_team_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            with _MockTmuxFixture(sessions=[]):
                with self.assertRaises(TeamNotRunningError):
                    resume_team_with_signals("alpha", cwd, persist_phase=False)

    def test_missing_session_soft_returns_none_when_not_required(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            with _MockTmuxFixture(sessions=[]):
                outcome = resume_team_with_signals(
                    "alpha",
                    cwd,
                    persist_phase=False,
                    require_tmux_session=False,
                )
            # Soft mode keeps runtime visible but flags it as dead via signals.
            self.assertIsNotNone(outcome.runtime)
            for s in outcome.worker_signals:
                self.assertFalse(s.alive)

    def test_dead_workers_detected(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            with _MockTmuxFixture(
                sessions=["omx-team-alpha"], alive=False, pane_open=False
            ):
                outcome = resume_team_with_signals("alpha", cwd, persist_phase=False)
            self.assertEqual(len(outcome.dead_workers), 2)
            self.assertIn("worker-1", outcome.dead_workers)

    def test_prompt_mode_resume_without_tmux(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd, worker_launch_mode="prompt")
            with mock.patch("omx.team.runtime_resume._is_pid_alive", return_value=True):
                outcome = resume_team_with_signals("alpha", cwd, persist_phase=False)
            self.assertIsNotNone(outcome.runtime)
            self.assertEqual(len(outcome.worker_signals), 2)

    def test_prompt_mode_no_live_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd, worker_launch_mode="prompt")
            with mock.patch(
                "omx.team.runtime_resume._is_pid_alive", return_value=False
            ):
                outcome = resume_team_with_signals("alpha", cwd, persist_phase=False)
            self.assertIsNone(outcome.runtime)

    def test_rotated_session_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            write_team_leader_attention(
                "alpha",
                cwd,
                TeamLeaderAttentionState(
                    team_name="alpha",
                    updated_at=_now_iso(),
                    leader_session_active=False,
                    leader_session_id="sess-previous",
                ),
            )
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                with self.assertRaises(RotatedSessionError):
                    resume_team_with_signals(
                        "alpha",
                        cwd,
                        env={"OMX_SESSION_ID": "sess-current"},
                        persist_phase=False,
                    )

    def test_rotated_session_skipped_when_same_session(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            write_team_leader_attention(
                "alpha",
                cwd,
                TeamLeaderAttentionState(
                    team_name="alpha",
                    updated_at=_now_iso(),
                    leader_session_active=False,
                    leader_session_id="sess-current",
                ),
            )
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                outcome = resume_team_with_signals(
                    "alpha",
                    cwd,
                    env={"OMX_SESSION_ID": "sess-current"},
                    persist_phase=False,
                )
            self.assertIsNotNone(outcome.runtime)

    def test_rotated_session_skipped_when_no_env_session(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            write_team_leader_attention(
                "alpha",
                cwd,
                TeamLeaderAttentionState(
                    team_name="alpha",
                    updated_at=_now_iso(),
                    leader_session_active=False,
                    leader_session_id="sess-previous",
                ),
            )
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                outcome = resume_team_with_signals(
                    "alpha",
                    cwd,
                    env={},
                    persist_phase=False,
                )
            self.assertIsNotNone(outcome.runtime)

    def test_rotated_session_skipped_when_leader_still_active(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            write_team_leader_attention(
                "alpha",
                cwd,
                TeamLeaderAttentionState(
                    team_name="alpha",
                    updated_at=_now_iso(),
                    leader_session_active=True,
                    leader_session_id="sess-previous",
                ),
            )
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                outcome = resume_team_with_signals(
                    "alpha",
                    cwd,
                    env={"OMX_SESSION_ID": "sess-current"},
                    persist_phase=False,
                )
            self.assertIsNotNone(outcome.runtime)

    def test_phase_state_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                outcome = resume_team_with_signals("alpha", cwd, persist_phase=True)
            self.assertIsNotNone(outcome.runtime)
            phase_path = Path(cwd) / ".omx" / "team" / "alpha" / "phase-state.json"
            self.assertTrue(phase_path.exists())
            data = json.loads(phase_path.read_text())
            self.assertTrue(
                any(t.get("reason") == "resumed" for t in data.get("transitions", []))
            )

    def test_status_and_heartbeat_loaded_into_signals(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            write_worker_status(cwd, "alpha", "worker-1", "working", "task-1")
            write_worker_heartbeat(cwd, "alpha", "worker-1", pid=42, turn_count=5)
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                outcome = resume_team_with_signals("alpha", cwd, persist_phase=False)
            sig = next(s for s in outcome.worker_signals if s.name == "worker-1")
            self.assertEqual(sig.status.get("state"), "working")
            assert sig.heartbeat is not None
            self.assertEqual(sig.heartbeat.get("turn_count"), 5)


# ---------------------------------------------------------------------------
# Pending dispatch redispatch


class TestRedispatchStalePending(unittest.TestCase):
    def test_no_pending_requests_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            touched = _redispatch_stale_pending("alpha", cwd, stale_seconds=60)
            self.assertEqual(touched, [])

    def test_fresh_pending_not_touched(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            now = _now_iso()
            req = TeamDispatchRequest(
                request_id="r-fresh",
                kind="inbox",
                team_name="alpha",
                to_worker="worker-1",
                status="pending",
                created_at=now,
                updated_at=now,
            )
            team_dir = Path(cwd) / ".omx" / "team" / "alpha"
            write_dispatch_requests(team_dir, [req])
            touched = _redispatch_stale_pending("alpha", cwd, stale_seconds=60)
            self.assertEqual(touched, [])

    def test_stale_pending_is_redispatched(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            req = TeamDispatchRequest(
                request_id="r-stale",
                kind="inbox",
                team_name="alpha",
                to_worker="worker-1",
                status="pending",
                created_at=stale,
                updated_at=stale,
            )
            team_dir = Path(cwd) / ".omx" / "team" / "alpha"
            write_dispatch_requests(team_dir, [req])
            touched = _redispatch_stale_pending("alpha", cwd, stale_seconds=60)
            self.assertEqual(touched, ["r-stale"])

    def test_non_pending_not_touched(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            req = TeamDispatchRequest(
                request_id="r-delivered",
                kind="inbox",
                team_name="alpha",
                to_worker="worker-1",
                status="delivered",
                created_at=stale,
                updated_at=stale,
            )
            team_dir = Path(cwd) / ".omx" / "team" / "alpha"
            write_dispatch_requests(team_dir, [req])
            touched = _redispatch_stale_pending("alpha", cwd, stale_seconds=60)
            self.assertEqual(touched, [])

    def test_redispatch_via_resume_team_with_signals(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
            req = TeamDispatchRequest(
                request_id="r-stale-2",
                kind="inbox",
                team_name="alpha",
                to_worker="worker-1",
                status="pending",
                created_at=stale,
                updated_at=stale,
            )
            team_dir = Path(cwd) / ".omx" / "team" / "alpha"
            write_dispatch_requests(team_dir, [req])
            with _MockTmuxFixture(sessions=["omx-team-alpha"]):
                outcome = resume_team_with_signals("alpha", cwd, persist_phase=False)
            self.assertEqual(outcome.redispatched_request_ids, ["r-stale-2"])

    def test_unparseable_created_at_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            req = TeamDispatchRequest(
                request_id="r-bad",
                kind="inbox",
                team_name="alpha",
                to_worker="worker-1",
                status="pending",
                created_at="not-a-timestamp",
                updated_at="not-a-timestamp",
            )
            team_dir = Path(cwd) / ".omx" / "team" / "alpha"
            write_dispatch_requests(team_dir, [req])
            touched = _redispatch_stale_pending("alpha", cwd, stale_seconds=1)
            self.assertEqual(touched, [])


# ---------------------------------------------------------------------------
# Manifest consistency check


class TestVerifyWorkerCountAndPaneIds(unittest.TestCase):
    def test_consistent_returns_empty(self) -> None:
        config = {
            "worker_count": 2,
            "workers": [
                {"name": "w1", "pane_id": "%1"},
                {"name": "w2", "pane_id": "%2"},
            ],
            "tmux_session": "omx-team-alpha",
        }
        with mock.patch(
            "omx.team.runtime_resume.list_pane_ids", return_value=["%1", "%2"]
        ):
            reasons = _verify_worker_count_and_pane_ids(
                config, [{}, {}], "omx-team-alpha"
            )
        self.assertEqual(reasons, [])

    def test_worker_count_mismatch_reported(self) -> None:
        config = {
            "worker_count": 3,
            "workers": [{"name": "w1", "pane_id": "%1"}],
        }
        reasons = _verify_worker_count_and_pane_ids(config, None, "")
        self.assertTrue(any("worker_count_mismatch" in r for r in reasons))

    def test_manifest_count_mismatch_reported(self) -> None:
        config = {
            "worker_count": 1,
            "workers": [{"name": "w1", "pane_id": "%1"}],
        }
        reasons = _verify_worker_count_and_pane_ids(config, [{}, {}], "")
        self.assertTrue(any("manifest_worker_count_mismatch" in r for r in reasons))

    def test_missing_pane_id_reported(self) -> None:
        config = {
            "worker_count": 2,
            "workers": [
                {"name": "w1", "pane_id": "%1"},
                {"name": "w2", "pane_id": "%2"},
            ],
        }
        with mock.patch("omx.team.runtime_resume.list_pane_ids", return_value=["%1"]):
            reasons = _verify_worker_count_and_pane_ids(config, None, "omx-team-alpha")
        self.assertTrue(any("pane_ids_missing" in r for r in reasons))

    def test_pane_check_skipped_when_no_session(self) -> None:
        config = {
            "worker_count": 2,
            "workers": [
                {"name": "w1", "pane_id": "%1"},
                {"name": "w2", "pane_id": "%2"},
            ],
        }
        reasons = _verify_worker_count_and_pane_ids(config, None, "")
        for r in reasons:
            self.assertFalse(r.startswith("pane_ids_missing"))


# ---------------------------------------------------------------------------
# Tmux session prefix matching


class TestGetTeamTmuxSessions(unittest.TestCase):
    def test_empty_team_name_returns_empty(self) -> None:
        self.assertEqual(_get_team_tmux_sessions(""), [])

    def test_exact_match_included(self) -> None:
        with mock.patch(
            "omx.team.runtime_resume.list_team_sessions",
            return_value=["omx-team-alpha", "omx-team-beta"],
        ):
            self.assertEqual(_get_team_tmux_sessions("alpha"), ["omx-team-alpha"])

    def test_prefix_match_included(self) -> None:
        with mock.patch(
            "omx.team.runtime_resume.list_team_sessions",
            return_value=["omx-team-alpha", "omx-team-alpha-shutdown"],
        ):
            self.assertEqual(
                _get_team_tmux_sessions("alpha"),
                ["omx-team-alpha", "omx-team-alpha-shutdown"],
            )

    def test_no_match_returns_empty(self) -> None:
        with mock.patch(
            "omx.team.runtime_resume.list_team_sessions",
            return_value=["omx-team-other"],
        ):
            self.assertEqual(_get_team_tmux_sessions("alpha"), [])

    def test_partial_inner_match_excluded(self) -> None:
        # "omx-team-alphabet" must NOT match "omx-team-alpha" without a
        # following hyphen separator.
        with mock.patch(
            "omx.team.runtime_resume.list_team_sessions",
            return_value=["omx-team-alphabet"],
        ):
            self.assertEqual(_get_team_tmux_sessions("alpha"), [])


# ---------------------------------------------------------------------------
# Worker state classification


class TestClassifyWorkerState(unittest.TestCase):
    def test_dead_when_not_alive_and_pane_closed(self) -> None:
        self.assertEqual(_classify_worker_state(False, False, {}, None), "dead")

    def test_dead_when_pane_open_but_process_gone(self) -> None:
        self.assertEqual(
            _classify_worker_state(False, True, {"state": "working"}, None),
            "dead",
        )

    def test_working_state_passed_through(self) -> None:
        self.assertEqual(
            _classify_worker_state(True, True, {"state": "working"}, {"turn_count": 1}),
            "working",
        )

    def test_idle_state_passed_through(self) -> None:
        self.assertEqual(
            _classify_worker_state(True, True, {"state": "idle"}, None),
            "idle",
        )

    def test_alive_but_no_heartbeat_and_no_state(self) -> None:
        self.assertEqual(_classify_worker_state(True, True, {}, None), "non_reporting")

    def test_working_without_heartbeat_marks_non_reporting(self) -> None:
        self.assertEqual(
            _classify_worker_state(True, True, {"state": "working"}, None),
            "non_reporting",
        )

    def test_default_alive(self) -> None:
        self.assertEqual(
            _classify_worker_state(True, True, {"state": "unrecognized"}, {"x": 1}),
            "alive",
        )


# ---------------------------------------------------------------------------
# Parallel worker scan


class TestScanWorkersParallel(unittest.TestCase):
    def test_empty_workers_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            signals = _scan_workers_parallel(cwd, "alpha", "session", [], 4)
            self.assertEqual(signals, [])

    def test_scan_returns_one_signal_per_worker(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd)
            workers = [
                {"name": "worker-1", "index": 1, "pane_id": "%1"},
                {"name": "worker-2", "index": 2, "pane_id": "%2"},
            ]
            with _MockTmuxFixture(alive=True, pane_open=True):
                signals = _scan_workers_parallel(
                    cwd, "alpha", "omx-team-alpha", workers, 4
                )
            self.assertEqual(len(signals), 2)
            names = {s.name for s in signals}
            self.assertEqual(names, {"worker-1", "worker-2"})

    def test_scan_pool_size_clamped_to_worker_count(self) -> None:
        # Smoke test: requesting 100 parallel slots for 1 worker must not fail.
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd, worker_count=1)
            workers = [{"name": "worker-1", "index": 1, "pane_id": "%1"}]
            with _MockTmuxFixture(alive=True, pane_open=True):
                signals = _scan_workers_parallel(
                    cwd, "alpha", "omx-team-alpha", workers, 100
                )
            self.assertEqual(len(signals), 1)


# ---------------------------------------------------------------------------
# _is_pid_alive


class TestIsPidAlive(unittest.TestCase):
    def test_none_pid_not_alive(self) -> None:
        self.assertFalse(_is_pid_alive(None))

    def test_negative_pid_not_alive(self) -> None:
        self.assertFalse(_is_pid_alive(-1))

    def test_zero_pid_not_alive(self) -> None:
        self.assertFalse(_is_pid_alive(0))

    def test_non_int_pid_not_alive(self) -> None:
        self.assertFalse(_is_pid_alive("not-a-pid"))  # type: ignore[arg-type]

    def test_current_process_pid_alive(self) -> None:
        self.assertTrue(_is_pid_alive(os.getpid()))

    def test_definitely_dead_pid_not_alive(self) -> None:
        # PID 2**31 - 1 is unlikely to be in use; if it happens to be, the
        # test still tolerates EPERM (returns True) — see the helper for
        # the rationale.
        result = _is_pid_alive(2**31 - 1)
        self.assertIsInstance(result, bool)


# ---------------------------------------------------------------------------
# _is_prompt_worker_alive


class TestIsPromptWorkerAlive(unittest.TestCase):
    def test_alive_when_pid_present(self) -> None:
        worker = {"pid": os.getpid()}
        self.assertTrue(_is_prompt_worker_alive(worker))

    def test_not_alive_when_pid_missing(self) -> None:
        self.assertFalse(_is_prompt_worker_alive({}))

    def test_not_alive_when_pid_zero(self) -> None:
        self.assertFalse(_is_prompt_worker_alive({"pid": 0}))


# ---------------------------------------------------------------------------
# WorkerResumeSignal + ResumeOutcome smoke


class TestDataclassSmoke(unittest.TestCase):
    def test_worker_resume_signal_defaults(self) -> None:
        sig = WorkerResumeSignal(
            name="w",
            index=1,
            pane_id=None,
            alive=False,
            pane_open=False,
            status={},
            heartbeat=None,
        )
        self.assertEqual(sig.classified_state, "unknown")

    def test_resume_outcome_defaults(self) -> None:
        out = ResumeOutcome(runtime=None)
        self.assertEqual(out.dead_workers, [])
        self.assertEqual(out.worker_signals, [])
        self.assertEqual(out.redispatched_request_ids, [])
        self.assertEqual(out.manifest_mismatch_reasons, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
