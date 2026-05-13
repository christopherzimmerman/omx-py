"""Tests for omx.team.tmux_session — pure helpers + mocked subprocess flow.

Strategy:

* **Pure helpers** (build*Args, sanitize_team_name, escape, hook-name slugs,
  shell-quoting, model-extraction, CLI resolution, send-plan): test the
  pure return values directly without touching the OS.
* **Subprocess-calling helpers** (is_tmux_available, has_current_tmux_client_context,
  list_pane_ids, capture_pane, kill_worker, etc.): patch
  ``omx.team.tmux_session._run_tmux`` (or :func:`subprocess.run` for the
  ``is_tmux_available`` probe) and assert behavior.

We deliberately avoid `time.sleep` in the polling helpers by patching
:func:`omx.team.tmux_session.sleep_fractional_seconds` and
``time.monotonic``/``time.sleep`` where needed.
"""

from __future__ import annotations

import subprocess
import unittest
from typing import Any
from unittest import mock

from omx.team import tmux_session as ts


def _ok(stdout: str = "") -> ts._TmuxResult:
    return ts._TmuxResult(ok=True, stdout=stdout)


def _fail(stderr: str = "boom") -> ts._TmuxResult:
    return ts._TmuxResult(ok=False, stderr=stderr)


# --- Sanitize / hook-name slug ---------------------------------------------


class TestSanitizeTeamName(unittest.TestCase):
    def test_lowercases_and_hyphenates(self):
        self.assertEqual(ts.sanitize_team_name("My Team!"), "my-team")

    def test_collapses_repeated_separators(self):
        self.assertEqual(ts.sanitize_team_name("hello___world"), "hello-world")

    def test_strips_leading_and_trailing_hyphens(self):
        self.assertEqual(ts.sanitize_team_name("---foo---"), "foo")

    def test_truncates_to_30_chars(self):
        long = "a" * 100
        self.assertEqual(len(ts.sanitize_team_name(long)), 30)

    def test_truncation_does_not_leave_trailing_hyphen(self):
        name = ("a" * 29) + "-bbbb"
        result = ts.sanitize_team_name(name)
        self.assertFalse(result.endswith("-"))
        self.assertLessEqual(len(result), 30)

    def test_empty_after_sanitization_raises(self):
        with self.assertRaises(ValueError):
            ts.sanitize_team_name("!!!")
        with self.assertRaises(ValueError):
            ts.sanitize_team_name("")


class TestNormalizeHookToken(unittest.TestCase):
    def test_replaces_special_chars(self):
        self.assertEqual(
            ts._normalize_tmux_hook_token("team:1/foo bar"), "team_1_foo_bar"
        )

    def test_empty_token_falls_back_to_unknown(self):
        self.assertEqual(ts._normalize_tmux_hook_token(""), "unknown")
        self.assertEqual(ts._normalize_tmux_hook_token("###"), "unknown")

    def test_collapses_repeated_underscores(self):
        self.assertEqual(ts._normalize_tmux_hook_token("a___b"), "a_b")


# --- HUD/resize hook builders (pure) ---------------------------------------


class TestHudHookBuilders(unittest.TestCase):
    def test_build_resize_hook_target(self):
        self.assertEqual(ts.build_resize_hook_target("sess", "0"), "sess:0")

    def test_build_resize_hook_name_components(self):
        # TS does not lowercase tokens — preserve casing.
        name = ts.build_resize_hook_name("Team A", "sess", "0", "%42")
        self.assertTrue(name.startswith("omx_resize_"))
        self.assertIn("Team_A", name)
        self.assertIn("sess", name)
        self.assertTrue(name.endswith("_42"))

    def test_build_hud_pane_target_adds_percent_prefix(self):
        self.assertEqual(ts.build_hud_pane_target("42"), "%42")
        self.assertEqual(ts.build_hud_pane_target("%42"), "%42")

    def test_register_resize_hook_args_shape(self):
        args = ts.build_register_resize_hook_args("sess:0", "hook_name", "%42")
        self.assertEqual(args[0], "set-hook")
        self.assertIn("-t", args)
        self.assertIn("sess:0", args)
        # Slot is "client-resized[<idx>]"
        slot = next(a for a in args if a.startswith("client-resized["))
        self.assertTrue(slot.endswith("]"))
        # Last arg is the run-shell wrapper
        self.assertTrue(args[-1].startswith("run-shell -b "))

    def test_unregister_resize_hook_args_shape(self):
        args = ts.build_unregister_resize_hook_args("sess:0", "hook_name")
        self.assertEqual(args[:3], ["set-hook", "-u", "-t"])
        self.assertEqual(args[3], "sess:0")

    def test_client_attached_hook_name_distinct_from_resize(self):
        resize = ts.build_resize_hook_name("t", "s", "0", "%1")
        attached = ts.build_client_attached_reconcile_hook_name("t", "s", "0", "%1")
        self.assertNotEqual(resize, attached)
        self.assertTrue(attached.startswith("omx_attached_"))

    def test_register_client_attached_args_uses_client_attached_slot(self):
        args = ts.build_register_client_attached_reconcile_args("sess:0", "hook", "%1")
        slot = next(a for a in args if a.startswith("client-attached["))
        self.assertTrue(slot.endswith("]"))

    def test_schedule_delayed_hud_resize_uses_sleep(self):
        args = ts.build_schedule_delayed_hud_resize_args("%42", delay_seconds=5)
        self.assertEqual(args[0], "run-shell")
        self.assertEqual(args[1], "-b")
        self.assertIn("sleep 5", args[2])

    def test_schedule_delayed_clamps_invalid_delay(self):
        # Non-finite / non-positive delays should fall back to default.
        args = ts.build_schedule_delayed_hud_resize_args("%42", delay_seconds=-3)
        self.assertIn(f"sleep {ts.HUD_RESIZE_RECONCILE_DELAY_SECONDS}", args[2])

    def test_reconcile_hud_resize_args(self):
        args = ts.build_reconcile_hud_resize_args("%42")
        self.assertEqual(args[0], "run-shell")
        self.assertIn("resize-pane", args[1])

    def test_unregister_resize_hook_invokes_tmux(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok()) as run:
            self.assertTrue(ts.unregister_resize_hook("sess:0", "hook"))
            run.assert_called_once()
            self.assertEqual(run.call_args.args[0][:3], ["set-hook", "-u", "-t"])


