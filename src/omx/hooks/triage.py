"""Prompt triage heuristic — classifies prompts into routing lanes.

Port of src/hooks/triage-heuristic.ts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TRIVIAL_PATTERNS = re.compile(
    r"^(hi|hey|hello|thanks|thank you|yes|no|ok|okay|sure|great|nice|cool|yep|nope|nah)\s*[.!?]*$",
    re.IGNORECASE,
)

OPT_OUT_PHRASES = {
    "just chat",
    "plain answer",
    "no workflow",
    "don't route",
    "don't use a skill",
    "explain only",
}

EXPLORE_STARTERS = re.compile(
    r"^(explain|what|where|why|how|tell me about|describe|show me how|can you explain|could you explain)\b",
    re.IGNORECASE,
)

RESEARCHER_SIGNALS = re.compile(
    r"\b(official docs|upstream docs|api docs|release notes|changelog|version|compatibility"
    r"|web|internet|online|external|github|npm|pypi|mdn|stackoverflow"
    r"|find|look up|research|search|check|verify|read|consult|collect|gather)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TriageDecision:
    """Result of triaging a user prompt into a routing lane.

    Attributes:
        lane: Routing lane ("HEAVY", "LIGHT", or "PASS").
        destination: Suggested agent/skill target (e.g. "explore", "autopilot").
        reason: Human-readable explanation for the classification.
    """

    lane: str  # "HEAVY", "LIGHT", "PASS"
    destination: str | None = (
        None  # "explore", "executor", "designer", "researcher", "autopilot"
    )
    reason: str = ""


def triage_prompt(text: str) -> TriageDecision:
    """Classify a user prompt into PASS, LIGHT, or HEAVY routing lane.

    Uses pattern matching heuristics to determine whether a prompt
    should bypass routing (PASS), go to a single agent (LIGHT),
    or trigger multi-agent orchestration (HEAVY).

    Args:
        text: Raw user prompt text.

    Returns:
        TriageDecision with lane, destination, and reason.
    """
    stripped = text.strip()

    if not stripped:
        return TriageDecision(lane="PASS", reason="empty prompt")

    lower = stripped.lower()

    # Trivial acknowledgements
    if TRIVIAL_PATTERNS.match(stripped):
        return TriageDecision(lane="PASS", reason="trivial acknowledgement")

    # Opt-out phrases
    if any(phrase in lower for phrase in OPT_OUT_PHRASES):
        return TriageDecision(lane="PASS", reason="opt-out phrase")

    # Short ambiguous prompts
    if len(stripped.split()) <= 3 and not stripped.endswith("?"):
        return TriageDecision(lane="PASS", reason="ambiguous short prompt")

    # Explore-style questions
    if EXPLORE_STARTERS.match(stripped):
        return TriageDecision(
            lane="LIGHT", destination="explore", reason="explore-style question"
        )

    # Research signals
    if RESEARCHER_SIGNALS.search(stripped):
        return TriageDecision(
            lane="LIGHT", destination="researcher", reason="research signals detected"
        )

    # Longer imperative prompts → HEAVY
    words = stripped.split()
    if len(words) >= 8:
        return TriageDecision(
            lane="HEAVY",
            destination="autopilot",
            reason="goal-shaped imperative prompt",
        )

    # Default to LIGHT executor
    return TriageDecision(
        lane="LIGHT", destination="executor", reason="default single-agent"
    )
