"""Role routing for team orchestration.

Port of ``src/team/role-router.ts``.

Two layers:

* **Layer 1 — Prompt loading**: :func:`load_role_prompt`, :func:`is_known_role`,
  :func:`list_available_roles` discover behavioral prompts on disk.
* **Layer 2 — Heuristic role routing**: :func:`route_task_to_role` (TS-parity
  signature) inspects a task description and returns a :class:`RoleRouterResult`
  with role, confidence, and reason.

Python-only legacy helpers (:func:`infer_role_from_task` and the legacy
``route_task_to_role`` dict-based dispatch) are preserved under
:func:`infer_role_from_task_legacy` and :func:`route_task_to_role_legacy` to
avoid breaking any future caller; they have no current Python consumers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


# ─── Layer 1: Prompt Loading ─────────────────────────────────────────────────

# Role names must be lowercase alphanumeric with hyphens (e.g., 'test-engineer').
SAFE_ROLE_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")


def load_role_prompt(role: str, prompts_dir: str | Path) -> str | None:
    """Load behavioral prompt content for a given agent role.

    Returns ``None`` if the prompt file does not exist, the role name is
    invalid, or the file content is blank after trimming.

    Args:
        role: Role name (lowercase, alphanumeric, hyphens).
        prompts_dir: Directory containing ``<role>.md`` prompt files.

    Returns:
        Trimmed prompt content, or ``None``.
    """
    if not SAFE_ROLE_PATTERN.match(role):
        return None
    file_path = Path(prompts_dir) / f"{role}.md"
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    stripped = content.strip()
    return stripped or None


def is_known_role(role: str, prompts_dir: str | Path) -> bool:
    """Return ``True`` if a role has a corresponding prompt file on disk."""
    if not SAFE_ROLE_PATTERN.match(role):
        return False
    return (Path(prompts_dir) / f"{role}.md").is_file()


def list_available_roles(prompts_dir: str | Path) -> list[str]:
    """List all available roles by scanning the prompts directory.

    Returns role names (filename without ``.md`` extension) sorted alphabetically.
    Returns an empty list if the directory does not exist or cannot be read.
    """
    base = Path(prompts_dir)
    try:
        entries = list(base.iterdir())
    except (OSError, FileNotFoundError):
        return []
    return sorted(
        p.name[:-3] for p in entries if p.is_file() and p.name.endswith(".md")
    )


# ─── Layer 2: Heuristic Role Routing ─────────────────────────────────────────

Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class RoleRouterResult:
    """Result of routing a task to an agent role.

    Attributes:
        role: Selected role name.
        confidence: ``"high"``, ``"medium"``, or ``"low"``.
        reason: Human-readable explanation of the routing decision.
    """

    role: str
    confidence: Confidence
    reason: str


# Lane intent labels — internal classification used by ``_infer_lane_intent``.
LaneIntent = Literal[
    "implementation",
    "verification",
    "review",
    "debug",
    "design",
    "docs",
    "build-fix",
    "cleanup",
    "unknown",
]


# Keyword-to-role mapping categories.
# Order matters: first match wins within a category, but higher keyword count
# wins across categories.
ROLE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "test-engineer",
        (
            "test",
            "spec",
            "coverage",
            "tdd",
            "jest",
            "vitest",
            "mocha",
            "pytest",
            "unit test",
            "integration test",
            "e2e",
            "테스트",  # 테스트
            "커버리지",  # 커버리지
        ),
    ),
    (
        "designer",
        (
            "ui",
            "component",
            "layout",
            "css",
            "design",
            "responsive",
            "tailwind",
            "react",
            "frontend",
            "styling",
            "ux",
            "디자인",  # 디자인
            "레이아웃",  # 레이아웃
            "컴포넌트",  # 컴포넌트
        ),
    ),
    (
        "build-fixer",
        (
            "build",
            "compile",
            "tsc",
            "type error",
            "typescript error",
            "build error",
            "compilation",
            "빌드",  # 빌드
            "컴파일",  # 컴파일
            "타입 오류",  # 타입 오류
        ),
    ),
    (
        "debugger",
        (
            "debug",
            "investigate",
            "root cause",
            "regression",
            "stack trace",
            "bisect",
            "diagnose",
            "디버그",  # 디버그
            "조사",  # 조사
            "원인",  # 원인
        ),
    ),
    (
        "writer",
        (
            "doc",
            "readme",
            "migration guide",
            "changelog",
            "comment",
            "documentation",
            "api doc",
            "문서",  # 문서
            "가이드",  # 가이드
            "변경로그",  # 변경로그
        ),
    ),
    (
        "quality-reviewer",
        (
            "review",
            "audit",
            "quality",
            "lint",
            "anti-pattern",
            "code review",
            "검토",  # 검토
            "리뷰",  # 리뷰
        ),
    ),
    (
        "security-reviewer",
        (
            "security",
            "owasp",
            "xss",
            "injection",
            "cve",
            "vulnerability",
            "보안",  # 보안
            "취약점",  # 취약점
        ),
    ),
    (
        "code-simplifier",
        (
            "refactor",
            "simplify",
            "clean up",
            "reduce complexity",
            "consolidate",
            "리팩터",  # 리팩터
            "단순화",  # 단순화
        ),
    ),
]


# Intent regexes — kept verbose for parity with the TS source.
# Note: ``\b`` does not match between ASCII word chars and Hangul, so Korean
# alternatives in TS are written without ``\b``; we mirror that exactly.
IMPLEMENTATION_INTENT = re.compile(
    r"\b(?:add|build|create|fix|implement|make|migrate|repair|ship|support|update|wire)\b"
    r"|(?:구현|추가|수정|업데이트|지원)",
    re.IGNORECASE,
)
REVIEW_INTENT = re.compile(
    r"\b(?:audit|check|inspect|review|validate|verify)\b"
    r"|(?:검토|리뷰|감사|확인|검증)",
    re.IGNORECASE,
)
PRIMARY_TEST_INTENT = re.compile(
    r"^(?:add|create|expand|improve|increase|write)\b.*\b(?:tests?|specs?|coverage)\b"
    r"|^(?:테스트\s*(?:추가|작성)|커버리지\s*추가)",
    re.IGNORECASE,
)
DOCS_INTENT = re.compile(
    r"\b(?:docs?|documentation|readme|guide|changelog)\b"
    r"|(?:문서|가이드|README|변경로그)",
    re.IGNORECASE,
)
PRIMARY_DOCS_INTENT = re.compile(
    r"^(?:document|draft|write|update)\b.*\b(?:docs?|documentation|readme|guide|changelog)\b"
    r"|^(?:문서\s*(?:업데이트|작성)|README\s*업데이트|가이드\s*작성)",
    re.IGNORECASE,
)
DEBUG_INTENT = re.compile(
    r"\b(?:debug|diagnose|investigate|root cause|trace|bisect)\b"
    r"|(?:디버그|조사|원인)",
    re.IGNORECASE,
)
DESIGN_INTENT = re.compile(
    r"\b(?:design|layout|style)\b"
    r"|\b(?:build|create)\b.*\b(?:ui|component|frontend)\b"
    r"|(?:디자인|레이아웃|스타일|컴포넌트)",
    re.IGNORECASE,
)
BUILD_FIX_INTENT = re.compile(
    r"\b(?:build|compile|tsc|type error|compilation)\b"
    r"|(?:빌드|컴파일|타입 오류)",
    re.IGNORECASE,
)
CLEANUP_INTENT = re.compile(
    r"\b(?:clean up|consolidate|reduce complexity|refactor|simplify)\b"
    r"|(?:정리|단순화|리팩터)",
    re.IGNORECASE,
)
SECURITY_DOMAIN = re.compile(
    r"\b(?:auth|authentication|authorization|cve|injection|owasp|security|vulnerability|xss)\b"
    r"|(?:보안|인증|인가|취약점)",
    re.IGNORECASE,
)
LOCAL_EXPLORATION_VERB = re.compile(
    r"\b(?:check|find|inspect|locate|look up|lookup|map|review|search|trace|understand|where(?:\s+is|\s+are)?|which files?|what files?)\b",
    re.IGNORECASE,
)
LOCAL_EXPLORATION_SUBJECT = re.compile(
    r"\b(?:file|files|symbol|symbols|repo|repository|codebase|path|paths|usage|usages|"
    r"reference|references|relationship|relationships|wiring|flow|implementation|local)\b",
    re.IGNORECASE,
)
LOCAL_USAGE_DISCOVERY = re.compile(
    r"\b(?:call sites?|current(?:ly)? use|how\s+we\s+use|integration points?|our usage|where\s+we\s+use)\b",
    re.IGNORECASE,
)
_LOCAL_USAGE_CONTEXT = re.compile(
    r"\b(?:current|currently|dependency|existing|local|our|package|packages|repo|repository|sdk|sdks|library|libraries)\b",
    re.IGNORECASE,
)
DEPENDENCY_EVALUATION_SIGNAL = re.compile(
    r"\b(?:dependency|dependencies|package|packages|sdk|sdks|library|libraries|"
    r"framework|frameworks|crate|crates|npm|pypi|crates\.io|license|licenses|"
    r"maintenance|download stats?|migration path|vendor)\b",
    re.IGNORECASE,
)
DEPENDENCY_EVALUATION_VERB = re.compile(
    r"\b(?:adopt|assess|choose|compare|evaluate|recommend|replace|select|swap|upgrade)\b",
    re.IGNORECASE,
)
DEPENDENCY_EVALUATION_CONTEXT = re.compile(
    r"\b(?:candidate|candidates|comparison|download stats?|license|licenses|"
    r"maintenance|migration path|options?|replacement|risk|trade-?offs?|upgrade|vendor)\b",
    re.IGNORECASE,
)
DEPENDENCY_IMPLEMENTATION_SIGNAL = re.compile(
    r"\b(?:adapter|api|call sites?|client|clients|code(?:path|paths)?|endpoint|endpoints|"
    r"flow|flows|handler|handlers|implementation|imports?|integrat(?:e|ion)|module|modules|"
    r"refactor|wire)\b",
    re.IGNORECASE,
)
_DEPENDENCY_IMPL_REPLACE_VERB = re.compile(
    r"\b(?:adopt|migrate|port|replace|replacement|swap|upgrade|wire)\b",
    re.IGNORECASE,
)
_DEPENDENCY_IMPL_EVAL_VERB = re.compile(
    r"\b(?:assess|choose|compare|evaluate|options?|recommend|select|trade-?offs?)\b",
    re.IGNORECASE,
)
RESEARCH_SIGNAL = re.compile(
    r"\b(?:official docs?|upstream docs?|vendor docs?|reference|references|api docs?|"
    r"release notes?|changelog|version(?:ing)?|compatib(?:ility|le)|research)\b",
    re.IGNORECASE,
)
RESEARCH_VERB = re.compile(
    r"\b(?:check|consult|investigate|look up|lookup|read|research|review|study|verify)\b",
    re.IGNORECASE,
)
_RESEARCH_DOC_TERMS = re.compile(
    r"\b(?:compatib(?:ility|le)|official docs?|release notes?|upstream docs?|vendor docs?|version(?:ing)?)\b",
    re.IGNORECASE,
)
CHOSEN_TECH_RESEARCH_SIGNAL = re.compile(
    r"\b(?:api|apis|framework|frameworks|library|libraries|sdk|sdks|service|services|tool|tools|vendor)\b",
    re.IGNORECASE,
)
CHOSEN_TECH_RESEARCH_NEED = re.compile(
    r"\b(?:behavior|best way|configuration|configure|example|examples|feature|features?|"
    r"how(?:\s+do|\s+to)?|in the wild|lifecycle|option|options|parameter|parameters|usage|"
    r"what(?:\s+does|\s+is)|when(?:\s+does|\s+should)|why(?:\s+does)?)\b",
    re.IGNORECASE,
)
DOCS_DELIVERABLE_VERB = re.compile(
    r"\b(?:add|document|draft|edit|prepare|publish|refresh|revise|update|write)\b",
    re.IGNORECASE,
)
DOCS_DELIVERABLE_NOUN = re.compile(
    r"\b(?:api docs?|changelog|comments?|documentation|docs?|guide|guides|readme|release notes?)\b",
    re.IGNORECASE,
)
_BUILD_FIX_REPAIR_VERB = re.compile(
    r"\b(?:fix|resolve|repair)\b|(?:수정|해결)",
    re.IGNORECASE,
)


def _is_local_exploration_task(text: str) -> bool:
    if LOCAL_EXPLORATION_VERB.search(text) and LOCAL_EXPLORATION_SUBJECT.search(text):
        return True
    if LOCAL_USAGE_DISCOVERY.search(text) and _LOCAL_USAGE_CONTEXT.search(text):
        return True
    return False


def _is_documentation_deliverable_task(text: str) -> bool:
    if PRIMARY_DOCS_INTENT.search(text):
        return True
    return bool(
        DOCS_DELIVERABLE_VERB.search(text) and DOCS_DELIVERABLE_NOUN.search(text)
    )


def _is_implementation_heavy_dependency_task(text: str) -> bool:
    return bool(
        DEPENDENCY_EVALUATION_SIGNAL.search(text)
        and IMPLEMENTATION_INTENT.search(text)
        and DEPENDENCY_IMPLEMENTATION_SIGNAL.search(text)
        and _DEPENDENCY_IMPL_REPLACE_VERB.search(text)
        and not _DEPENDENCY_IMPL_EVAL_VERB.search(text)
    )


def _is_dependency_evaluation_task(text: str) -> bool:
    if _is_documentation_deliverable_task(
        text
    ) or _is_implementation_heavy_dependency_task(text):
        return False
    return bool(
        DEPENDENCY_EVALUATION_SIGNAL.search(text)
        and (
            DEPENDENCY_EVALUATION_VERB.search(text)
            or DEPENDENCY_EVALUATION_CONTEXT.search(text)
        )
    )


def _is_research_task(text: str) -> bool:
    docs_driven_research = bool(
        RESEARCH_SIGNAL.search(text)
        and (RESEARCH_VERB.search(text) or _RESEARCH_DOC_TERMS.search(text))
    )
    chosen_technology_guidance = bool(
        CHOSEN_TECH_RESEARCH_SIGNAL.search(text)
        and CHOSEN_TECH_RESEARCH_NEED.search(text)
    )
    return (
        (docs_driven_research or chosen_technology_guidance)
        and not _is_documentation_deliverable_task(text)
        and not _is_local_exploration_task(text)
        and not _is_dependency_evaluation_task(text)
    )


def _infer_lane_intent(text: str) -> LaneIntent:
    if BUILD_FIX_INTENT.search(text) and _BUILD_FIX_REPAIR_VERB.search(text):
        return "build-fix"
    if DEBUG_INTENT.search(text):
        return "debug"
    if REVIEW_INTENT.search(text):
        return "review"
    if PRIMARY_TEST_INTENT.search(text):
        return "verification"
    if PRIMARY_DOCS_INTENT.search(text) or DOCS_INTENT.search(text):
        return "docs"
    if DESIGN_INTENT.search(text):
        return "design"
    if CLEANUP_INTENT.search(text):
        return "cleanup"
    if IMPLEMENTATION_INTENT.search(text):
        return "implementation"
    return "unknown"


# Phase-context labels used in routing reason strings only — NOT applied
# as role assignments.
PHASE_CONTEXT_LABELS: dict[str, str] = {
    "team-verify": "verifier",
    "team-fix": "build-fixer",
    "team-plan": "planner",
    "team-prd": "analyst",
}


def route_task_to_role(
    task_subject: str,
    task_description: str = "",
    phase: str | None = None,
    fallback_role: str = "executor",
) -> RoleRouterResult:
    """Map a task description to the best agent role using keyword heuristics.

    Falls back to ``fallback_role`` when confidence is low.

    Port of TS ``routeTaskToRole``. ``phase`` accepts any TS ``TeamPhase`` string
    (e.g. ``"team-plan"``, ``"team-verify"``) or ``None``; only phases listed in
    :data:`PHASE_CONTEXT_LABELS` influence the diagnostic reason.

    Args:
        task_subject: Short task subject (e.g. title or first line).
        task_description: Optional longer description.
        phase: Optional team phase string for low-confidence fallback hints.
        fallback_role: Role returned when no keyword/intent matches.

    Returns:
        :class:`RoleRouterResult` with role, confidence, and reason.
    """
    text = f"{task_subject} {task_description}".lower()
    intent = _infer_lane_intent(text)

    if intent == "build-fix":
        return RoleRouterResult(
            role="build-fixer",
            confidence="high",
            reason="primary intent is build/compile repair",
        )

    if intent == "debug":
        return RoleRouterResult(
            role="debugger",
            confidence="high",
            reason="primary intent is investigation/debugging",
        )

    if _is_local_exploration_task(text):
        return RoleRouterResult(
            role="explore",
            confidence="high",
            reason="primary intent is local codebase/file/symbol exploration",
        )

    if _is_implementation_heavy_dependency_task(text):
        return RoleRouterResult(
            role=fallback_role,
            confidence="medium",
            reason=(
                "dependency/sdk terms appear inside implementation-heavy replacement work, "
                "so using fallback implementation lane"
            ),
        )

    if _is_dependency_evaluation_task(text):
        return RoleRouterResult(
            role="dependency-expert",
            confidence="high",
            reason="primary intent is external dependency/package evaluation",
        )

    if _is_research_task(text):
        return RoleRouterResult(
            role="researcher",
            confidence="high",
            reason="primary intent is external documentation/reference research",
        )

    if intent == "docs":
        return RoleRouterResult(
            role="writer",
            confidence="high",
            reason="primary intent is documentation deliverable",
        )

    if intent == "design":
        return RoleRouterResult(
            role="designer",
            confidence="high",
            reason="primary intent is UI/design implementation",
        )

    if intent == "cleanup":
        return RoleRouterResult(
            role="code-simplifier",
            confidence="high",
            reason="primary intent is simplification/refactor work",
        )

    if intent == "review":
        if SECURITY_DOMAIN.search(text):
            return RoleRouterResult(
                role="security-reviewer",
                confidence="high",
                reason="primary intent is security-focused review",
            )
        return RoleRouterResult(
            role="quality-reviewer",
            confidence="high",
            reason="primary intent is review/verification",
        )

    if intent == "verification":
        return RoleRouterResult(
            role="test-engineer",
            confidence="high",
            reason="primary intent is test/verification output",
        )

    if intent == "implementation" and SECURITY_DOMAIN.search(text):
        return RoleRouterResult(
            role=fallback_role,
            confidence="medium",
            reason=(
                "security/auth domain detected but task intent is implementation, "
                "so using fallback implementation lane"
            ),
        )

    # Score each role category by keyword match count.
    best_role = ""
    best_count = 0
    best_keyword = ""

    for role, keywords in ROLE_KEYWORDS:
        count = 0
        matched_keyword = ""
        for kw in keywords:
            if kw in text:
                count += 1
                if not matched_keyword:
                    matched_keyword = kw
        if count > best_count:
            best_count = count
            best_role = role
            best_keyword = matched_keyword

    if best_count >= 2:
        return RoleRouterResult(
            role=best_role,
            confidence="high",
            reason=f'matched {best_count} keywords in {best_role} category (e.g., "{best_keyword}")',
        )

    if best_count == 1:
        return RoleRouterResult(
            role=best_role,
            confidence="medium",
            reason=f'matched keyword "{best_keyword}" for {best_role}',
        )

    # Low confidence: phase-context inference only.
    if phase:
        phase_default = PHASE_CONTEXT_LABELS.get(phase)
        if phase_default:
            return RoleRouterResult(
                role=fallback_role,  # fallback per plan
                confidence="low",
                reason=f"no keyword match; phase {phase} suggests {phase_default} but using fallback",
            )

    return RoleRouterResult(
        role=fallback_role,
        confidence="low",
        reason="no keyword match; using fallback role",
    )


# ─── Legacy Python helpers ───────────────────────────────────────────────────
#
# These predate the TS-parity port. They have no current Python callers but are
# preserved so any future code that imports them continues to work.

# File extension to role mapping (legacy).
EXTENSION_ROLES: dict[str, str] = {
    ".test.": "verifier",
    ".spec.": "verifier",
    "_test.": "verifier",
    ".css": "designer",
    ".scss": "designer",
    ".html": "designer",
    ".tsx": "executor",
    ".jsx": "executor",
    ".sql": "data-analyst",
    ".py": "executor",
    ".rs": "executor",
    ".go": "executor",
    ".ts": "executor",
    ".js": "executor",
}

# Keyword to role mapping (legacy).
KEYWORD_ROLES: dict[str, str] = {
    "test": "verifier",
    "spec": "verifier",
    "bug": "debugger",
    "fix": "debugger",
    "debug": "debugger",
    "design": "designer",
    "ui": "designer",
    "ux": "designer",
    "review": "quality-reviewer",
    "security": "security-reviewer",
    "perf": "executor",
    "refactor": "executor",
    "deploy": "devops",
    "ci": "devops",
    "infrastructure": "devops",
}


def infer_role_from_task_legacy(
    description: str,
    file_paths: list[str] | None = None,
) -> str:
    """Infer a role from a task description and file paths (legacy)."""
    desc_lower = description.lower()

    for keyword, role in KEYWORD_ROLES.items():
        if keyword in desc_lower:
            return role

    if file_paths:
        for path in file_paths:
            for ext, role in EXTENSION_ROLES.items():
                if ext in path.lower():
                    return role

    return "executor"


def route_task_to_role_legacy(
    task: dict[str, Any],
    available_roles: list[str] | None = None,
) -> str:
    """Route a task to the best available role from a roster (legacy dict API)."""
    inferred = infer_role_from_task_legacy(
        task.get("description", ""),
        task.get("file_paths"),
    )

    if available_roles and inferred not in available_roles:
        if "executor" in available_roles:
            return "executor"
        return available_roles[0] if available_roles else "executor"

    return inferred