# --- Sleep helpers ----------------------------------------------------------


class TestSleepFractional(unittest.TestCase):
    def test_zero_and_negative_skip(self):
        called = []
        ts.sleep_fractional_seconds(0, sleep_impl=lambda ms: called.append(ms))
        ts.sleep_fractional_seconds(-1, sleep_impl=lambda ms: called.append(ms))
        self.assertEqual(called, [])

    def test_caps_at_max(self):
        called: list[int] = []
        ts.sleep_fractional_seconds(120, sleep_impl=lambda ms: called.append(ms))
        self.assertEqual(called, [ts.MAX_FRACTIONAL_SLEEP_MS])

    def test_passes_milliseconds_to_impl(self):
        called: list[int] = []
        ts.sleep_fractional_seconds(0.25, sleep_impl=lambda ms: called.append(ms))
        self.assertEqual(called, [250])


# --- Platform detection ------------------------------------------------------


class TestPlatformDetection(unittest.TestCase):
    def test_is_msys_or_git_bash_non_windows(self):
        self.assertFalse(
            ts.is_msys_or_git_bash(env={"MSYSTEM": "MINGW64"}, platform="linux")
        )

    def test_is_msys_or_git_bash_with_msystem(self):
        self.assertTrue(
            ts.is_msys_or_git_bash(env={"MSYSTEM": "MINGW64"}, platform="win32")
        )

    def test_is_msys_or_git_bash_with_ostype(self):
        self.assertTrue(
            ts.is_msys_or_git_bash(env={"OSTYPE": "msys"}, platform="win32")
        )

    def test_is_msys_or_git_bash_no_markers(self):
        self.assertFalse(ts.is_msys_or_git_bash(env={}, platform="win32"))

    def test_fallback_msys_path_translation(self):
        self.assertEqual(
            ts._fallback_msys_path_translation("C:\\Users\\foo"),
            "/c/Users/foo",
        )
        self.assertEqual(
            ts._fallback_msys_path_translation("/already/posix"),
            "/already/posix",
        )

    def test_translate_path_for_msys_returns_input_on_non_msys(self):
        out = ts.translate_path_for_msys("C:\\foo", env={}, platform="linux")
        self.assertEqual(out, "C:\\foo")

    def test_translate_path_for_msys_uses_cygpath(self):
        def fake_spawn(argv):
            return subprocess.CompletedProcess(argv, 0, "/c/cyg/out\n", "")

        out = ts.translate_path_for_msys(
            "C:\\cyg\\out",
            env={"MSYSTEM": "MINGW64"},
            platform="win32",
            spawn_impl=fake_spawn,
        )
        self.assertEqual(out, "/c/cyg/out")

    def test_translate_path_for_msys_fallback_on_cygpath_failure(self):
        def fake_spawn(argv):
            return subprocess.CompletedProcess(argv, 1, "", "missing")

        out = ts.translate_path_for_msys(
            "D:\\fall\\back",
            env={"MSYSTEM": "MINGW64"},
            platform="win32",
            spawn_impl=fake_spawn,
        )
        self.assertEqual(out, "/d/fall/back")


# --- Shell quoting ----------------------------------------------------------


class TestShellQuoting(unittest.TestCase):
    def test_single_quote_simple(self):
        self.assertEqual(ts._shell_quote_single("foo"), "'foo'")

    def test_single_quote_with_apostrophe(self):
        self.assertEqual(ts._shell_quote_single("it's"), "'it'\\''s'")

    def test_powershell_quote(self):
        self.assertEqual(ts._quote_powershell_arg("o'reilly"), "'o''reilly'")

    def test_encode_powershell_command_roundtrip(self):
        encoded = ts._encode_powershell_command("echo hi")
        import base64

        self.assertEqual(base64.b64decode(encoded).decode("utf-16-le"), "echo hi")


# --- Worker CLI resolution --------------------------------------------------


class TestNormalizeTeamWorkerCliMode(unittest.TestCase):
    def test_default_auto(self):
        self.assertEqual(ts._normalize_team_worker_cli_mode(None), "auto")
        self.assertEqual(ts._normalize_team_worker_cli_mode(""), "auto")
        self.assertEqual(ts._normalize_team_worker_cli_mode(" AUTO "), "auto")

    def test_valid_values(self):
        for v in ("codex", "claude", "gemini"):
            self.assertEqual(ts._normalize_team_worker_cli_mode(v), v)

    def test_invalid_raises(self):
        with self.assertRaises(ValueError):
            ts._normalize_team_worker_cli_mode("openai")


