"""Task complexity heuristic.

Port of src/hooks/task-size-detector.ts. Classifies user prompts as
small, medium, or large based on word count and signal patterns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class TaskSize(StrEnum):
    """Task size classification."""

    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


@dataclass
class TaskSizeResult:
    """Result of task size classification.

    Attributes:
        size: Classified task size.
        reason: Human-readable reason for the classification.
        word_count: Number of words in the prompt.
        has_escape_hatch: Whether an escape hatch prefix was detected.
        escape_prefix_used: The escape prefix if detected.
    """

    size: TaskSize
    reason: str
    word_count: int
    has_escape_hatch: bool
    escape_prefix_used: str | None = None


@dataclass(frozen=True)
class TaskSizeThresholds:
    """Word limit thresholds for task size classification.

    Attributes:
        small_word_limit: Prompts under this limit are small (unless overridden).
        large_word_limit: Prompts over this limit are large.
    """

    small_word_limit: int = 50
    large_word_limit: int = 200


DEFAULT_THRESHOLDS = TaskSizeThresholds()

_ESCAPE_HATCH_PREFIXES = (
    "quick:",
    "simple:",
    "tiny:",
    "minor:",
    "small:",
    "just:",
    "only:",
)

_SMALL_TASK_SIGNALS = (
    re.compile(r"\btypo\b", re.I),
    re.compile(r"\bspelling\b", re.I),
    re.compile(r"\brename\s+\w+\s+to\b", re.I),
    re.compile(r"\bone[\s-]liner?\b", re.I),
    re.compile(r"\bone[\s-]line\s+fix\b", re.I),
    re.compile(r"\bsingle\s+file\b", re.I),
    re.compile(r"\bin\s+this\s+file\b", re.I),
    re.compile(r"\bthis\s+function\b", re.I),
    re.compile(r"\bthis\s+line\b", re.I),
    re.compile(r"\bminor\s+(fix|change|update|tweak)\b", re.I),
    re.compile(r"\bfix\s+(a\s+)?typo\b", re.I),
    re.compile(r"\badd\s+a?\s*comment\b", re.I),
    re.compile(r"\bwhitespace\b", re.I),
    re.compile(r"\bindentation\b", re.I),
    re.compile(r"\bformat(ting)?\s+(this|the)\b", re.I),
    re.compile(r"\bquick\s+fix\b", re.I),
    re.compile(r"\bsmall\s+(fix|change|tweak|update)\b", re.I),
    re.compile(r"\bupdate\s+(the\s+)?version\b", re.I),
    re.compile(r"\bbump\s+version\b", re.I),
)

_LARGE_TASK_SIGNALS = (
    re.compile(r"\barchitect(ure|ural)?\b", re.I),
    re.compile(r"\brefactor\b", re.I),
    re.compile(r"\bredesign\b", re.I),
    re.compile(r"\bfrom\s+scratch\b", re.I),
    re.compile(r"\bcross[\s-]cutting\b", re.I),
    re.compile(r"\bentire\s+(codebase|project|application|app|system)\b", re.I),
    re.compile(r"\ball\s+(files|modules|components)\b", re.I),
    re.compile(r"\bmultiple\s+files\b", re.I),
    re.compile(r"\bacross\s+(the\s+)?(codebase|project|files|modules)\b", re.I),
    re.compile(r"\bsystem[\s-]wide\b", re.I),
    re.compile(r"\bmigrat(e|ion)\b", re.I),
    re.compile(r"\bfull[\s-]stack\b", re.I),
    re.compile(r"\bend[\s-]to[\s-]end\b", re.I),
    re.compile(r"\boverhaul\b", re.I),
    re.compile(r"\bcomprehensive\b", re.I),
    re.compile(r"\bextensive\b", re.I),
    re.compile(r"\bimplement\s+(a\s+)?(new\s+)?system\b", re.I),
    re.compile(r"\bbuild\s+(a\s+)?(complete|full|new)\b", re.I),
)


def count_words(text: str) -> int:
    """Count words in a prompt (splits on whitespace).

    Args:
        text: Input text.

    Returns:
        Number of words.
    """
    return len(text.split())


def detect_escape_hatch(text: str) -> str | None:
    """Check if the prompt starts with a lightweight escape hatch prefix.

    Args:
        text: Input text.

    Returns:
        The prefix if found, ``None`` otherwise.
    """
    trimmed = text.strip().lower()
    for prefix in _ESCAPE_HATCH_PREFIXES:
        if trimmed.startswith(prefix):
            return prefix
    return None


def has_small_task_signals(text: str) -> bool:
    """Check for small task signal patterns.

    Args:
        text: Input text.

    Returns:
        True if small task signals are present.
    """
    return any(p.search(text) for p in _SMALL_TASK_SIGNALS)


def has_large_task_signals(text: str) -> bool:
    """Check for large task signal patterns.

    Args:
        text: Input text.

    Returns:
        True if large task signals are present.
    """
    return any(p.search(text) for p in _LARGE_TASK_SIGNALS)


def classify_task_size(
    text: str,
    thresholds: TaskSizeThresholds = DEFAULT_THRESHOLDS,
) -> TaskSizeResult:
    """Classify a user prompt as small, medium, or large.

    Classification rules (in priority order):
        1. Escape hatch prefix -> always small
        2. Large task signals -> large
        3. Prompt > large_word_limit -> large
        4. Small task signals AND within limits -> small
        5. Prompt < small_word_limit -> small
        6. Everything else -> medium

    Args:
        text: User prompt text.
        thresholds: Word count thresholds.

    Returns:
        Classification result.
    """
    word_count = count_words(text)
    escape_prefix = detect_escape_hatch(text)

    if escape_prefix is not None:
        return TaskSizeResult(
            size=TaskSize.SMALL,
            reason=f'Escape hatch prefix detected: "{escape_prefix}"',
            word_count=word_count,
            has_escape_hatch=True,
            escape_prefix_used=escape_prefix,
        )

    has_large = has_large_task_signals(text)
    has_small = has_small_task_signals(text)

    if has_large:
        return TaskSizeResult(
            size=TaskSize.LARGE,
            reason="Large task signals detected (architecture/refactor/cross-cutting scope)",
            word_count=word_count,
            has_escape_hatch=False,
        )

    if word_count > thresholds.large_word_limit:
        return TaskSizeResult(
            size=TaskSize.LARGE,
            reason=f"Prompt length ({word_count} words) exceeds large task threshold ({thresholds.large_word_limit})",
            word_count=word_count,
            has_escape_hatch=False,
        )

    if has_small and not has_large:
        return TaskSizeResult(
            size=TaskSize.SMALL,
            reason="Small task signals detected (single file / minor change)",
            word_count=word_count,
            has_escape_hatch=False,
        )

    if word_count <= thresholds.small_word_limit:
        return TaskSizeResult(
            size=TaskSize.SMALL,
            reason=f"Prompt length ({word_count} words) is within small task threshold ({thresholds.small_word_limit})",
            word_count=word_count,
            has_escape_hatch=False,
        )

    return TaskSizeResult(
        size=TaskSize.MEDIUM,
        reason=f"Prompt length ({word_count} words) is in medium range",
        word_count=word_count,
        has_escape_hatch=False,
    )


HEAVY_MODE_KEYWORDS: frozenset[str] = frozenset(
    {
        "ralph",
        "autopilot",
        "team",
        "ultrawork",
        "swarm",
        "ralplan",
        "ccg",
    }
)


def is_heavy_mode(keyword_type: str) -> bool:
    """Check if a keyword type is a heavy orchestration mode.

    Args:
        keyword_type: Keyword/mode type string.

    Returns:
        True if it is a heavy orchestration mode.
    """
    return keyword_type in HEAVY_MODE_KEYWORDS
