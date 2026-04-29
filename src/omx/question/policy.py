"""Question policy evaluation.

Port of src/question/policy.ts. Determines whether a question prompt
is allowed based on active workflows, team state, and environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


BLOCKED_EXECUTION_SKILLS: frozenset[str] = frozenset(
    {
        "autopilot",
        "autoresearch",
        "team",
        "ralph",
        "ultrawork",
        "ultraqa",
    }
)


@dataclass
class QuestionPolicyDecision:
    """Result of evaluating whether a question prompt is allowed.

    Attributes:
        allowed: Whether the question is allowed.
        session_id: Session identifier used for evaluation.
        code: Denial reason code, if not allowed.
        message: Human-readable denial message, if not allowed.
        fallback_allowed: Whether a fallback question path is available.
        active_modes: Currently active workflow modes.
        active_skills: Currently active canonical skills.
        active_teams: Currently active team descriptors.
    """

    allowed: bool = False
    session_id: str | None = None
    code: str | None = None
    message: str | None = None
    fallback_allowed: bool = False
    active_modes: list[str] = field(default_factory=list)
    active_skills: list[str] = field(default_factory=list)
    active_teams: list[dict[str, Any]] = field(default_factory=list)


def _safe_string(value: Any) -> str:
    """Coerce to string, returning '' for non-strings."""
    return value if isinstance(value, str) else ""


def _has_worker_context(env: dict[str, str] | None = None) -> bool:
    """Check if running as a team worker."""
    env = env or dict(os.environ)
    return _safe_string(env.get("OMX_TEAM_WORKER", "")).strip() != ""


def evaluate_question_policy(
    cwd: str,
    *,
    explicit_session_id: str | None = None,
    env: dict[str, str] | None = None,
    active_modes: list[str] | None = None,
    active_skills: list[str] | None = None,
    active_teams: list[dict[str, Any]] | None = None,
) -> QuestionPolicyDecision:
    """Evaluate whether an interactive question prompt is allowed.

    Checks worker context, active teams, and blocked execution modes
    to decide if the current session may ask the user a question.

    Args:
        cwd: Working directory.
        explicit_session_id: Explicit session ID override.
        env: Environment variables (defaults to os.environ).
        active_modes: Pre-resolved active workflow modes.
        active_skills: Pre-resolved active canonical skills.
        active_teams: Pre-resolved active team descriptors.

    Returns:
        QuestionPolicyDecision indicating whether the question is allowed.
    """
    env = env or dict(os.environ)
    session_id = explicit_session_id or env.get("OMX_SESSION_ID", "").strip() or None
    modes = active_modes or []
    skills = active_skills or []
    teams = active_teams or []

    if _has_worker_context(env):
        return QuestionPolicyDecision(
            allowed=False,
            session_id=session_id,
            code="worker_blocked",
            message=(
                "omx question is unavailable for OMX team workers; "
                "only non-team leader sessions may ask user questions."
            ),
            fallback_allowed=False,
        )

    if teams:
        summary = ", ".join(
            f"{t.get('teamName', '?')} ({t.get('phase', '?')})" for t in teams
        )
        return QuestionPolicyDecision(
            allowed=False,
            session_id=session_id,
            code="team_blocked",
            message=f"omx question is unavailable while this session owns active team mode: {summary}.",
            fallback_allowed=False,
            active_modes=modes,
            active_skills=skills,
            active_teams=teams,
        )

    blocked_modes = [m for m in modes if m in BLOCKED_EXECUTION_SKILLS]
    blocked_skills = [s for s in skills if s in BLOCKED_EXECUTION_SKILLS]
    blocked = list(dict.fromkeys(blocked_modes + blocked_skills))

    if blocked:
        return QuestionPolicyDecision(
            allowed=False,
            session_id=session_id,
            code="active_execution_mode_blocked",
            message=(
                "omx question is unavailable while auto-executing "
                f"workflows are active: {', '.join(blocked)}."
            ),
            fallback_allowed=False,
            active_modes=modes,
            active_skills=skills,
            active_teams=teams,
        )

    return QuestionPolicyDecision(
        allowed=True,
        fallback_allowed=True,
        session_id=session_id,
        active_modes=modes,
        active_skills=skills,
        active_teams=teams,
    )