class TestResolveTeamWorkerCli(unittest.TestCase):
    def test_env_force_overrides_args(self):
        self.assertEqual(
            ts.resolve_team_worker_cli([], env={"OMX_TEAM_WORKER_CLI": "claude"}),
            "claude",
        )

    def test_auto_picks_codex_default(self):
        self.assertEqual(ts.resolve_team_worker_cli([], env={}), "codex")

    def test_auto_picks_claude_from_model(self):
        self.assertEqual(
            ts.resolve_team_worker_cli(["--model", "claude-3.5"], env={}),
            "claude",
        )

    def test_auto_picks_gemini_from_model(self):
        self.assertEqual(
            ts.resolve_team_worker_cli(["--model=gemini-1.5-pro"], env={}),
            "gemini",
        )

    def test_resolve_launch_mode_default_interactive(self):
        self.assertEqual(ts.resolve_team_worker_launch_mode({}), "interactive")

    def test_resolve_launch_mode_prompt(self):
        self.assertEqual(
            ts.resolve_team_worker_launch_mode(
                {"OMX_TEAM_WORKER_LAUNCH_MODE": "prompt"}
            ),
            "prompt",
        )

    def test_resolve_launch_mode_invalid_raises(self):
        with self.assertRaises(ValueError):
            ts.resolve_team_worker_launch_mode({"OMX_TEAM_WORKER_LAUNCH_MODE": "wat"})


class TestResolveTeamWorkerCliPlan(unittest.TestCase):
    def test_default_uniform_plan(self):
        plan = ts.resolve_team_worker_cli_plan(3, [], env={})
        self.assertEqual(plan, ["codex", "codex", "codex"])

    def test_env_uniform_override(self):
        plan = ts.resolve_team_worker_cli_plan(
            2, [], env={"OMX_TEAM_WORKER_CLI": "claude"}
        )
        self.assertEqual(plan, ["claude", "claude"])

    def test_map_single_entry_broadcasts(self):
        plan = ts.resolve_team_worker_cli_plan(
            2, [], env={"OMX_TEAM_WORKER_CLI_MAP": "gemini"}
        )
        self.assertEqual(plan, ["gemini", "gemini"])

    def test_map_per_worker_values(self):
        plan = ts.resolve_team_worker_cli_plan(
            3, [], env={"OMX_TEAM_WORKER_CLI_MAP": "codex,claude,gemini"}
        )
        self.assertEqual(plan, ["codex", "claude", "gemini"])

    def test_map_auto_resolves_via_args(self):
        plan = ts.resolve_team_worker_cli_plan(
            2,
            ["--model", "claude-sonnet"],
            env={"OMX_TEAM_WORKER_CLI_MAP": "auto,auto"},
        )
        self.assertEqual(plan, ["claude", "claude"])

    def test_map_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            ts.resolve_team_worker_cli_plan(
                3, [], env={"OMX_TEAM_WORKER_CLI_MAP": "codex,claude"}
            )

    def test_map_empty_entry_raises(self):
        with self.assertRaises(ValueError):
            ts.resolve_team_worker_cli_plan(
                3, [], env={"OMX_TEAM_WORKER_CLI_MAP": "codex,,gemini"}
            )

    def test_invalid_worker_count_raises(self):
        with self.assertRaises(ValueError):
            ts.resolve_team_worker_cli_plan(0, [], env={})


class TestTranslateLaunchArgsForCli(unittest.TestCase):
    def test_codex_passthrough_copies_args(self):
        original = ["--model", "gpt-5"]
        out = ts.translate_worker_launch_args_for_cli("codex", original)
        self.assertEqual(out, original)
        self.assertIsNot(out, original)

    def test_claude_drops_other_args_and_adds_skip_flag(self):
        out = ts.translate_worker_launch_args_for_cli("claude", ["--model", "gpt-5"])
        self.assertEqual(out, [ts.CLAUDE_SKIP_PERMISSIONS_FLAG])

    def test_gemini_with_prompt_and_model(self):
        out = ts.translate_worker_launch_args_for_cli(
            "gemini",
            ["--model", "gemini-1.5"],
            initial_prompt="hello",
        )
        self.assertIn(ts.GEMINI_APPROVAL_MODE_FLAG, out)
        self.assertIn(ts.GEMINI_APPROVAL_MODE_YOLO, out)
        self.assertIn(ts.GEMINI_PROMPT_INTERACTIVE_FLAG, out)
        self.assertIn("hello", out)
        self.assertIn("gemini-1.5", out)

    def test_gemini_without_gemini_model_skips_model_flag(self):
        out = ts.translate_worker_launch_args_for_cli(
            "gemini",
            ["--model", "claude-3"],
        )
        self.assertNotIn("claude-3", out)


class TestExtractModelOverride(unittest.TestCase):
    def test_space_separated(self):
        self.assertEqual(ts._extract_model_override(["--model", "foo"]), "foo")

    def test_equals_form(self):
        self.assertEqual(ts._extract_model_override(["--model=bar"]), "bar")

    def test_orphan_returns_none(self):
        self.assertIsNone(ts._extract_model_override(["--model"]))

    def test_skips_flag_following_model(self):
        self.assertIsNone(ts._extract_model_override(["--model", "--other"]))

    def test_last_wins(self):
        self.assertEqual(
            ts._extract_model_override(["--model", "a", "--model", "b"]),
            "b",
        )


class TestAssertCliBinaryAvailable(unittest.TestCase):
    def test_passes_when_exists(self):
        ts.assert_team_worker_cli_binary_available("codex", exists_impl=lambda _b: True)

    def test_raises_with_helpful_message(self):
        with self.assertRaises(RuntimeError) as ctx:
            ts.assert_team_worker_cli_binary_available(
                "codex", exists_impl=lambda _b: False
            )
        self.assertIn("codex", str(ctx.exception))
        self.assertIn("OMX_TEAM_WORKER_CLI", str(ctx.exception))


