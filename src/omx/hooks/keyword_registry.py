"""Skill keyword registry.

Port of src/hooks/keyword-registry.ts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordTriggerDefinition:
    """A keyword-to-skill trigger mapping.

    Attributes:
        keyword: Trigger keyword or phrase.
        skill: Target skill name.
        priority: Priority for disambiguation (higher wins).
        guidance: Human-readable guidance text.
    """

    keyword: str
    skill: str
    priority: int
    guidance: str


KEYWORD_TRIGGER_DEFINITIONS: tuple[KeywordTriggerDefinition, ...] = (
    KeywordTriggerDefinition(
        "$ralph", "ralph", 9, "Activate ralph persistence loop with verification"
    ),
    KeywordTriggerDefinition(
        "don't stop", "ralph", 9, "Activate ralph persistence loop with verification"
    ),
    KeywordTriggerDefinition(
        "must complete", "ralph", 9, "Activate ralph persistence loop with verification"
    ),
    KeywordTriggerDefinition(
        "keep going", "ralph", 9, "Activate ralph persistence loop with verification"
    ),
    KeywordTriggerDefinition(
        "$autopilot",
        "autopilot",
        10,
        "Activate autopilot skill for autonomous execution",
    ),
    KeywordTriggerDefinition(
        "build me", "autopilot", 10, "Activate autopilot skill for autonomous execution"
    ),
    KeywordTriggerDefinition(
        "I want a", "autopilot", 10, "Activate autopilot skill for autonomous execution"
    ),
    KeywordTriggerDefinition(
        "$ultrawork", "ultrawork", 10, "Activate ultrawork parallel execution mode"
    ),
    KeywordTriggerDefinition(
        "ulw", "ultrawork", 10, "Activate ultrawork parallel execution mode"
    ),
    KeywordTriggerDefinition(
        "parallel", "ultrawork", 10, "Activate ultrawork parallel execution mode"
    ),
    KeywordTriggerDefinition(
        "$ultraqa", "ultraqa", 8, "Activate UltraQA cycling workflow"
    ),
    KeywordTriggerDefinition(
        "$analyze", "analyze", 7, "Activate deep analysis workflow"
    ),
    KeywordTriggerDefinition(
        "investigate", "analyze", 7, "Activate deep analysis workflow"
    ),
    KeywordTriggerDefinition(
        "$deep-interview",
        "deep-interview",
        8,
        "Activate Ouroboros-inspired Socratic ambiguity-gated interview workflow",
    ),
    KeywordTriggerDefinition(
        "deep interview",
        "deep-interview",
        8,
        "Activate Ouroboros-inspired Socratic ambiguity-gated interview workflow",
    ),
    KeywordTriggerDefinition(
        "gather requirements",
        "deep-interview",
        8,
        "Activate Ouroboros-inspired Socratic ambiguity-gated interview workflow",
    ),
    KeywordTriggerDefinition(
        "interview me",
        "deep-interview",
        8,
        "Activate Ouroboros-inspired Socratic ambiguity-gated interview workflow",
    ),
    KeywordTriggerDefinition(
        "don't assume",
        "deep-interview",
        8,
        "Activate Ouroboros-inspired Socratic ambiguity-gated interview workflow",
    ),
    KeywordTriggerDefinition(
        "ouroboros",
        "deep-interview",
        8,
        "Activate Ouroboros-inspired Socratic ambiguity-gated interview workflow",
    ),
    KeywordTriggerDefinition(
        "interview",
        "deep-interview",
        8,
        "Activate Ouroboros-inspired Socratic ambiguity-gated interview workflow",
    ),
    KeywordTriggerDefinition("$plan", "plan", 8, "Activate planning skill"),
    KeywordTriggerDefinition("plan this", "plan", 8, "Activate planning skill"),
    KeywordTriggerDefinition("plan the", "plan", 8, "Activate planning skill"),
    KeywordTriggerDefinition("let's plan", "plan", 8, "Activate planning skill"),
    KeywordTriggerDefinition(
        "$ralplan",
        "ralplan",
        11,
        "Activate consensus planning (planner + architect + critic)",
    ),
    KeywordTriggerDefinition(
        "consensus plan",
        "ralplan",
        11,
        "Activate consensus planning (planner + architect + critic)",
    ),
    KeywordTriggerDefinition(
        "$autoresearch",
        "autoresearch",
        10,
        "Activate autoresearch validator-gated research loop",
    ),
    KeywordTriggerDefinition("$team", "team", 8, "Activate coordinated team mode"),
    KeywordTriggerDefinition(
        "swarm",
        "team",
        8,
        "Activate coordinated team mode (swarm is a compatibility alias for team)",
    ),
    KeywordTriggerDefinition(
        "coordinated team", "team", 8, "Activate coordinated team mode"
    ),
    KeywordTriggerDefinition(
        "coordinated swarm",
        "team",
        8,
        "Activate coordinated team mode (swarm is a compatibility alias for team)",
    ),
    KeywordTriggerDefinition("$cancel", "cancel", 5, "Cancel active execution modes"),
    KeywordTriggerDefinition("stop", "cancel", 5, "Cancel active execution modes"),
    KeywordTriggerDefinition("abort", "cancel", 5, "Cancel active execution modes"),
    KeywordTriggerDefinition("$tdd", "tdd", 6, "Activate test-driven workflow"),
    KeywordTriggerDefinition("tdd", "tdd", 6, "Activate test-driven workflow"),
    KeywordTriggerDefinition("test first", "tdd", 6, "Activate test-driven workflow"),
    KeywordTriggerDefinition(
        "$build-fix", "build-fix", 6, "Activate build-fix workflow"
    ),
    KeywordTriggerDefinition(
        "fix build", "build-fix", 6, "Activate build-fix workflow"
    ),
    KeywordTriggerDefinition(
        "type errors", "build-fix", 6, "Activate build-fix workflow"
    ),
    KeywordTriggerDefinition("$wiki", "wiki", 5, "Activate the project wiki skill"),
    KeywordTriggerDefinition(
        "wiki query", "wiki", 5, "Activate the project wiki skill for search"
    ),
    KeywordTriggerDefinition(
        "wiki add", "wiki", 5, "Activate the project wiki skill for page creation"
    ),
    KeywordTriggerDefinition(
        "wiki lint", "wiki", 5, "Activate the project wiki skill for wiki health checks"
    ),
    KeywordTriggerDefinition(
        "code review", "code-review", 6, "Activate code-review workflow"
    ),
    KeywordTriggerDefinition(
        "$code-review", "code-review", 6, "Activate code-review workflow"
    ),
    KeywordTriggerDefinition(
        "review code", "code-review", 6, "Activate code-review workflow"
    ),
    KeywordTriggerDefinition(
        "$security-review", "security-review", 6, "Activate security-review workflow"
    ),
    KeywordTriggerDefinition(
        "security review", "security-review", 6, "Activate security-review workflow"
    ),
)


def compare_keyword_matches(
    a: KeywordTriggerDefinition,
    b: KeywordTriggerDefinition,
) -> int:
    """Compare two keyword matches for sorting (higher priority first, longer keyword first).

    Args:
        a: First match.
        b: Second match.

    Returns:
        Negative if *a* should sort before *b*, positive if after, zero if equal.
    """
    if b.priority != a.priority:
        return b.priority - a.priority
    if len(b.keyword) != len(a.keyword):
        return len(b.keyword) - len(a.keyword)
    return (a.keyword > b.keyword) - (a.keyword < b.keyword)
