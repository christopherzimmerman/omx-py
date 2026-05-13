"""Tests for omx.team.runtime_helpers — standalone runtime helpers."""

from __future__ import annotations

import unittest

from omx.team.runtime_helpers import (
    TeamSession,
    apply_created_interactive_session_to_config,
    cleanup_team_worker_launch_orphaned_mcp_processes,
    resolve_worker_launch_args_from_env,
    should_prekill_interactive_shutdown_process_trees,
)


# --- apply_created_interactive_session_to_config -------------------------


class TestApplyCreatedInteractiveSessionToConfig(unittest.TestCase):
    def test_mutates_config_and_pane_ids(self) -> None:
        config = {
            "workers": [
                {"name": "w1", "pane_id": None},
                {"name": "w2", "pane_id": None},
            ]
        }
        session = TeamSession(
            name="omx-team-foo",
            leader_pane_id="%0",
            hud_pane_id="%1",
            resize_hook_name="hook",
            resize_hook_target="%0",
            worker_pane_ids=["%2", "%3"],
        )
        out: list[str | None] = []
        apply_created_interactive_session_to_config(config, session, out)

        self.assertEqual(config["tmux_session"], "omx-team-foo")
        self.assertEqual(config["leader_pane_id"], "%0")
        self.assertEqual(config["hud_pane_id"], "%1")
        self.assertEqual(config["resize_hook_name"], "hook")
        self.assertEqual(config["resize_hook_target"], "%0")
        self.assertEqual(out, ["%2", "%3"])
        self.assertEqual(config["workers"][0]["pane_id"], "%2")
        self.assertEqual(config["workers"][1]["pane_id"], "%3")

    def test_no_workers_in_session(self) -> None:
        config = {"workers": []}
        session = TeamSession(name="s", leader_pane_id="%0", worker_pane_ids=[])
        out: list[str | None] = []
        apply_created_interactive_session_to_config(config, session, out)
        self.assertEqual(config["tmux_session"], "s")
        self.assertEqual(out, [])

    def test_hud_pane_id_can_be_none(self) -> None:
        config = {"workers": []}
        session = TeamSession(
            name="s", leader_pane_id="%0", hud_pane_id=None, worker_pane_ids=[]
        )
        apply_created_interactive_session_to_config(config, session, [])
        self.assertIsNone(config["hud_pane_id"])

    def test_pane_id_with_none_slot(self) -> None:
        # Pre-existing worker_pane_ids list may already contain None slots.
        config = {"workers": [{"pane_id": None}, {"pane_id": None}]}
        session = TeamSession(
            name="s",
            leader_pane_id="%0",
            worker_pane_ids=[None, "%5"],
        )
        out: list[str | None] = [None, None]
        apply_created_interactive_session_to_config(config, session, out)
        self.assertEqual(out, [None, "%5"])
        self.assertIsNone(config["workers"][0]["pane_id"])
        self.assertEqual(config["workers"][1]["pane_id"], "%5")

    def test_more_session_panes_than_config_workers(self) -> None:
        # Defensive: helper should not raise if config has fewer worker slots.
        config = {"workers": [{"pane_id": None}]}
        session = TeamSession(
            name="s",
            leader_pane_id="%0",
            worker_pane_ids=["%a", "%b", "%c"],
        )
        out: list[str | None] = []
        apply_created_interactive_session_to_config(config, session, out)
        self.assertEqual(out, ["%a", "%b", "%c"])
        self.assertEqual(config["workers"][0]["pane_id"], "%a")


# --- should_prekill_interactive_shutdown_process_trees -------------------


class TestShouldPrekillInteractiveShutdownProcessTrees(unittest.TestCase):
    def test_detached_session_returns_true(self) -> None:
        self.assertTrue(
            should_prekill_interactive_shutdown_process_trees("omx-team-foo")
        )

    def test_shared_window_session_returns_false(self) -> None:
        self.assertFalse(
            should_prekill_interactive_shutdown_process_trees("attached:0.1")
        )

    def test_empty_string_returns_true(self) -> None:
        self.assertTrue(should_prekill_interactive_shutdown_process_trees(""))


# --- cleanup_team_worker_launch_orphaned_mcp_processes -------------------