class TestResolveWorkerCliForSend(unittest.TestCase):
    def test_explicit_wins(self):
        self.assertEqual(
            ts.resolve_worker_cli_for_send(1, worker_cli="gemini", env={}),
            "gemini",
        )

    def test_falls_through_to_env(self):
        self.assertEqual(
            ts.resolve_worker_cli_for_send(1, env={"OMX_TEAM_WORKER_CLI": "claude"}),
            "claude",
        )

    def test_uses_map_entry(self):
        self.assertEqual(
            ts.resolve_worker_cli_for_send(
                2,
                env={"OMX_TEAM_WORKER_CLI_MAP": "codex,gemini"},
            ),
            "gemini",
        )

    def test_map_auto_uses_launch_args(self):
        self.assertEqual(
            ts.resolve_worker_cli_for_send(
                1,
                launch_args=["--model=claude-3"],
                env={"OMX_TEAM_WORKER_CLI_MAP": "auto"},
            ),
            "claude",
        )


# --- Submit plan / adaptive retry -------------------------------------------


class TestBuildWorkerSubmitPlan(unittest.TestCase):
    def test_interrupt_strategy_marks_interrupt(self):
        plan = ts.build_worker_submit_plan(
            "interrupt", "codex", pane_busy_at_start=False, allow_adaptive_retry=True
        )
        self.assertTrue(plan.should_interrupt)
        self.assertFalse(plan.queue_first_round)
        self.assertTrue(plan.allow_adaptive_retry)

    def test_queue_first_round_only_for_codex(self):
        plan = ts.build_worker_submit_plan(
            "queue", "claude", pane_busy_at_start=True, allow_adaptive_retry=True
        )
        self.assertFalse(plan.queue_first_round)
        self.assertEqual(plan.submit_key_presses_per_round, 1)
        self.assertFalse(plan.allow_adaptive_retry)

    def test_auto_busy_codex_queues(self):
        plan = ts.build_worker_submit_plan(
            "auto", "codex", pane_busy_at_start=True, allow_adaptive_retry=True
        )
        self.assertTrue(plan.queue_first_round)
        self.assertEqual(plan.submit_key_presses_per_round, 2)
        self.assertTrue(plan.allow_adaptive_retry)

    def test_auto_idle_codex_no_queue(self):
        plan = ts.build_worker_submit_plan(
            "auto", "codex", pane_busy_at_start=False, allow_adaptive_retry=True
        )
        self.assertFalse(plan.queue_first_round)


class TestShouldAttemptAdaptiveRetry(unittest.TestCase):
    def test_disabled_when_allow_false(self):
        self.assertFalse(
            ts.should_attempt_adaptive_retry("auto", True, False, "> ", "trigger")
        )

    def test_disabled_when_strategy_not_auto(self):
        self.assertFalse(
            ts.should_attempt_adaptive_retry(
                "queue", True, True, "trigger\n> ", "trigger"
            )
        )

    def test_disabled_when_not_busy(self):
        self.assertFalse(
            ts.should_attempt_adaptive_retry(
                "auto", False, True, "trigger\n> ", "trigger"
            )
        )

    def test_disabled_when_text_missing(self):
        self.assertFalse(
            ts.should_attempt_adaptive_retry(
                "auto", True, True, "no match here\n> ", "trigger"
            )
        )

    def test_disabled_when_active_task(self):
        self.assertFalse(
            ts.should_attempt_adaptive_retry(
                "auto", True, True, "trigger\nThinking...\n", "trigger"
            )
        )

    def test_returns_true_when_all_conditions_met(self):
        self.assertTrue(
            ts.should_attempt_adaptive_retry(
                "auto", True, True, "trigger\n> ", "trigger"
            )
        )


class TestAssertWorkerTriggerText(unittest.TestCase):
    def test_too_long(self):
        with self.assertRaises(ValueError):
            ts._assert_worker_trigger_text("x" * 200)

    def test_empty(self):
        with self.assertRaises(ValueError):
            ts._assert_worker_trigger_text("   ")

    def test_injection_marker_rejected(self):
        with self.assertRaises(ValueError):
            ts._assert_worker_trigger_text(f"hi {ts.INJECTION_MARKER}")

    def test_valid(self):
        ts._assert_worker_trigger_text("hello")


class TestSendToWorkerStdin(unittest.TestCase):
    def test_writes_trailing_newline(self):
        class FakeStdin:
            closed = False

            def __init__(self) -> None:
                self.buf = ""

            def write(self, s: str) -> None:
                self.buf += s

        fake = FakeStdin()
        ts.send_to_worker_stdin(fake, "hello")
        self.assertEqual(fake.buf, "hello\n")

    def test_rejects_closed_stdin(self):
        class Closed:
            closed = True

            def write(self, _s: str) -> None:
                pass

        with self.assertRaises(RuntimeError):
            ts.send_to_worker_stdin(Closed(), "hi")

    def test_rejects_none_stdin(self):
        with self.assertRaises(RuntimeError):
            ts.send_to_worker_stdin(None, "hi")

    def test_validates_trigger_text(self):
        class FakeStdin:
            closed = False

            def write(self, _s: str) -> None:
                pass

        with self.assertRaises(ValueError):
            ts.send_to_worker_stdin(FakeStdin(), "")


# --- Normalize tmux capture -------------------------------------------------


class TestNormalizeTmuxCapture(unittest.TestCase):
    def test_strips_ansi_escapes(self):
        self.assertEqual(ts.normalize_tmux_capture("\x1b[1mhi\x1b[0m"), "hi")

    def test_strips_trailing_empty_lines(self):
        self.assertEqual(ts.normalize_tmux_capture("hi\n\n\n"), "hi")

    def test_non_string_returns_empty(self):
        self.assertEqual(ts.normalize_tmux_capture(None), "")  # type: ignore[arg-type]


class TestPaneHasQueuedCodexSubmission(unittest.TestCase):
    def test_detects_tool_call_queue_warning(self):
        self.assertTrue(
            ts._pane_has_queued_codex_submission(
                "messages to be submitted after next tool call"
            )
        )

    def test_detects_esc_interrupt_warning(self):
        self.assertTrue(
            ts._pane_has_queued_codex_submission(
                "press esc to interrupt and send immediately"
            )
        )

    def test_empty_returns_false(self):
        self.assertFalse(ts._pane_has_queued_codex_submission(""))
        self.assertFalse(ts._pane_has_queued_codex_submission(None))


