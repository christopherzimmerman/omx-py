"""Triage Heuristic — pure, synchronous prompt classifier (3-lane routing).

Port of `src/hooks/triage-heuristic.ts` (1:1 sync conversion).

Advisory-only — never activates workflows, never touches state or fs.

Lanes:
    PASS  — trivial acknowledgements, explicit opt-out phrases, or
            ambiguous short prompts.
    LIGHT — single-agent destination: explore | executor | designer | researcher.
    HEAVY — autopilot; longer goal-shaped imperative prompts.

This module is the canonical heuristic that matches the TS implementation
rule-for-rule. The earlier coarser `omx.hooks.triage` legacy classifier was
retired once all callers migrated here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

TriageLane = Literal["HEAVY", "LIGHT", "PASS"]
LightDestination = Literal["explore", "executor", "designer", "researcher"]
TriageDestination = Literal[
    "explore", "executor", "designer", "researcher", "autopilot"
]


@dataclass(frozen=True)
class TriageDecision:
    """Result of triaging a user prompt into a routing lane.

    Attributes:
        lane: Routing lane ("HEAVY", "LIGHT", or "PASS").
        destination: Optional suggested target. For LIGHT lanes one of
            "explore" | "executor" | "designer" | "researcher"; for HEAVY
            lanes "autopilot". `None` for PASS results.
        reason: Stable machine-readable classification reason.
    """

    lane: str
    destination: str | None = None
    reason: str = ""


# ---------------------------------------------------------------------------
# Module-scope constants (precompiled once)
# ---------------------------------------------------------------------------

# Prompts that are trivially empty acknowledgements.
_TRIVIAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:hi+|hey|hello|thanks?|thank\s+you|yes|no|ok(?:ay)?|sure|great|good"
        r"|got\s+it|sounds?\s+good|yep|yup|nope|cool|awesome|perfect)\.?$"
    ),
)

# Explicit opt-out substrings — checked as case-insensitive includes
# (the caller lowercases the prompt before scanning).
_OPT_OUT_PHRASES: tuple[str, ...] = (
    "just chat",
    "plain answer",
    "no workflow",
    "don't route",
    "do not route",
    "don't use a skill",
    "do not use a skill",
    "talk through",
    "explain only",
)

# Starters that indicate an explanatory / question prompt → LIGHT/explore.
_EXPLORE_STARTERS: tuple[str, ...] = (
    "explain ",
    "what ",
    "where ",
    "why ",
    "how does ",
    "how do ",
    "how is ",
    "tell me about ",
    "describe ",
    "show me how ",
    "can you explain ",
    "could you explain ",
)

# External docs / reference lookup prompts → LIGHT/researcher.
_RESEARCHER_EXTERNAL_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:official docs?|upstream docs?|vendor docs?|api docs?|reference docs?"
        r"|release notes?|changelog|version(?:ing)?|compatib(?:ility|le)|documentation)\b"
    ),
    re.compile(
        r"\b(?:web|internet|online|external sources?|external citations?"
        r"|source-backed|in the wild)\b"
    ),
    re.compile(r"\b(?:github|npm|pypi|crates\.io|mdn|stackoverflow)\b"),
    re.compile(
        r"(?:공식\s*(?:문서|docs?)|외부\s*(?:자료|문서|소스)|웹에서|인터넷에서"
        r"|출처|레퍼런스|릴리즈\s*노트|버전\s*호환|호환성)"
    ),
)

_RESEARCHER_EXTERNAL_ROUTE_OVERRIDE_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:web|internet|online|external sources?|external citations?"
        r"|source-backed|in the wild)\b"
    ),
    re.compile(r"\b(?:github|npm|pypi|crates\.io|mdn|stackoverflow)\b"),
    re.compile(r"(?:외부\s*(?:자료|문서|소스)|웹에서|인터넷에서|출처)"),
)

_RESEARCHER_LOOKUP_VERBS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:find|look up|lookup|research|search|check|verify|read|consult|collect|gather)\b"
    ),
    re.compile(
        r"(?:찾아줘|찾아봐|찾아|검색해|검색|조사해|조사|확인해|확인|알아봐|알아내)"
    ),
)

_RESEARCHER_TECH_SUBJECTS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:api|apis|sdk|sdks|framework|frameworks|library|libraries|package|packages"
        r"|service|services|tool|tools|vendor|browser|runtime)\b"
    ),
)

_RESEARCHER_TECH_NEEDS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:behavior|best way|configuration|configure|example|examples|feature|features?"
        r"|how(?:\s+do|\s+to)?|lifecycle|option|options|parameter|parameters|usage"
        r"|what(?:\s+does|\s+is)|when(?:\s+does|\s+should)|why(?:\s+does)?)\b"
    ),
)

_IMPLEMENTATION_ACTION_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:add|build|change|create|delete|fix|implement|integrate|migrate|modify"
        r"|patch|plan|planning|refactor|remove|replace|rewrite|scaffold|set up|update|wire)\b"
    ),
    re.compile(r"(?:구현|추가|수정|변경|삭제|교체|마이그레이션|연동|적용)"),
)

_IMPLEMENTATION_CONNECTOR_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:after|and|based on|then|using|with)\b"),
    re.compile(
        r"[,;].*\b(?:find|look up|lookup|research|search|check|verify|read|consult|collect|gather)\b"
    ),
    re.compile(
        r"(?:기반으로|보고|읽고|찾고|확인하고|사용해서|써서|로\s*구현|로\s*수정)"
    ),
)

_LOCAL_RESEARCH_EXCLUSION_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:repo|repository|codebase|local|in-repo|source tree|working tree)\b"
    ),
    re.compile(
        r"\b(?:this|current|our)\s+(?:project|workspace|code|repo|repository|codebase)\b"
    ),
    re.compile(r"\bin\s+(?:the\s+)?(?:project|workspace)\b"),
    re.compile(
        r"\b(?:src|lib|test|spec|app|pages|components|hooks|utils|services|dist|build|scripts)/[\w./\-]+"
    ),
    re.compile(
        r"(?:^|[\s\"'`(])(?:\.{1,2}/|/|[\w.-]+/)[\w./\-]+"
        r"\.(?:ts|js|py|go|rs|java|tsx|jsx|vue|svelte|rb|c|cpp|h|css|scss|html|json|yaml|yml|toml)\b"
    ),
    re.compile(
        r"(?:이\s*(?:레포|저장소|코드베이스)|레포에서|저장소에서|코드베이스에서|소스에서|파일에서)"
    ),
)

# Starters / keywords for visual / styling prompts → LIGHT/designer.
_DESIGNER_STARTERS: tuple[str, ...] = (
    "make the button",
    "style ",
    "color ",
    "adjust spacing",
    "ui ",
    "change the color",
    "change the font",
    "change the style",
    "update the style",
    "update the design",
    "change the design",
    "change the layout",
    "update the layout",
)

# Terms that make broad design verbs visual/UI-specific enough for designer.
# Kept intentionally concrete so product, architecture, auth, and deployment
# redesign prompts can still reach the safer HEAVY path.
_VISUAL_DESIGN_TERMS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:ui|ux|visual|style|styling|css|layout|spacing|color|font|typography)\b"
    ),
    re.compile(
        r"\b(?:button|page|screen|panel|modal|form|navbar|sidebar|header|footer|card|component)\b"
    ),
)

_BROAD_DESIGN_STARTERS: tuple[str, ...] = ("redesign ",)

_STRUCTURAL_REDESIGN_TERMS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:auth|authentication|authorization|flow|pipeline|deployment|deploy|architecture"
        r"|system|api|backend|database|data|schema|orm|infra|infrastructure)\b"
    ),
)

# Patterns that indicate a short, anchored edit → LIGHT/executor.
# Anchors: file path (src/...), line reference, rename/fix-typo phrase.
_EXECUTOR_ANCHOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bsrc/[\w./\-]+\.\w+\b"),
    re.compile(r"\blib/[\w./\-]+\.\w+\b"),
    re.compile(r"\btest/[\w./\-]+\.\w+\b"),
    re.compile(r"\bspec/[\w./\-]+\.\w+\b"),
    re.compile(r"\bline\s+\d+\b"),
    re.compile(r"\brename\b.+\bin\b"),
    re.compile(r"\bfix\s+typo\s+in\b"),
    re.compile(r"\badd\b.+\bto\s+line\s+\d+\b"),
)

# Imperative verbs that, combined with sufficient word count and no anchor,
# signal a HEAVY goal-shaped prompt.
_HEAVY_IMPERATIVE_VERBS: tuple[str, ...] = (
    "add ",
    "implement ",
    "refactor ",
    "build ",
    "create ",
    "migrate ",
    "rewrite ",
    "redesign ",
    "integrate ",
    "set up ",
    "configure ",
    "extract ",
    "split ",
    "merge ",
    "update ",
    "remove ",
    "delete ",
    "replace ",
    "convert ",
    "generate ",
    "scaffold ",
    "deploy ",
    "automate ",
)

# Word count threshold: prompts with MORE than this many words that start
# with an imperative verb are classified HEAVY. Set to 5 so that 6+ word
# imperative prompts route correctly while ultra-short imperatives fall
# through to PASS.
HEAVY_WORD_THRESHOLD = 5

# Upper bound (inclusive) on word count for a short "?"-ending prompt to
# still count as an exploration question. Longer interrogative prompts fall
# through to later rules (which may classify them as HEAVY or PASS).
SHORT_QUESTION_WORD_LIMIT = 10

# Upper bound (inclusive) on word count for a prompt to still qualify as a
# short anchored edit (LIGHT/executor). Longer anchored prompts are treated
# as goal-shaped work and may be classified HEAVY by later rules instead.
ANCHORED_EDIT_WORD_LIMIT = 15


def _is_question_or_explanation(normalized: str, word_count: int) -> bool:
    for starter in _EXPLORE_STARTERS:
        if normalized.startswith(starter):
            return True
    return word_count <= SHORT_QUESTION_WORD_LIMIT and normalized.endswith("?")


def _has_anchored_edit_pattern(normalized: str) -> bool:
    return any(p.search(normalized) for p in _EXECUTOR_ANCHOR_PATTERNS)


def _any_match(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(p.search(text) for p in patterns)


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------


def triage_prompt(prompt: str) -> TriageDecision:
    """Classify a user prompt into one of three routing lanes.

    Mirrors the TypeScript ``triagePrompt`` rule order exactly. Rules are
    short-circuit; the first match wins.

    Args:
        prompt: Raw user prompt text.

    Returns:
        A `TriageDecision` with `lane`, optional `destination`, and a stable
        machine-readable `reason` string.
    """
    normalized = prompt.strip().lower()
    word_count = 0 if len(normalized) == 0 else len(re.split(r"\s+", normalized))

    # Rule 1: Empty / trivial acknowledgements → PASS
    if len(normalized) == 0:
        return TriageDecision(lane="PASS", reason="empty_input")

    for pattern in _TRIVIAL_PATTERNS:
        if pattern.search(normalized):
            return TriageDecision(lane="PASS", reason="trivial_acknowledgement")

    # Rule 2: Explicit opt-out → PASS
    for phrase in _OPT_OUT_PHRASES:
        if phrase in normalized:
            return TriageDecision(lane="PASS", reason="explicit_opt_out")

    has_local_research_anchor = _any_match(
        _LOCAL_RESEARCH_EXCLUSION_SIGNALS, normalized
    )
    has_implementation_action = _any_match(_IMPLEMENTATION_ACTION_SIGNALS, normalized)
    has_implementation_connector = _any_match(
        _IMPLEMENTATION_CONNECTOR_SIGNALS, normalized
    )
    has_research_lookup_verb = _any_match(_RESEARCHER_LOOKUP_VERBS, normalized)
    has_question_or_explanation = _is_question_or_explanation(normalized, word_count)
    has_anchored_edit = _has_anchored_edit_pattern(normalized)
    has_external_research_signal = _any_match(_RESEARCHER_EXTERNAL_SIGNALS, normalized)
    has_external_route_override = _any_match(
        _RESEARCHER_EXTERNAL_ROUTE_OVERRIDE_SIGNALS, normalized
    )

    # Rule 3: Obvious question / explanation → LIGHT/explore
    if has_question_or_explanation and not (
        has_research_lookup_verb
        and has_external_research_signal
        and (not has_local_research_anchor or has_external_route_override)
    ):
        return TriageDecision(
            lane="LIGHT", destination="explore", reason="question_or_explanation"
        )

    # Rule 4: Short anchored edit → LIGHT/executor
    if (
        word_count <= ANCHORED_EDIT_WORD_LIMIT
        and has_anchored_edit
        and not (has_research_lookup_verb and has_external_route_override)
    ):
        return TriageDecision(
            lane="LIGHT", destination="executor", reason="anchored_edit"
        )

    # Rule 5: Repo-local lookup → LIGHT/explore
    if (
        has_local_research_anchor
        and has_research_lookup_verb
        and not has_implementation_action
        and not has_external_route_override
    ):
        return TriageDecision(
            lane="LIGHT", destination="explore", reason="local_reference_lookup"
        )

    # Rule 6: Implementation/planning-shaped research prompt → HEAVY
    if (
        word_count > HEAVY_WORD_THRESHOLD
        and has_implementation_action
        and has_implementation_connector
        and (has_external_research_signal or has_research_lookup_verb)
    ):
        return TriageDecision(
            lane="HEAVY",
            destination="autopilot",
            reason="implementation_research_goal",
        )

    # Rule 7: External docs / source-backed lookup → LIGHT/researcher
    if (
        (not has_local_research_anchor or has_external_route_override)
        and not has_implementation_action
        and has_research_lookup_verb
        and (
            has_external_research_signal
            or (
                _any_match(_RESEARCHER_TECH_SUBJECTS, normalized)
                and _any_match(_RESEARCHER_TECH_NEEDS, normalized)
            )
        )
    ):
        return TriageDecision(
            lane="LIGHT", destination="researcher", reason="external_reference_research"
        )

    # Rule 8: Structural redesign goals → HEAVY
    if any(normalized.startswith(s) for s in _BROAD_DESIGN_STARTERS) and _any_match(
        _STRUCTURAL_REDESIGN_TERMS, normalized
    ):
        return TriageDecision(
            lane="HEAVY", destination="autopilot", reason="structural_redesign_goal"
        )

    # Rule 9: Obvious visual / styling → LIGHT/designer
    for starter in _DESIGNER_STARTERS:
        if normalized.startswith(starter):
            return TriageDecision(
                lane="LIGHT", destination="designer", reason="visual_styling_prompt"
            )
    for starter in _BROAD_DESIGN_STARTERS:
        if normalized.startswith(starter) and _any_match(
            _VISUAL_DESIGN_TERMS, normalized
        ):
            return TriageDecision(
                lane="LIGHT", destination="designer", reason="visual_styling_prompt"
            )

    # Rule 10: Longer goal-shaped imperative → HEAVY
    if word_count > HEAVY_WORD_THRESHOLD:
        for verb in _HEAVY_IMPERATIVE_VERBS:
            if normalized.startswith(verb):
                return TriageDecision(
                    lane="HEAVY", destination="autopilot", reason="long_imperative_goal"
                )

    # Rule 11: Fallback → PASS
    return TriageDecision(lane="PASS", reason="ambiguous_short_prompt")


__all__ = [
    "ANCHORED_EDIT_WORD_LIMIT",
    "HEAVY_WORD_THRESHOLD",
    "LightDestination",
    "SHORT_QUESTION_WORD_LIMIT",
    "TriageDecision",
    "TriageDestination",
    "TriageLane",
    "triage_prompt",
]
