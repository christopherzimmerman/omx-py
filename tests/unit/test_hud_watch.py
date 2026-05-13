"""Tests for the HUD watch loop, shell escape, and tmux split args (Phase 8)."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from omx.hud.constants import HUD_TMUX_HEIGHT_LINES
from omx.hud.types import HudFlags
from omx.hud.watch import (
    HUD_USAGE,
    HudCommandDeps,
    RunWatchModeDeps,
    build_tmux_split_args,
    hud_command,
    run_watch_mode,
    shell_escape,
    watch_render_loop,
)


# ---------------------------------------------------------------------------
# shell_escape
# ---------------------------------------------------------------------------


class ShellEscapeTests(unittest.TestCase):
    def test_plain_string(self) -> None:
        self.assertEqual(shell_escape("hello"), "'hello'")

    def test_empty_string(self) -> None:
        self.assertEqual(shell_escape(""), "''")

    def test_spaces(self) -> None:
        self.assertEqual(shell_escape("a b c"), "'a b c'")

    def test_single_quote_escape(self) -> None:
        # apostrophe should close, escape, reopen — `'\''`
        self.assertEqual(shell_escape("it's"), "'it'\\''s'")

    def test_multiple_single_quotes(self) -> None:
        self.assertEqual(shell_escape("'a'b'"), "''\\''a'\\''b'\\'''")

    def test_dollar_sign_preserved(self) -> None:
        # Single-quoting means $ is literal; no escape needed.
        self.assertEqual(shell_escape("$HOME"), "'$HOME'")

    def test_double_quotes_preserved(self) -> None:
        self.assertEqual(shell_escape('say "hi"'), "'say \"hi\"'")

    def test_backslash_preserved(self) -> None:
        # Backslash has no special meaning inside single quotes.
        self.assertEqual(shell_escape("a\\b"), "'a\\b'")

    def test_newline_preserved(self) -> None:
        self.assertEqual(shell_escape("a\nb"), "'a\nb'")


# ---------------------------------------------------------------------------
# build_tmux_split_args
# ---------------------------------------------------------------------------


class BuildTmuxSplitArgsTests(unittest.TestCase):
    def test_minimal_command_shape(self) -> None:
        args = build_tmux_split_args("/work", "/usr/local/bin/omx")
        self.assertEqual(args[0], "split-window")
        self.assertEqual(args[1], "-v")
        self.assertEqual(args[2], "-l")
        self.assertEqual(args[3], str(HUD_TMUX_HEIGHT_LINES))
        self.assertEqual(args[4], "-c")
        self.assertEqual(args[5], "/work")
        self.assertEqual(
            args[6],
            "node '/usr/local/bin/omx' hud --watch",
        )

    def test_preset_appended(self) -> None:
        args = build_tmux_split_args("/work", "/bin/omx", preset="focused")
        self.assertIn("--preset=focused", args[-1])

    def test_invalid_preset_dropped(self) -> None:
        args = build_tmux_split_args("/work", "/bin/omx", preset="weird")
        self.assertNotIn("--preset", args[-1])

    def test_session_id_prefixes_env(self) -> None:
        args = build_tmux_split_args("/work", "/bin/omx", session_id="abc-123")
        self.assertTrue(args[-1].startswith("OMX_SESSION_ID='abc-123' "))

    def test_session_id_trimmed(self) -> None:
        args = build_tmux_split_args("/work", "/bin/omx", session_id="  trimmed  ")
        self.assertTrue(args[-1].startswith("OMX_SESSION_ID='trimmed' "))

    def test_empty_session_id_not_added(self) -> None:
        args = build_tmux_split_args("/work", "/bin/omx", session_id="   ")
        self.assertNotIn("OMX_SESSION_ID", args[-1])

    def test_special_chars_in_omx_bin_escaped(self) -> None:
        args = build_tmux_split_args("/work", "/path with space/omx")
        self.assertIn("node '/path with space/omx' hud --watch", args[-1])

    def test_cwd_passed_literally(self) -> None:
        args = build_tmux_split_args("/path with spaces", "/bin/omx")
        self.assertEqual(args[5], "/path with spaces")  # No quoting; argv literal.


# ---------------------------------------------------------------------------
# watch_render_loop
# ---------------------------------------------------------------------------


class WatchRenderLoopTests(unittest.TestCase):
    def test_runs_until_max_iterations(self) -> None:
        calls = []
        result = watch_render_loop(
            lambda: calls.append(1),
            interval_ms=1,
            max_iterations=3,
            sleep_fn=lambda ms, intr: None,
        )
        self.assertEqual(result, 3)
        self.assertEqual(len(calls), 3)

    def test_interrupt_stops_loop(self) -> None:
        calls = []
        state = {"stop": False}

        def render() -> None:
            calls.append(1)
            if len(calls) == 2:
                state["stop"] = True

        watch_render_loop(
            render,
            interval_ms=1,
            interrupt=lambda: state["stop"],
            sleep_fn=lambda ms, intr: None,
        )
        self.assertEqual(len(calls), 2)

    def test_error_forwarded_to_handler(self) -> None:
        errors: list[BaseException] = []

        def render() -> None:
            raise RuntimeError("boom")

        watch_render_loop(
            render,
            interval_ms=1,
            on_error=errors.append,
            sleep_fn=lambda ms, intr: None,
            max_iterations=1,
        )
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], RuntimeError)

    def test_error_in_render_does_not_kill_loop(self) -> None:
        attempts: list[int] = []

        def render() -> None:
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("transient")

        watch_render_loop(
            render,
            interval_ms=1,
            max_iterations=3,
            sleep_fn=lambda ms, intr: None,
        )
        self.assertEqual(len(attempts), 3)

    def test_interval_passed_to_sleep_fn(self) -> None:
        seen_ms: list[float] = []

        def fake_sleep(ms: float, intr) -> None:
            seen_ms.append(ms)

        watch_render_loop(
            lambda: None,
            interval_ms=500,
            max_iterations=2,
            sleep_fn=fake_sleep,
        )
        self.assertGreaterEqual(len(seen_ms), 1)
        self.assertLessEqual(seen_ms[0], 500)

    def test_interrupt_before_first_tick_returns_zero(self) -> None:
        calls = []
        watch_render_loop(
            lambda: calls.append(1),
            interrupt=lambda: True,
            sleep_fn=lambda ms, intr: None,
        )
        self.assertEqual(len(calls), 0)


# ---------------------------------------------------------------------------
# run_watch_mode
# ---------------------------------------------------------------------------


class RunWatchModeTests(unittest.TestCase):
    def test_returns_zero_when_watch_disabled(self) -> None:
        flags = HudFlags(watch=False)
        rc = run_watch_mode("/tmp", flags)
        self.assertEqual(rc, 0)

    def test_non_tty_without_ci_errors(self) -> None:
        stderr = io.StringIO()
        rc = run_watch_mode(
            "/tmp",
            HudFlags(watch=True),
            RunWatchModeDeps(
                is_tty=False,
                env={},
                write_stdout=lambda _: None,
                write_stderr=stderr.write,
                register_sigint=lambda _h: None,
                read_all_state_fn=lambda _cwd: object(),  # not called
                read_hud_config_fn=lambda _cwd: object(),
                render_hud_fn=lambda *_: "",
                run_authority_tick_fn=lambda _cwd: None,
                sleep_fn=lambda *_a, **_k: None,
                max_iterations=1,
            ),
        )
        self.assertEqual(rc, 1)
        self.assertIn("HUD watch mode requires a TTY", stderr.getvalue())

    def test_render_loop_invoked(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        renders: list[int] = []

        rc = run_watch_mode(
            "/tmp",
            HudFlags(watch=True, preset="focused"),
            RunWatchModeDeps(
                is_tty=True,
                env={},
                read_all_state_fn=lambda _cwd: object(),
                read_hud_config_fn=lambda _cwd: type("C", (), {"preset": "focused"})(),
                render_hud_fn=lambda ctx, preset, opts: (
                    renders.append(1) or f"HUD-{preset}"
                ),
                run_authority_tick_fn=lambda _cwd: None,
                write_stdout=stdout.write,
                write_stderr=stderr.write,
                register_sigint=lambda _h: None,
                sleep_fn=lambda *_a, **_k: None,
                max_iterations=3,
            ),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(renders), 3)
        # Cursor hide + ANSI clear sequences must be emitted
        self.assertIn("\x1b[?25l", stdout.getvalue())
        self.assertIn("\x1b[2J\x1b[H", stdout.getvalue())
        self.assertIn("HUD-focused", stdout.getvalue())

    def test_render_failure_sets_exit_code(self) -> None:
        stderr = io.StringIO()
        rc = run_watch_mode(
            "/tmp",
            HudFlags(watch=True),
            RunWatchModeDeps(
                is_tty=True,
                env={},
                read_all_state_fn=lambda _cwd: object(),
                read_hud_config_fn=lambda _cwd: type("C", (), {"preset": "focused"})(),
                render_hud_fn=lambda *_: (_ for _ in ()).throw(RuntimeError("oops")),
                run_authority_tick_fn=lambda _cwd: None,
                write_stdout=lambda _: None,
                write_stderr=stderr.write,
                register_sigint=lambda _h: None,
                sleep_fn=lambda *_a, **_k: None,
                max_iterations=3,
            ),
        )
        self.assertEqual(rc, 1)
        self.assertIn("HUD watch render failed: oops", stderr.getvalue())

    def test_user_interrupt_terminates(self) -> None:
        flag = {"stop": False}

        def render(_ctx: object, _preset: str, _opts: object) -> str:
            flag["stop"] = True
            return "frame"

        rc = run_watch_mode(
            "/tmp",
            HudFlags(watch=True),
            RunWatchModeDeps(
                is_tty=True,
                env={},
                read_all_state_fn=lambda _cwd: object(),
                read_hud_config_fn=lambda _cwd: type("C", (), {"preset": "focused"})(),
                render_hud_fn=render,
                run_authority_tick_fn=lambda _cwd: None,
                write_stdout=lambda _: None,
                write_stderr=lambda _: None,
                register_sigint=lambda _h: None,
                sleep_fn=lambda *_a, **_k: None,
                interrupt=lambda: flag["stop"],
            ),
        )
        self.assertEqual(rc, 0)

    def test_ci_allows_non_tty(self) -> None:
        renders: list[int] = []
        rc = run_watch_mode(
            "/tmp",
            HudFlags(watch=True),
            RunWatchModeDeps(
                is_tty=False,
                env={"CI": "1"},
                read_all_state_fn=lambda _cwd: object(),
                read_hud_config_fn=lambda _cwd: type("C", (), {"preset": "focused"})(),
                render_hud_fn=lambda *_: renders.append(1) or "frame",
                run_authority_tick_fn=lambda _cwd: None,
                write_stdout=lambda _: None,
                write_stderr=lambda _: None,
                register_sigint=lambda _h: None,
                sleep_fn=lambda *_a, **_k: None,
                max_iterations=1,
            ),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(renders), 1)


# ---------------------------------------------------------------------------
# hud_command
# ---------------------------------------------------------------------------


class HudCommandTests(unittest.TestCase):
    def test_help_flag_prints_usage(self) -> None:
        out = io.StringIO()
        rc = hud_command(["--help"], HudCommandDeps(write_stdout=out.write))
        self.assertEqual(rc, 0)
        self.assertIn(HUD_USAGE, out.getvalue())

    def test_short_help_flag(self) -> None:
        out = io.StringIO()
        rc = hud_command(["-h"], HudCommandDeps(write_stdout=out.write))
        self.assertEqual(rc, 0)
        self.assertIn("--watch", out.getvalue())

    def test_tmux_branch(self) -> None:
        called: list[tuple[str, HudFlags]] = []
        rc = hud_command(
            ["--tmux"],
            HudCommandDeps(
                cwd="/work",
                launch_tmux_fn=lambda cwd, flags: called.append((cwd, flags)) or 0,
            ),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(called[0][0], "/work")
        self.assertTrue(called[0][1].tmux)

    def test_watch_branch(self) -> None:
        called: list[tuple[str, HudFlags]] = []
        rc = hud_command(
            ["--watch", "--preset=full"],
            HudCommandDeps(
                cwd="/work",
                run_watch_mode_fn=lambda cwd, flags: called.append((cwd, flags)) or 0,
            ),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(called[0][0], "/work")
        self.assertTrue(called[0][1].watch)
        self.assertEqual(called[0][1].preset, "full")

    def test_render_once_default(self) -> None:
        called: list[tuple[str, HudFlags]] = []
        rc = hud_command(
            [],
            HudCommandDeps(
                cwd="/work",
                render_once_fn=lambda cwd, flags: called.append((cwd, flags)) or 0,
            ),
        )
        self.assertEqual(rc, 0)
        self.assertEqual(len(called), 1)
        self.assertFalse(called[0][1].watch)

    def test_invalid_preset_dropped(self) -> None:
        called: list[HudFlags] = []
        hud_command(
            ["--preset=weird"],
            HudCommandDeps(
                cwd="/work",
                render_once_fn=lambda _c, f: called.append(f) or 0,
            ),
        )
        self.assertIsNone(called[0].preset)

    def test_json_flag_parsed(self) -> None:
        called: list[HudFlags] = []
        hud_command(
            ["--json"],
            HudCommandDeps(
                cwd="/work",
                render_once_fn=lambda _c, f: called.append(f) or 0,
            ),
        )
        self.assertTrue(called[0].json)


# ---------------------------------------------------------------------------
# Default render once + tmux launch (sanity-only; subprocess mocked)
# ---------------------------------------------------------------------------


class DefaultBehaviorTests(unittest.TestCase):
    def test_default_launch_tmux_without_tmux_env_errors(self) -> None:
        from omx.hud.watch import _default_launch_tmux

        stderr_buf = io.StringIO()
        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(os, "environ", new={**os.environ}),
        ):
            os.environ.pop("TMUX", None)
            with patch("sys.stderr", new=stderr_buf):
                rc = _default_launch_tmux("/work", HudFlags(tmux=True))
        self.assertEqual(rc, 1)
        self.assertIn("Not inside a tmux session", stderr_buf.getvalue())

    def test_default_launch_tmux_calls_subprocess(self) -> None:
        from omx.hud.watch import _default_launch_tmux

        env = {**os.environ, "TMUX": "/tmp/tmux-sock"}
        with (
            patch.dict(os.environ, env, clear=False),
            patch("subprocess.run") as mock_run,
            patch("sys.argv", ["/path/omx-bin"]),
        ):
            mock_run.return_value = None
            rc = _default_launch_tmux("/work", HudFlags(tmux=True))
            self.assertEqual(rc, 0)
            args, kwargs = mock_run.call_args
            cmd = args[0]
            self.assertEqual(cmd[0], "tmux")
            self.assertIn("split-window", cmd)
            self.assertIn("/work", cmd)

    def test_default_render_once_writes_output(self) -> None:
        from omx.hud.watch import _default_render_once

        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with patch("sys.stdout", new=buf):
                rc = _default_render_once(tmp, HudFlags())
            self.assertEqual(rc, 0)
            self.assertIn("[OMX", buf.getvalue())

    def test_default_render_once_json_mode(self) -> None:
        from omx.hud.watch import _default_render_once

        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with patch("sys.stdout", new=buf):
                rc = _default_render_once(tmp, HudFlags(json=True))
            self.assertEqual(rc, 0)
            # JSON output should at least contain `version` key field name.
            self.assertIn("version", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
