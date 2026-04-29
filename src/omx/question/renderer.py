"""Question renderer — terminal display and answer injection.

Port of src/question/renderer.ts. Handles strategy selection, answer
formatting, and renderer liveness checks using ANSI terminal codes.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from typing import Any

from omx.question.types import AnswerKind, QuestionAnswer, QuestionRendererState


class QuestionRendererStrategy(StrEnum):
    """Strategy for how a question renderer is launched."""

    INSIDE_TMUX = "inside-tmux"
    DETACHED_TMUX = "detached-tmux"
    INLINE_TTY = "inline-tty"
    WINDOWS_CONSOLE = "windows-console"
    TEST_NOOP = "test-noop"
    UNSUPPORTED = "unsupported"


_PANE_ID_RE = re.compile(r"^%\d+$")


def _is_pane_id(value: str | None) -> bool:
    """Check if a value looks like a tmux pane ID (%N)."""
    if not value:
        return False
    return bool(_PANE_ID_RE.match(value.strip()))


def _safe_string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def resolve_question_renderer_strategy(
    env: dict[str, str] | None = None,
    *,
    stdin_is_tty: bool = False,
    stdout_is_tty: bool = False,
) -> QuestionRendererStrategy:
    """Resolve which renderer strategy to use based on environment.

    Args:
        env: Environment variables (defaults to os.environ).
        stdin_is_tty: Whether stdin is a TTY.
        stdout_is_tty: Whether stdout is a TTY.

    Returns:
        The appropriate QuestionRendererStrategy.
    """
    env = env or dict(os.environ)
    platform = os.name  # 'nt' on Windows, 'posix' on Unix

    if _safe_string(env.get("OMX_QUESTION_TEST_RENDERER", "")).strip() == "noop":
        return QuestionRendererStrategy.TEST_NOOP

    # Windows psmux bridge check
    if platform == "nt":
        return_pane = _safe_string(
            env.get("OMX_QUESTION_RETURN_PANE", "") or env.get("OMX_LEADER_PANE_ID", "")
        ).strip()
        if _is_pane_id(return_pane):
            return QuestionRendererStrategy.WINDOWS_CONSOLE
        tmux = _safe_string(env.get("TMUX", "")).strip().lower()
        tmux_pane = _safe_string(env.get("TMUX_PANE", "")).strip()
        if tmux and ("psmux" in tmux or _is_pane_id(tmux_pane)):
            return QuestionRendererStrategy.WINDOWS_CONSOLE

    # Inside tmux
    if _safe_string(env.get("TMUX", "")).strip():
        return QuestionRendererStrategy.INSIDE_TMUX

    # Explicit return pane
    return_pane = _safe_string(
        env.get("OMX_QUESTION_RETURN_PANE", "") or env.get("OMX_LEADER_PANE_ID", "")
    ).strip()
    if _is_pane_id(return_pane):
        return QuestionRendererStrategy.INSIDE_TMUX

    # Inline TTY fallback on Windows
    if platform == "nt" and stdin_is_tty and stdout_is_tty:
        return QuestionRendererStrategy.INLINE_TTY

    return QuestionRendererStrategy.UNSUPPORTED


def format_question_answer_for_injection(answer: QuestionAnswer) -> str:
    """Format an answer for injection into a tmux pane.

    Args:
        answer: The user's answer.

    Returns:
        Formatted text safe for tmux send-keys injection.
    """
    prefix = "[omx question answered]"
    match answer.kind:
        case AnswerKind.OTHER:
            text = answer.other_text or str(answer.value)
        case AnswerKind.MULTI:
            if isinstance(answer.value, list):
                text = ", ".join(answer.value)
            else:
                text = str(answer.value)
        case _:
            text = str(answer.value)

    raw = f"{prefix} {text}"
    # Sanitise for tmux injection (strip control chars)
    return re.sub(r"[\x00-\x1f\x7f]", "", raw).strip()


def is_question_renderer_alive(
    renderer: QuestionRendererState | None,
) -> bool:
    """Check if a question renderer process is still alive.

    Args:
        renderer: The renderer state to check.

    Returns:
        True if the renderer appears alive or state is unknown.
    """
    if renderer is None:
        return True

    if renderer.renderer == "windows-console":
        pid = renderer.pid
        if pid is None or pid <= 0:
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    # For tmux-based renderers and inline-tty, assume alive
    # (full tmux liveness check requires tmux binary access)
    return True
