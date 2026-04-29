"""OMX HUD - ANSI Color Utilities.

Port of src/hud/colors.ts.
Terminal color codes for statusline rendering.
"""

from __future__ import annotations

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
CYAN = "\x1b[36m"

_color_enabled = True


def set_color_enabled(enabled: bool) -> None:
    """Set whether ANSI color codes are emitted.

    Args:
        enabled: True to enable colors, False to disable.
    """
    global _color_enabled
    _color_enabled = enabled


def is_color_enabled() -> bool:
    """Return whether ANSI color codes are enabled.

    Returns:
        True if colors are enabled.
    """
    return _color_enabled


def _wrap_color(code: str, text: str) -> str:
    """Wrap text in an ANSI color code."""
    if not _color_enabled:
        return text
    return f"{code}{text}{RESET}"


def green(text: str) -> str:
    """Wrap text in green ANSI color.

    Args:
        text: Text to colorize.

    Returns:
        Colorized string.
    """
    return _wrap_color(GREEN, text)


def yellow(text: str) -> str:
    """Wrap text in yellow ANSI color.

    Args:
        text: Text to colorize.

    Returns:
        Colorized string.
    """
    return _wrap_color(YELLOW, text)


def cyan(text: str) -> str:
    """Wrap text in cyan ANSI color.

    Args:
        text: Text to colorize.

    Returns:
        Colorized string.
    """
    return _wrap_color(CYAN, text)


def dim(text: str) -> str:
    """Wrap text in dim ANSI style.

    Args:
        text: Text to dim.

    Returns:
        Dimmed string.
    """
    return _wrap_color(DIM, text)


def bold(text: str) -> str:
    """Wrap text in bold ANSI style.

    Args:
        text: Text to bold.

    Returns:
        Bolded string.
    """
    return _wrap_color(BOLD, text)


def get_ralph_color(iteration: int, max_iterations: int) -> str:
    """Get ANSI color code based on ralph iteration progress.

    Args:
        iteration: Current iteration number.
        max_iterations: Maximum iterations.

    Returns:
        ANSI color code string, or empty string if colors disabled.
    """
    if not _color_enabled:
        return ""
    warning_threshold = int(max_iterations * 0.7)
    critical_threshold = int(max_iterations * 0.9)

    if iteration >= critical_threshold:
        return RED
    if iteration >= warning_threshold:
        return YELLOW
    return GREEN