# --- Pane target / list_pane_ids -------------------------------------------


class TestPaneTarget(unittest.TestCase):
    def test_uses_pane_id_when_present(self):
        self.assertEqual(ts._pane_target("sess", 1, "%5"), "%5")

    def test_session_with_window_uses_dot(self):
        self.assertEqual(ts._pane_target("sess:0", 2), "sess:0.2")

    def test_session_without_window_uses_colon(self):
        self.assertEqual(ts._pane_target("sess", 3), "sess:3")


class TestListPaneIds(unittest.TestCase):
    def test_filters_non_percent_lines(self):
        stdout = "%1\tcodex\tsh\nbad\tx\ty\n%2\tcodex\tsh\n"
        with mock.patch.object(ts, "_run_tmux", return_value=_ok(stdout)):
            self.assertEqual(ts.list_pane_ids("sess:0"), ["%1", "%2"])

    def test_empty_on_failure(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_fail()):
            self.assertEqual(ts.list_pane_ids("sess:0"), [])


class TestChooseTeamLeaderPaneId(unittest.TestCase):
    def test_returns_preferred_when_not_hud(self):
        panes = [
            ts.TmuxPaneInfo("%1", "node", "node app.js"),
            ts.TmuxPaneInfo("%2", "codex", "codex"),
        ]
        self.assertEqual(ts.choose_team_leader_pane_id(panes, "%2"), "%2")

    def test_skips_hud_watch_pane(self):
        panes = [
            ts.TmuxPaneInfo("%1", "node", "omx hud --watch"),
            ts.TmuxPaneInfo("%2", "codex", "codex"),
        ]
        self.assertEqual(ts.choose_team_leader_pane_id(panes, "%1"), "%2")

    def test_falls_back_to_preferred_when_only_hud_exists(self):
        panes = [ts.TmuxPaneInfo("%1", "node", "omx hud --watch")]
        self.assertEqual(ts.choose_team_leader_pane_id(panes, "%9"), "%9")


# --- Trust / bypass / update prompts ---------------------------------------


class TestPromptDetection(unittest.TestCase):
    def test_trust_prompt(self):
        self.assertTrue(
            ts._pane_has_trust_prompt(
                "Do you trust the contents of this directory?\nYes, continue\nNo, quit\n"
            )
        )
        self.assertFalse(ts._pane_has_trust_prompt("Hi there\n> "))

    def test_lenient_bypass_prompt(self):
        self.assertTrue(
            ts._pane_has_bypass_prompt("Bypass Permissions mode\nYes, I accept\n")
        )
        self.assertFalse(ts._pane_has_bypass_prompt("> "))

    def test_strict_bypass_requires_all_markers(self):
        partial = "Bypass Permissions mode\nYes, I accept\n"
        full = (
            "Bypass Permissions mode\n1. No, exit\n2. Yes, I accept\nEnter to confirm\n"
        )
        self.assertFalse(ts._pane_has_strict_bypass_prompt(partial))
        self.assertTrue(ts._pane_has_strict_bypass_prompt(full))

    def test_update_prompt(self):
        self.assertTrue(ts._pane_has_update_prompt("update available now"))
        self.assertFalse(ts._pane_has_update_prompt("nothing to see"))


# --- Worker liveness -------------------------------------------------------


class TestWorkerLiveness(unittest.TestCase):
    def test_get_worker_pane_pid_parses_first_line(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok("4242\n")):
            self.assertEqual(ts.get_worker_pane_pid("sess", 1, "%1"), 4242)

    def test_get_worker_pane_pid_returns_none_on_failure(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_fail()):
            self.assertIsNone(ts.get_worker_pane_pid("sess", 1, "%1"))

    def test_get_worker_pane_pid_returns_none_on_invalid(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok("not a pid\n")):
            self.assertIsNone(ts.get_worker_pane_pid("sess", 1, "%1"))

    def test_is_worker_alive_false_when_dead_flag(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok("1 1234\n")):
            self.assertFalse(ts.is_worker_alive("sess", 1, "%1"))

    def test_is_worker_alive_uses_pid_check(self):
        with (
            mock.patch.object(ts, "_run_tmux", return_value=_ok("0 1234\n")),
            mock.patch.object(ts, "_pid_is_alive", return_value=True),
        ):
            self.assertTrue(ts.is_worker_alive("sess", 1, "%1"))

    def test_is_worker_alive_dead_pid(self):
        with (
            mock.patch.object(ts, "_run_tmux", return_value=_ok("0 1234\n")),
            mock.patch.object(ts, "_pid_is_alive", return_value=False),
        ):
            self.assertFalse(ts.is_worker_alive("sess", 1, "%1"))

    def test_is_worker_pane_open(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok("0\n")):
            self.assertTrue(ts.is_worker_pane_open("sess", 1, "%1"))
        with mock.patch.object(ts, "_run_tmux", return_value=_ok("1\n")):
            self.assertFalse(ts.is_worker_pane_open("sess", 1, "%1"))
        with mock.patch.object(ts, "_run_tmux", return_value=_fail()):
            self.assertFalse(ts.is_worker_pane_open("sess", 1, "%1"))