class TestCleanupTeamWorkerLaunchOrphanedMcpProcesses(unittest.TestCase):
    def test_no_args_runs_default_noop(self) -> None:
        # Must not raise. Default cleanup returns empty failed_pids → no warning.
        warnings: list[str] = []
        cleanup_team_worker_launch_orphaned_mcp_processes(write_warning=warnings.append)
        self.assertEqual(warnings, [])

    def test_failed_pids_emits_warning(self) -> None:
        warnings: list[str] = []
        cleanup_team_worker_launch_orphaned_mcp_processes(
            cleanup=lambda: {"failed_pids": [123, 456]},
            write_warning=warnings.append,
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("Failed to reap 2", warnings[0])

    def test_no_failed_pids_no_warning(self) -> None:
        warnings: list[str] = []
        cleanup_team_worker_launch_orphaned_mcp_processes(
            cleanup=lambda: {"failed_pids": [], "reaped_pids": [99]},
            write_warning=warnings.append,
        )
        self.assertEqual(warnings, [])

    def test_cleanup_exception_logs_warning(self) -> None:
        warnings: list[str] = []

        def boom() -> dict:
            raise RuntimeError("boom")

        cleanup_team_worker_launch_orphaned_mcp_processes(
            cleanup=boom, write_warning=warnings.append
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("pre-launch MCP cleanup failed", warnings[0])
        self.assertIn("boom", warnings[0])

    def test_cleanup_non_dict_result_is_tolerated(self) -> None:
        warnings: list[str] = []
        cleanup_team_worker_launch_orphaned_mcp_processes(
            cleanup=lambda: None,  # type: ignore[arg-type,return-value]
            write_warning=warnings.append,
        )
        self.assertEqual(warnings, [])


# --- resolve_worker_launch_args_from_env --------------------------------


class TestResolveWorkerLaunchArgsFromEnv(unittest.TestCase):
    def test_empty_env_uses_fallback_for_executor(self) -> None:
        args = resolve_worker_launch_args_from_env(env={}, agent_type="executor")
        self.assertEqual(args, ["--model", "gpt-5.5"])

    def test_empty_env_for_low_complexity_agent(self) -> None:
        args = resolve_worker_launch_args_from_env(env={}, agent_type="explore-low")
        self.assertEqual(args, ["--model", "gpt-5.3-codex-spark"])

    def test_unknown_agent_falls_back_to_no_model(self) -> None:
        # Unknown agent type → no fallback model, no env override → empty argv.
        args = resolve_worker_launch_args_from_env(env={}, agent_type="mystery")
        self.assertEqual(args, [])

    def test_omx_default_frontier_model_overrides_hardcoded_default(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={"OMX_DEFAULT_FRONTIER_MODEL": "claude-opus-4.7"},
            agent_type="executor",
        )
        self.assertEqual(args, ["--model", "claude-opus-4.7"])

    def test_omx_team_worker_launch_args_overrides_default(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={"OMX_TEAM_WORKER_LAUNCH_ARGS": "--model my-model"},
            agent_type="executor",
        )
        self.assertEqual(args, ["--model", "my-model"])

    def test_launch_args_override_takes_precedence_over_env_default(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={
                "OMX_TEAM_WORKER_LAUNCH_ARGS": "--model explicit",
                "OMX_DEFAULT_FRONTIER_MODEL": "frontier-default",
            },
            agent_type="executor",
        )
        self.assertEqual(args, ["--model", "explicit"])

    def test_inherited_leader_model_used_when_env_args_silent(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={}, agent_type="executor", inherited_leader_model="leader-model"
        )
        self.assertEqual(args, ["--model", "leader-model"])

    def test_env_args_beat_inherited_leader_model(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={"OMX_TEAM_WORKER_LAUNCH_ARGS": "--model env-wins"},
            agent_type="executor",
            inherited_leader_model="leader-loses",
        )
        # Both env override --model and inherited --model are in `all_args`,
        # but env model wins selection. The first --model arg in passthrough
        # is parsed away; selected model is appended at end.
        self.assertEqual(args.count("--model"), 1)
        self.assertIn("env-wins", args)
        self.assertNotIn("leader-loses", args)

    def test_inline_model_flag_form(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={"OMX_TEAM_WORKER_LAUNCH_ARGS": "--model=inline"},
            agent_type="executor",
        )
        self.assertEqual(args, ["--model", "inline"])

    def test_bypass_flag_preserved(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={
                "OMX_TEAM_WORKER_LAUNCH_ARGS": (
                    "--dangerously-bypass-approvals-and-sandbox --model x"
                )
            },
            agent_type="executor",
        )
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertIn("--model", args)
        self.assertIn("x", args)

    def test_madmax_alias_collapses_to_canonical_bypass(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={"OMX_TEAM_WORKER_LAUNCH_ARGS": "--madmax"},
            agent_type="executor",
        )
        self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)
        self.assertNotIn("--madmax", args)

    def test_explicit_reasoning_override_preserved(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={
                "OMX_TEAM_WORKER_LAUNCH_ARGS": (
                    '-c model_reasoning_effort="high" --model x'
                )
            },
            agent_type="executor",
        )
        self.assertIn("-c", args)
        # Find the -c entry and ensure next arg is the reasoning override.
        idx = args.index("-c")
        self.assertEqual(args[idx + 1], 'model_reasoning_effort="high"')

    def test_preferred_reasoning_applied_when_no_explicit_override(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={}, agent_type="executor", preferred_reasoning="medium"
        )
        self.assertIn("-c", args)
        idx = args.index("-c")
        self.assertEqual(args[idx + 1], 'model_reasoning_effort="medium"')

    def test_invalid_preferred_reasoning_ignored(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={}, agent_type="executor", preferred_reasoning="bogus"
        )
        self.assertNotIn("-c", args)

    def test_explicit_reasoning_override_beats_preferred(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={"OMX_TEAM_WORKER_LAUNCH_ARGS": ('-c model_reasoning_effort="xhigh"')},
            agent_type="executor",
            preferred_reasoning="low",
        )
        idx = args.index("-c")
        self.assertEqual(args[idx + 1], 'model_reasoning_effort="xhigh"')
        # No second -c block.
        self.assertEqual(args.count("-c"), 1)

    def test_none_env_falls_back_to_os_environ(self) -> None:
        # Just ensure the path does not raise; the actual resolution depends
        # on the test process env. Pass an empty dict for stability of the
        # other tests, then a None-env smoke check here.
        args = resolve_worker_launch_args_from_env(env=None, agent_type="mystery")
        # Unknown agent + (likely) no override → empty argv.
        self.assertIsInstance(args, list)

    def test_spark_legacy_env_var_honoured(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={"OMX_SPARK_MODEL": "legacy-spark"},
            agent_type="explore-low",
        )
        self.assertEqual(args, ["--model", "legacy-spark"])

    def test_new_spark_env_var_wins_over_legacy(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={
                "OMX_DEFAULT_SPARK_MODEL": "new-spark",
                "OMX_SPARK_MODEL": "legacy-spark",
            },
            agent_type="explore-low",
        )
        self.assertEqual(args, ["--model", "new-spark"])

    def test_orphan_model_flag_dropped(self) -> None:
        # `--model` with no following value (and nothing else) → silently dropped.
        args = resolve_worker_launch_args_from_env(
            env={"OMX_TEAM_WORKER_LAUNCH_ARGS": "--model"},
            agent_type="executor",
        )
        # Falls back to the env/agent default model.
        self.assertEqual(args, ["--model", "gpt-5.5"])

    def test_whitespace_only_launch_args_treated_empty(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={"OMX_TEAM_WORKER_LAUNCH_ARGS": "   "},
            agent_type="executor",
        )
        self.assertEqual(args, ["--model", "gpt-5.5"])

    def test_passthrough_args_retained(self) -> None:
        args = resolve_worker_launch_args_from_env(
            env={"OMX_TEAM_WORKER_LAUNCH_ARGS": "--foo bar --model m"},
            agent_type="executor",
        )
        # --foo bar are unknown flags and must survive.
        self.assertIn("--foo", args)
        self.assertIn("bar", args)
        # Selected --model appended at the end.
        self.assertEqual(args[-2:], ["--model", "m"])


if __name__ == "__main__":
    unittest.main()
