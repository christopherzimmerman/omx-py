"""Reply listener daemon.

Background daemon that polls Discord and Telegram for replies to notification
messages, sanitizes input, verifies the target pane, and injects reply text.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from omx.notifications.tmux_detector import (
    capture_pane_content,
)
from omx.notifications.formatter import parse_tmux_tail

_STATE_DIR = Path.home() / ".omx" / "state"
_PID_FILE = _STATE_DIR / "reply-listener.pid"
_STATE_FILE = _STATE_DIR / "reply-listener-state.json"
_CONFIG_FILE = _STATE_DIR / "reply-listener-config.json"
_LOG_FILE = _STATE_DIR / "reply-listener.log"
_MAX_LOG_SIZE = 1 * 1024 * 1024

_MIN_POLL_INTERVAL_MS = 500
_MAX_POLL_INTERVAL_MS = 60_000
_DEFAULT_POLL_INTERVAL_MS = 3_000
_DEFAULT_RATE_LIMIT = 10
_DEFAULT_MAX_MSG_LENGTH = 500
_MAX_MSG_LENGTH_MAX = 4_000
_REPLY_ACK_CAPTURE_LINES = 200
_REPLY_ACK_SUMMARY_MAX_CHARS = 700
_REPLY_ACK_PREFIX = "Injected into Codex CLI session."
_REPLY_ACK_FALLBACK = "Recent output summary unavailable."

_SENSITIVE_KEY_RE = re.compile(
    r"""(["']?(?:api[_-]?key|token|secret|password|credentials?|authorization)["']?\s*[=:]\s*)"""
    r"""(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\n]+)""",
    re.IGNORECASE,
)
_SENSITIVE_TOKEN_PATTERNS = [
    re.compile(
        r"(?:sk-(?:proj-|live-|test-)?|ghp_|gho_|ghs_|ghu_|github_pat_|xox[bpsar]-|glpat-|AKIA[A-Z0-9])\S+"
    ),
]


@dataclass
class ReplyListenerState:
    """State of the reply listener daemon.

    Attributes:
        is_running: Whether the daemon is currently running.
        pid: Daemon process ID.
        started_at: ISO timestamp when daemon started.
        last_poll_at: ISO timestamp of last poll.
        telegram_last_update_id: Last Telegram update ID processed.
        discord_last_message_id: Last Discord message ID processed.
        messages_injected: Total messages injected count.
        errors: Total error count.
        last_error: Last error message.
    """

    is_running: bool = False
    pid: int | None = None
    started_at: str | None = None
    last_poll_at: str | None = None
    telegram_last_update_id: int | None = None
    discord_last_message_id: str | None = None
    messages_injected: int = 0
    errors: int = 0
    last_error: str | None = None


@dataclass
class ReplyListenerDaemonConfig:
    """Configuration for the reply listener daemon.

    Attributes:
        enabled: Whether reply listening is enabled.
        poll_interval_ms: Polling interval in milliseconds.
        max_message_length: Maximum message length.
        rate_limit_per_minute: Rate limit per minute.
        include_prefix: Whether to include platform prefix.
        authorized_discord_user_ids: Authorized Discord user IDs.
        telegram_enabled: Whether Telegram polling is enabled.
        telegram_bot_token: Telegram bot token.
        telegram_chat_id: Telegram chat ID.
        discord_enabled: Whether Discord polling is enabled.
        discord_bot_token: Discord bot token.
        discord_channel_id: Discord channel ID.
        discord_mention: Discord mention string.
    """

    enabled: bool = True
    poll_interval_ms: int = _DEFAULT_POLL_INTERVAL_MS
    max_message_length: int = _DEFAULT_MAX_MSG_LENGTH
    rate_limit_per_minute: int = _DEFAULT_RATE_LIMIT
    include_prefix: bool = True
    authorized_discord_user_ids: list[str] = field(default_factory=list)
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    discord_enabled: bool = False
    discord_bot_token: str | None = None
    discord_channel_id: str | None = None
    discord_mention: str | None = None


@dataclass
class DaemonResponse:
    """Response from a daemon control operation.

    Attributes:
        success: Whether the operation succeeded.
        message: Human-readable status message.
        state: Current daemon state.
        error: Error message if failed.
    """

    success: bool
    message: str
    state: ReplyListenerState | None = None
    error: str | None = None


def sanitize_reply_input(text: str) -> str:
    """Sanitize reply input for safe injection into a tmux pane.

    Args:
        text: Raw input text.

    Returns:
        Sanitized text safe for injection.
    """
    result = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    result = re.sub(r"[\u200e\u200f\u202a-\u202e\u2066-\u2069]", "", result)
    result = re.sub(r"\r?\n", " ", result)
    result = result.replace("\\", "\\\\")
    result = result.replace("`", "\\`")
    result = result.replace("$(", "\\$(")
    result = result.replace("${", "\\${")
    return result.strip()


def redact_sensitive_tokens(text: str) -> str:
    """Redact sensitive tokens from text.

    Args:
        text: Text that may contain secrets.

    Returns:
        Text with secrets redacted.
    """

    def _redact_keyed(m: re.Match) -> str:
        prefix = m.group(1)
        value = m.group(0)[len(prefix) :].lstrip()
        quote = '"' if value.startswith('"') else "'" if value.startswith("'") else ""
        return f"{prefix}{quote}[REDACTED]{quote}"

    result = _SENSITIVE_KEY_RE.sub(_redact_keyed, text)
    for pattern in _SENSITIVE_TOKEN_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    return result


def capture_reply_acknowledgement_summary(pane_id: str) -> str | None:
    """Capture a summary of recent pane output for reply acknowledgement.

    Args:
        pane_id: tmux pane ID to capture from.

    Returns:
        Cleaned and redacted summary, or None.
    """
    raw = capture_pane_content(pane_id, _REPLY_ACK_CAPTURE_LINES)
    if not raw:
        return None

    summary = redact_sensitive_tokens(
        re.sub(
            r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
            "",
            parse_tmux_tail(raw).replace("\r", ""),
        ).strip()
    )

    if not summary:
        return None
    if len(summary) <= _REPLY_ACK_SUMMARY_MAX_CHARS:
        return summary
    return summary[: _REPLY_ACK_SUMMARY_MAX_CHARS - 1].rstrip() + "\u2026"


def format_reply_acknowledgement(summary: str | None) -> str:
    """Format a reply acknowledgement message.

    Args:
        summary: Recent output summary, or None.

    Returns:
        Formatted acknowledgement message.
    """
    if not summary:
        return f"{_REPLY_ACK_PREFIX}\n\n{_REPLY_ACK_FALLBACK}"
    return f"{_REPLY_ACK_PREFIX}\n\nRecent output:\n{summary}"


class RateLimiter:
    """Simple sliding-window rate limiter.

    Attributes:
        max_per_minute: Maximum allowed actions per minute.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._max_per_minute = max_per_minute
        self._timestamps: list[float] = []
        self._window_ms = 60 * 1000

    def can_proceed(self) -> bool:
        """Check if an action is allowed and record it.

        Returns:
            True if the action is allowed.
        """
        now = time.time() * 1000
        self._timestamps = [t for t in self._timestamps if now - t < self._window_ms]
        if len(self._timestamps) >= self._max_per_minute:
            return False
        self._timestamps.append(now)
        return True

    def reset(self) -> None:
        """Reset the rate limiter."""
        self._timestamps = []


def normalize_reply_listener_config(
    config: ReplyListenerDaemonConfig,
) -> ReplyListenerDaemonConfig:
    """Normalize reply listener config values to valid ranges.

    Args:
        config: Raw daemon config.

    Returns:
        Normalized daemon config.
    """
    discord_enabled = config.discord_enabled or bool(
        config.discord_bot_token and config.discord_channel_id
    )
    telegram_enabled = config.telegram_enabled or bool(
        config.telegram_bot_token and config.telegram_chat_id
    )

    def _clamp(val: int, default: int, min_v: int, max_v: int | None = None) -> int:
        if val < min_v:
            return default
        if max_v is not None and val > max_v:
            return max_v
        return val

    return ReplyListenerDaemonConfig(
        enabled=config.enabled,
        poll_interval_ms=_clamp(
            config.poll_interval_ms,
            _DEFAULT_POLL_INTERVAL_MS,
            _MIN_POLL_INTERVAL_MS,
            _MAX_POLL_INTERVAL_MS,
        ),
        max_message_length=_clamp(
            config.max_message_length, _DEFAULT_MAX_MSG_LENGTH, 1, _MAX_MSG_LENGTH_MAX
        ),
        rate_limit_per_minute=max(1, config.rate_limit_per_minute),
        include_prefix=config.include_prefix,
        authorized_discord_user_ids=[
            uid
            for uid in config.authorized_discord_user_ids
            if isinstance(uid, str) and uid.strip()
        ],
        telegram_enabled=telegram_enabled,
        telegram_bot_token=config.telegram_bot_token,
        telegram_chat_id=config.telegram_chat_id,
        discord_enabled=discord_enabled,
        discord_bot_token=config.discord_bot_token,
        discord_channel_id=config.discord_channel_id,
        discord_mention=config.discord_mention,
    )


def _is_process_running(pid: int) -> bool:
    """Check if a process with the given PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pid_file() -> int | None:
    """Read PID from the PID file."""
    try:
        if not _PID_FILE.exists():
            return None
        content = _PID_FILE.read_text(encoding="utf-8").strip()
        return int(content) if content else None
    except Exception:
        return None


def is_daemon_running() -> bool:
    """Check if the reply listener daemon is running.

    Returns:
        True if daemon is currently running.
    """
    pid = _read_pid_file()
    if pid is None:
        return False
    if not _is_process_running(pid):
        try:
            _PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return False
    return True


def get_reply_listener_status() -> DaemonResponse:
    """Get the current reply listener daemon status.

    Returns:
        DaemonResponse with current status.
    """
    running = is_daemon_running()
    state = _read_daemon_state()

    if not running and not state:
        return DaemonResponse(
            success=True, message="Reply listener daemon has never been started"
        )

    if not running and state:
        state.is_running = False
        state.pid = None
        return DaemonResponse(
            success=True, message="Reply listener daemon is not running", state=state
        )

    return DaemonResponse(
        success=True, message="Reply listener daemon is running", state=state
    )


def _read_daemon_state() -> ReplyListenerState | None:
    """Read daemon state from file."""
    try:
        if not _STATE_FILE.exists():
            return None
        raw = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        return ReplyListenerState(
            is_running=raw.get("isRunning", False),
            pid=raw.get("pid"),
            started_at=raw.get("startedAt"),
            last_poll_at=raw.get("lastPollAt"),
            telegram_last_update_id=raw.get("telegramLastUpdateId"),
            discord_last_message_id=raw.get("discordLastMessageId"),
            messages_injected=raw.get("messagesInjected", 0),
            errors=raw.get("errors", 0),
            last_error=raw.get("lastError"),
        )
    except Exception:
        return None
