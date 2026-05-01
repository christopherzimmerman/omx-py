"""Codex native hook handler.

Executable as ``python -m omx.scripts.codex_native_hook``.
Reads event data from stdin (JSON) and dispatches to the
appropriate handler based on the event type.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

# Known skill keywords for detection
SKILL_KEYWORDS: list[str] = [
    "team",
    "ralph",
    "autopilot",
    "deep-interview",
    "ultrawork",
    "ultraqa",
    "autoresearch",
    "ralplan",
    "plan",
    "tdd",
    "build-fix",
    "wiki",
    "code-review",
    "security-review",
    "analyze",
    "cancel",
]


def main() -> None:
    """Entry point for the native hook handler.

    Reads the event type from argv[1] (if provided) and event data
    from stdin as JSON. Dispatches to the appropriate handler.
    """
    event_type = sys.argv[1] if len(sys.argv) > 1 else None

    # Read event data from stdin
    event_data: dict = {}
    try:
        raw = sys.stdin.read()
        if raw.strip():
            event_data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        pass

    if not event_type:
        event_type = event_data.get("event", "unknown")

    cwd = os.environ.get("OMX_CWD", os.getcwd())
    # Prefer the omx-launched session id; fall back to the provider's session
    # id from the hook payload (claude includes session_id in every event).
    session_id = os.environ.get("OMX_SESSION_ID") or str(
        event_data.get("session_id") or ""
    )

    try:
        _dispatch_event(event_type, event_data, cwd, session_id)
    except Exception as exc:
        # Never crash the hook — log and exit cleanly
        _log_error(cwd, event_type, str(exc))
        sys.exit(0)


def _dispatch_event(
    event_type: str,
    event_data: dict,
    cwd: str,
    session_id: str,
) -> None:
    """Route event to the appropriate handler.

    Args:
        event_type: The hook event name.
        event_data: Parsed JSON event payload.
        cwd: Working directory.
        session_id: Current session ID.
    """
    match event_type:
        case "session-start":
            _handle_session_start(event_data, cwd, session_id)
        case "user-prompt-submit":
            _handle_user_prompt_submit(event_data, cwd, session_id)
        case "pre-tool-use":
            _handle_pre_tool_use(event_data, cwd, session_id)
        case "post-tool-use":
            _handle_post_tool_use(event_data, cwd, session_id)
        case "stop":
            _handle_stop(event_data, cwd, session_id)
        case _:
            pass  # Unknown event, silently ignore

    # Dispatch to plugins
    _dispatch_to_plugins(event_type, event_data, cwd, session_id)


def _handle_session_start(event_data: dict, cwd: str, session_id: str) -> None:
    """Handle session-start event.

    Args:
        event_data: Event payload.
        cwd: Working directory.
        session_id: Session identifier.
    """
    from omx.hooks.session import write_session_start

    native_sid = event_data.get("session_id") or event_data.get("native_session_id")
    write_session_start(
        cwd, session_id=session_id or None, native_session_id=native_sid
    )


def _handle_user_prompt_submit(event_data: dict, cwd: str, session_id: str) -> None:
    """Handle user-prompt-submit event.

    Detects skill keywords in the prompt text and writes skill-active
    state when a keyword is found.

    Args:
        event_data: Event payload with prompt text.
        cwd: Working directory.
        session_id: Session identifier.
    """
    prompt_text = _extract_prompt_text(event_data)
    if not prompt_text:
        return

    skill = detect_skill_from_prompt(prompt_text)
    if skill:
        _activate_skill(skill, cwd, session_id)


def _handle_pre_tool_use(event_data: dict, cwd: str, session_id: str) -> None:
    """Handle pre-tool-use event.

    Args:
        event_data: Event payload.
        cwd: Working directory.
        session_id: Session identifier.
    """
    # Currently a pass-through for plugin dispatch


def _handle_post_tool_use(event_data: dict, cwd: str, session_id: str) -> None:
    """Handle post-tool-use event.

    Args:
        event_data: Event payload.
        cwd: Working directory.
        session_id: Session identifier.
    """
    # Currently a pass-through for plugin dispatch


def _handle_stop(event_data: dict, cwd: str, session_id: str) -> None:
    """Handle stop event.

    Args:
        event_data: Event payload.
        cwd: Working directory.
        session_id: Session identifier.
    """
    if session_id:
        from omx.hooks.session import write_session_end

        write_session_end(cwd, session_id)


def _extract_prompt_text(event_data: dict) -> str:
    """Extract prompt text from event data.

    Args:
        event_data: Event payload.

    Returns:
        The prompt text, or empty string if not found.
    """
    # Try various known payload shapes
    if "prompt" in event_data:
        return str(event_data["prompt"])
    if "text" in event_data:
        return str(event_data["text"])
    context = event_data.get("context", {})
    if isinstance(context, dict):
        if "prompt" in context:
            return str(context["prompt"])
        if "text" in context:
            return str(context["text"])
    return ""


def detect_skill_from_prompt(text: str) -> str | None:
    """Detect a skill keyword in prompt text.

    Checks for $keyword triggers and natural language keyword matches
    using the keyword registry.

    Args:
        text: The user's prompt text.

    Returns:
        Matched skill name, or None.
    """
    stripped = text.strip().lower()
    if not stripped:
        return None

    # Check $keyword triggers first
    if stripped.startswith("$"):
        keyword = stripped[1:].split()[0] if stripped[1:] else ""
        if keyword in SKILL_KEYWORDS:
            return keyword

    # Fall back to keyword registry for natural language triggers
    try:
        from omx.hooks.keyword_registry import (
            KEYWORD_TRIGGER_DEFINITIONS,
            compare_keyword_matches,
        )

        matches = [
            defn
            for defn in KEYWORD_TRIGGER_DEFINITIONS
            if defn.keyword.lower() in stripped
        ]
        if matches:
            import functools

            matches.sort(key=functools.cmp_to_key(compare_keyword_matches))
            return matches[0].skill
    except ImportError:
        pass

    return None


def _activate_skill(skill: str, cwd: str, session_id: str) -> None:
    """Write skill-active state for the detected skill.

    Args:
        skill: The skill name to activate.
        cwd: Working directory.
        session_id: Session identifier.
    """
    try:
        from omx.state.operations import state_write

        state_write(
            "skill-active",
            cwd,
            {
                "active": True,
                "skill": skill,
                "activated_at": datetime.now(timezone.utc).isoformat(),
            },
            session_id or None,
        )
    except Exception:
        pass  # Best-effort


def _dispatch_to_plugins(
    event_type: str,
    event_data: dict,
    cwd: str,
    session_id: str,
) -> None:
    """Dispatch hook event to extensibility plugins.

    Args:
        event_type: The hook event name.
        event_data: Event payload.
        cwd: Working directory.
        session_id: Session identifier.
    """
    try:
        from omx.hooks.dispatcher import dispatch_hook_event_runtime
        from omx.hooks.types import build_native_hook_event

        context = event_data.get("context", event_data)
        event = build_native_hook_event(
            event_type,
            context if isinstance(context, dict) else {},
            session_id=session_id or None,
        )
        dispatch_hook_event_runtime(cwd, event)
    except Exception:
        pass  # Best-effort plugin dispatch


def _log_error(cwd: str, event_type: str, error: str) -> None:
    """Log a hook error to the daily log file.

    Args:
        cwd: Working directory.
        event_type: The event that caused the error.
        error: Error message.
    """
    try:
        from omx.hooks.session import append_to_log

        append_to_log(
            cwd,
            {
                "event": "hook_error",
                "hook_event": event_type,
                "error": error,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception:
        pass  # Cannot log — silently discard


if __name__ == "__main__":
    main()
