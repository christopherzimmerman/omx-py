"""Tests for ``omx.team.runtime_start.start_team`` and its private helpers.

The strategy is heavy use of ``unittest.mock`` to keep these tests fully
hermetic:

* ``tmux`` interactions are stubbed at the module-import surface
  (``is_tmux_available``, ``has_current_tmux_client_context``,
  ``create_team_session``, ``wait_for_worker_ready``, etc.) so no real
  tmux server is required.
* Worktree provisioning is stubbed at ``is_git_repository`` +
  ``plan_worktree_target`` + ``ensure_worktree`` so the tests do not
  depend on git state.
* ``_spawn_prompt_worker`` (the prompt-mode child-process spawn) is
  monkeypatched to return a fake PID so the prompt path is fully
  exercised without launching a real CLI.
* ``role_router.load_role_prompt`` is replaced so role lookup is
  deterministic.

Coverage groups:

* Env-knob helpers (``_resolve_*`` family + skip-ready toggle).
* Preflight (``_assert_team_startup_is_non_destructive``,
  ``_assert_nested_team_allowed``, stale-team detection, governance).
* Happy path — interactive (Codex worker_cli) — 1, 2, and 4 workers.
* Happy path — interactive (Claude worker_cli).
* Happy path — prompt mode (Claude / Gemini).
* Per-worker readiness ``ready_prompt_timeout`` → recoverable record.
* Dispatch retry loop (success on 2nd / 3rd attempt).
* Worker setup failure → rollback (session destroy + state cleanup +
  worktree rollback + instructions removal).
* Partial worktree provisioning rollback.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import omx.team.runtime_start as rs
from omx.team.mcp_comm import DispatchOutcome, DispatchTransport
from omx.team.runtime_types import TeamStartOptions
from omx.team.tmux_session import TeamSession as TmuxTeamSession
from omx.team.worktree import EnsureWorktreeResult, WorktreeMode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tmux_session(
    *, worker_count: int = 2, session: str = "omx-team-alpha"
) -> TmuxTeamSession:
    worker_panes = [f"%w{i}" for i in range(1, worker_count + 1)]
    return TmuxTeamSession(
        name=session,
        worker_count=worker_count,
        cwd="/tmp/fake-cwd",
        worker_pane_ids=worker_panes,
        leader_pane_id="%leader",
        hud_pane_id="%hud",
        resize_hook_name="hook-resize",
        resize_hook_target="omx-team-alpha:0",
    )


def _ok_dispatch() -> DispatchOutcome:
    return DispatchOutcome(
        ok=True,
        transport=DispatchTransport.HOOK.value,
        reason="queued_for_hook_dispatch",
    )


def _fail_dispatch(reason: str = "startup_no_evidence") -> DispatchOutcome:
    return DispatchOutcome(
        ok=False,
        transport=DispatchTransport.NONE.value,
        reason=reason,
    )


# Common patcher set used by every happy-path test. We patch *inside*
# the runtime_start module so the import-time binding gets swapped.
def _patch_runtime(
    *,
    interactive: bool = True,
    tmux_session: TmuxTeamSession | None = None,
    dispatch_outcomes: list[DispatchOutcome] | None = None,
    worker_count: int = 2,
    git_repo: bool = False,
    worker_ready: bool = True,
    pane_open: bool = True,
    worker_cli: str | list[str] = "codex",
):
    """Return a context manager that installs the standard mock stack."""

    if isinstance(worker_cli, str):
        cli_plan = [worker_cli] * worker_count
    else:
        cli_plan = list(worker_cli)

    session = (
        tmux_session if tmux_session else _make_tmux_session(worker_count=worker_count)
    )

    def _dispatch_side_effect(*args, **kwargs):
        if not dispatch_outcomes:
            return _ok_dispatch()
        return dispatch_outcomes.pop(0)

    patches = [
        mock.patch.object(rs, "is_tmux_available", return_value=interactive),
        mock.patch.object(
            rs, "has_current_tmux_client_context", return_value=interactive
        ),
        mock.patch.object(rs, "create_team_session", return_value=session),
        mock.patch.object(rs, "destroy_team_session"),
        mock.patch.object(rs, "kill_worker_by_pane_id"),
        mock.patch.object(rs, "unregister_resize_hook", return_value=True),
        mock.patch.object(rs, "list_team_sessions", return_value=[]),
        mock.patch.object(rs, "wait_for_worker_ready", return_value=worker_ready),
        mock.patch.object(rs, "is_worker_pane_open", return_value=pane_open),
        mock.patch.object(rs, "get_worker_pane_pid", return_value=12345),
        mock.patch.object(rs, "dismiss_trust_prompt_if_present", return_value=False),
        mock.patch.object(rs, "resolve_team_worker_cli_plan", return_value=cli_plan),
        mock.patch.object(rs, "is_git_repository", return_value=git_repo),
        mock.patch.object(
            rs, "_dispatch_startup_inbox", side_effect=_dispatch_side_effect
        ),
        mock.patch.object(rs, "load_role_prompt", return_value=None),
        # Prompt-mode child spawn returns a fake PID
        mock.patch.object(rs, "_spawn_prompt_worker", return_value=99999),
    ]

    class _Ctx:
        def __enter__(self):
            self.mocks = [p.start() for p in patches]
            return self

        def __exit__(self, *a):
            for p in reversed(patches):
                p.stop()
            return False

    return _Ctx()


# ---------------------------------------------------------------------------
# Env-knob helpers
# ---------------------------------------------------------------------------


class TestEnvKnobs(unittest.TestCase):
    def test_resolve_team_worker_launch_mode_default_interactive(self) -> None:
        self.assertEqual(rs._resolve_team_worker_launch_mode({}), "interactive")
        self.assertEqual(
            rs._resolve_team_worker_launch_mode(
                {"OMX_TEAM_WORKER_LAUNCH_MODE": "INTERACTIVE"}
            ),
            "interactive",
        )

    def test_resolve_team_worker_launch_mode_prompt(self) -> None:
        self.assertEqual(
            rs._resolve_team_worker_launch_mode(
                {"OMX_TEAM_WORKER_LAUNCH_MODE": "prompt"}
            ),
            "prompt",
        )

    def test_resolve_worker_ready_timeout_default(self) -> None:
        self.assertEqual(rs._resolve_worker_ready_timeout_ms({}), 45_000)
        self.assertEqual(
            rs._resolve_worker_ready_timeout_ms({"OMX_TEAM_READY_TIMEOUT_MS": "100"}),
            45_000,
        )

    def test_resolve_worker_ready_timeout_clamps_floor(self) -> None:
        self.assertEqual(
            rs._resolve_worker_ready_timeout_ms({"OMX_TEAM_READY_TIMEOUT_MS": "30000"}),
            30_000,
        )

    def test_resolve_worker_startup_evidence_timeout_default(self) -> None:
        value = rs._resolve_worker_startup_evidence_timeout_ms({}, 45_000)
        # Floor is STARTUP_EVIDENCE_TIMEOUT_MS=2_000 and ceiling is the
        # launch-timeout (5_000), so the resolver lands on 5_000.
        self.assertEqual(value, 5_000)

    def test_resolve_worker_startup_evidence_timeout_explicit(self) -> None:
        self.assertEqual(
            rs._resolve_worker_startup_evidence_timeout_ms(
                {"OMX_TEAM_STARTUP_EVIDENCE_TIMEOUT_MS": "7000"}, 45_000
            ),
            7000,
        )

    def test_resolve_worker_startup_evidence_timeout_below_floor(self) -> None:
        # Values below the 500ms floor fall back to the computed default.
        self.assertEqual(
            rs._resolve_worker_startup_evidence_timeout_ms(
                {"OMX_TEAM_STARTUP_EVIDENCE_TIMEOUT_MS": "100"}, 45_000
            ),
            5_000,
        )

    def test_resolve_startup_dispatch_retries_default(self) -> None:
        self.assertEqual(rs._resolve_startup_dispatch_retries({}), 3)

    def test_resolve_startup_dispatch_retries_clamped(self) -> None:
        self.assertEqual(
            rs._resolve_startup_dispatch_retries(
                {"OMX_TEAM_STARTUP_DISPATCH_RETRIES": "1"}
            ),
            1,
        )
        self.assertEqual(
            rs._resolve_startup_dispatch_retries(
                {"OMX_TEAM_STARTUP_DISPATCH_RETRIES": "100"}
            ),
            3,
        )
        self.assertEqual(
            rs._resolve_startup_dispatch_retries(
                {"OMX_TEAM_STARTUP_DISPATCH_RETRIES": "0"}
            ),
            1,
        )

    def test_resolve_startup_dispatch_retry_delay_default(self) -> None:
        self.assertEqual(rs._resolve_startup_dispatch_retry_delay_s({}), 3.0)

    def test_resolve_startup_dispatch_retry_delay_explicit(self) -> None:
        # 1500ms ⇒ 1.5 seconds; clamped under STARTUP_DISPATCH_RETRY_DELAY_S=3
        self.assertEqual(
            rs._resolve_startup_dispatch_retry_delay_s(
                {"OMX_TEAM_STARTUP_DISPATCH_RETRY_DELAY_MS": "1500"}
            ),
            1.5,
        )

    def test_should_skip_worker_ready_wait_truthy(self) -> None:
        for v in ("1", "true", "yes"):
            self.assertTrue(
                rs._should_skip_worker_ready_wait({"OMX_TEAM_SKIP_READY_WAIT": v})
            )

    def test_should_skip_worker_ready_wait_default(self) -> None:
        self.assertFalse(rs._should_skip_worker_ready_wait({}))

    def test_is_recoverable_reason(self) -> None:
        for reason in (
            "startup_no_evidence",
            "fallback_attempted_but_unconfirmed",
            "ready_prompt_timeout",
        ):
            self.assertTrue(rs._is_recoverable_interactive_startup_reason(reason))

    def test_is_non_recoverable_reason(self) -> None:
        for reason in ("worker_dead", "not_attempted", "boom"):
            self.assertFalse(rs._is_recoverable_interactive_startup_reason(reason))

    def test_resolve_instruction_state_root(self) -> None:
        self.assertEqual(
            rs._resolve_instruction_state_root("/path/to/wt"),
            rs.WORKTREE_TRIGGER_STATE_ROOT,
        )
        self.assertIsNone(rs._resolve_instruction_state_root(None))
        self.assertIsNone(rs._resolve_instruction_state_root(""))


# ---------------------------------------------------------------------------
# Set/restore model instructions file
# ---------------------------------------------------------------------------


class TestModelInstructionsFileLifecycle(unittest.TestCase):
    def setUp(self) -> None:
        # Cleanly isolate the module's global dict + env var.
        rs._previous_model_instructions_file_by_team.clear()
        self._orig_env = os.environ.get(rs.MODEL_INSTRUCTIONS_FILE_ENV)
        os.environ.pop(rs.MODEL_INSTRUCTIONS_FILE_ENV, None)

    def tearDown(self) -> None:
        rs._previous_model_instructions_file_by_team.clear()
        if self._orig_env is None:
            os.environ.pop(rs.MODEL_INSTRUCTIONS_FILE_ENV, None)
        else:
            os.environ[rs.MODEL_INSTRUCTIONS_FILE_ENV] = self._orig_env

    def test_set_then_restore_when_no_prior_value(self) -> None:
        rs._set_team_model_instructions_file("alpha", "/tmp/agents.md")
        self.assertEqual(os.environ[rs.MODEL_INSTRUCTIONS_FILE_ENV], "/tmp/agents.md")
        rs._restore_team_model_instructions_file("alpha")
        self.assertNotIn(rs.MODEL_INSTRUCTIONS_FILE_ENV, os.environ)

    def test_set_then_restore_keeps_prior_value(self) -> None:
        os.environ[rs.MODEL_INSTRUCTIONS_FILE_ENV] = "/prior"
        rs._set_team_model_instructions_file("alpha", "/tmp/agents.md")
        self.assertEqual(os.environ[rs.MODEL_INSTRUCTIONS_FILE_ENV], "/tmp/agents.md")
        rs._restore_team_model_instructions_file("alpha")
        self.assertEqual(os.environ[rs.MODEL_INSTRUCTIONS_FILE_ENV], "/prior")

    def test_restore_without_set_is_noop(self) -> None:
        rs._restore_team_model_instructions_file("never-set")
        self.assertNotIn(rs.MODEL_INSTRUCTIONS_FILE_ENV, os.environ)


# ---------------------------------------------------------------------------
# Canonical team state root + preflight helpers
# ---------------------------------------------------------------------------


class TestCanonicalTeamStateRoot(unittest.TestCase):
    def test_resolves_relative_to_leader_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            self.assertEqual(
                rs._resolve_canonical_team_state_root(cwd),
                str(Path(cwd).resolve() / ".omx" / "state"),
            )


class TestParseTeamWorkerContext(unittest.TestCase):
    def test_none_or_empty(self) -> None:
        self.assertIsNone(rs._parse_team_worker_context(None))
        self.assertIsNone(rs._parse_team_worker_context(""))
        self.assertIsNone(rs._parse_team_worker_context("   "))

    def test_missing_worker_returns_none(self) -> None:
        self.assertIsNone(rs._parse_team_worker_context("team-only"))

    def test_team_and_worker(self) -> None:
        parsed = rs._parse_team_worker_context("alpha/worker-1")
        self.assertEqual(parsed, {"team_name": "alpha", "worker_name": "worker-1"})


class TestResolveLeaderSessionId(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name
        # Save + scrub env keys
        self._saved = {
            k: os.environ.pop(k, None)
            for k in ("OMX_SESSION_ID", "CODEX_SESSION_ID", "SESSION_ID")
        }

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_returns_env_when_set(self) -> None:
        os.environ["OMX_SESSION_ID"] = "session-xyz"
        self.assertEqual(rs._resolve_leader_session_id(self.cwd), "session-xyz")

    def test_codex_session_id_fallback(self) -> None:
        os.environ["CODEX_SESSION_ID"] = "codex-abc"
        self.assertEqual(rs._resolve_leader_session_id(self.cwd), "codex-abc")

    def test_reads_session_json(self) -> None:
        p = Path(self.cwd) / ".omx" / "state" / "session.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"session_id": "from-file"}))
        self.assertEqual(rs._resolve_leader_session_id(self.cwd), "from-file")

    def test_returns_empty_when_no_source(self) -> None:
        self.assertEqual(rs._resolve_leader_session_id(self.cwd), "")

    def test_returns_empty_on_corrupt_json(self) -> None:
        p = Path(self.cwd) / ".omx" / "state" / "session.json"
        p.parent.mkdir(parents=True)
        p.write_text("not-json")
        self.assertEqual(rs._resolve_leader_session_id(self.cwd), "")


class TestAssertNestedTeamAllowed(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name
        self._saved = os.environ.pop("OMX_TEAM_WORKER", None)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("OMX_TEAM_WORKER", None)
        else:
            os.environ["OMX_TEAM_WORKER"] = self._saved
        self._tmp.cleanup()

    def test_no_worker_context_returns(self) -> None:
        rs._assert_nested_team_allowed(self.cwd)  # should not raise

    def test_worker_context_with_no_manifest_raises(self) -> None:
        os.environ["OMX_TEAM_WORKER"] = "parent/worker-1"
        with self.assertRaises(RuntimeError) as ctx:
            rs._assert_nested_team_allowed(self.cwd)
        self.assertIn("nested_team_disallowed", str(ctx.exception))

    def test_worker_context_with_nested_allowed_manifest(self) -> None:
        os.environ["OMX_TEAM_WORKER"] = "parent/worker-1"
        from omx.team.state.manifest import (
            PermissionsSnapshot,
            TeamLeader,
            TeamManifestV2,
            write_team_manifest_v2,
        )

        manifest = TeamManifestV2(
            schema_version=2,
            name="parent",
            task="t",
            leader=TeamLeader(session_id="s1"),
            permissions_snapshot=PermissionsSnapshot(),
            policy={},
            governance={"nested_teams_allowed": True},
            tmux_session="omx-team-parent",
            worker_count=1,
            workers=[],
            next_task_id=1,
            created_at="2026-01-01T00:00:00Z",
        )
        write_team_manifest_v2(manifest, self.cwd)
        rs._assert_nested_team_allowed(self.cwd)  # should not raise


class TestAssertTeamStartupNonDestructive(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_when_no_state(self) -> None:
        rs._assert_team_startup_is_non_destructive("alpha", self.cwd, "sid")

    def test_raises_team_name_conflict_when_active_config_exists(self) -> None:
        team_dir = Path(self.cwd) / ".omx" / "team" / "alpha"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"tmux_session": "omx-team-alpha"})
        )
        with self.assertRaises(RuntimeError) as ctx:
            rs._assert_team_startup_is_non_destructive("alpha", self.cwd, "sid")
        self.assertIn("team_name_conflict", str(ctx.exception))

    def test_returns_when_phase_is_terminal(self) -> None:
        team_dir = Path(self.cwd) / ".omx" / "team" / "alpha"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text(
            json.dumps({"tmux_session": "omx-team-alpha"})
        )
        (team_dir / "phase.json").write_text(json.dumps({"current_phase": "complete"}))
        rs._assert_team_startup_is_non_destructive("alpha", self.cwd, "sid")

    def test_raises_leader_session_conflict(self) -> None:
        # Build a foreign team's manifest claiming the same leader cwd.
        from omx.team.state.manifest import (
            PermissionsSnapshot,
            TeamLeader,
            TeamManifestV2,
            write_team_manifest_v2,
        )

        manifest = TeamManifestV2(
            schema_version=2,
            name="other",
            task="t",
            leader=TeamLeader(session_id="other-session"),
            permissions_snapshot=PermissionsSnapshot(),
            policy={},
            governance=None,
            tmux_session="omx-team-other",
            worker_count=1,
            workers=[],
            next_task_id=1,
            created_at="2026-01-01T00:00:00Z",
        )
        write_team_manifest_v2(manifest, self.cwd)
        with self.assertRaises(RuntimeError) as ctx:
            rs._assert_team_startup_is_non_destructive("alpha", self.cwd, "my-session")
        self.assertIn("leader_session_conflict", str(ctx.exception))


class TestDetectAndCleanStaleTeam(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cwd = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_returns_when_state_absent(self) -> None:
        rs._detect_and_clean_stale_team("alpha", self.cwd, 2, None)  # no raise

    def test_skips_when_session_alive(self) -> None:
        (Path(self.cwd) / ".omx" / "team" / "alpha").mkdir(parents=True)
        with mock.patch.object(
            rs, "list_team_sessions", return_value=["omx-team-alpha"]
        ):
            rs._detect_and_clean_stale_team("alpha", self.cwd, 2, None)

    def test_cleans_when_no_worktrees(self) -> None:
        team_dir = Path(self.cwd) / ".omx" / "team" / "alpha"
        team_dir.mkdir(parents=True)
        (team_dir / "config.json").write_text("{}")
        with (
            mock.patch.object(rs, "list_team_sessions", return_value=[]),
            mock.patch.object(rs.subprocess, "run") as run_mock,
        ):
            run_mock.return_value = mock.Mock(returncode=0, stdout=self.cwd)
            rs._detect_and_clean_stale_team("alpha", self.cwd, 2, None)
        self.assertFalse(team_dir.exists())

    def test_raises_without_confirm_when_worktrees_exist(self) -> None:
        team_dir = Path(self.cwd) / ".omx" / "team" / "alpha"
        team_dir.mkdir(parents=True)
        (Path(self.cwd) / ".omx" / "team" / "alpha" / "worktrees" / "worker-1").mkdir(
            parents=True
        )
        with (
            mock.patch.object(rs, "list_team_sessions", return_value=[]),
            mock.patch.object(rs.subprocess, "run") as run_mock,
        ):
            run_mock.return_value = mock.Mock(returncode=0, stdout=self.cwd)
            with self.assertRaises(RuntimeError) as ctx:
                rs._detect_and_clean_stale_team("alpha", self.cwd, 1, None)
        self.assertIn("stale_team_artifacts", str(ctx.exception))

    def test_confirm_declined_raises(self) -> None:
        team_dir = Path(self.cwd) / ".omx" / "team" / "alpha"
        team_dir.mkdir(parents=True)
        (Path(self.cwd) / ".omx" / "team" / "alpha" / "worktrees" / "worker-1").mkdir(
            parents=True
        )
        with (
            mock.patch.object(rs, "list_team_sessions", return_value=[]),
            mock.patch.object(rs.subprocess, "run") as run_mock,
            mock.patch.object(rs, "is_worktree_dirty", return_value=False),
        ):
            run_mock.return_value = mock.Mock(returncode=0, stdout=self.cwd)
            with self.assertRaises(RuntimeError) as ctx:
                rs._detect_and_clean_stale_team(
                    "alpha", self.cwd, 1, confirm_fn=lambda _summary: False
                )
        self.assertIn("stale_team_cleanup_declined", str(ctx.exception))


# ---------------------------------------------------------------------------
# Recoverable startup issue recorder
# ---------------------------------------------------------------------------


class TestRecordRecoverableStartupIssue(unittest.TestCase):
    def test_writes_status_and_event(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            # Ensure the team dir exists so write_worker_status works.
            (Path(cwd) / ".omx" / "team" / "alpha" / "workers" / "worker-1").mkdir(
                parents=True
            )
            rs._record_recoverable_startup_issue(
                team_name="alpha",
                worker_name="worker-1",
                task_ids=["1", "2"],
                reason="ready_prompt_timeout",
                cwd=cwd,
            )
            status_path = (
                Path(cwd)
                / ".omx"
                / "team"
                / "alpha"
                / "workers"
                / "worker-1"
                / "status.json"
            )
            self.assertTrue(status_path.exists())
            payload = json.loads(status_path.read_text())
            self.assertEqual(payload["state"], "unknown")
            self.assertEqual(payload["reason"], "ready_prompt_timeout")
            self.assertEqual(payload["current_task_id"], "1")

    def test_handles_status_io_failure(self) -> None:
        # No team dir on disk; the recorder must swallow exceptions.
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.object(
                rs, "write_worker_status", side_effect=OSError("nope")
            ):
                rs._record_recoverable_startup_issue(
                    team_name="alpha",
                    worker_name="worker-1",
                    task_ids=[],
                    reason="ready_prompt_timeout",
                    cwd=cwd,
                )  # should not raise


# ---------------------------------------------------------------------------
# Happy path — interactive (Codex)
# ---------------------------------------------------------------------------


class TestStartTeamInteractiveCodex(unittest.TestCase):
    def test_single_worker_codex_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=1, worker_cli="codex"):
                runtime = rs.start_team(
                    "alpha",
                    "do work",
                    "executor",
                    1,
                    tasks=[{"subject": "s1", "description": "d1"}],
                    cwd=cwd,
                )
            self.assertEqual(runtime.team_name, "alpha")
            self.assertEqual(runtime.sanitized_name, "alpha")
            self.assertEqual(runtime.session_name, "omx-team-alpha")
            self.assertEqual(runtime.config["worker_count"], 1)
            self.assertEqual(runtime.config["workers"][0]["worker_cli"], "codex")
            # Persisted artifacts
            self.assertTrue(
                (Path(cwd) / ".omx" / "team" / "alpha" / "manifest.v2.json").exists()
            )
            self.assertTrue(
                (
                    Path(cwd)
                    / ".omx"
                    / "team"
                    / "alpha"
                    / "workers"
                    / "worker-1"
                    / "inbox.md"
                ).exists()
            )

    def test_two_workers_codex_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=2, worker_cli="codex"):
                runtime = rs.start_team(
                    "beta",
                    "task",
                    "executor",
                    2,
                    tasks=[
                        {"description": "t1", "owner": "worker-1"},
                        {"description": "t2", "owner": "worker-2"},
                    ],
                    cwd=cwd,
                )
            self.assertEqual(len(runtime.config["workers"]), 2)
            # Worker pane IDs from the stubbed TeamSession should propagate
            self.assertEqual(runtime.config["workers"][0]["pane_id"], "%w1")
            self.assertEqual(runtime.config["workers"][1]["pane_id"], "%w2")

    def test_four_workers_codex_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=4, worker_cli="codex"):
                runtime = rs.start_team(
                    "gamma",
                    "task",
                    "executor",
                    4,
                    tasks=[],
                    cwd=cwd,
                )
            self.assertEqual(len(runtime.config["workers"]), 4)


# ---------------------------------------------------------------------------
# Happy path — interactive (Claude worker_cli)
# ---------------------------------------------------------------------------


class TestStartTeamInteractiveClaude(unittest.TestCase):
    def test_claude_worker_cli_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=2, worker_cli="claude"):
                runtime = rs.start_team(
                    "claude-team",
                    "task",
                    "executor",
                    2,
                    tasks=[],
                    cwd=cwd,
                )
            for w in runtime.config["workers"]:
                self.assertEqual(w["worker_cli"], "claude")

    def test_mixed_codex_claude_cli_map(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=2, worker_cli=["codex", "claude"]):
                runtime = rs.start_team(
                    "mixed",
                    "task",
                    "executor",
                    2,
                    tasks=[],
                    cwd=cwd,
                )
            self.assertEqual(runtime.config["workers"][0]["worker_cli"], "codex")
            self.assertEqual(runtime.config["workers"][1]["worker_cli"], "claude")


# ---------------------------------------------------------------------------
# Happy path — prompt mode (Claude)
# ---------------------------------------------------------------------------


class TestStartTeamPromptMode(unittest.TestCase):
    def setUp(self) -> None:
        # Force prompt mode through env
        self._saved = os.environ.pop("OMX_TEAM_WORKER_LAUNCH_MODE", None)
        os.environ["OMX_TEAM_WORKER_LAUNCH_MODE"] = "prompt"

    def tearDown(self) -> None:
        os.environ.pop("OMX_TEAM_WORKER_LAUNCH_MODE", None)
        if self._saved is not None:
            os.environ["OMX_TEAM_WORKER_LAUNCH_MODE"] = self._saved

    def test_prompt_mode_claude(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(interactive=False, worker_count=2, worker_cli="claude"):
                runtime = rs.start_team(
                    "p-claude",
                    "task",
                    "executor",
                    2,
                    tasks=[],
                    cwd=cwd,
                )
            # In prompt mode TS leaves ``sessionName`` at its initial
            # ``omx-team-<name>`` value but updates the persisted
            # ``config.tmux_session`` to ``prompt-<name>``.
            self.assertEqual(runtime.session_name, "omx-team-p-claude")
            self.assertEqual(runtime.config["tmux_session"], "prompt-p-claude")
            # Each worker should have been assigned the stubbed PID
            for w in runtime.config["workers"]:
                self.assertEqual(w["pid"], 99999)

    def test_prompt_mode_gemini_with_initial_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(interactive=False, worker_count=1, worker_cli="gemini"):
                rs.start_team(
                    "p-gemini",
                    "task",
                    "executor",
                    1,
                    tasks=[],
                    cwd=cwd,
                )
            # Gemini path writes the initial inbox at plan-build time.
            self.assertTrue(
                (
                    Path(cwd)
                    / ".omx"
                    / "team"
                    / "p-gemini"
                    / "workers"
                    / "worker-1"
                    / "inbox.md"
                ).exists()
            )


# ---------------------------------------------------------------------------
# Preflight failure: tmux unavailable / not inside tmux
# ---------------------------------------------------------------------------


class TestStartTeamTmuxPreflightFailures(unittest.TestCase):
    def test_no_tmux_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(interactive=True):
                with mock.patch.object(rs, "is_tmux_available", return_value=False):
                    with self.assertRaises(RuntimeError) as ctx:
                        rs.start_team("alpha", "t", "executor", 1, tasks=[], cwd=cwd)
            self.assertIn("tmux", str(ctx.exception))

    def test_no_tmux_client_context_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(interactive=True):
                with mock.patch.object(
                    rs, "has_current_tmux_client_context", return_value=False
                ):
                    with self.assertRaises(RuntimeError) as ctx:
                        rs.start_team("alpha", "t", "executor", 1, tasks=[], cwd=cwd)
            self.assertIn("leader pane", str(ctx.exception).lower())


# ---------------------------------------------------------------------------
# Ready-prompt timeout → recoverable record (not raised)
# ---------------------------------------------------------------------------


class TestStartTeamReadyPromptTimeout(unittest.TestCase):
    def test_ready_timeout_records_recoverable_when_pane_alive(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=1, worker_ready=False, pane_open=True):
                runtime = rs.start_team("alpha", "t", "executor", 1, tasks=[], cwd=cwd)
            # We still got a runtime back; status.json should record the
            # recoverable startup issue.
            status_path = (
                Path(cwd)
                / ".omx"
                / "team"
                / "alpha"
                / "workers"
                / "worker-1"
                / "status.json"
            )
            self.assertTrue(status_path.exists())
            payload = json.loads(status_path.read_text())
            self.assertEqual(payload["reason"], "ready_prompt_timeout")
            self.assertEqual(runtime.team_name, "alpha")

    def test_ready_timeout_with_dead_pane_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=1, worker_ready=False, pane_open=False):
                with self.assertRaises(RuntimeError) as ctx:
                    rs.start_team("alpha", "t", "executor", 1, tasks=[], cwd=cwd)
            # Rollback may decorate the message; the original reason should
            # still appear.
            self.assertIn("did not become ready", str(ctx.exception))


# ---------------------------------------------------------------------------
# Dispatch retry loop
# ---------------------------------------------------------------------------


class TestStartTeamDispatchRetry(unittest.TestCase):
    def test_dispatch_succeeds_on_second_attempt(self) -> None:
        outcomes = [
            _fail_dispatch("startup_no_evidence"),
            _ok_dispatch(),
        ]
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.object(rs.time, "sleep"):  # speed up retry delay
                with _patch_runtime(
                    worker_count=1,
                    dispatch_outcomes=outcomes,
                ):
                    runtime = rs.start_team(
                        "alpha", "t", "executor", 1, tasks=[], cwd=cwd
                    )
            self.assertEqual(runtime.team_name, "alpha")
            self.assertEqual(outcomes, [])  # both consumed

    def test_dispatch_recoverable_after_all_retries_records_issue(self) -> None:
        outcomes = [_fail_dispatch("startup_no_evidence")] * 3
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.object(rs.time, "sleep"):
                with _patch_runtime(
                    worker_count=1,
                    dispatch_outcomes=outcomes,
                    pane_open=True,
                ):
                    runtime = rs.start_team(
                        "alpha", "t", "executor", 1, tasks=[], cwd=cwd
                    )
            status_path = (
                Path(cwd)
                / ".omx"
                / "team"
                / "alpha"
                / "workers"
                / "worker-1"
                / "status.json"
            )
            self.assertTrue(status_path.exists())
            payload = json.loads(status_path.read_text())
            self.assertEqual(payload["reason"], "startup_no_evidence")
            self.assertEqual(runtime.team_name, "alpha")

    def test_dispatch_non_recoverable_raises_after_retries(self) -> None:
        outcomes = [_fail_dispatch("worker_dead")] * 3
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.object(rs.time, "sleep"):
                with _patch_runtime(
                    worker_count=1,
                    dispatch_outcomes=outcomes,
                    pane_open=True,
                ):
                    with self.assertRaises(RuntimeError) as ctx:
                        rs.start_team("alpha", "t", "executor", 1, tasks=[], cwd=cwd)
            self.assertIn("worker_notify_failed", str(ctx.exception))


# ---------------------------------------------------------------------------
# Rollback path
# ---------------------------------------------------------------------------


class TestStartTeamRollback(unittest.TestCase):
    def test_create_team_session_failure_rolls_back_state(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=2):
                with mock.patch.object(
                    rs, "create_team_session", side_effect=RuntimeError("boom")
                ):
                    with self.assertRaises(RuntimeError) as ctx:
                        rs.start_team("alpha", "t", "executor", 2, tasks=[], cwd=cwd)
            self.assertIn("boom", str(ctx.exception))
            # Team state directory should be cleaned up
            self.assertFalse((Path(cwd) / ".omx" / "team" / "alpha").exists())

    def test_dispatch_failure_destroys_session(self) -> None:
        outcomes = [_fail_dispatch("worker_dead")] * 3
        destroyed_sessions: list[str] = []

        def _record_destroy(name: str) -> None:
            destroyed_sessions.append(name)

        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.object(rs.time, "sleep"):
                with _patch_runtime(
                    worker_count=1,
                    dispatch_outcomes=outcomes,
                ):
                    with mock.patch.object(
                        rs, "destroy_team_session", side_effect=_record_destroy
                    ):
                        with self.assertRaises(RuntimeError):
                            rs.start_team(
                                "alpha", "t", "executor", 1, tasks=[], cwd=cwd
                            )
            self.assertEqual(destroyed_sessions, ["omx-team-alpha"])

    def test_split_pane_session_kills_panes_not_session(self) -> None:
        """When tmux_session contains ':', rollback kills panes, not the session."""
        # Build a TeamSession whose name embeds a pane selector so the
        # rollback branch follows the kill-panes path.
        session = TmuxTeamSession(
            name="my-session:0",
            worker_count=1,
            cwd="/tmp/fake-cwd",
            worker_pane_ids=["%w1"],
            leader_pane_id="%leader",
            hud_pane_id="%hud",
            resize_hook_name=None,
            resize_hook_target=None,
        )
        outcomes = [_fail_dispatch("worker_dead")] * 3
        killed: list[str] = []
        destroyed: list[str] = []

        def _kill(pane_id, leader_pane_id=None):
            killed.append(pane_id)

        def _destroy(name):
            destroyed.append(name)

        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.object(rs.time, "sleep"):
                with _patch_runtime(
                    worker_count=1,
                    dispatch_outcomes=outcomes,
                    tmux_session=session,
                ):
                    with (
                        mock.patch.object(
                            rs, "kill_worker_by_pane_id", side_effect=_kill
                        ),
                        mock.patch.object(
                            rs, "destroy_team_session", side_effect=_destroy
                        ),
                    ):
                        with self.assertRaises(RuntimeError):
                            rs.start_team(
                                "alpha", "t", "executor", 1, tasks=[], cwd=cwd
                            )
            # destroy_team_session must not be called in split-pane mode
            self.assertEqual(destroyed, [])
            # All created worker panes should have been killed
            self.assertIn("%w1", killed)

    def test_init_team_state_failure_propagates(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=1):
                with mock.patch.object(
                    rs, "init_team_state", side_effect=RuntimeError("init-boom")
                ):
                    with self.assertRaises(RuntimeError) as ctx:
                        rs.start_team("alpha", "t", "executor", 1, tasks=[], cwd=cwd)
            # Rollback should still clear partial state if any.
            self.assertIn("init-boom", str(ctx.exception))


# ---------------------------------------------------------------------------
# Governance policy
# ---------------------------------------------------------------------------


class TestStartTeamGovernance(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.pop("OMX_TEAM_WORKER", None)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("OMX_TEAM_WORKER", None)
        else:
            os.environ["OMX_TEAM_WORKER"] = self._saved

    def test_nested_team_disallowed_blocks_startup(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            # Simulate caller running inside a worker context — no parent
            # manifest exists, so the gate fires.
            os.environ["OMX_TEAM_WORKER"] = "parent/worker-1"
            with _patch_runtime(worker_count=1):
                with self.assertRaises(RuntimeError) as ctx:
                    rs.start_team("child", "t", "executor", 1, tasks=[], cwd=cwd)
            self.assertIn("nested_team_disallowed", str(ctx.exception))


# ---------------------------------------------------------------------------
# Worktree provisioning + rollback
# ---------------------------------------------------------------------------


class TestStartTeamWorktreeProvisioning(unittest.TestCase):
    def test_worktree_mode_rollback_on_failure(self) -> None:
        """A worktree-enabled run that fails inside create_team_session must
        invoke ``rollback_provisioned_worktrees`` exactly once."""

        rb_calls: list[tuple] = []

        def _record_rb(*args, **kwargs):
            rb_calls.append((args, kwargs))

        # Build a fake EnsureWorktreeResult so the rollback path has entries.
        fake_result = EnsureWorktreeResult(
            enabled=True,
            repo_root="/repo",
            worktree_path="/repo/.omx/team/alpha/worktrees/worker-1",
            detached=True,
            branch_name=None,
            created=True,
            reused=False,
            created_branch=False,
        )

        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=1):
                with (
                    mock.patch.object(rs, "is_git_repository", return_value=True),
                    mock.patch.object(
                        rs,
                        "plan_worktree_target",
                        return_value=mock.Mock(enabled=True),
                    ),
                    mock.patch.object(rs, "ensure_worktree", return_value=fake_result),
                    mock.patch.object(
                        rs, "assert_clean_leader_workspace_for_worker_worktrees"
                    ),
                    mock.patch.object(
                        rs, "rollback_provisioned_worktrees", side_effect=_record_rb
                    ),
                    mock.patch.object(
                        rs, "create_team_session", side_effect=RuntimeError("boom")
                    ),
                    mock.patch.object(
                        rs,
                        "write_worker_worktree_root_agents_file",
                        return_value="/tmp/agents.md",
                    ),
                    mock.patch.object(rs, "remove_worker_worktree_root_agents_file"),
                ):
                    # Need a worktree mode in options to take the worktree
                    # branch.
                    opts = TeamStartOptions(
                        worktree_mode={
                            "enabled": True,
                            "detached": True,
                            "name": None,
                        }
                    )
                    with self.assertRaises(RuntimeError) as ctx:
                        rs.start_team(
                            "alpha",
                            "t",
                            "executor",
                            1,
                            tasks=[],
                            cwd=cwd,
                            options=opts,
                        )
            self.assertIn("boom", str(ctx.exception))
            self.assertEqual(len(rb_calls), 1)


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------


class TestStartTeamSanitization(unittest.TestCase):
    def test_team_name_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=1):
                runtime = rs.start_team(
                    "Some_TEAM!", "t", "executor", 1, tasks=[], cwd=cwd
                )
            # sanitize_team_name lower-cases + strips non-allowed chars.
            self.assertEqual(runtime.team_name, runtime.sanitized_name)
            self.assertNotIn("!", runtime.sanitized_name)
            self.assertNotIn("_", runtime.sanitized_name)


# ---------------------------------------------------------------------------
# Tasks are persisted
# ---------------------------------------------------------------------------


class TestStartTeamTasksPersistence(unittest.TestCase):
    def test_tasks_persisted_to_disk(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=2):
                rs.start_team(
                    "alpha",
                    "t",
                    "executor",
                    2,
                    tasks=[
                        {
                            "subject": "t1-subject",
                            "description": "t1",
                            "owner": "worker-1",
                            "role": "executor",
                        },
                        {"description": "t2", "owner": "worker-2"},
                    ],
                    cwd=cwd,
                )
            tasks_file = Path(cwd) / ".omx" / "team" / "alpha" / "tasks.json"
            self.assertTrue(tasks_file.exists())
            payload = json.loads(tasks_file.read_text())
            self.assertEqual(len(payload["tasks"]), 2)
            self.assertEqual(payload["tasks"][0]["owner"], "worker-1")


# ---------------------------------------------------------------------------
# Worker pane PIDs propagated to identity files
# ---------------------------------------------------------------------------


class TestStartTeamWorkerIdentities(unittest.TestCase):
    def test_identity_files_written(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with _patch_runtime(worker_count=2):
                rs.start_team("alpha", "t", "executor", 2, tasks=[], cwd=cwd)
            for i in (1, 2):
                identity_path = (
                    Path(cwd)
                    / ".omx"
                    / "team"
                    / "alpha"
                    / "workers"
                    / f"worker-{i}"
                    / "identity.json"
                )
                self.assertTrue(identity_path.exists())
                payload = json.loads(identity_path.read_text())
                self.assertEqual(payload["name"], f"worker-{i}")
                self.assertEqual(payload["worker_cli"], "codex")
                self.assertEqual(payload["pid"], 12345)


# ---------------------------------------------------------------------------
# DispatchOutcome plumbing
# ---------------------------------------------------------------------------


class TestDispatchStartupInbox(unittest.TestCase):
    def test_dispatch_inbox_returns_dispatch_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            # ensure team-state dir + worker dir exist so queue_inbox_instruction
            # can write the inbox file.
            (Path(cwd) / ".omx" / "team" / "alpha" / "workers" / "worker-1").mkdir(
                parents=True
            )
            with (
                mock.patch.object(rs, "queue_inbox_instruction") as queue_mock,
            ):
                queue_mock.return_value = _ok_dispatch()
                outcome = rs._dispatch_startup_inbox(
                    team_name="alpha",
                    worker_name="worker-1",
                    worker_index=1,
                    pane_id="%w1",
                    worker_cli="codex",
                    inbox="hello",
                    trigger_message="go",
                    intent="followup-relaunch",
                    cwd=cwd,
                    worker_launch_mode="interactive",
                )
            self.assertTrue(outcome.ok)
            queue_mock.assert_called_once()
            params = queue_mock.call_args.args[0]
            self.assertEqual(
                params.transport_preference, "hook_preferred_with_fallback"
            )
            self.assertTrue(params.fallback_allowed)

    def test_dispatch_inbox_prompt_mode_uses_prompt_stdin(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.object(rs, "queue_inbox_instruction") as queue_mock:
                queue_mock.return_value = _ok_dispatch()
                rs._dispatch_startup_inbox(
                    team_name="alpha",
                    worker_name="worker-1",
                    worker_index=1,
                    pane_id=None,
                    worker_cli="claude",
                    inbox="hello",
                    trigger_message="go",
                    intent="followup-relaunch",
                    cwd=cwd,
                    worker_launch_mode="prompt",
                )
            params = queue_mock.call_args.args[0]
            self.assertEqual(params.transport_preference, "prompt_stdin")
            self.assertFalse(params.fallback_allowed)


# ---------------------------------------------------------------------------
# Materialize-worker-startup-state
# ---------------------------------------------------------------------------


class TestMaterializeWorkerStartupState(unittest.TestCase):
    def test_identity_pid_from_pane_in_interactive_mode(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            (Path(cwd) / ".omx" / "team" / "alpha" / "workers" / "worker-1").mkdir(
                parents=True
            )
            plan = rs._WorkerBootstrapPlan(
                worker_name="worker-1",
                worker_workspace={"cwd": cwd},
                worker_tasks=[],
                worker_role="executor",
                role_prompt_content=None,
                instructions_file_path="/tmp/AGENTS.md",
                inbox="inbox",
                trigger="trigger",
                trigger_intent="followup-relaunch",
                initial_prompt=None,
                worker_launch_args=[],
                worker_cli="codex",
            )
            config: dict = {
                "workers": [
                    {
                        "name": "worker-1",
                        "index": 1,
                        "role": "executor",
                        "worker_cli": "codex",
                    }
                ]
            }
            with mock.patch.object(rs, "get_worker_pane_pid", return_value=55555):
                rs._materialize_worker_startup_state(
                    team_name="alpha",
                    bootstrap_plan=plan,
                    worker_index=1,
                    pane_id="%w1",
                    worker_launch_mode="interactive",
                    session_name="omx-team-alpha",
                    config=config,
                    team_state_root=str(Path(cwd) / ".omx" / "state"),
                    leader_cwd=cwd,
                )
            self.assertEqual(config["workers"][0]["pid"], 55555)
            self.assertEqual(config["workers"][0]["pane_id"], "%w1")
            identity_path = (
                Path(cwd)
                / ".omx"
                / "team"
                / "alpha"
                / "workers"
                / "worker-1"
                / "identity.json"
            )
            self.assertTrue(identity_path.exists())
            payload = json.loads(identity_path.read_text())
            self.assertEqual(payload["pid"], 55555)

    def test_identity_pid_from_config_in_prompt_mode(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            (Path(cwd) / ".omx" / "team" / "alpha" / "workers" / "worker-1").mkdir(
                parents=True
            )
            plan = rs._WorkerBootstrapPlan(
                worker_name="worker-1",
                worker_workspace={"cwd": cwd},
                worker_tasks=[],
                worker_role="executor",
                role_prompt_content=None,
                instructions_file_path="/tmp/AGENTS.md",
                inbox="inbox",
                trigger="trigger",
                trigger_intent="followup-relaunch",
                initial_prompt=None,
                worker_launch_args=[],
                worker_cli="claude",
            )
            config: dict = {
                "workers": [
                    {
                        "name": "worker-1",
                        "index": 1,
                        "role": "executor",
                        "worker_cli": "claude",
                        "pid": 77777,
                    }
                ]
            }
            rs._materialize_worker_startup_state(
                team_name="alpha",
                bootstrap_plan=plan,
                worker_index=1,
                pane_id=None,
                worker_launch_mode="prompt",
                session_name="prompt-alpha",
                config=config,
                team_state_root=str(Path(cwd) / ".omx" / "state"),
                leader_cwd=cwd,
            )
            identity_path = (
                Path(cwd)
                / ".omx"
                / "team"
                / "alpha"
                / "workers"
                / "worker-1"
                / "identity.json"
            )
            payload = json.loads(identity_path.read_text())
            self.assertEqual(payload["pid"], 77777)
            self.assertNotIn("pane_id", payload)


# ---------------------------------------------------------------------------
# Effective worktree mode resolver
# ---------------------------------------------------------------------------


class TestResolveEffectiveWorktreeMode(unittest.TestCase):
    def test_non_git_cwd_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.object(rs, "is_git_repository", return_value=False):
                mode = rs._resolve_effective_team_worktree_mode(cwd, None)
            self.assertFalse(mode.enabled)

    def test_requested_mode_honoured_in_git(self) -> None:
        requested = WorktreeMode(enabled=True, detached=False, name="my-branch")
        with tempfile.TemporaryDirectory() as cwd:
            with mock.patch.object(rs, "is_git_repository", return_value=True):
                mode = rs._resolve_effective_team_worktree_mode(cwd, requested)
            self.assertTrue(mode.enabled)
            self.assertEqual(mode.name, "my-branch")

    def test_probe_failure_returns_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            with (
                mock.patch.object(rs, "is_git_repository", return_value=True),
                mock.patch.object(
                    rs, "plan_worktree_target", side_effect=RuntimeError("nope")
                ),
            ):
                mode = rs._resolve_effective_team_worktree_mode(cwd, None)
            self.assertFalse(mode.enabled)


if __name__ == "__main__":
    unittest.main()
