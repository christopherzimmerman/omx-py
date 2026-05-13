"""Tests for the HUD renderer (Phase 8 port of src/hud/render.ts)."""

from __future__ import annotations

import re
import unittest
from datetime import datetime, timedelta, timezone

from omx.hud.colors import set_color_enabled
from omx.hud.renderer import (
    RenderHudOptions,
    count_rendered_hud_lines,
    render_hud,
    render_statusline,
)
from omx.hud.types import (
    AutopilotStateForHud,
    AutoresearchStateForHud,
    DeepInterviewStateForHud,
    HudMetrics,
    HudNotifyState,
    HudPreset,
    HudRenderContext,
    RalphStateForHud,
    RalplanStateForHud,
    SessionStateForHud,
    TeamStateForHud,
    UltraqaStateForHud,
    UltraworkStateForHud,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip(value: str) -> str:
    return _ANSI_RE.sub("", value)


def _iso(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


class RenderHudColorTests(unittest.TestCase):
    """Render behavior with ANSI colors disabled (deterministic output)."""

    def setUp(self) -> None:
        set_color_enabled(False)

    def tearDown(self) -> None:
        set_color_enabled(True)

    def test_idle_state_renders_no_active_modes(self) -> None:
        ctx = HudRenderContext()
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("[OMX]", out)
        self.assertIn("No active modes.", out)

    def test_version_strips_leading_v(self) -> None:
        ctx = HudRenderContext(version="v1.2.3")
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("[OMX#1.2.3]", out)

    def test_version_without_leading_v(self) -> None:
        ctx = HudRenderContext(version="2.0.0")
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("[OMX#2.0.0]", out)

    def test_git_branch_renders(self) -> None:
        ctx = HudRenderContext(git_branch="feature/x")
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("feature/x", out)

    def test_git_branch_sanitizes_control_chars(self) -> None:
        ctx = HudRenderContext(git_branch="main\x07boom")
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("mainboom", out)
        self.assertNotIn("\x07", out)

    def test_ralph_with_iteration(self) -> None:
        ctx = HudRenderContext(
            ralph=RalphStateForHud(active=True, iteration=3, max_iterations=10)
        )
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("ralph:3/10", out)

    def test_ralph_without_iteration_falls_back_to_label(self) -> None:
        ctx = HudRenderContext(ralph=RalphStateForHud(active=True))
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("ralph", out)
        self.assertNotIn("ralph:", out)

    def test_ultrawork_renders(self) -> None:
        ctx = HudRenderContext(ultrawork=UltraworkStateForHud(active=True))
        self.assertIn("ultrawork", render_hud(ctx, HudPreset.FOCUSED))

    def test_autopilot_uses_active_default_phase(self) -> None:
        ctx = HudRenderContext(autopilot=AutopilotStateForHud(active=True))
        self.assertIn("autopilot:active", render_hud(ctx, HudPreset.FOCUSED))

    def test_autopilot_custom_phase(self) -> None:
        ctx = HudRenderContext(
            autopilot=AutopilotStateForHud(active=True, current_phase="planning")
        )
        self.assertIn("autopilot:planning", render_hud(ctx, HudPreset.FOCUSED))

    def test_ralplan_with_iteration_pending_planning(self) -> None:
        ctx = HudRenderContext(
            ralplan=RalplanStateForHud(
                active=True, iteration=2, planning_complete=False
            )
        )
        self.assertIn("ralplan:2/?", render_hud(ctx, HudPreset.FOCUSED))

    def test_ralplan_with_iteration_complete(self) -> None:
        ctx = HudRenderContext(
            ralplan=RalplanStateForHud(active=True, iteration=4, planning_complete=True)
        )
        self.assertIn("ralplan:4/4", render_hud(ctx, HudPreset.FOCUSED))

    def test_ralplan_without_iteration(self) -> None:
        ctx = HudRenderContext(
            ralplan=RalplanStateForHud(active=True, current_phase="review")
        )
        self.assertIn("ralplan:review", render_hud(ctx, HudPreset.FOCUSED))

    def test_deep_interview_with_lock_suffix(self) -> None:
        ctx = HudRenderContext(
            deep_interview=DeepInterviewStateForHud(
                active=True, current_phase="round1", input_lock_active=True
            )
        )
        self.assertIn("interview:round1:lock", render_hud(ctx, HudPreset.FOCUSED))

    def test_deep_interview_without_lock(self) -> None:
        ctx = HudRenderContext(
            deep_interview=DeepInterviewStateForHud(active=True, current_phase="round1")
        )
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("interview:round1", out)
        self.assertNotIn(":lock", out)

    def test_autoresearch_phase(self) -> None:
        ctx = HudRenderContext(
            autoresearch=AutoresearchStateForHud(active=True, current_phase="reading")
        )
        self.assertIn("research:reading", render_hud(ctx, HudPreset.FOCUSED))

    def test_ultraqa_phase(self) -> None:
        ctx = HudRenderContext(
            ultraqa=UltraqaStateForHud(active=True, current_phase="fix")
        )
        self.assertIn("qa:fix", render_hud(ctx, HudPreset.FOCUSED))

    def test_team_with_agent_count(self) -> None:
        ctx = HudRenderContext(
            team=TeamStateForHud(active=True, agent_count=3, team_name="alpha")
        )
        self.assertIn("team:3 workers", render_hud(ctx, HudPreset.FOCUSED))

    def test_team_with_only_name(self) -> None:
        ctx = HudRenderContext(team=TeamStateForHud(active=True, team_name="alpha"))
        self.assertIn("team:alpha", render_hud(ctx, HudPreset.FOCUSED))

    def test_team_bare(self) -> None:
        ctx = HudRenderContext(team=TeamStateForHud(active=True))
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("team", out)
        self.assertNotIn("team:", out)

    def test_metrics_turns_only_for_current_session(self) -> None:
        session_start = datetime.now(timezone.utc) - timedelta(minutes=10)
        last_activity = session_start - timedelta(minutes=30)
        ctx = HudRenderContext(
            metrics=HudMetrics(
                total_turns=20,
                session_turns=4,
                last_activity=_iso(last_activity),
            ),
            session=SessionStateForHud(
                session_id="abc", started_at=_iso(session_start)
            ),
        )
        # last_activity is BEFORE session start → metrics treated as stale.
        self.assertNotIn("turns:", render_hud(ctx, HudPreset.FOCUSED))

    def test_metrics_turns_render_when_current(self) -> None:
        session_start = datetime.now(timezone.utc) - timedelta(minutes=30)
        last_activity = datetime.now(timezone.utc) - timedelta(minutes=1)
        ctx = HudRenderContext(
            metrics=HudMetrics(
                total_turns=20,
                session_turns=4,
                last_activity=_iso(last_activity),
            ),
            session=SessionStateForHud(
                session_id="abc", started_at=_iso(session_start)
            ),
        )
        self.assertIn("turns:4", render_hud(ctx, HudPreset.FOCUSED))

    def test_metrics_tokens_formatting(self) -> None:
        ctx = HudRenderContext(
            metrics=HudMetrics(session_total_tokens=1_500_000),
        )
        self.assertIn("tokens:1.5M", render_hud(ctx, HudPreset.FOCUSED))

    def test_metrics_tokens_thousands(self) -> None:
        ctx = HudRenderContext(metrics=HudMetrics(session_total_tokens=2500))
        self.assertIn("tokens:2.5k", render_hud(ctx, HudPreset.FOCUSED))

    def test_metrics_tokens_small(self) -> None:
        ctx = HudRenderContext(metrics=HudMetrics(session_total_tokens=42))
        self.assertIn("tokens:42", render_hud(ctx, HudPreset.FOCUSED))

    def test_metrics_tokens_sum_when_total_missing(self) -> None:
        ctx = HudRenderContext(
            metrics=HudMetrics(session_input_tokens=200, session_output_tokens=300)
        )
        self.assertIn("tokens:500", render_hud(ctx, HudPreset.FOCUSED))

    def test_metrics_quota_rounds_five_hour(self) -> None:
        ctx = HudRenderContext(metrics=HudMetrics(five_hour_limit_pct=42.7))
        self.assertIn("quota:5h:43%", render_hud(ctx, HudPreset.FOCUSED))

    def test_metrics_quota_weekly(self) -> None:
        ctx = HudRenderContext(metrics=HudMetrics(weekly_limit_pct=10.2))
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("quota:wk:10%", out)

    def test_metrics_quota_omits_when_zero(self) -> None:
        ctx = HudRenderContext(
            metrics=HudMetrics(five_hour_limit_pct=0, weekly_limit_pct=0)
        )
        self.assertNotIn("quota:", render_hud(ctx, HudPreset.FOCUSED))

    def test_last_activity_seconds(self) -> None:
        last = datetime.now(timezone.utc) - timedelta(seconds=10)
        ctx = HudRenderContext(hud_notify=HudNotifyState(last_turn_at=_iso(last)))
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertRegex(_strip(out), r"last:\d+s ago")

    def test_last_activity_minutes(self) -> None:
        last = datetime.now(timezone.utc) - timedelta(minutes=5)
        ctx = HudRenderContext(hud_notify=HudNotifyState(last_turn_at=_iso(last)))
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertRegex(_strip(out), r"last:\d+m ago")

    def test_session_duration_minutes(self) -> None:
        started = datetime.now(timezone.utc) - timedelta(minutes=2)
        ctx = HudRenderContext(
            session=SessionStateForHud(session_id="x", started_at=_iso(started))
        )
        self.assertRegex(_strip(render_hud(ctx, HudPreset.FOCUSED)), r"session:\d+m")

    def test_session_duration_hours(self) -> None:
        started = datetime.now(timezone.utc) - timedelta(hours=2, minutes=15)
        ctx = HudRenderContext(
            session=SessionStateForHud(session_id="x", started_at=_iso(started))
        )
        self.assertRegex(
            _strip(render_hud(ctx, HudPreset.FOCUSED)), r"session:\d+h\d+m"
        )

    def test_session_duration_seconds(self) -> None:
        started = datetime.now(timezone.utc) - timedelta(seconds=20)
        ctx = HudRenderContext(
            session=SessionStateForHud(session_id="x", started_at=_iso(started))
        )
        self.assertRegex(_strip(render_hud(ctx, HudPreset.FOCUSED)), r"session:\d+s")

    def test_full_preset_includes_total_turns(self) -> None:
        ctx = HudRenderContext(metrics=HudMetrics(total_turns=42, session_turns=2))
        # FOCUSED preset omits total_turns; FULL includes it.
        focused = render_hud(ctx, HudPreset.FOCUSED)
        full = render_hud(ctx, HudPreset.FULL)
        self.assertNotIn("total-turns", focused)
        self.assertIn("total-turns:42", full)

    def test_minimal_preset_omits_quota(self) -> None:
        ctx = HudRenderContext(metrics=HudMetrics(five_hour_limit_pct=30.0))
        out = render_hud(ctx, HudPreset.MINIMAL)
        self.assertNotIn("quota", out)

    def test_invalid_preset_falls_back_to_focused(self) -> None:
        ctx = HudRenderContext(autopilot=AutopilotStateForHud(active=True))
        out = render_hud(ctx, "totally-unknown")
        # autopilot belongs to FOCUSED + FULL, not MINIMAL, so it must render.
        self.assertIn("autopilot:active", out)

    def test_max_width_wraps_long_output(self) -> None:
        ctx = HudRenderContext(
            git_branch="feature/very-long-branch-name",
            ralph=RalphStateForHud(active=True, iteration=1, max_iterations=10),
            ultrawork=UltraworkStateForHud(active=True),
            team=TeamStateForHud(active=True, team_name="alpha"),
        )
        out = render_hud(
            ctx, HudPreset.FULL, RenderHudOptions(max_width=30, max_lines=3)
        )
        lines = out.split("\n")
        self.assertGreaterEqual(len(lines), 2)
        for line in lines:
            self.assertLessEqual(len(_strip(line)), 30)

    def test_max_lines_caps_output(self) -> None:
        ctx = HudRenderContext(
            git_branch="feature/x",
            ralph=RalphStateForHud(active=True, iteration=1, max_iterations=10),
            ultrawork=UltraworkStateForHud(active=True),
            autopilot=AutopilotStateForHud(active=True),
            team=TeamStateForHud(active=True, agent_count=3),
        )
        out = render_hud(
            ctx, HudPreset.FULL, RenderHudOptions(max_width=20, max_lines=2)
        )
        lines = out.split("\n")
        self.assertLessEqual(len(lines), 2)

    def test_options_default_when_none(self) -> None:
        ctx = HudRenderContext()
        out = render_hud(ctx, HudPreset.FOCUSED, options=None)
        self.assertIn("[OMX]", out)


class RenderHudColoredTests(unittest.TestCase):
    """Spot-check that ANSI codes appear when colors are enabled."""

    def setUp(self) -> None:
        set_color_enabled(True)

    def test_label_is_bold(self) -> None:
        ctx = HudRenderContext()
        out = render_hud(ctx, HudPreset.FOCUSED)
        self.assertIn("\x1b[1m", out)

    def test_ralph_uses_color_when_enabled(self) -> None:
        ctx = HudRenderContext(
            ralph=RalphStateForHud(active=True, iteration=1, max_iterations=10)
        )
        out = render_hud(ctx, HudPreset.FOCUSED)
        # green at low iteration counts
        self.assertIn("\x1b[32m", out)


class CountRenderedHudLinesTests(unittest.TestCase):
    """Tests for ``count_rendered_hud_lines``."""

    def test_single_line(self) -> None:
        self.assertEqual(count_rendered_hud_lines("hello"), 1)

    def test_multi_line(self) -> None:
        self.assertEqual(count_rendered_hud_lines("a\nb\nc"), 3)

    def test_strips_carriage_returns(self) -> None:
        self.assertEqual(count_rendered_hud_lines("a\r\nb\r\nc"), 3)

    def test_empty_string(self) -> None:
        self.assertEqual(count_rendered_hud_lines(""), 1)


class RenderStatuslineCompatTests(unittest.TestCase):
    """The legacy compact statusline helper still works."""

    def test_returns_string(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            out = render_statusline(tmp)
            self.assertIsInstance(out, str)


if __name__ == "__main__":
    unittest.main()
