"""HUD statusline renderer.

Port of src/hud/render.ts. Renders HudRenderContext into formatted ANSI strings.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from omx.hud.colors import (
    RESET,
    bold,
    cyan,
    dim,
    get_ralph_color,
    green,
    is_color_enabled,
    yellow,
)
from omx.hud.constants import HUD_TMUX_MAX_HEIGHT_LINES
from omx.hud.state import read_hud_state
from omx.hud.types import HudPreset, HudRenderContext
from omx.state.operations import state_list_active
from omx.state.paths import resolve_working_directory

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _sep() -> str:
    """Return the live `' | '` separator, re-evaluated each call."""
    return dim(" | ")


@dataclass
class RenderHudOptions:
    """Optional sizing inputs for renderHud.

    Attributes:
        max_width: Maximum visible width per line.
        max_lines: Maximum number of rendered lines.
    """

    max_width: int | None = None
    max_lines: int | None = None


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _sanitize_dynamic_text(value: str) -> str:
    """Strip control characters from untrusted dynamic text."""
    return _CONTROL_CHARS_RE.sub("", value)


def _strip_ansi(value: str) -> str:
    """Strip ANSI SGR sequences from a string."""
    return _ANSI_SGR_RE.sub("", value)


def _visible_length(value: str) -> int:
    """Return the visible (non-ANSI) length of a string."""
    return len(_strip_ansi(value))


def _format_token_count(value: int | float) -> str:
    """Format a token count as `1.2M`, `1.2k`, or raw integer."""
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{int(value)}"


def _is_finite_number(value: object) -> bool:
    """Return True if *value* is a finite int or float."""
    if isinstance(value, bool):  # bool is a subclass of int; reject
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    return False


def _parse_iso_ms(value: str | None) -> float | None:
    """Parse an ISO 8601 timestamp and return milliseconds since the epoch."""
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000.0
    except (TypeError, ValueError):
        return None


def _now_ms() -> float:
    """Return the current time in milliseconds since the epoch."""
    return datetime.now(timezone.utc).timestamp() * 1000.0


def _attr(ctx: HudRenderContext | object, name: str) -> object:
    """Read an attribute or mapping key from *ctx*, returning None if absent."""
    if isinstance(ctx, dict):
        return ctx.get(name)
    return getattr(ctx, name, None)


def _is_current_session_metrics(ctx: HudRenderContext) -> bool:
    """Return True if metrics belong to the current session window."""
    metrics = _attr(ctx, "metrics")
    session = _attr(ctx, "session")
    if metrics is None or session is None:
        return True
    started_at = _attr(session, "started_at")
    last_activity = _attr(metrics, "last_activity")
    if not started_at or not last_activity:
        return True
    session_start = _parse_iso_ms(started_at if isinstance(started_at, str) else None)
    last = _parse_iso_ms(last_activity if isinstance(last_activity, str) else None)
    if session_start is None or last is None:
        return True
    return last >= session_start


# ---------------------------------------------------------------------------
# Element renderers
# ---------------------------------------------------------------------------


def _render_git_branch(ctx: HudRenderContext) -> str | None:
    branch = _attr(ctx, "git_branch")
    if not branch or not isinstance(branch, str):
        return None
    sanitized = _sanitize_dynamic_text(branch)
    if not sanitized:
        return None
    return cyan(sanitized)


def _render_ralph(ctx: HudRenderContext) -> str | None:
    ralph = _attr(ctx, "ralph")
    if not ralph:
        return None
    iteration = _attr(ralph, "iteration")
    max_iter = _attr(ralph, "max_iterations")
    if not _is_finite_number(iteration) or not _is_finite_number(max_iter):
        return yellow("ralph")
    if not is_color_enabled():
        return f"ralph:{iteration}/{max_iter}"
    color = get_ralph_color(int(iteration), int(max_iter))  # type: ignore[arg-type]
    return f"{color}ralph:{iteration}/{max_iter}{RESET}"


def _render_ultrawork(ctx: HudRenderContext) -> str | None:
    if not _attr(ctx, "ultrawork"):
        return None
    return cyan("ultrawork")


def _render_autopilot(ctx: HudRenderContext) -> str | None:
    autopilot = _attr(ctx, "autopilot")
    if not autopilot:
        return None
    raw_phase = _attr(autopilot, "current_phase") or "active"
    phase = _sanitize_dynamic_text(str(raw_phase)) or "active"
    return yellow(f"autopilot:{phase}")


def _render_ralplan(ctx: HudRenderContext) -> str | None:
    ralplan = _attr(ctx, "ralplan")
    if not ralplan:
        return None
    iteration = _attr(ralplan, "iteration")
    planning_complete = _attr(ralplan, "planning_complete") is True
    if isinstance(iteration, (int, float)) and _is_finite_number(iteration):
        max_label: int | str = int(iteration) if planning_complete else "?"
        return cyan(f"ralplan:{int(iteration)}/{max_label}")
    raw_phase = _attr(ralplan, "current_phase") or "active"
    phase = _sanitize_dynamic_text(str(raw_phase)) or "active"
    return cyan(f"ralplan:{phase}")


def _render_deep_interview(ctx: HudRenderContext) -> str | None:
    deep = _attr(ctx, "deep_interview")
    if not deep:
        return None
    raw_phase = _attr(deep, "current_phase") or "active"
    phase = _sanitize_dynamic_text(str(raw_phase)) or "active"
    lock_suffix = ":lock" if _attr(deep, "input_lock_active") else ""
    return yellow(f"interview:{phase}{lock_suffix}")


def _render_autoresearch(ctx: HudRenderContext) -> str | None:
    auto = _attr(ctx, "autoresearch")
    if not auto:
        return None
    raw_phase = _attr(auto, "current_phase") or "active"
    phase = _sanitize_dynamic_text(str(raw_phase)) or "active"
    return cyan(f"research:{phase}")


def _render_ultraqa(ctx: HudRenderContext) -> str | None:
    qa = _attr(ctx, "ultraqa")
    if not qa:
        return None
    raw_phase = _attr(qa, "current_phase") or "active"
    phase = _sanitize_dynamic_text(str(raw_phase)) or "active"
    return green(f"qa:{phase}")


def _render_team(ctx: HudRenderContext) -> str | None:
    team = _attr(ctx, "team")
    if not team:
        return None
    count = _attr(team, "agent_count")
    name_raw = _attr(team, "team_name") or ""
    name = _sanitize_dynamic_text(str(name_raw)) if name_raw else ""
    if isinstance(count, int) and count > 0:
        return green(f"team:{count} workers")
    if name:
        return green(f"team:{name}")
    return green("team")


def _render_turns(ctx: HudRenderContext) -> str | None:
    metrics = _attr(ctx, "metrics")
    if metrics is None or not _is_current_session_metrics(ctx):
        return None
    turns = _attr(metrics, "session_turns")
    if turns is None:
        return None
    return dim(f"turns:{turns}")


def _render_tokens(ctx: HudRenderContext) -> str | None:
    metrics = _attr(ctx, "metrics")
    if metrics is None or not _is_current_session_metrics(ctx):
        return None
    total = _attr(metrics, "session_total_tokens")
    if total is None:
        inp = _attr(metrics, "session_input_tokens") or 0
        out = _attr(metrics, "session_output_tokens") or 0
        try:
            total = int(inp) + int(out)
        except (TypeError, ValueError):
            return None
    if not _is_finite_number(total) or total is None or total <= 0:
        return None
    return dim(f"tokens:{_format_token_count(total)}")


def _render_quota(ctx: HudRenderContext) -> str | None:
    metrics = _attr(ctx, "metrics")
    if metrics is None or not _is_current_session_metrics(ctx):
        return None
    parts: list[str] = []
    five_hour = _attr(metrics, "five_hour_limit_pct")
    weekly = _attr(metrics, "weekly_limit_pct")
    if (
        isinstance(five_hour, (int, float))
        and _is_finite_number(five_hour)
        and five_hour > 0
    ):
        parts.append(f"5h:{round(float(five_hour))}%")
    if isinstance(weekly, (int, float)) and _is_finite_number(weekly) and weekly > 0:
        parts.append(f"wk:{round(float(weekly))}%")
    if not parts:
        return None
    return dim(f"quota:{','.join(parts)}")


def _render_last_activity(ctx: HudRenderContext) -> str | None:
    notify = _attr(ctx, "hud_notify")
    if notify is None:
        return None
    last_at_raw = _attr(notify, "last_turn_at")
    if not isinstance(last_at_raw, str) or not last_at_raw:
        return None
    last_at = _parse_iso_ms(last_at_raw)
    if last_at is None:
        return None
    diff_sec = max(0, round((_now_ms() - last_at) / 1000))
    if diff_sec < 60:
        return dim(f"last:{diff_sec}s ago")
    diff_min = round(diff_sec / 60)
    return dim(f"last:{diff_min}m ago")


def _render_total_turns(ctx: HudRenderContext) -> str | None:
    metrics = _attr(ctx, "metrics")
    if metrics is None:
        return None
    total = _attr(metrics, "total_turns")
    if not total:
        return None
    return dim(f"total-turns:{total}")


def _render_session_duration(ctx: HudRenderContext) -> str | None:
    session = _attr(ctx, "session")
    if session is None:
        return None
    started_at_raw = _attr(session, "started_at")
    if not isinstance(started_at_raw, str) or not started_at_raw:
        return None
    started_at = _parse_iso_ms(started_at_raw)
    if started_at is None:
        return None
    diff_sec = max(0, round((_now_ms() - started_at) / 1000))
    if diff_sec < 60:
        return dim(f"session:{diff_sec}s")
    if diff_sec < 3600:
        return dim(f"session:{round(diff_sec / 60)}m")
    hours = diff_sec // 3600
    mins = round((diff_sec % 3600) / 60)
    return dim(f"session:{hours}h{mins}m")


# ---------------------------------------------------------------------------
# Preset element ordering
# ---------------------------------------------------------------------------

ElementRenderer = Callable[[HudRenderContext], "str | None"]

_MINIMAL_ELEMENTS: list[ElementRenderer] = [
    _render_git_branch,
    _render_ralph,
    _render_ultrawork,
    _render_ralplan,
    _render_deep_interview,
    _render_autoresearch,
    _render_ultraqa,
    _render_team,
    _render_turns,
]

_FOCUSED_ELEMENTS: list[ElementRenderer] = [
    _render_git_branch,
    _render_ralph,
    _render_ultrawork,
    _render_autopilot,
    _render_ralplan,
    _render_deep_interview,
    _render_autoresearch,
    _render_ultraqa,
    _render_team,
    _render_turns,
    _render_tokens,
    _render_quota,
    _render_session_duration,
    _render_last_activity,
]

_FULL_ELEMENTS: list[ElementRenderer] = [
    _render_git_branch,
    _render_ralph,
    _render_ultrawork,
    _render_autopilot,
    _render_ralplan,
    _render_deep_interview,
    _render_autoresearch,
    _render_ultraqa,
    _render_team,
    _render_turns,
    _render_tokens,
    _render_quota,
    _render_session_duration,
    _render_last_activity,
    _render_total_turns,
]


def _get_elements(preset: str) -> list[ElementRenderer]:
    if preset == HudPreset.MINIMAL or preset == "minimal":
        return _MINIMAL_ELEMENTS
    if preset == HudPreset.FULL or preset == "full":
        return _FULL_ELEMENTS
    return _FOCUSED_ELEMENTS


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------


def _ellipsize_segment(segment: str, max_width: int) -> str:
    """Truncate *segment* with an ellipsis to fit *max_width* visible characters."""
    if not isinstance(max_width, int) or max_width <= 0:
        return ""
    if _visible_length(segment) <= max_width:
        return segment
    plain = _strip_ansi(segment)
    if len(plain) <= max_width:
        return plain
    if max_width <= 1:
        return "…"
    if max_width <= 4:
        return f"{plain[: max(0, max_width - 1)]}…"
    head = max(1, math.ceil((max_width - 1) / 2))
    tail = max(1, math.floor((max_width - 1) / 2))
    return f"{plain[:head]}…{plain[-tail:]}"


def _wrap_hud_parts(label: str, parts: list[str], options: RenderHudOptions) -> str:
    """Wrap rendered parts under a label, respecting width and line caps."""
    raw_max_width = options.max_width
    if (
        isinstance(raw_max_width, int)
        and not isinstance(raw_max_width, bool)
        and raw_max_width > 0
    ):
        max_width: float = max(12, int(raw_max_width))
    elif (
        isinstance(raw_max_width, float)
        and math.isfinite(raw_max_width)
        and raw_max_width > 0
    ):
        max_width = max(12, int(raw_max_width))
    else:
        max_width = math.inf

    raw_max_lines = options.max_lines
    if (
        isinstance(raw_max_lines, int)
        and not isinstance(raw_max_lines, bool)
        and raw_max_lines > 0
    ):
        max_lines = max(1, int(raw_max_lines))
    elif (
        isinstance(raw_max_lines, float)
        and math.isfinite(raw_max_lines)
        and raw_max_lines > 0
    ):
        max_lines = max(1, int(raw_max_lines))
    else:
        max_lines = HUD_TMUX_MAX_HEIGHT_LINES

    if not math.isfinite(max_width):
        return f"{label} {_sep().join(parts)}"

    lines: list[str] = []
    indent = " " * max(0, _visible_length(label) + 1)
    current_line = label
    has_content = False

    def _push_line() -> tuple[str, bool]:
        lines.append(current_line)
        return indent, False

    for part in parts:
        line_prefix = indent if has_content else f"{label} "
        available = max(1, int(max_width) - _visible_length(line_prefix))
        segment = _ellipsize_segment(part, available)
        separator = _sep() if has_content else " "
        candidate = f"{current_line}{separator}{segment}"
        if _visible_length(candidate) <= max_width:
            current_line = candidate
            has_content = True
            continue

        if len(lines) + 1 < max_lines:
            current_line, has_content = _push_line()
            current_line = f"{current_line}{segment}"
            has_content = True
            continue

        overflow = dim("…")
        overflow_candidate = f"{current_line}{_sep() if has_content else ' '}{overflow}"
        if _visible_length(overflow_candidate) <= max_width:
            current_line = overflow_candidate
        else:
            current_line = _ellipsize_segment(current_line, int(max_width) - 1) + "…"
        has_content = True
        break

    lines.append(current_line)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_hud(
    ctx: HudRenderContext,
    preset: str | HudPreset = HudPreset.FOCUSED,
    options: RenderHudOptions | None = None,
) -> str:
    """Render the HUD statusline from context and preset.

    Args:
        ctx: HUD render context with mode/state details.
        preset: Preset name (minimal, focused, full).
        options: Optional sizing inputs.

    Returns:
        Multi-line ANSI string ready to print.
    """
    opts = options or RenderHudOptions()
    preset_value = str(preset)
    elements = _get_elements(preset_value)
    parts: list[str] = []
    for fn in elements:
        rendered = fn(ctx)
        if rendered is not None:
            parts.append(rendered)

    version = _attr(ctx, "version")
    ver_suffix = ""
    if isinstance(version, str) and version:
        ver_suffix = f"#{version.lstrip('v') if version.startswith('v') else version}"
    label = bold(f"[OMX{ver_suffix}]")

    if not parts:
        return _wrap_hud_parts(label, [dim("No active modes.")], opts)
    return _wrap_hud_parts(label, parts, opts)


def count_rendered_hud_lines(text: str) -> int:
    """Count rendered lines, ignoring carriage returns.

    Args:
        text: Rendered HUD text.

    Returns:
        Number of newline-separated lines.
    """
    return len(text.replace("\r", "").split("\n"))


# ---------------------------------------------------------------------------
# Backwards-compatible helper retained for existing statusline callers
# ---------------------------------------------------------------------------


def render_statusline(cwd: str | None = None, preset: str | None = None) -> str:
    """Render the legacy compact statusline string.

    Args:
        cwd: Working directory override for state resolution.
        preset: Optional HUD preset name (reserved for future use).

    Returns:
        Formatted statusline string (e.g. "[autopilot] tools:5").
    """
    resolved = str(resolve_working_directory(cwd))
    result = state_list_active(resolved)
    active_modes = result.get("active_modes", [])
    hud = read_hud_state()
    tool_calls = hud.get("tool_calls", 0)

    parts: list[str] = []
    if active_modes:
        parts.append(f"[{','.join(active_modes)}]")
    else:
        parts.append("[idle]")
    if tool_calls:
        parts.append(f"tools:{tool_calls}")
    return " ".join(parts)
