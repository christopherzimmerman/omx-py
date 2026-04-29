"""Tests for the HUD module completion."""

from __future__ import annotations

import unittest

from omx.hud.types import (
    DEFAULT_HUD_CONFIG,
    HudFlags,
    HudGitDisplay,
    HudMetrics,
    HudPreset,
    HudRenderContext,
    RalphStateForHud,
)
from omx.hud.colors import (
    RESET,
    bold,
    cyan,
    dim,
    get_ralph_color,
    green,
    is_color_enabled,
    set_color_enabled,
    yellow,
)
from omx.hud.constants import (
    HUD_RESIZE_RECONCILE_DELAY_SECONDS,
    HUD_TMUX_HEIGHT_LINES,
    HUD_TMUX_MAX_HEIGHT_LINES,
    HUD_TMUX_TEAM_HEIGHT_LINES,
)
from omx.hud.tmux import (
    TmuxPaneSnapshot,
    build_hud_watch_command,
    find_hud_watch_pane_ids,
    is_hud_watch_pane,
    parse_pane_id_from_tmux_output,
    parse_tmux_pane_snapshot,
    shell_escape_single,
)
from omx.hud.authority import RunHudAuthorityTickOptions, run_hud_authority_tick
from omx.hud.reconcile import (
    reconcile_hud_for_prompt_submit,
)


class TestHudTypes(unittest.TestCase):
    """Tests for HUD type dataclasses."""

    def test_default_hud_config(self) -> None:
        self.assertEqual(DEFAULT_HUD_CONFIG.preset, "focused")
        self.assertEqual(DEFAULT_HUD_CONFIG.git.display, "repo-branch")

    def test_hud_preset_enum(self) -> None:
        self.assertEqual(HudPreset.MINIMAL, "minimal")
        self.assertEqual(HudPreset.FOCUSED, "focused")
        self.assertEqual(HudPreset.FULL, "full")

    def test_hud_git_display_enum(self) -> None:
        self.assertEqual(HudGitDisplay.BRANCH, "branch")
        self.assertEqual(HudGitDisplay.REPO_BRANCH, "repo-branch")

    def test_hud_flags(self) -> None:
        flags = HudFlags(watch=True, json=False, tmux=False)
        self.assertTrue(flags.watch)
        self.assertFalse(flags.json)
        self.assertIsNone(flags.preset)

    def test_hud_metrics(self) -> None:
        metrics = HudMetrics(
            total_turns=10, session_turns=5, last_activity="2025-01-01"
        )
        self.assertEqual(metrics.total_turns, 10)

    def test_hud_render_context(self) -> None:
        ctx = HudRenderContext(version="1.0", git_branch="main")
        self.assertEqual(ctx.version, "1.0")
        self.assertIsNone(ctx.ralph)

    def test_ralph_state(self) -> None:
        state = RalphStateForHud(active=True, iteration=3, max_iterations=10)
        self.assertTrue(state.active)
        self.assertEqual(state.iteration, 3)


class TestHudColors(unittest.TestCase):
    """Tests for HUD ANSI color utilities."""

    def setUp(self) -> None:
        set_color_enabled(True)

    def tearDown(self) -> None:
        set_color_enabled(True)

    def test_color_wrapping(self) -> None:
        result = green("hello")
        self.assertIn("hello", result)
        self.assertTrue(result.startswith("\x1b["))
        self.assertTrue(result.endswith(RESET))

    def test_all_color_functions(self) -> None:
        for func in (green, yellow, cyan, dim, bold):
            result = func("test")
            self.assertIn("test", result)

    def test_colors_disabled(self) -> None:
        set_color_enabled(False)
        self.assertFalse(is_color_enabled())
        self.assertEqual(green("hi"), "hi")
        self.assertEqual(bold("hi"), "hi")

    def test_ralph_color_green(self) -> None:
        color = get_ralph_color(1, 10)
        self.assertIn("\x1b[32m", color)

    def test_ralph_color_yellow(self) -> None:
        color = get_ralph_color(7, 10)
        self.assertIn("\x1b[33m", color)

    def test_ralph_color_red(self) -> None:
        color = get_ralph_color(9, 10)
        self.assertIn("\x1b[31m", color)

    def test_ralph_color_disabled(self) -> None:
        set_color_enabled(False)
        self.assertEqual(get_ralph_color(5, 10), "")


class TestHudConstants(unittest.TestCase):
    """Tests for HUD constants."""

    def test_tmux_heights(self) -> None:
        self.assertEqual(HUD_TMUX_HEIGHT_LINES, 3)
        self.assertEqual(HUD_TMUX_TEAM_HEIGHT_LINES, 3)
        self.assertEqual(HUD_TMUX_MAX_HEIGHT_LINES, 5)

    def test_reconcile_delay(self) -> None:
        self.assertEqual(HUD_RESIZE_RECONCILE_DELAY_SECONDS, 2)


