"""Codex hooks.json registration and management.

Port of src/config/codex-hooks.ts.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

MANAGED_HOOK_EVENTS = (
    "SessionStart",
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "Stop",
)

JsonObject = dict[str, Any]


def _is_plain_object(value: Any) -> bool:
    return isinstance(value, dict)


def _build_command_hook(
    command: str,
    *,
    matcher: str | None = None,
    status_message: str | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    hook: dict[str, Any] = {"type": "command", "command": command}
    if status_message:
        hook["statusMessage"] = status_message
    if timeout is not None:
        hook["timeout"] = timeout
    entry: dict[str, Any] = {}
    if matcher:
        entry["matcher"] = matcher
    entry["hooks"] = [hook]
    return entry


def build_managed_codex_hooks_config(pkg_root: str) -> dict[str, Any]:
    """Build the managed hooks config for a given package root.

    Args:
        pkg_root: Package root directory.

    Returns:
        Dict with ``hooks`` key containing managed hook entries.
    """
    import sys

    command = f"{sys.executable} -u -m omx.scripts.codex_native_hook"
    return {
        "hooks": {
            "SessionStart": [
                _build_command_hook(
                    f"{command} session-start", matcher="startup|resume"
                )
            ],
            "PreToolUse": [
                _build_command_hook(f"{command} pre-tool-use", matcher="Bash")
            ],
            "PostToolUse": [_build_command_hook(f"{command} post-tool-use")],
            "UserPromptSubmit": [_build_command_hook(f"{command} user-prompt-submit")],
            "Stop": [_build_command_hook(f"{command} stop", timeout=30)],
        },
    }


def parse_codex_hooks_config(content: str) -> tuple[JsonObject, JsonObject] | None:
    """Parse hooks.json content.

    Args:
        content: Raw JSON string.

    Returns:
        Tuple of (root object, hooks sub-object) or ``None`` on failure.
    """
    try:
        parsed = json.loads(content)
        if not _is_plain_object(parsed):
            return None
        root = copy.deepcopy(parsed)
        hooks = (
            copy.deepcopy(parsed.get("hooks", {}))
            if _is_plain_object(parsed.get("hooks"))
            else {}
        )
        return root, hooks
    except (json.JSONDecodeError, TypeError):
        return None


_OMX_HOOK_RE = re.compile(
    r"(?:codex-native-hook\.js|omx\.scripts\.codex_native_hook)(?:[\"'\s]|$)"
)


def _is_omx_managed_hook_command(command: str) -> bool:
    return bool(_OMX_HOOK_RE.search(command))


def _count_managed_hooks_in_entry(entry: Any) -> int:
    if not _is_plain_object(entry) or not isinstance(entry.get("hooks"), list):
        return 0
    return sum(
        1
        for hook in entry["hooks"]
        if _is_plain_object(hook)
        and hook.get("type") == "command"
        and isinstance(hook.get("command"), str)
        and _is_omx_managed_hook_command(hook["command"])
    )


def get_missing_managed_codex_hook_events(content: str) -> list[str] | None:
    """Get list of managed hook events missing from hooks config.

    Args:
        content: Raw hooks.json content.

    Returns:
        List of missing event names, or ``None`` if parsing fails.
    """
    result = parse_codex_hooks_config(content)
    if result is None:
        return None
    _, hooks = result
    missing = []
    for event_name in MANAGED_HOOK_EVENTS:
        entries = hooks.get(event_name, [])
        if not isinstance(entries, list):
            entries = []
        has_managed = any(_count_managed_hooks_in_entry(e) > 0 for e in entries)
        if not has_managed:
            missing.append(event_name)
    return missing


def _strip_managed_hooks_from_entry(entry: Any) -> tuple[Any | None, int]:
    if not _is_plain_object(entry) or not isinstance(entry.get("hooks"), list):
        return copy.deepcopy(entry), 0
    original_hooks = entry["hooks"]
    next_hooks = [
        hook
        for hook in original_hooks
        if not (
            _is_plain_object(hook)
            and hook.get("type") == "command"
            and isinstance(hook.get("command"), str)
            and _is_omx_managed_hook_command(hook["command"])
        )
    ]
    removed = len(original_hooks) - len(next_hooks)
    if removed == 0:
        return copy.deepcopy(entry), 0
    if not next_hooks:
        return None, removed
    result = copy.deepcopy(entry)
    result["hooks"] = next_hooks
    return result, removed


def _serialize_hooks(root: JsonObject) -> str:
    return json.dumps(root, indent=2) + "\n"


def merge_managed_codex_hooks_config(
    existing_content: str | None,
    pkg_root: str,
) -> str:
    """Merge managed hooks into existing hooks.json content.

    Args:
        existing_content: Existing hooks.json content (may be ``None``).
        pkg_root: Package root directory.

    Returns:
        Updated hooks.json content string.
    """
    managed = build_managed_codex_hooks_config(pkg_root)
    parsed = (
        parse_codex_hooks_config(existing_content)
        if isinstance(existing_content, str)
        else None
    )

    next_root = copy.deepcopy(parsed[0]) if parsed else {}
    next_hooks = copy.deepcopy(parsed[1]) if parsed else {}

    for event_name in MANAGED_HOOK_EVENTS:
        existing_entries = next_hooks.get(event_name, [])
        if not isinstance(existing_entries, list):
            existing_entries = []
        preserved: list[Any] = []
        for entry in existing_entries:
            stripped, _ = _strip_managed_hooks_from_entry(entry)
            if stripped is not None:
                preserved.append(stripped)
        next_hooks[event_name] = [
            *preserved,
            *[copy.deepcopy(e) for e in managed["hooks"][event_name]],
        ]

    if next_hooks:
        next_root["hooks"] = next_hooks
    else:
        next_root.pop("hooks", None)

    return _serialize_hooks(next_root)


def remove_managed_codex_hooks(existing_content: str) -> tuple[str | None, int]:
    """Remove all OMX-managed hooks from hooks.json content.

    Args:
        existing_content: Existing hooks.json content.

    Returns:
        Tuple of (updated content or ``None`` if empty, removed count).
    """
    parsed = parse_codex_hooks_config(existing_content)
    if parsed is None:
        return existing_content, 0

    next_root = copy.deepcopy(parsed[0])
    next_hooks = copy.deepcopy(parsed[1])
    removed_count = 0

    for event_name, raw_entries in list(next_hooks.items()):
        if not isinstance(raw_entries, list):
            continue
        preserved: list[Any] = []
        for entry in raw_entries:
            stripped, removed = _strip_managed_hooks_from_entry(entry)
            removed_count += removed
            if stripped is not None:
                preserved.append(stripped)
        if preserved:
            next_hooks[event_name] = preserved
        else:
            del next_hooks[event_name]

    if removed_count == 0:
        return existing_content, 0

    if next_hooks:
        next_root["hooks"] = next_hooks
    else:
        next_root.pop("hooks", None)

    if not next_root:
        return None, removed_count

    return _serialize_hooks(next_root), removed_count
