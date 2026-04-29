"""Deep interview workflow for extracting detailed requirements.

Port of src/question/deep-interview.ts. Manages question obligation
lifecycle and enforcement state for deep-interview sessions.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from omx.question.client import OmxQuestionSuccessPayload, run_omx_question
from omx.state.paths import get_state_file_path

DEEP_INTERVIEW_STATE_FILE = "deep-interview-state.json"


@dataclass
class DeepInterviewQuestionEnforcement:
    """Enforcement state for a deep-interview question obligation.

    Attributes:
        obligation_id: Unique obligation identifier.
        source: Always 'omx-question'.
        status: Lifecycle status (pending/satisfied/cleared).
        lifecycle_outcome: Terminal lifecycle outcome type.
        requested_at: ISO timestamp when the obligation was created.
        question_id: Question that satisfied the obligation.
        satisfied_at: ISO timestamp when satisfied.
        cleared_at: ISO timestamp when cleared.
        clear_reason: Reason the obligation was cleared.
    """

    obligation_id: str = ""
    source: str = "omx-question"
    status: str = "pending"
    lifecycle_outcome: str = "askuserQuestion"
    requested_at: str = ""
    question_id: str | None = None
    satisfied_at: str | None = None
    cleared_at: str | None = None
    clear_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        d: dict[str, Any] = {
            "obligation_id": self.obligation_id,
            "source": self.source,
            "status": self.status,
            "lifecycle_outcome": self.lifecycle_outcome,
            "requested_at": self.requested_at,
        }
        if self.question_id is not None:
            d["question_id"] = self.question_id
        if self.satisfied_at is not None:
            d["satisfied_at"] = self.satisfied_at
        if self.cleared_at is not None:
            d["cleared_at"] = self.cleared_at
        if self.clear_reason is not None:
            d["clear_reason"] = self.clear_reason
        return d

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> DeepInterviewQuestionEnforcement:
        """Deserialise from dict."""
        return DeepInterviewQuestionEnforcement(
            obligation_id=raw.get("obligation_id", ""),
            source=raw.get("source", "omx-question"),
            status=raw.get("status", "pending"),
            lifecycle_outcome=raw.get("lifecycle_outcome", "askuserQuestion"),
            requested_at=raw.get("requested_at", ""),
            question_id=raw.get("question_id"),
            satisfied_at=raw.get("satisfied_at"),
            cleared_at=raw.get("cleared_at"),
            clear_reason=raw.get("clear_reason"),
        )


def _safe_string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _parse_timestamp_ms(value: Any) -> int | None:
    """Parse an ISO timestamp to epoch milliseconds."""
    raw = _safe_string(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except (ValueError, OSError):
        return None


def _build_obligation_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    iso = now.isoformat().replace(":", "-").replace(".", "-")
    rand = f"{random.getrandbits(32):08x}"
    return f"deep-interview-question-{iso}-{rand}"


def _read_deep_interview_state(
    cwd: str, session_id: str | None = None
) -> dict[str, Any] | None:
    """Read deep-interview state from disk."""
    state_path = get_state_file_path(DEEP_INTERVIEW_STATE_FILE, cwd, session_id)
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_deep_interview_state(
    cwd: str,
    state: dict[str, Any],
    session_id: str | None = None,
) -> None:
    """Write deep-interview state to disk."""
    state_path = get_state_file_path(DEEP_INTERVIEW_STATE_FILE, cwd, session_id)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def create_deep_interview_question_obligation(
    now: datetime | None = None,
) -> DeepInterviewQuestionEnforcement:
    """Create a new pending question obligation.

    Args:
        now: Optional timestamp override.

    Returns:
        A new DeepInterviewQuestionEnforcement in pending state.
    """
    now = now or datetime.now(timezone.utc)
    return DeepInterviewQuestionEnforcement(
        obligation_id=_build_obligation_id(now),
        source="omx-question",
        status="pending",
        lifecycle_outcome="askuserQuestion",
        requested_at=now.isoformat(),
    )


def is_pending_deep_interview_question_enforcement(
    enforcement: dict[str, Any] | None,
) -> bool:
    """Check whether an enforcement state represents a pending obligation.

    Args:
        enforcement: Enforcement dict or None.

    Returns:
        True if the enforcement is pending.
    """
    if not enforcement or not isinstance(enforcement, dict):
        return False
    return (
        _safe_string(enforcement.get("obligation_id", "")).strip() != ""
        and _safe_string(enforcement.get("status", "")).strip().lower() == "pending"
    )


def satisfy_deep_interview_question_obligation(
    enforcement: DeepInterviewQuestionEnforcement,
    question_id: str,
    now: datetime | None = None,
) -> DeepInterviewQuestionEnforcement:
    """Mark an obligation as satisfied by a question answer.

    Args:
        enforcement: The current enforcement state.
        question_id: Question that satisfied it.
        now: Optional timestamp override.

    Returns:
        Updated enforcement in satisfied state.
    """
    now = now or datetime.now(timezone.utc)
    enforcement.status = "satisfied"
    enforcement.question_id = question_id
    enforcement.satisfied_at = now.isoformat()
    enforcement.cleared_at = None
    enforcement.clear_reason = None
    return enforcement


def clear_deep_interview_question_obligation(
    enforcement: DeepInterviewQuestionEnforcement | None,
    reason: str,
    now: datetime | None = None,
) -> DeepInterviewQuestionEnforcement | None:
    """Clear a pending obligation with a reason.

    Args:
        enforcement: The current enforcement state (or None).
        reason: Clear reason ('handoff', 'abort', or 'error').
        now: Optional timestamp override.

    Returns:
        Updated enforcement in cleared state, or None.
    """
    if enforcement is None:
        return None
    if enforcement.status != "pending":
        return enforcement
    now = now or datetime.now(timezone.utc)
    enforcement.status = "cleared"
    enforcement.cleared_at = now.isoformat()
    enforcement.clear_reason = reason
    return enforcement


def update_deep_interview_question_enforcement(
    cwd: str,
    session_id: str | None,
    updater: Any,
) -> dict[str, Any] | None:
    """Update the question enforcement in the deep-interview state file.

    Args:
        cwd: Working directory.
        session_id: Session scope.
        updater: Callable taking current enforcement and returning updated.

    Returns:
        Updated state dict, or None if state file doesn't exist.
    """
    normalised_session = _safe_string(session_id).strip() or None
    state = _read_deep_interview_state(cwd, normalised_session)
    if state is None:
        return None

    current_raw = state.get("question_enforcement")
    current = (
        DeepInterviewQuestionEnforcement.from_dict(current_raw)
        if current_raw and isinstance(current_raw, dict)
        else None
    )

    next_enforcement = updater(current)
    now_iso = datetime.now(timezone.utc).isoformat()
    state["updated_at"] = now_iso

    if next_enforcement is not None:
        state["question_enforcement"] = (
            next_enforcement.to_dict()
            if isinstance(next_enforcement, DeepInterviewQuestionEnforcement)
            else next_enforcement
        )
        if (
            isinstance(next_enforcement, DeepInterviewQuestionEnforcement)
            and next_enforcement.status == "pending"
        ):
            state["lifecycle_outcome"] = "askuserQuestion"
            state["run_outcome"] = "blocked_on_user"
            state["active"] = False
            state.setdefault("completed_at", now_iso)
    else:
        state.pop("question_enforcement", None)

    if next_enforcement is None or (
        isinstance(next_enforcement, DeepInterviewQuestionEnforcement)
        and next_enforcement.status != "pending"
    ):
        state.pop("lifecycle_outcome", None)
        state.pop("run_outcome", None)

    _write_deep_interview_state(cwd, state, normalised_session)
    return state


def run_deep_interview_question(
    question_input: dict[str, Any],
    *,
    cwd: str | None = None,
) -> OmxQuestionSuccessPayload:
    """Run a deep-interview question with obligation tracking.

    Creates a pending obligation, runs the question, and satisfies
    or clears the obligation based on the outcome.

    Args:
        question_input: Question input dict (must contain 'question').
        cwd: Working directory.

    Returns:
        OmxQuestionSuccessPayload with the answer.

    Raises:
        OmxQuestionError: If the question subprocess fails.
    """
    import os

    effective_cwd = cwd or os.getcwd()
    session_id = (
        _safe_string(question_input.get("session_id", "")).strip()
        or os.environ.get("OMX_SESSION_ID", "").strip()
        or None
    )
    obligation = create_deep_interview_question_obligation()

    update_deep_interview_question_enforcement(
        effective_cwd,
        session_id,
        lambda _: obligation,
    )

    try:
        enriched_input = {
            **question_input,
            "source": question_input.get("source", "deep-interview"),
        }
        if session_id:
            enriched_input["session_id"] = session_id

        result = run_omx_question(enriched_input, cwd=effective_cwd)

        update_deep_interview_question_enforcement(
            effective_cwd,
            session_id,
            lambda current: (
                satisfy_deep_interview_question_obligation(current, result.question_id)
                if current and current.obligation_id == obligation.obligation_id
                else current
            ),
        )
        return result

    except Exception:
        update_deep_interview_question_enforcement(
            effective_cwd,
            session_id,
            lambda current: (
                clear_deep_interview_question_obligation(current, "error")
                if current and current.obligation_id == obligation.obligation_id
                else current
            ),
        )
        raise
