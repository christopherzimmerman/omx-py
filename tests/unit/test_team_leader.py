"""Tests for omx.team.state.leader — leader identity + leader-attention state.

Covers:
  * TeamLeader serialization round-trip.
  * TeamLeaderAttentionState normalization (unknown source / decision state
    fall back to safe defaults; coerced attention_reasons).
  * read_team_leader_attention returns None on missing / corrupt files.
  * write_team_leader_attention round-trips via read_team_leader_attention
    and forces team_name on disk to match the argument.
  * mark_team_leader_session_stopped is idempotent — repeated calls keep the
    team stopped and never silently drop the stronger native_stop source if
    a later call comes in as native_session_end.
  * mark_owned_teams_leader_session_stopped correctly handles 0 / 1 / many
    teams, skips teams in a terminal phase, and only touches teams whose
    manifest leader.session_id matches.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omx.team.state.leader import (
    LEADER_ATTENTION_SOURCES,
    LEADER_DECISION_STATES,
    TERMINAL_PHASES,
    TeamLeader,
    TeamLeaderAttentionState,
    mark_owned_teams_leader_session_stopped,
    mark_team_leader_session_stopped,
    mark_team_leader_stop_observed,
    read_team_leader_attention,
    write_team_leader_attention,
)
from omx.team.state.manifest import (
    PermissionsSnapshot,
    TeamManifestV2,
    write_team_manifest_v2,
)


def _write_manifest(cwd: str, team_name: str, leader_session_id: str) -> None:
    """Write a minimal V2 manifest so the owned-teams sweep can find
    leader.session_id. Uses the real ``write_team_manifest_v2`` to guarantee
    the manifest passes the reader's schema validation."""
    manifest = TeamManifestV2(
        name=team_name,
        task="test task",
        leader=TeamLeader(
            session_id=leader_session_id,
            worker_id="leader-fixed",
            role="coordinator",
        ),
        permissions_snapshot=PermissionsSnapshot(),
        tmux_session=f"omx-team-{team_name}",
        worker_count=1,
        workers=[],
        next_task_id=1,
        created_at="2026-01-01T00:00:00+00:00",
    )
    write_team_manifest_v2(manifest, cwd)


