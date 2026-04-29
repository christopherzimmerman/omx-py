"""Session registry module.

Maps platform message IDs to tmux pane IDs for reply correlation.
Uses JSONL append format for atomic writes.

Registry location: ~/.omx/state/reply-session-registry.jsonl
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

_REGISTRY_PATH = Path.home() / ".omx" / "state" / "reply-session-registry.jsonl"
_MAX_AGE_MS = 24 * 60 * 60 * 1000  # 24 hours


@dataclass
class SessionMapping:
    """Maps a platform message to a tmux pane for reply correlation.

    Attributes:
        platform: Platform name (discord-bot or telegram).
        message_id: Platform message ID.
        session_id: OMX session ID.
        tmux_pane_id: tmux pane ID for injection.
        tmux_session_name: tmux session name.
        event: Notification event that created this mapping.
        created_at: ISO timestamp of creation.
        project_path: Project directory path.
    """

    platform: str
    message_id: str
    session_id: str
    tmux_pane_id: str
    tmux_session_name: str
    event: str
    created_at: str
    project_path: str | None = None


def _ensure_registry_dir() -> None:
    """Ensure the registry directory exists."""
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)


def _read_all_mappings() -> list[SessionMapping]:
    """Read all mappings from the registry file (no locking)."""
    if not _REGISTRY_PATH.exists():
        return []
    try:
        content = _REGISTRY_PATH.read_text(encoding="utf-8")
        result: list[SessionMapping] = []
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
                result.append(
                    SessionMapping(
                        platform=raw.get("platform", ""),
                        message_id=raw.get("messageId", ""),
                        session_id=raw.get("sessionId", ""),
                        tmux_pane_id=raw.get("tmuxPaneId", ""),
                        tmux_session_name=raw.get("tmuxSessionName", ""),
                        event=raw.get("event", ""),
                        created_at=raw.get("createdAt", ""),
                        project_path=raw.get("projectPath"),
                    )
                )
            except Exception:
                pass
        return result
    except Exception:
        return []


def _write_registry(mappings: list[SessionMapping]) -> None:
    """Write all mappings to the registry file."""
    _ensure_registry_dir()
    if not mappings:
        _REGISTRY_PATH.write_text("", encoding="utf-8")
        return
    lines = [json.dumps(_mapping_to_dict(m)) for m in mappings]
    _REGISTRY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _mapping_to_dict(m: SessionMapping) -> dict:
    """Convert a mapping to a JSON-serializable dict."""
    d: dict = {
        "platform": m.platform,
        "messageId": m.message_id,
        "sessionId": m.session_id,
        "tmuxPaneId": m.tmux_pane_id,
        "tmuxSessionName": m.tmux_session_name,
        "event": m.event,
        "createdAt": m.created_at,
    }
    if m.project_path:
        d["projectPath"] = m.project_path
    return d


def register_message(mapping: SessionMapping) -> bool:
    """Register a message-to-pane mapping.

    Args:
        mapping: The session mapping to register.

    Returns:
        True if registration succeeded.
    """
    try:
        _ensure_registry_dir()
        line = json.dumps(_mapping_to_dict(mapping)) + "\n"
        with open(_REGISTRY_PATH, "a", encoding="utf-8") as f:
            f.write(line)
        return True
    except Exception:
        return False


def load_all_mappings() -> list[SessionMapping]:
    """Load all session mappings from the registry.

    Returns:
        List of all current mappings.
    """
    return _read_all_mappings()


def lookup_by_message_id(platform: str, message_id: str) -> SessionMapping | None:
    """Look up a mapping by platform and message ID.

    Args:
        platform: Platform name.
        message_id: Message ID to look up.

    Returns:
        The matching mapping, or None.
    """
    mappings = load_all_mappings()
    for m in reversed(mappings):
        if m.platform == platform and m.message_id == message_id:
            return m
    return None


def remove_session(session_id: str) -> None:
    """Remove all mappings for a session.

    Args:
        session_id: Session ID to remove.
    """
    mappings = _read_all_mappings()
    filtered = [m for m in mappings if m.session_id != session_id]
    if len(filtered) < len(mappings):
        _write_registry(filtered)


def remove_messages_by_pane(pane_id: str) -> None:
    """Remove all mappings for a tmux pane.

    Args:
        pane_id: Pane ID to remove.
    """
    mappings = _read_all_mappings()
    filtered = [m for m in mappings if m.tmux_pane_id != pane_id]
    if len(filtered) < len(mappings):
        _write_registry(filtered)


def prune_stale() -> None:
    """Remove mappings older than 24 hours."""
    now_ms = time.time() * 1000
    mappings = _read_all_mappings()
    filtered: list[SessionMapping] = []
    for m in mappings:
        try:
            from datetime import datetime

            created_ms = (
                datetime.fromisoformat(m.created_at.replace("Z", "+00:00")).timestamp()
                * 1000
            )
            if now_ms - created_ms < _MAX_AGE_MS:
                filtered.append(m)
        except Exception:
            pass

    if len(filtered) < len(mappings):
        _write_registry(filtered)
