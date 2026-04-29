"""Session status updates.

Provides status command detection and session status reply building
for Discord/Telegram reply listener integration.
"""

from __future__ import annotations

from omx.notifications.session_registry import SessionMapping

DISCORD_STATUS_COMMAND = "status"
DISCORD_STATUS_STALE_AFTER_MS = 5 * 60_000
DISCORD_STATUS_MAX_SUBAGENTS = 3
NO_TRACKED_SESSION_MESSAGE = "No tracked OMX session is associated with this message."
STATUS_DATA_UNAVAILABLE_MESSAGE = (
    "Tracked OMX session found, but status data is unavailable."
)


def is_discord_status_command(input_text: str) -> bool:
    """Check if input text is the Discord status command.

    Args:
        input_text: The message text to check.

    Returns:
        True if it is the status command.
    """
    return input_text.strip().lower() == DISCORD_STATUS_COMMAND


def _shorten_identifier(identifier: str) -> str:
    """Shorten an identifier to 6 characters."""
    trimmed = identifier.strip()
    if len(trimmed) <= 6:
        return trimmed
    return trimmed[:6]


def build_discord_session_status_reply(mapping: SessionMapping) -> str:
    """Build a session status reply message for Discord.

    Produces a minimal status reply since the Python port does not have
    access to the full runtime/session state infrastructure yet.

    Args:
        mapping: The session mapping to report on.

    Returns:
        Status reply message string.
    """
    if not mapping.project_path:
        return STATUS_DATA_UNAVAILABLE_MESSAGE

    tmux_session_name = (mapping.tmux_session_name or "").strip() or "unknown"
    tmux_pane_id = (mapping.tmux_pane_id or "").strip() or "unknown"

    lines = [
        "Tracked OMX session status",
        f"Session: {mapping.session_id}",
        "State: unknown",
        f"Tmux: {tmux_session_name} / {tmux_pane_id}",
    ]

    return "\n".join(lines)
