"""Notification message formatters.

Produces human-readable notification messages for each event type.
Supports markdown (Discord/Telegram) and plain text (Slack/webhook) formats.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from omx.notifications.types import FullNotificationPayload

# ANSI CSI escape sequences and two-character escapes
_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[A-Za-z])")

# OMX UI chrome: spinner/progress indicator characters
_SPINNER_LINE_RE = re.compile(r"^[\u25cf\u23bf\u273b\u00b7\u25fc]")

# tmux expand hint
_CTRL_O_RE = re.compile(r"ctrl\+o to expand", re.IGNORECASE)

# Lines composed entirely of box-drawing characters and whitespace
_BOX_DRAWING_RE = re.compile(
    r"^[\s\u2500\u2550\u2502\u2551\u250c\u2510\u2514\u2518\u252c\u2534\u251c\u2524"
    r"\u2554\u2557\u255a\u255d\u2560\u2563\u2566\u2569\u256c\u255f\u2562\u2564\u2567"
    r"\u256a\u2501\u2503\u250f\u2513\u2517\u251b\u2523\u252b\u2533\u253b\u254b\u2520\u2528\u252f\u2537\u253f\u2542]+$"
)

# OMX HUD status lines
_OMX_HUD_RE = re.compile(r"\[OMX[#\]]")

# Bypass-permissions indicator lines
_BYPASS_PERM_RE = re.compile(r"^\u23f5")

# Bare shell prompt with no command after it
_BARE_PROMPT_RE = re.compile(r"^[\u276f>$%#]+$")

# Minimum ratio of alphanumeric characters for a line to be "meaningful"
_MIN_ALNUM_RATIO = 0.15

# Unicode-aware letters/numbers for density checks
_UNICODE_ALNUM_RE = re.compile(r"[\w]", re.UNICODE)

# Maximum number of meaningful output blocks
_MAX_TAIL_BLOCKS = 10

# Maximum recent-output character budget
_MAX_TAIL_CHARS = 1200


def parse_tmux_tail(raw: str) -> str:
    """Parse raw tmux pane output into clean, human-readable text.

    Strips ANSI escape codes, removes UI chrome lines, drops low-density lines,
    groups continuation lines, and keeps the most recent blocks within budget.

    Args:
        raw: Raw tmux pane output text.

    Returns:
        Cleaned text suitable for notification messages.
    """
    blocks: list[list[str]] = []

    for line in raw.split("\n"):
        stripped = _ANSI_RE.sub("", line)
        trimmed = stripped.strip()

        if not trimmed:
            continue
        if _SPINNER_LINE_RE.search(trimmed):
            continue
        if _CTRL_O_RE.search(trimmed):
            continue
        if _BOX_DRAWING_RE.match(trimmed):
            continue
        if _OMX_HUD_RE.search(trimmed):
            continue
        if _BYPASS_PERM_RE.match(trimmed):
            continue
        if _BARE_PROMPT_RE.match(trimmed):
            continue

        # Unicode-aware density check
        alnum_count = len(_UNICODE_ALNUM_RE.findall(trimmed))
        if len(trimmed) >= 8 and alnum_count / len(trimmed) < _MIN_ALNUM_RATIO:
            continue

        cleaned_line = stripped.rstrip()
        is_continuation = bool(re.match(r"^[\t ]+", cleaned_line))

        if is_continuation and blocks:
            blocks[-1].append(cleaned_line)
            continue

        blocks.append([cleaned_line])

    block_texts = ["\n".join(block) for block in blocks]
    recent_blocks: list[str] = []
    total_chars = 0

    for i in range(len(block_texts) - 1, -1, -1):
        if len(recent_blocks) >= _MAX_TAIL_BLOCKS:
            break

        block = block_texts[i]
        next_total = total_chars + len(block) + (1 if recent_blocks else 0)

        if recent_blocks and next_total > _MAX_TAIL_CHARS:
            break

        recent_blocks.insert(0, block)
        total_chars = next_total

    return "\n".join(recent_blocks)


def _format_duration(ms: int | None) -> str:
    """Format duration from milliseconds to human-readable string."""
    if not ms:
        return "unknown"
    seconds = ms // 1000
    minutes = seconds // 60
    hours = minutes // 60

    if hours > 0:
        return f"{hours}h {minutes % 60}m {seconds % 60}s"
    if minutes > 0:
        return f"{minutes}m {seconds % 60}s"
    return f"{seconds}s"


def _project_display(payload: FullNotificationPayload) -> str:
    """Get project display name from payload."""
    if payload.project_name:
        return payload.project_name
    if payload.project_path:
        return (
            PurePosixPath(payload.project_path).name
            or payload.project_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        )
    return "unknown"


def _build_tmux_tail_block(payload: FullNotificationPayload) -> str:
    """Build tmux tail block with code fence."""
    if not payload.tmux_tail:
        return ""
    cleaned = parse_tmux_tail(payload.tmux_tail)
    if not cleaned:
        return ""
    return f"\n**Recent output:**\n```\n{cleaned}\n```"


def _build_footer(payload: FullNotificationPayload, markdown: bool = True) -> str:
    """Build footer with tmux and project info."""
    parts: list[str] = []

    if payload.tmux_session:
        if markdown:
            parts.append(f"**tmux:** `{payload.tmux_session}`")
        else:
            parts.append(f"tmux: {payload.tmux_session}")

    project = _project_display(payload)
    if markdown:
        parts.append(f"**project:** `{project}`")
    else:
        parts.append(f"project: {project}")

    return " | ".join(parts)


def format_session_start(payload: FullNotificationPayload) -> str:
    """Format a session-start notification message.

    Args:
        payload: The notification payload.

    Returns:
        Formatted message string.
    """
    from datetime import datetime

    time_str = ""
    if payload.timestamp:
        try:
            dt = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
            time_str = dt.strftime("%H:%M:%S")
        except Exception:
            time_str = payload.timestamp

    project = _project_display(payload)
    lines = [
        "# Session Started",
        "",
        f"**Session:** `{payload.session_id}`",
        f"**Project:** `{project}`",
        f"**Time:** {time_str}",
    ]

    if payload.tmux_session:
        lines.append(f"**tmux:** `{payload.tmux_session}`")

    return "\n".join(lines)


def format_session_stop(payload: FullNotificationPayload) -> str:
    """Format a session-stop notification message.

    Args:
        payload: The notification payload.

    Returns:
        Formatted message string.
    """
    lines = ["# Session Continuing", ""]

    if payload.active_mode:
        lines.append(f"**Mode:** {payload.active_mode}")

    if payload.iteration is not None and payload.max_iterations is not None:
        lines.append(f"**Iteration:** {payload.iteration}/{payload.max_iterations}")

    if payload.incomplete_tasks is not None and payload.incomplete_tasks > 0:
        lines.append(f"**Incomplete tasks:** {payload.incomplete_tasks}")

    tail = _build_tmux_tail_block(payload)
    if tail:
        lines.append(tail)

    lines.append("")
    lines.append(_build_footer(payload, True))

    return "\n".join(lines)


def format_session_end(payload: FullNotificationPayload) -> str:
    """Format a session-end notification message.

    Args:
        payload: The notification payload.

    Returns:
        Formatted message string.
    """
    duration = _format_duration(payload.duration_ms)

    lines = [
        "# Session Ended",
        "",
        f"**Session:** `{payload.session_id}`",
        f"**Duration:** {duration}",
        f"**Reason:** {payload.reason or 'unknown'}",
    ]

    if payload.agents_spawned is not None:
        completed = payload.agents_completed or 0
        lines.append(f"**Agents:** {completed}/{payload.agents_spawned} completed")

    if payload.modes_used and len(payload.modes_used) > 0:
        lines.append(f"**Modes:** {', '.join(payload.modes_used)}")

    if payload.context_summary:
        lines.extend(["", f"**Summary:** {payload.context_summary}"])

    tail = _build_tmux_tail_block(payload)
    if tail:
        lines.append(tail)

    lines.append("")
    lines.append(_build_footer(payload, True))

    return "\n".join(lines)


def format_session_idle(payload: FullNotificationPayload) -> str:
    """Format a session-idle notification message.

    Args:
        payload: The notification payload.

    Returns:
        Formatted message string.
    """
    lines = ["# Session Idle", ""]
    lines.append("Codex has finished and is waiting for input.")
    lines.append("")

    if payload.reason:
        lines.append(f"**Reason:** {payload.reason}")

    if payload.modes_used and len(payload.modes_used) > 0:
        lines.append(f"**Modes:** {', '.join(payload.modes_used)}")

    tail = _build_tmux_tail_block(payload)
    if tail:
        lines.append(tail)

    lines.append("")
    lines.append(_build_footer(payload, True))

    return "\n".join(lines)


def format_ask_user_question(payload: FullNotificationPayload) -> str:
    """Format an ask-user-question notification message.

    Args:
        payload: The notification payload.

    Returns:
        Formatted message string.
    """
    lines = ["# Input Needed", ""]

    if payload.question:
        lines.append(f"**Question:** {payload.question}")
        lines.append("")

    lines.append("Codex is waiting for your response.")
    lines.append("")
    lines.append(_build_footer(payload, True))

    return "\n".join(lines)


def format_notification(payload: FullNotificationPayload) -> str:
    """Format a notification message based on event type.

    Args:
        payload: The notification payload.

    Returns:
        Formatted message string.
    """
    match payload.event:
        case "session-start":
            return format_session_start(payload)
        case "session-stop":
            return format_session_stop(payload)
        case "session-end":
            return format_session_end(payload)
        case "session-idle":
            return format_session_idle(payload)
        case "ask-user-question":
            return format_ask_user_question(payload)
        case _:
            return payload.message or f"Event: {payload.event}"
