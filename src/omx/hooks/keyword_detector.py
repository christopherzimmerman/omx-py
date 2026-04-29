"""Keyword detection for skill activation.

Port of src/hooks/keyword-detector.ts.
"""

from __future__ import annotations

import re

CONTINUATION_PATTERNS = [
    re.compile(r"^[/\\]?\s*keep going", re.IGNORECASE),
    re.compile(r"^[/\\]?\s*continue", re.IGNORECASE),
    re.compile(r"^[/\\]?\s*resume", re.IGNORECASE),
]


def is_continuation_prompt(text: str) -> bool:
    """Check if the prompt is a continuation keyword."""
    return any(p.search(text.strip()) for p in CONTINUATION_PATTERNS)


def detect_skill_keyword(text: str, available_skills: list[str]) -> str | None:
    """Detect a skill keyword in user input (e.g. $autopilot, $team).

    Args:
        text: Raw user input text.
        available_skills: List of valid skill names to match against.

    Returns:
        Matched skill name, or None if no match found.
    """
    stripped = text.strip()
    if not stripped.startswith("$"):
        return None
    keyword = stripped[1:].split()[0].lower() if stripped[1:] else None
    if keyword and keyword in available_skills:
        return keyword
    return None
