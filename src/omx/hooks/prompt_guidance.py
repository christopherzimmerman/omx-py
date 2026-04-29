"""Prompt guidance contract definitions.

Port of src/hooks/prompt-guidance-contract.ts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class GuidanceSurfaceContract:
    """A contract specifying required patterns in a guidance surface file.

    Attributes:
        id: Unique contract identifier.
        path: Relative path to the guidance file.
        required_patterns: Regex patterns that must appear in the file.
    """

    id: str
    path: str
    required_patterns: tuple[re.Pattern[str], ...] = ()


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


_ROOT_TEMPLATE_PATTERNS = (
    _rx(r"quality-first.*intent-deepening responses"),
    _rx(r"clear, low-risk, reversible next steps"),
    _rx(r"AUTO-CONTINUE.*clear.*already-requested.*low-risk.*reversible.*local"),
    _rx(
        r"ASK only.*destructive.*irreversible.*credential-gated.*external-production.*materially scope-changing"
    ),
    _rx(r"AUTO-CONTINUE branches.*permission-handoff phrasing"),
    _rx(r"do not ask or instruct humans.*ordinary non-destructive.*reversible actions"),
    _rx(r"OMX runtime manipulation.*agent responsibilities"),
    _rx(r"Keep going unless blocked"),
    _rx(r"Ask only when blocked|Ask only when progress is impossible"),
    _rx(r"local overrides?.*non-conflicting instructions"),
    _rx(r"reflexive web/tool escalation"),
    _rx(r"Choose the lane before acting"),
    _rx(r"Solo execute"),
    _rx(r"Outside active `team`/`swarm` mode, use `executor`"),
    _rx(r"Reserve `worker` strictly for active `team`/`swarm` sessions"),
    _rx(r"Leader responsibilities"),
    _rx(r"Worker responsibilities"),
    _rx(
        r"Route to `explore` for repo-local file / symbol / pattern / relationship lookup"
    ),
    _rx(r"explore` owns facts about this repo"),
    _rx(r"Route to `researcher` when the main need is official docs"),
    _rx(r"technology is already chosen"),
    _rx(r"Route to `dependency-expert` when the main need is package / SDK selection"),
    _rx(
        r"whether / which package, SDK, or framework to adopt, upgrade, replace, or migrate"
    ),
    _rx(r"Use mixed routing deliberately"),
    _rx(r"boundary crossings upward"),
    _rx(r"Stop / escalate"),
    _rx(r"Default update/final shape"),
    _rx(r"do not skip prerequisites|task is grounded and verified"),
    _rx(r"quality-first evidence summaries"),
)

_CORE_ROLE_PATTERNS = {
    "executor": (
        _rx(r"quality-first.*intent-deepening outputs"),
        _rx(r"reflexive web/tool escalation"),
        _rx(r"local overrides?.*non-conflicting constraints"),
        _rx(r"task is grounded and verified"),
        _rx(r"AUTO-CONTINUE.*clear.*already-requested.*low-risk.*reversible.*local"),
        _rx(
            r"ASK only.*destructive.*irreversible.*credential-gated.*external-production.*materially scope-changing"
        ),
        _rx(r"AUTO-CONTINUE branches.*permission-handoff phrasing"),
        _rx(r"Keep going unless blocked"),
        _rx(r"Ask only when progress is impossible|Ask only when blocked"),
    ),
    "planner": (
        _rx(r"quality-first.*intent-deepening plan summaries"),
        _rx(r"reflexive web/tool escalation"),
        _rx(r"local overrides?.*non-conflicting constraints"),
        _rx(r"plan is grounded in evidence"),
        _rx(r"AUTO-CONTINUE.*clear.*already-requested.*low-risk.*reversible.*local"),
        _rx(
            r"ASK only.*destructive.*irreversible.*credential-gated.*external-production.*materially scope-changing"
        ),
        _rx(r"AUTO-CONTINUE branches.*permission-handoff phrasing"),
        _rx(r"Keep advancing the current planning branch unless blocked"),
        _rx(r"Ask only when a real planning blocker|Ask only when blocked"),
    ),
    "verifier": (
        _rx(r"quality-first, evidence-dense summaries"),
        _rx(r"proof that matters|tool churn"),
        _rx(r"verdict is grounded"),
        _rx(r"non-conflicting acceptance criteria"),
        _rx(r"AUTO-CONTINUE.*clear.*already-requested.*low-risk.*reversible.*local"),
        _rx(
            r"ASK only.*destructive.*irreversible.*credential-gated.*external-production.*materially scope-changing"
        ),
        _rx(r"AUTO-CONTINUE branches.*permission-handoff phrasing"),
        _rx(r"Keep gathering evidence until the verdict is grounded or blocked"),
        _rx(
            r"Ask only when the acceptance target is materially unclear|Ask only when blocked"
        ),
    ),
}

_WAVE_TWO_PATTERNS = (
    _rx(r"Default final-output shape: quality-first and evidence-dense"),
    _rx(r"Treat newer user task updates as local overrides"),
    _rx(r"user says `continue`"),
)

_CATALOG_PATTERNS = (
    _rx(r"Default final-output shape: quality-first and evidence-dense"),
    _rx(r"Treat newer user task updates as local overrides"),
    _rx(r"user says `continue`"),
)

_SKILL_PATTERNS = (
    _rx(r"concise, evidence-dense progress and completion reporting"),
    _rx(r"local overrides for the active workflow branch"),
    _rx(r"user says `continue`"),
)

_ULTRAWORK_SKILL_PATTERNS = (
    *_SKILL_PATTERNS,
    _rx(r"Gather enough context before implementation"),
    _rx(r"Define pass/fail acceptance criteria before launching execution lanes"),
    _rx(r"run a direct-tool lane and one or more background evidence lanes"),
    _rx(r"Choose self vs delegate deliberately"),
    _rx(
        r"Manual QA notes are recorded when the task needs a human-visible or behavior-level check"
    ),
    _rx(
        r"Ralph owns persistence, architect verification, deslop, and the full verified-completion promise"
    ),
)

ROOT_TEMPLATE_CONTRACTS: tuple[GuidanceSurfaceContract, ...] = (
    GuidanceSurfaceContract(
        id="agents-template",
        path="templates/AGENTS.md",
        required_patterns=_ROOT_TEMPLATE_PATTERNS,
    ),
)

CORE_ROLE_CONTRACTS: tuple[GuidanceSurfaceContract, ...] = tuple(
    GuidanceSurfaceContract(
        id=name,
        path=f"prompts/{name}.md",
        required_patterns=patterns,
    )
    for name, patterns in _CORE_ROLE_PATTERNS.items()
)

SCENARIO_ROLE_CONTRACTS: tuple[GuidanceSurfaceContract, ...] = (
    GuidanceSurfaceContract(
        id="executor-scenarios",
        path="prompts/executor.md",
        required_patterns=(
            _rx(r"user says `continue`"),
            _rx(r"make a PR targeting dev"),
            _rx(r"merge to dev if CI green"),
            _rx(r"confirm CI is green, then merge"),
        ),
    ),
    GuidanceSurfaceContract(
        id="planner-scenarios",
        path="prompts/planner.md",
        required_patterns=(
            _rx(r"user says `continue`"),
            _rx(r"user says `make a PR`"),
            _rx(r"user says `merge if CI green`"),
            _rx(r"scoped condition on the next operational step"),
        ),
    ),
    GuidanceSurfaceContract(
        id="verifier-scenarios",
        path="prompts/verifier.md",
        required_patterns=(
            _rx(r"user says `merge if CI green`"),
            _rx(r"confirm they are green"),
            _rx(r"user says `continue`"),
            _rx(r"keep gathering the required evidence"),
        ),
    ),
)

_WAVE_TWO_NAMES = (
    "architect",
    "critic",
    "debugger",
    "test-engineer",
    "code-reviewer",
    "quality-reviewer",
    "security-reviewer",
    "researcher",
    "explore",
)

WAVE_TWO_CONTRACTS: tuple[GuidanceSurfaceContract, ...] = tuple(
    GuidanceSurfaceContract(
        id=name, path=f"prompts/{name}.md", required_patterns=_WAVE_TWO_PATTERNS
    )
    for name in _WAVE_TWO_NAMES
)

_CATALOG_NAMES = (
    "analyst",
    "api-reviewer",
    "build-fixer",
    "dependency-expert",
    "designer",
    "git-master",
    "information-architect",
    "performance-reviewer",
    "product-analyst",
    "product-manager",
    "qa-tester",
    "quality-strategist",
    "style-reviewer",
    "ux-researcher",
    "vision",
    "writer",
)

CATALOG_CONTRACTS: tuple[GuidanceSurfaceContract, ...] = tuple(
    GuidanceSurfaceContract(
        id=name, path=f"prompts/{name}.md", required_patterns=_CATALOG_PATTERNS
    )
    for name in _CATALOG_NAMES
)

LEGACY_PROMPT_CONTRACTS: tuple[GuidanceSurfaceContract, ...] = (
    GuidanceSurfaceContract(
        id="code-simplifier",
        path="prompts/code-simplifier.md",
        required_patterns=(
            _rx(r"local overrides for the active simplification scope"),
            _rx(r"simplification result is grounded"),
            _rx(r"<Scenario_Examples>"),
        ),
    ),
)

SPECIALIZED_PROMPT_CONTRACTS: tuple[GuidanceSurfaceContract, ...] = (
    GuidanceSurfaceContract(
        id="sisyphus-lite",
        path="prompts/sisyphus-lite.md",
        required_patterns=(
            _rx(r"quality-first.*intent-deepening outputs"),
            _rx(r"Treat newer user instructions as local overrides"),
            _rx(r"No evidence = not complete"),
            _rx(r"specialized worker behavior prompt|worker behavior prompt"),
        ),
    ),
)

_SKILL_CONTRACT_NAMES = (
    "analyze",
    "autopilot",
    "build-fix",
    "code-review",
    "plan",
    "ralph",
    "ralplan",
    "security-review",
    "team",
    "ultraqa",
)

SKILL_CONTRACTS: tuple[GuidanceSurfaceContract, ...] = (
    *tuple(
        GuidanceSurfaceContract(
            id=name,
            path=f"skills/{name}/SKILL.md",
            required_patterns=_SKILL_PATTERNS,
        )
        for name in _SKILL_CONTRACT_NAMES
    ),
    GuidanceSurfaceContract(
        id="ultrawork",
        path="skills/ultrawork/SKILL.md",
        required_patterns=_ULTRAWORK_SKILL_PATTERNS,
    ),
)