def _write_phase(cwd: str, team_name: str, current_phase: str) -> None:
    team_dir = Path(cwd) / ".omx" / "team" / team_name
    team_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "current_phase": current_phase,
        "max_fix_attempts": 3,
        "current_fix_attempt": 0,
        "transitions": [],
        "updated_at": "2026-01-01T00:00:00+00:00",
    }
    (team_dir / "phase-state.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


class TestTeamLeaderDataclass(unittest.TestCase):
    def test_round_trip_minimal(self) -> None:
        leader = TeamLeader(session_id="s1", worker_id="leader", role="leader")
        d = leader.to_dict()
        # thread_id is optional and should be omitted when None.
        self.assertNotIn("thread_id", d)
        restored = TeamLeader.from_dict(d)
        self.assertEqual(restored, leader)

    def test_round_trip_with_thread_id(self) -> None:
        leader = TeamLeader(
            session_id="s1", worker_id="leader", role="leader", thread_id="t1"
        )
        d = leader.to_dict()
        self.assertEqual(d["thread_id"], "t1")
        restored = TeamLeader.from_dict(d)
        self.assertEqual(restored.thread_id, "t1")


class TestTeamLeaderAttentionStateNormalization(unittest.TestCase):
    def test_unknown_source_falls_back_to_notify_hook(self) -> None:
        state = TeamLeaderAttentionState.from_dict(
            {"team_name": "t", "source": "not_a_real_source"}, team_name_default="t"
        )
        self.assertEqual(state.source, "notify_hook")
        self.assertIn(state.source, LEADER_ATTENTION_SOURCES)

    def test_unknown_decision_state_falls_back_to_still_actionable(self) -> None:
        state = TeamLeaderAttentionState.from_dict(
            {"team_name": "t", "leader_decision_state": "garbage"},
            team_name_default="t",
        )
        self.assertEqual(state.leader_decision_state, "still_actionable")
        self.assertIn(state.leader_decision_state, LEADER_DECISION_STATES)

    def test_attention_reasons_coerced_to_str_list(self) -> None:
        state = TeamLeaderAttentionState.from_dict(
            {
                "team_name": "t",
                "attention_reasons": ["good", "", 42, None, "  ", "ok"],
            },
            team_name_default="t",
        )
        # Non-strings and empty / whitespace-only strings are dropped.
        self.assertEqual(state.attention_reasons, ["good", "ok"])

    def test_missing_team_name_uses_default(self) -> None:
        state = TeamLeaderAttentionState.from_dict({}, team_name_default="fallback")
        self.assertEqual(state.team_name, "fallback")

    def test_non_string_session_id_normalized_to_none(self) -> None:
        state = TeamLeaderAttentionState.from_dict(
            {"team_name": "t", "leader_session_id": 12345}, team_name_default="t"
        )
        self.assertIsNone(state.leader_session_id)


class TestLeaderAttentionReadWrite(unittest.TestCase):
    def test_read_returns_none_when_file_missing(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            self.assertIsNone(read_team_leader_attention("alpha", cwd))

    def test_read_returns_none_on_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team_dir = Path(cwd) / ".omx" / "team" / "alpha"
            team_dir.mkdir(parents=True, exist_ok=True)
            (team_dir / "leader-attention.json").write_text(
                "{not valid json", encoding="utf-8"
            )
            self.assertIsNone(read_team_leader_attention("alpha", cwd))

    def test_read_returns_none_when_payload_is_not_object(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team_dir = Path(cwd) / ".omx" / "team" / "alpha"
            team_dir.mkdir(parents=True, exist_ok=True)
            (team_dir / "leader-attention.json").write_text("[]", encoding="utf-8")
            self.assertIsNone(read_team_leader_attention("alpha", cwd))

    def test_write_then_read_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            original = TeamLeaderAttentionState(
                team_name="alpha",
                updated_at="2026-01-01T00:00:00+00:00",
                source="native_stop",
                leader_decision_state="done_waiting_on_leader",
                leader_attention_pending=True,
                leader_attention_reason="leader_session_stopped",
                attention_reasons=["leader_session_stopped"],
                leader_stale=False,
                leader_session_active=False,
                leader_session_id="sid-1",
                leader_session_stopped_at="2026-01-01T00:00:00+00:00",
                unread_leader_message_count=3,
                work_remaining=False,
                stalled_for_ms=12500,
            )
            write_team_leader_attention("alpha", cwd, original)
            roundtrip = read_team_leader_attention("alpha", cwd)
            self.assertIsNotNone(roundtrip)
            assert roundtrip is not None  # narrows for type checker
            self.assertEqual(roundtrip.team_name, "alpha")
            self.assertEqual(roundtrip.source, "native_stop")
            self.assertEqual(roundtrip.leader_decision_state, "done_waiting_on_leader")
            self.assertEqual(roundtrip.attention_reasons, ["leader_session_stopped"])
            self.assertEqual(roundtrip.leader_session_id, "sid-1")
            self.assertEqual(roundtrip.unread_leader_message_count, 3)
            self.assertEqual(roundtrip.stalled_for_ms, 12500)
            self.assertFalse(roundtrip.leader_session_active)

    def test_write_forces_team_name_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            # Deliberately mismatched team_name in payload vs. argument.
            state = TeamLeaderAttentionState(
                team_name="wrong_name",
                updated_at="2026-01-01T00:00:00+00:00",
                source="notify_hook",
            )
            write_team_leader_attention("alpha", cwd, state)
            raw = json.loads(
                (
                    Path(cwd) / ".omx" / "team" / "alpha" / "leader-attention.json"
                ).read_text(encoding="utf-8")
            )
            # The writer must clobber team_name to match the directory.
            self.assertEqual(raw["team_name"], "alpha")


class TestMarkTeamLeaderStopped(unittest.TestCase):
    def test_mark_session_stopped_creates_record(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            result = mark_team_leader_session_stopped(
                "alpha", cwd, "sid-1", now_iso="2026-01-01T00:00:00+00:00"
            )
            self.assertEqual(result.team_name, "alpha")
            self.assertEqual(result.source, "native_session_end")
            self.assertFalse(result.leader_session_active)
            self.assertEqual(result.leader_session_id, "sid-1")
            self.assertEqual(
                result.leader_session_stopped_at, "2026-01-01T00:00:00+00:00"
            )
            # Verify it persisted.
            persisted = read_team_leader_attention("alpha", cwd)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertEqual(persisted.leader_session_id, "sid-1")

    def test_mark_stopped_is_idempotent(self) -> None:
        """Calling mark_stopped twice keeps the team stopped and never
        regresses leader_session_active back to True."""
        with tempfile.TemporaryDirectory() as cwd:
            first = mark_team_leader_session_stopped(
                "alpha", cwd, "sid-1", now_iso="2026-01-01T00:00:00+00:00"
            )
            second = mark_team_leader_session_stopped(
                "alpha", cwd, "sid-1", now_iso="2026-01-01T00:01:00+00:00"
            )
            self.assertFalse(first.leader_session_active)
            self.assertFalse(second.leader_session_active)
            self.assertEqual(second.leader_session_id, "sid-1")
            # The refreshed timestamps are preserved.
            self.assertEqual(
                second.leader_session_stopped_at, "2026-01-01T00:01:00+00:00"
            )

    def test_native_stop_is_preserved_against_later_session_end(self) -> None:
        """If a team is first marked via native_stop, a follow-up
        native_session_end must NOT downgrade the source field."""
        with tempfile.TemporaryDirectory() as cwd:
            first = mark_team_leader_stop_observed(
                "alpha",
                cwd,
                "sid-1",
                now_iso="2026-01-01T00:00:00+00:00",
                source="native_stop",
            )
            self.assertEqual(first.source, "native_stop")
            second = mark_team_leader_stop_observed(
                "alpha",
                cwd,
                "sid-1",
                now_iso="2026-01-01T00:00:30+00:00",
                source="native_session_end",
            )
            # native_stop is the stronger signal — must win.
            self.assertEqual(second.source, "native_stop")

    def test_mark_stopped_preserves_existing_session_id_when_arg_empty(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            mark_team_leader_session_stopped(
                "alpha", cwd, "sid-1", now_iso="2026-01-01T00:00:00+00:00"
            )
            # Empty session id on the follow-up must not wipe the existing one.
            follow_up = mark_team_leader_session_stopped(
                "alpha", cwd, "", now_iso="2026-01-01T00:00:30+00:00"
            )
            self.assertEqual(follow_up.leader_session_id, "sid-1")


class TestMarkOwnedTeamsStopped(unittest.TestCase):
    def test_zero_teams_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            result = mark_owned_teams_leader_session_stopped(cwd, "sid-1")
            self.assertEqual(result, [])

    def test_empty_session_id_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _write_manifest(cwd, "alpha", "sid-1")
            result = mark_owned_teams_leader_session_stopped(cwd, "   ")
            self.assertEqual(result, [])

    def test_one_team_owned_by_session(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _write_manifest(cwd, "alpha", "sid-1")
            result = mark_owned_teams_leader_session_stopped(
                cwd, "sid-1", now_iso="2026-01-01T00:00:00+00:00"
            )
            self.assertEqual(result, ["alpha"])
            persisted = read_team_leader_attention("alpha", cwd)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertFalse(persisted.leader_session_active)

    def test_many_teams_only_matching_are_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _write_manifest(cwd, "alpha", "sid-1")
            _write_manifest(cwd, "beta", "sid-1")
            _write_manifest(cwd, "gamma", "sid-other")
            result = mark_owned_teams_leader_session_stopped(
                cwd, "sid-1", now_iso="2026-01-01T00:00:00+00:00"
            )
            self.assertEqual(sorted(result), ["alpha", "beta"])
            # gamma must remain untouched.
            self.assertIsNone(read_team_leader_attention("gamma", cwd))
            self.assertIsNotNone(read_team_leader_attention("alpha", cwd))
            self.assertIsNotNone(read_team_leader_attention("beta", cwd))

    def test_terminal_phase_teams_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _write_manifest(cwd, "alpha", "sid-1")
            _write_manifest(cwd, "beta", "sid-1")
            _write_phase(cwd, "alpha", "complete")
            _write_phase(cwd, "beta", "planning")
            result = mark_owned_teams_leader_session_stopped(
                cwd, "sid-1", now_iso="2026-01-01T00:00:00+00:00"
            )
            self.assertEqual(result, ["beta"])
            self.assertIsNone(read_team_leader_attention("alpha", cwd))

    def test_team_without_manifest_is_skipped(self) -> None:
        """A directory under .omx/team/ that has no manifest.json is not
        "owned" by anything — the sweep must not crash on it."""
        with tempfile.TemporaryDirectory() as cwd:
            # Create a directory with no manifest.
            (Path(cwd) / ".omx" / "team" / "orphan").mkdir(parents=True)
            _write_manifest(cwd, "alpha", "sid-1")
            result = mark_owned_teams_leader_session_stopped(
                cwd, "sid-1", now_iso="2026-01-01T00:00:00+00:00"
            )
            self.assertEqual(result, ["alpha"])

    def test_terminal_phases_constant_matches_orchestrator(self) -> None:
        # Sanity guard: TS orchestrator declares these three terminal phases.
        self.assertEqual(
            TERMINAL_PHASES, frozenset({"complete", "failed", "cancelled"})
        )


if __name__ == "__main__":
    unittest.main()
