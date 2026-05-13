"""Ralph persistence — state and artifact management.

Port of src/ralph/persistence.ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.state.paths import get_state_dir
from omx.visual.constants import VISUAL_NEXT_ACTIONS_LIMIT

DEFAULT_VISUAL_THRESHOLD = 90
_VISUAL_FEEDBACK_RETENTION = 30


def _iso_now() -> str:
    """Return current UTC time as an ISO8601 string with millisecond precision.

    Matches the shape of TS ``new Date().toISOString()`` (e.g.
    ``2025-01-02T03:04:05.678Z``).
    """
    now = datetime.now(tz=timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _stable_json_pretty(value: Any) -> str:
    """Serialize ``value`` as pretty JSON with sorted keys (TS stableJsonPretty parity)."""
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Dataclasses (TS interfaces)
# ---------------------------------------------------------------------------


@dataclass
class RalphVisualFeedback:
    """Visual feedback payload recorded against a Ralph run.

    Attributes:
        score: Similarity score (0-100).
        verdict: Visual verdict status string ("pass" | "revise" | "fail").
        category_match: Whether the verdict categories matched.
        differences: Concrete differences flagged by the verdict.
        suggestions: Recommended next actions.
        reasoning: Optional human-readable rationale.
        threshold: Optional override for the passing threshold.
    """

    score: float
    verdict: str
    category_match: bool
    differences: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    reasoning: str | None = None
    threshold: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "score": self.score,
            "verdict": self.verdict,
            "category_match": self.category_match,
            "differences": list(self.differences),
            "suggestions": list(self.suggestions),
        }
        if self.reasoning is not None:
            d["reasoning"] = self.reasoning
        if self.threshold is not None:
            d["threshold"] = self.threshold
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RalphVisualFeedback:
        return cls(
            score=d["score"],
            verdict=d["verdict"],
            category_match=bool(d["category_match"]),
            differences=list(d.get("differences", [])),
            suggestions=list(d.get("suggestions", [])),
            reasoning=d.get("reasoning"),
            threshold=d.get("threshold"),
        )


@dataclass
class RalphProgressLedger:
    """Canonical Ralph progress ledger persisted to ``ralph-progress.json``.

    Attributes:
        schema_version: Ledger schema version (currently ``2``).
        entries: Progress entries (free-form dicts; usually migrated legacy lines).
        visual_feedback: Recorded visual-verdict entries, capped at the most
            recent :data:`_VISUAL_FEEDBACK_RETENTION` items.
        source: Optional source path the ledger was migrated from.
        source_sha256: SHA256 of the migration source content.
        strategy: Migration strategy tag (e.g. ``one-way-read-only``).
        created_at: ISO8601 creation timestamp.
        updated_at: ISO8601 last-update timestamp.
    """

    schema_version: int = 2
    entries: list[dict[str, Any]] = field(default_factory=list)
    visual_feedback: list[dict[str, Any]] = field(default_factory=list)
    source: str | None = None
    source_sha256: str | None = None
    strategy: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "entries": list(self.entries),
            "visual_feedback": list(self.visual_feedback),
        }
        for key in ("source", "source_sha256", "strategy", "created_at", "updated_at"):
            value = getattr(self, key)
            if value is not None:
                d[key] = value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RalphProgressLedger:
        schema_version = d.get("schema_version")
        if not isinstance(schema_version, int):
            schema_version = 2
        entries_raw = d.get("entries")
        entries = list(entries_raw) if isinstance(entries_raw, list) else []
        vf_raw = d.get("visual_feedback")
        visual_feedback = list(vf_raw) if isinstance(vf_raw, list) else []
        return cls(
            schema_version=schema_version,
            entries=entries,
            visual_feedback=visual_feedback,
            source=d.get("source"),
            source_sha256=d.get("source_sha256"),
            strategy=d.get("strategy"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
        )


@dataclass
class RalphCanonicalArtifacts:
    """Result of ensuring the canonical Ralph artifact set.

    Attributes:
        canonical_progress_path: Path to ``ralph-progress.json``.
        migrated_prd: True if a legacy PRD was migrated this call.
        migrated_progress: True if a legacy progress file was migrated this call.
        canonical_prd_path: Path to the canonical PRD markdown if any was found
            or migrated.
    """

    canonical_progress_path: str
    migrated_prd: bool = False
    migrated_progress: bool = False
    canonical_prd_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "canonical_progress_path": self.canonical_progress_path,
            "migrated_prd": self.migrated_prd,
            "migrated_progress": self.migrated_progress,
        }
        if self.canonical_prd_path is not None:
            d["canonical_prd_path"] = self.canonical_prd_path
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RalphCanonicalArtifacts:
        return cls(
            canonical_progress_path=d["canonical_progress_path"],
            migrated_prd=bool(d.get("migrated_prd", False)),
            migrated_progress=bool(d.get("migrated_progress", False)),
            canonical_prd_path=d.get("canonical_prd_path"),
        )


# ---------------------------------------------------------------------------
# Existing helpers (preserved)
# ---------------------------------------------------------------------------


def ensure_canonical_ralph_artifacts(cwd: str, session_id: str | None = None) -> None:
    """Ensure ralph artifact directories (plans/, evidence/, checkpoints/) exist."""
    state_dir = get_state_dir(cwd, session_id)
    ralph_dir = state_dir.parent / "ralph"
    ralph_dir.mkdir(parents=True, exist_ok=True)

    for subdir in ("plans", "evidence", "checkpoints"):
        (ralph_dir / subdir).mkdir(exist_ok=True)


def read_ralph_plan(cwd: str, session_id: str | None = None) -> str | None:
    """Read the current ralph plan."""
    state_dir = get_state_dir(cwd, session_id)
    plan_path = state_dir.parent / "ralph" / "plans" / "current.md"
    if plan_path.exists():
        return plan_path.read_text(encoding="utf-8")
    return None


def write_ralph_plan(cwd: str, content: str, session_id: str | None = None) -> None:
    """Write the current ralph plan."""
    ensure_canonical_ralph_artifacts(cwd, session_id)
    state_dir = get_state_dir(cwd, session_id)
    plan_path = state_dir.parent / "ralph" / "plans" / "current.md"
    plan_path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Canonical progress ledger IO
# ---------------------------------------------------------------------------


def _progress_path(cwd: str, session_id: str | None) -> Path:
    """Return the canonical ``ralph-progress.json`` path for ``cwd``/``session_id``."""
    return get_state_dir(cwd, session_id) / "ralph-progress.json"


def _ensure_progress_ledger_file(path: Path) -> None:
    """Create an empty progress ledger file at ``path`` if it does not exist."""
    if path.exists():
        return
    now = _iso_now()
    payload = RalphProgressLedger(
        schema_version=2,
        created_at=now,
        updated_at=now,
        entries=[],
        visual_feedback=[],
    ).to_dict()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_stable_json_pretty(payload) + "\n", encoding="utf-8")


def _read_progress_ledger(path: Path) -> RalphProgressLedger:
    """Read the canonical progress ledger at ``path``, repairing if missing/corrupt."""
    if not path.exists():
        _ensure_progress_ledger_file(path)
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("ledger root must be an object")
        ledger = RalphProgressLedger.from_dict(parsed)
    except (OSError, ValueError, json.JSONDecodeError):
        now = _iso_now()
        return RalphProgressLedger(
            schema_version=2,
            created_at=now,
            updated_at=now,
            entries=[],
            visual_feedback=[],
        )
    now = _iso_now()
    if not ledger.created_at:
        ledger.created_at = now
    ledger.updated_at = now
    return ledger


def record_ralph_visual_feedback(
    cwd: str,
    feedback: RalphVisualFeedback,
    session_id: str | None = None,
) -> None:
    """Record a visual verdict against the canonical Ralph progress ledger.

    Writes to ``<state_dir>/ralph-progress.json`` (TS:
    ``.omx/state/{scope}/ralph-progress.json``). The most recent
    :data:`_VISUAL_FEEDBACK_RETENTION` entries are retained.

    Args:
        cwd: Working directory containing the ``.omx/`` tree.
        feedback: Visual feedback payload to record.
        session_id: Optional session ID; when present, the ledger is scoped
            under ``.omx/state/sessions/<session_id>/``.
    """
    progress_path = _progress_path(cwd, session_id)
    ledger = _read_progress_ledger(progress_path)

    threshold_value = feedback.threshold
    if (
        threshold_value is None
        or not isinstance(threshold_value, (int, float))
        or threshold_value != threshold_value  # NaN check
        or threshold_value in (float("inf"), float("-inf"))
    ):
        threshold = DEFAULT_VISUAL_THRESHOLD
    else:
        threshold = float(threshold_value)

    suggestions = [s for s in feedback.suggestions if isinstance(s, str)]
    differences = [d for d in feedback.differences if isinstance(d, str)]
    raw_actions = [*suggestions, *(f"Resolve difference: {d}" for d in differences)]
    next_actions = [line.strip() for line in raw_actions if line and line.strip()]
    next_actions = next_actions[:VISUAL_NEXT_ACTIONS_LIMIT]

    reasoning = feedback.reasoning if feedback.reasoning is not None else ""
    entry: dict[str, Any] = {
        "recorded_at": _iso_now(),
        "score": feedback.score,
        "verdict": feedback.verdict,
        "category_match": feedback.category_match,
        "threshold": threshold,
        "passes_threshold": feedback.score >= threshold,
        "differences": list(differences),
        "suggestions": list(suggestions),
        "reasoning": reasoning,
        "next_actions": next_actions,
        "qualitative_feedback": {
            "summary": reasoning if reasoning else feedback.verdict,
            "next_actions": list(next_actions),
        },
    }

    visual_feedback = list(ledger.visual_feedback)
    visual_feedback.append(entry)
    ledger.visual_feedback = visual_feedback[-_VISUAL_FEEDBACK_RETENTION:]
    ledger.updated_at = _iso_now()

    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(
        _stable_json_pretty(ledger.to_dict()) + "\n",
        encoding="utf-8",
    )