class TestKillWorker(unittest.TestCase):
    def test_skips_when_target_is_leader(self):
        with mock.patch.object(ts, "_run_tmux") as run:
            ts.kill_worker("sess", 1, "%1", leader_pane_id="%1")
            run.assert_not_called()

    def test_escalates_to_kill_pane_when_alive(self):
        with (
            mock.patch.object(ts, "_run_tmux", return_value=_ok()) as run,
            mock.patch.object(ts, "is_worker_alive", return_value=True),
            mock.patch("time.sleep"),
        ):
            ts.kill_worker("sess", 1, "%1")
        # Expect C-c, C-d, kill-pane all sent.
        cmds = [call.args[0][0] for call in run.call_args_list]
        self.assertIn("send-keys", cmds)
        self.assertIn("kill-pane", cmds)

    def test_kill_worker_by_pane_id_skips_leader(self):
        with mock.patch.object(ts, "_run_tmux") as run:
            ts.kill_worker_by_pane_id("%1", leader_pane_id="%1")
            run.assert_not_called()

    def test_kill_worker_by_pane_id_rejects_non_percent(self):
        with mock.patch.object(ts, "_run_tmux") as run:
            ts.kill_worker_by_pane_id("sess:0.1")
            run.assert_not_called()

    def test_kill_worker_by_pane_id_kills_when_valid(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok()) as run:
            ts.kill_worker_by_pane_id("%5")
            run.assert_called_once_with(["kill-pane", "-t", "%5"])


# --- Tmux availability / context --------------------------------------------


class TestTmuxAvailable(unittest.TestCase):
    def test_returns_true_when_zero_exit(self):
        completed: Any = subprocess.CompletedProcess(
            ["tmux", "-V"], 0, "tmux 3.4\n", ""
        )
        with mock.patch("subprocess.run", return_value=completed):
            self.assertTrue(ts.is_tmux_available())

    def test_returns_false_when_missing(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            self.assertFalse(ts.is_tmux_available())

    def test_returns_false_when_nonzero_exit(self):
        completed: Any = subprocess.CompletedProcess(["tmux", "-V"], 1, "", "err")
        with mock.patch("subprocess.run", return_value=completed):
            self.assertFalse(ts.is_tmux_available())


class TestHasCurrentTmuxClientContext(unittest.TestCase):
    def test_returns_true_with_valid_context(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok("sess:0 %1")):
            self.assertTrue(ts.has_current_tmux_client_context())

    def test_returns_false_when_pane_id_missing_percent(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok("sess:0 1")):
            self.assertFalse(ts.has_current_tmux_client_context())

    def test_returns_false_on_tmux_failure(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_fail()):
            self.assertFalse(ts.has_current_tmux_client_context())


# --- Capture pane ----------------------------------------------------------


class TestCapturePane(unittest.TestCase):
    def test_returns_stdout_on_success(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok("hi")):
            self.assertEqual(ts.capture_pane("%1"), "hi")

    def test_returns_empty_on_failure(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_fail()):
            self.assertEqual(ts.capture_pane("%1"), "")

    def test_visible_capture_uses_no_scrollback(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok("vis")) as run:
            self.assertEqual(ts.capture_visible_pane("%1"), "vis")
            args = run.call_args.args[0]
            self.assertNotIn("-S", args)


# --- Session lifecycle helpers ---------------------------------------------


class TestSessionLifecycleHelpers(unittest.TestCase):
    def test_kill_team_session_calls_tmux(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok()) as run:
            ts.kill_team_session("omx-test")
            run.assert_called_once_with(["kill-session", "-t", "omx-test"])

    def test_destroy_session_tolerates_errors(self):
        with mock.patch.object(ts, "_run_tmux", side_effect=RuntimeError("nope")):
            # Must not raise.
            ts.destroy_team_session("missing")

    def test_is_session_alive(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok()):
            self.assertTrue(ts.is_session_alive("sess"))
        with mock.patch.object(ts, "_run_tmux", return_value=_fail()):
            self.assertFalse(ts.is_session_alive("sess"))

    def test_list_team_sessions_strips_window_suffix(self):
        with mock.patch.object(
            ts, "_run_tmux", return_value=_ok("omx-a\nomx-b:1\n\nomx-c\n")
        ):
            self.assertEqual(ts.list_team_sessions(), ["omx-a", "omx-b", "omx-c"])

    def test_list_team_sessions_empty_on_failure(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_fail()):
            self.assertEqual(ts.list_team_sessions(), [])


class TestNotifyLeaderStatus(unittest.TestCase):
    def test_returns_false_when_tmux_missing(self):
        with mock.patch.object(ts, "is_tmux_available", return_value=False):
            self.assertFalse(ts.notify_leader_status("sess", "hi"))

    def test_caps_long_message(self):
        captured: dict[str, list[str]] = {}

        def fake_run(args: list[str]) -> ts._TmuxResult:
            captured["args"] = args
            return _ok()

        with (
            mock.patch.object(ts, "is_tmux_available", return_value=True),
            mock.patch.object(ts, "_run_tmux", side_effect=fake_run),
        ):
            ts.notify_leader_status("sess", "x" * 250)
        msg = captured["args"][-1]
        self.assertEqual(len(msg), 180)
        self.assertTrue(msg.endswith("..."))

    def test_empty_message_returns_false(self):
        with mock.patch.object(ts, "is_tmux_available", return_value=True):
            self.assertFalse(ts.notify_leader_status("sess", "   "))


# --- Enable mouse / mitigate underline -------------------------------------


class TestEnableMouseScrolling(unittest.TestCase):
    def test_returns_false_when_set_option_fails(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_fail()):
            self.assertFalse(ts.enable_mouse_scrolling("sess"))

    def test_returns_true_on_success(self):
        with mock.patch.object(ts, "_run_tmux", return_value=_ok()):
            self.assertTrue(ts.enable_mouse_scrolling("sess"))


class TestMitigateCopyModeUnderline(unittest.TestCase):
    def test_empty_target_returns_false(self):
        self.assertFalse(ts.mitigate_copy_mode_underline_artifacts("  "))

    def test_append_no_underline_flags(self):
        result = ts._append_no_underline_style_flags("fg=red,bg=black")
        for flag in ts.TMUX_NO_UNDERLINE_STYLE_FLAGS:
            self.assertIn(flag, result)
        # Already-present flag should not be duplicated.
        again = ts._append_no_underline_style_flags(result)
        for flag in ts.TMUX_NO_UNDERLINE_STYLE_FLAGS:
            self.assertEqual(again.split(",").count(flag), 1)


# --- build_worker_process_launch_spec / startup command --------------------


class TestBuildWorkerProcessLaunchSpec(unittest.TestCase):
    def test_codex_includes_bypass_when_role_allows(self):
        spec = ts.build_worker_process_launch_spec(
            "team-a",
            1,
            launch_args=[],
            cwd="/tmp",
            extra_env={},
            worker_cli_override="codex",
        )
        self.assertEqual(spec.worker_cli, "codex")
        self.assertIn(ts.CODEX_BYPASS_FLAG, spec.args)
        self.assertIn("OMX_TEAM_WORKER", spec.env)
        self.assertEqual(spec.env["OMX_TEAM_WORKER"], "team-a/worker-1")

    def test_claude_only_keeps_skip_flag(self):
        spec = ts.build_worker_process_launch_spec(
            "team-a",
            2,
            launch_args=["--model", "claude-3"],
            cwd="/tmp",
            extra_env={},
            worker_cli_override="claude",
        )
        self.assertEqual(spec.worker_cli, "claude")
        self.assertEqual(spec.args, [ts.CLAUDE_SKIP_PERMISSIONS_FLAG])

    def test_extra_env_propagates(self):
        spec = ts.build_worker_process_launch_spec(
            "team-a",
            1,
            extra_env={"FOO": "bar", "EMPTY": ""},
            worker_cli_override="codex",
        )
        self.assertEqual(spec.env.get("FOO"), "bar")
        self.assertNotIn("EMPTY", spec.env)


class TestBuildWorkerStartupCommand(unittest.TestCase):
    def test_posix_returns_env_shell_command(self):
        # Force the non-Windows branch.
        with (
            mock.patch.object(ts, "is_native_windows", return_value=False),
            mock.patch.dict("os.environ", {"SHELL": "/bin/sh"}, clear=False),
        ):
            cmd = ts.build_worker_startup_command(
                "team-a",
                1,
                launch_args=[],
                cwd="/tmp",
                extra_env={},
                worker_cli_override="codex",
            )
        self.assertTrue(cmd.startswith("env "))
        self.assertIn("OMX_TEAM_WORKER=team-a/worker-1", cmd)
        self.assertIn("-c", cmd)


# --- Validate `_run_tmux` failure path -------------------------------------


class TestRunTmuxFailurePaths(unittest.TestCase):
    def test_missing_binary_returns_failure(self):
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("missing")):
            result = ts._run_tmux(["-V"])
            self.assertFalse(result.ok)
            self.assertIn("missing", result.stderr)

    def test_non_zero_exit_returns_failure(self):
        completed = subprocess.CompletedProcess(["tmux"], 2, "", "explode")
        with mock.patch("subprocess.run", return_value=completed):
            result = ts._run_tmux(["bogus"])
            self.assertFalse(result.ok)
            self.assertIn("explode", result.stderr)

    def test_success_strips_stdout(self):
        completed = subprocess.CompletedProcess(["tmux"], 0, "hello\n", "")
        with mock.patch("subprocess.run", return_value=completed):
            result = ts._run_tmux(["bogus"])
            self.assertTrue(result.ok)
            self.assertEqual(result.stdout, "hello")


