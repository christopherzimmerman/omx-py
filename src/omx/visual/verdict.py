"""Visual verdict parsing and feedback.

Port of src/visual/verdict.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from omx.visual.constants import VISUAL_NEXT_ACTIONS_LIMIT, VISUAL_VERDICT_STATUSES


@dataclass
class VisualVerdict:
    """Parsed visual verification verdict.

    Attributes:
        score: Similarity score (0-100).
        verdict: Verdict status (pass, revise, fail).
        category_match: Whether categories match.
        differences: List of differences found.
        suggestions: List of improvement suggestions.
        reasoning: Explanation of the verdict.
    """

    score: int = 0
    verdict: str = ""
    category_match: bool = False
    differences: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class VisualLoopFeedback(VisualVerdict):
    """Visual loop feedback extending the verdict.

    Attributes:
        threshold: Score threshold for passing.
        passes_threshold: Whether the score passes.
        next_actions: Recommended next actions.
    """

    threshold: int = 90
    passes_threshold: bool = False
    next_actions: list[str] = field(default_factory=list)


def _as_trimmed_string_array(value: Any, field_name: str) -> list[str]:
    """Validate and convert to a list of trimmed strings."""
    if not isinstance(value, list):
        raise ValueError(f"visual_verdict.{field_name} must be an array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"visual_verdict.{field_name} must contain strings")
        trimmed = item.strip()
        if trimmed:
            result.append(trimmed)
    return result


def _parse_visual_verdict_status(value: Any) -> str:
    """Validate and normalize a verdict status."""
    if not isinstance(value, str):
        raise ValueError(
            f"visual_verdict.verdict must be one of: {'|'.join(VISUAL_VERDICT_STATUSES)}"
        )
    normalized = value.strip().lower()
    if normalized not in VISUAL_VERDICT_STATUSES:
        raise ValueError(
            f"visual_verdict.verdict must be one of: {'|'.join(VISUAL_VERDICT_STATUSES)}"
        )
    return normalized


def parse_visual_verdict(input_data: Any) -> VisualVerdict:
    """Parse and validate a visual verdict from raw input.

    Args:
        input_data: Raw verdict data (dict expected).

    Returns:
        Validated VisualVerdict instance.

    Raises:
        ValueError: If the input is invalid.
    """
    if not isinstance(input_data, dict):
        raise ValueError("visual_verdict must be an object")

    score = input_data.get("score")
    if not isinstance(score, int) or score < 0 or score > 100:
        raise ValueError("visual_verdict.score must be an integer between 0 and 100")

    category_match = input_data.get("category_match")
    if not isinstance(category_match, bool):
        raise ValueError("visual_verdict.category_match must be a boolean")

    reasoning = input_data.get("reasoning")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("visual_verdict.reasoning must be a non-empty string")

    return VisualVerdict(
        score=score,
        verdict=_parse_visual_verdict_status(input_data.get("verdict")),
        category_match=category_match,
        differences=_as_trimmed_string_array(
            input_data.get("differences"), "differences"
        ),
        suggestions=_as_trimmed_string_array(
            input_data.get("suggestions"), "suggestions"
        ),
        reasoning=reasoning.strip(),
    )


def build_visual_loop_feedback(
    input_data: Any, threshold: int = 90
) -> VisualLoopFeedback:
    """Build visual loop feedback from raw input.

    Args:
        input_data: Raw verdict data.
        threshold: Score threshold for passing (default 90).

    Returns:
        VisualLoopFeedback instance.

    Raises:
        ValueError: If the input is invalid.
    """
    verdict = parse_visual_verdict(input_data)
    next_actions = [
        *verdict.suggestions,
        *(f"Fix: {d}" for d in verdict.differences),
    ][:VISUAL_NEXT_ACTIONS_LIMIT]

    return VisualLoopFeedback(
        score=verdict.score,
        verdict=verdict.verdict,
        category_match=verdict.category_match,
        differences=verdict.differences,
        suggestions=verdict.suggestions,
        reasoning=verdict.reasoning,
        threshold=threshold,
        passes_threshold=verdict.score >= threshold,
        next_actions=next_actions,
    )
