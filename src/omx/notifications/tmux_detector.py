"""tmux pane interaction utilities for reply listener.

Provides functions to capture pane content, analyze whether a pane is running
Codex CLI, and inject text into panes. Used by the reply-listener daemon.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass


def is_tmux_available() -> bool:
    """Check if tmux binary is available on PATH.

    Returns:
        True if tmux is found.
    """
    return shutil.which("tmux") is not None


def build_capture_pane_argv(pane_id: str, lines: int) -> list[str]:
    """Build the argv array for tmux capture-pane.

    Args:
        pane_id: tmux pane identifier, e.g. "%3".
        lines: Number of lines to capture.

    Returns:
        List of arguments for tmux.
    """
    return [
        "capture-pane",
        "-p",
        "-t",
        pane_id,
        "-S",
        str(-lines),
    ]


def capture_pane_content(pane_id: str, lines: int = 15) -> str:
    """Capture the last N lines from a tmux pane.

    Args:
        pane_id: tmux pane identifier.
        lines: Number of lines to capture.

    Returns:
        Captured pane content, or empty string on failure.
    """
    try:
        result = subprocess.run(
            ["tmux", *build_capture_pane_argv(pane_id, lines)],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        return result.stdout
    except Exception:
        return ""


@dataclass
class PaneAnalysis:
    """Analysis of tmux pane content.

    Attributes:
        has_codex: Whether pane appears to be running Codex.
        has_rate_limit_message: Whether rate limit messages are present.
        is_blocked: Whether the session appears blocked/waiting.
        confidence: Confidence score (0-1).
    """

    has_codex: bool = False
    has_rate_limit_message: bool = False
    is_blocked: bool = False
    confidence: float = 0.0


def analyze_pane_content(content: str) -> PaneAnalysis:
    """Analyze pane content to determine if it's running Codex CLI.

    Args:
        content: Pane content text.

    Returns:
        PaneAnalysis with detection results.
    """
    lower = content.lower()

    has_codex = any(kw in lower for kw in ("codex", "omx", "oh-my-codex", "openai"))

    has_rate_limit = any(kw in lower for kw in ("rate limit", "rate-limit", "429"))

    is_blocked = any(kw in lower for kw in ("waiting", "blocked", "paused"))

    confidence = 0.0
    if has_codex:
        confidence += 0.5
    if ">" in lower or "$" in lower:
        confidence += 0.1
    if "agent" in lower or "task" in lower:
        confidence += 0.1
    if content.strip():
        confidence += 0.1

    return PaneAnalysis(
        has_codex=has_codex,
        has_rate_limit_message=has_rate_limit,
        is_blocked=is_blocked,
        confidence=min(confidence, 1.0),
    )


def build_send_pane_argvs(
    pane_id: str,
    text: str,
    press_enter: bool = True,
) -> list[list[str]]:
    """Build tmux send-keys argv arrays for typing text into a pane.

    C-m is always sent in its own dedicated send-keys call to prevent
    newline/submit injection.

    Args:
        pane_id: tmux pane identifier.
        text: Text to type; embedded newlines replaced with spaces.
        press_enter: When True, appends C-m submit calls.

    Returns:
        List of argv arrays, one per send-keys invocation.
    """
    safe = re.sub(r"\r?\n", " ", text)
    argvs: list[list[str]] = [["send-keys", "-t", pane_id, "-l", "--", safe]]

    if press_enter:
        argvs.append(["send-keys", "-t", pane_id, "C-m"])
        argvs.append(["send-keys", "-t", pane_id, "C-m"])

    return argvs


_TMUX_TEXT_SETTLE_MS = 120
_TMUX_SUBMIT_REPEAT_DELAY_MS = 100


def send_to_pane(
    pane_id: str,
    text: str,
    press_enter: bool = True,
) -> bool:
    """Send text to a tmux pane via send-keys.

    Args:
        pane_id: tmux pane identifier.
        text: Text to send.
        press_enter: Whether to press Enter after sending.

    Returns:
        True if all send-keys succeeded.
    """
    argvs = build_send_pane_argvs(pane_id, text, press_enter)

    for i, argv in enumerate(argvs):
        try:
            result = subprocess.run(
                ["tmux", *argv],
                capture_output=True,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW
                if sys.platform == "win32"
                else 0,
            )
            if result.returncode != 0:
                return False
        except Exception:
            return False

        has_next = i < len(argvs) - 1
        if has_next:
            delay = _TMUX_TEXT_SETTLE_MS if i == 0 else _TMUX_SUBMIT_REPEAT_DELAY_MS
            time.sleep(delay / 1000.0)

    return True