# --- Dismiss trust prompt helper --------------------------------------------


class TestDismissTrustPromptIfPresent(unittest.TestCase):
    def test_returns_false_when_env_opt_out(self):
        with mock.patch.dict("os.environ", {"OMX_TEAM_AUTO_TRUST": "0"}, clear=False):
            self.assertFalse(ts.dismiss_trust_prompt_if_present("sess", 1, "%1"))

    def test_returns_false_when_tmux_missing(self):
        with (
            mock.patch.dict("os.environ", {}, clear=False),
            mock.patch.object(ts, "is_tmux_available", return_value=False),
        ):
            self.assertFalse(ts.dismiss_trust_prompt_if_present("sess", 1, "%1"))

    def test_returns_false_when_no_prompt(self):
        with (
            mock.patch.dict("os.environ", {"OMX_TEAM_AUTO_TRUST": "1"}, clear=False),
            mock.patch.object(ts, "is_tmux_available", return_value=True),
            mock.patch.object(ts, "_run_tmux", return_value=_ok("> ")),
        ):
            self.assertFalse(ts.dismiss_trust_prompt_if_present("sess", 1, "%1"))

    def test_dismisses_when_prompt_visible(self):
        capture = (
            "Do you trust the contents of this directory?\nYes, continue\nNo, quit\n"
        )
        results = [_ok(capture), _ok(), _ok()]
        with (
            mock.patch.dict("os.environ", {"OMX_TEAM_AUTO_TRUST": "1"}, clear=False),
            mock.patch.object(ts, "is_tmux_available", return_value=True),
            mock.patch.object(ts, "_run_tmux", side_effect=results) as run,
            mock.patch.object(ts, "sleep_fractional_seconds"),
        ):
            self.assertTrue(ts.dismiss_trust_prompt_if_present("sess", 1, "%1"))
        # Three tmux calls total: capture-pane + 2 send-keys
        self.assertEqual(run.call_count, 3)


# --- restore_standalone_hud_pane -------------------------------------------