class TestHudTmux(unittest.TestCase):
    """Tests for HUD tmux integration."""

    def test_parse_pane_snapshot(self) -> None:
        output = "%0\tnode\tnode omx.js hud --watch\n%1\tbash\tbash\n"
        panes = parse_tmux_pane_snapshot(output)
        self.assertEqual(len(panes), 2)
        self.assertEqual(panes[0].pane_id, "%0")
        self.assertEqual(panes[0].current_command, "node")

    def test_is_hud_watch_pane(self) -> None:
        pane = TmuxPaneSnapshot(
            pane_id="%1",
            current_command="node",
            start_command="node omx.js hud --watch",
        )
        self.assertTrue(is_hud_watch_pane(pane))

    def test_is_not_hud_watch_pane(self) -> None:
        pane = TmuxPaneSnapshot(
            pane_id="%1", current_command="bash", start_command="bash"
        )
        self.assertFalse(is_hud_watch_pane(pane))

    def test_find_hud_watch_pane_ids(self) -> None:
        panes = [
            TmuxPaneSnapshot(
                pane_id="%0", current_command="bash", start_command="bash"
            ),
            TmuxPaneSnapshot(
                pane_id="%1",
                current_command="node",
                start_command="node omx hud --watch",
            ),
            TmuxPaneSnapshot(
                pane_id="%2",
                current_command="node",
                start_command="node omx hud --watch",
            ),
        ]
        ids = find_hud_watch_pane_ids(panes, "%0")
        self.assertEqual(ids, ["%1", "%2"])

    def test_find_excludes_current(self) -> None:
        panes = [
            TmuxPaneSnapshot(
                pane_id="%1",
                current_command="node",
                start_command="node omx hud --watch",
            ),
        ]
        ids = find_hud_watch_pane_ids(panes, "%1")
        self.assertEqual(ids, [])

    def test_parse_pane_id(self) -> None:
        self.assertEqual(parse_pane_id_from_tmux_output("%5\n"), "%5")
        self.assertIsNone(parse_pane_id_from_tmux_output("error\n"))
        self.assertIsNone(parse_pane_id_from_tmux_output(""))

    def test_shell_escape_single(self) -> None:
        self.assertEqual(shell_escape_single("hello"), "'hello'")
        self.assertEqual(shell_escape_single("it's"), "'it'\\''s'")

    def test_build_hud_watch_command(self) -> None:
        cmd = build_hud_watch_command("/usr/bin/omx", "focused", None)
        self.assertIn("hud --watch", cmd)
        self.assertIn("--preset=focused", cmd)

    def test_build_hud_watch_command_no_preset(self) -> None:
        cmd = build_hud_watch_command("/usr/bin/omx")
        self.assertIn("hud --watch", cmd)
        self.assertNotIn("--preset", cmd)

    def test_build_hud_watch_command_with_session(self) -> None:
        cmd = build_hud_watch_command("/usr/bin/omx", session_id="abc123")
        self.assertIn("OMX_SESSION_ID=", cmd)


class TestHudAuthority(unittest.TestCase):
    """Tests for HUD authority."""

    def test_run_authority_tick(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            options = RunHudAuthorityTickOptions(cwd=tmpdir)
            # Should not raise
            run_hud_authority_tick(options)
            authority_path = (
                __import__("pathlib").Path(tmpdir)
                / ".omx"
                / "state"
                / "notify-fallback-authority-owner.json"
            )
            self.assertTrue(authority_path.exists())


class TestHudReconcile(unittest.TestCase):
    """Tests for HUD reconcile."""

    def test_skip_not_tmux(self) -> None:
        result = reconcile_hud_for_prompt_submit("/tmp", env={})
        self.assertEqual(result.status, "skipped_not_tmux")

    def test_skip_no_entry(self) -> None:
        result = reconcile_hud_for_prompt_submit(
            "/tmp",
            env={"TMUX": "1"},
            resolve_omx_bin_fn=lambda: None,
        )
        self.assertEqual(result.status, "skipped_no_entry")


class TestHudInit(unittest.TestCase):
    """Tests for HUD __init__ exports."""

    def test_imports(self) -> None:
        from omx.hud import (
            HUD_TMUX_HEIGHT_LINES,
        )

        self.assertIsNotNone(HUD_TMUX_HEIGHT_LINES)


if __name__ == "__main__":
    unittest.main()