class TestRestoreStandaloneHudPane(unittest.TestCase):
    def test_returns_none_for_invalid_leader(self):
        self.assertIsNone(ts.restore_standalone_hud_pane(None, "/tmp"))
        self.assertIsNone(ts.restore_standalone_hud_pane("", "/tmp"))
        self.assertIsNone(ts.restore_standalone_hud_pane("sess:0.1", "/tmp"))

    def test_returns_none_when_no_hud_command(self):
        with mock.patch.dict("os.environ", {}, clear=False):
            self.assertIsNone(ts.restore_standalone_hud_pane("%5", "/tmp"))

    def test_success_path_returns_new_pane_id(self):
        with (
            mock.patch.dict("os.environ", {"OMX_HUD_COMMAND": "echo hud"}, clear=False),
            mock.patch.object(
                ts,
                "_run_tmux",
                side_effect=[
                    _ok("%99"),  # split-window
                    _ok(),  # schedule-delayed
                    _ok(),  # reconcile
                    _ok(),  # select-pane
                ],
            ),
            mock.patch.object(ts, "is_native_windows", return_value=False),
        ):
            self.assertEqual(ts.restore_standalone_hud_pane("%5", "/tmp"), "%99")

    def test_returns_none_when_split_fails(self):
        with (
            mock.patch.dict("os.environ", {"OMX_HUD_COMMAND": "echo"}, clear=False),
            mock.patch.object(ts, "_run_tmux", return_value=_fail()),
        ):
            self.assertIsNone(ts.restore_standalone_hud_pane("%5", "/tmp"))


# --- create_team_session preflight ------------------------------------------


class TestCreateTeamSessionPreflight(unittest.TestCase):
    def test_raises_when_tmux_unavailable(self):
        with mock.patch.object(ts, "is_tmux_available", return_value=False):
            with self.assertRaises(RuntimeError):
                ts.create_team_session("t", 1, "/tmp")

    def test_raises_on_bad_worker_count(self):
        with (
            mock.patch.object(ts, "is_tmux_available", return_value=True),
        ):
            with self.assertRaises(ValueError):
                ts.create_team_session("t", 0, "/tmp")

    def test_raises_without_client_context(self):
        with (
            mock.patch.object(ts, "is_tmux_available", return_value=True),
            mock.patch.object(
                ts, "has_current_tmux_client_context", return_value=False
            ),
        ):
            with self.assertRaises(RuntimeError):
                ts.create_team_session("t", 1, "/tmp")


# --- has_model_instructions_override / resolve_worker_launch_args ----------


class TestModelInstructionsOverride(unittest.TestCase):
    def test_detects_model_instructions_short_flag(self):
        self.assertTrue(
            ts._has_model_instructions_override(["-c", 'model_instructions_file="/p"'])
        )

    def test_detects_inline_long_flag(self):
        self.assertTrue(
            ts._has_model_instructions_override(
                ['--config=model_instructions_file="/p"']
            )
        )

    def test_returns_false_when_absent(self):
        self.assertFalse(ts._has_model_instructions_override(["--model", "gpt-5"]))


class TestResolveWorkerLaunchArgs(unittest.TestCase):
    def test_appends_bypass_from_argv(self):
        out = ts._resolve_worker_launch_args(
            [],
            cwd="/tmp",
            env={"OMX_BYPASS_DEFAULT_SYSTEM_PROMPT": "0"},
            argv=["codex", ts.CODEX_BYPASS_FLAG],
        )
        self.assertIn(ts.CODEX_BYPASS_FLAG, out)

    def test_default_appends_model_instructions_override(self):
        out = ts._resolve_worker_launch_args([], cwd="/tmp", env={}, argv=["codex"])
        # -c model_instructions_file="..." should be present
        self.assertIn("-c", out)
        joined = " ".join(out)
        self.assertIn("model_instructions_file=", joined)

    def test_opt_out_skips_instructions_override(self):
        out = ts._resolve_worker_launch_args(
            [],
            cwd="/tmp",
            env={"OMX_BYPASS_DEFAULT_SYSTEM_PROMPT": "0"},
            argv=["codex"],
        )
        joined = " ".join(out)
        self.assertNotIn("model_instructions_file=", joined)


# --- Pane stability helper --------------------------------------------------


class TestWaitForPaneToRemainPresent(unittest.TestCase):
    def test_returns_false_for_non_percent_pane(self):
        self.assertFalse(ts._wait_for_pane_to_remain_present("sess:0", "1"))

    def test_returns_true_after_consecutive_seen(self):
        with (
            mock.patch.object(ts, "list_pane_ids", return_value=["%5"]),
            mock.patch.object(ts, "sleep_fractional_seconds"),
        ):
            self.assertTrue(
                ts._wait_for_pane_to_remain_present("sess:0", "%5", timeout_ms=200)
            )

    def test_returns_false_when_pane_missing(self):
        with (
            mock.patch.object(ts, "list_pane_ids", return_value=[]),
            mock.patch.object(ts, "sleep_fractional_seconds"),
        ):
            self.assertFalse(
                ts._wait_for_pane_to_remain_present("sess:0", "%5", timeout_ms=20)
            )


# --- wait_for_worker_ready legacy form -------------------------------------


class TestWaitForWorkerReadyLegacy(unittest.TestCase):
    def test_returns_true_when_ready_quickly(self):
        with (
            mock.patch.object(ts, "capture_pane", return_value="> "),
            mock.patch("time.sleep"),
            mock.patch("time.monotonic", side_effect=[0.0, 0.01, 1.0]),
        ):
            self.assertTrue(ts.wait_for_worker_ready("%1", 30000))

    def test_returns_false_on_timeout(self):
        with (
            mock.patch.object(ts, "capture_pane", return_value="busy"),
            mock.patch("time.sleep"),
            mock.patch("time.monotonic", side_effect=[0.0] + [9999.0] * 5),
        ):
            self.assertFalse(ts.wait_for_worker_ready("%1", 1000))


if __name__ == "__main__":
    unittest.main()
