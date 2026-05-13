"""Team dispatch request management.

Port of src/team/state/dispatch.ts.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.team.state.types import TeamDispatchRequest


DEFAULT_DISPATCH_LOCK_TIMEOUT_MS = 15_000
MIN_DISPATCH_LOCK_TIMEOUT_MS = 1_000
MAX_DISPATCH_LOCK_TIMEOUT_MS = 60_000


def resolve_dispatch_lock_timeout_ms(env: dict[str, str] | None = None) -> int:
    """Resolve the dispatch lock timeout from env, clamped to allowed bounds.

    TS source: state.ts::resolveDispatchLockTimeoutMs.
    """
    raw = (
        (env if env is not None else os.environ)
        .get("OMX_TEAM_DISPATCH_LOCK_TIMEOUT_MS", "")
        .strip()
    )
    if not raw:
        return DEFAULT_DISPATCH_LOCK_TIMEOUT_MS
    try:
        val = int(raw)
    except ValueError:
        return DEFAULT_DISPATCH_LOCK_TIMEOUT_MS
    return max(MIN_DISPATCH_LOCK_TIMEOUT_MS, min(val, MAX_DISPATCH_LOCK_TIMEOUT_MS))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


VALID_DISPATCH_STATUSES = {"pending", "notified", "delivered", "failed"}
VALID_DISPATCH_TRANSITIONS = {
    "pending": {"pending", "notified", "delivered", "failed"},
    "notified": {"delivered", "failed"},
}


def can_transition_dispatch_status(from_status: str, to_status: str) -> bool:
    """Check if a dispatch status transition is valid."""
    return to_status in VALID_DISPATCH_TRANSITIONS.get(from_status, set())


def normalize_dispatch_request(
    team_name: str,
    raw: dict[str, Any],
) -> TeamDispatchRequest | None:
    """Normalize and validate a raw dispatch request input.

    Args:
        team_name: The team name.
        raw: Raw dispatch request data.

    Returns:
        Normalized TeamDispatchRequest, or None if invalid.
    """
    kind = raw.get("kind", "inbox")
    if kind not in ("inbox", "mailbox", "nudge"):
        return None

    to_worker = raw.get("to_worker", "")
    if not to_worker:
        return None

    now = _now_iso()
    return TeamDispatchRequest(
        request_id=raw.get("request_id") or uuid.uuid4().hex[:16],
        kind=kind,
        team_name=team_name,
        to_worker=to_worker,
        worker_index=raw.get("worker_index"),
        pane_id=raw.get("pane_id"),
        trigger_message=raw.get("trigger_message", ""),
        message_id=raw.get("message_id"),
        inbox_correlation_key=raw.get("inbox_correlation_key"),
        transport_preference=raw.get("transport_preference", "transport_direct"),
        fallback_allowed=raw.get("fallback_allowed", True),
        status="pending",
        attempt_count=0,
        created_at=now,
        updated_at=now,
    )


def read_dispatch_requests(team_dir: Path) -> list[TeamDispatchRequest]:
    """Read dispatch requests from disk."""
    path = team_dir / "dispatch" / "requests.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return [TeamDispatchRequest.from_dict(r) for r in data.get("requests", [])]
    except (json.JSONDecodeError, OSError):
        return []


def write_dispatch_requests(
    team_dir: Path, requests: list[TeamDispatchRequest]
) -> None:
    """Write dispatch requests to disk."""
    path = team_dir / "dispatch" / "requests.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"requests": [r.to_dict() for r in requests]}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def enqueue_dispatch_request(
    team_dir: Path,
    team_name: str,
    request_input: dict[str, Any],
) -> TeamDispatchRequest | None:
    """Enqueue a new dispatch request, deduplicating against pending ones.

    Args:
        team_dir: Path to team state directory.
        team_name: Team name.
        request_input: Raw request input data.

    Returns:
        The enqueued request, or None if invalid or duplicate.
    """
    normalized = normalize_dispatch_request(team_name, request_input)
    if normalized is None:
        return None

    requests = read_dispatch_requests(team_dir)

    # Deduplicate: check for equivalent pending request
    for existing in requests:
        if existing.status != "pending":
            continue
        if (
            existing.kind == normalized.kind
            and existing.to_worker == normalized.to_worker
            and existing.message_id == normalized.message_id
            and existing.inbox_correlation_key == normalized.inbox_correlation_key
        ):
            return existing  # Already queued

    requests.append(normalized)
    write_dispatch_requests(team_dir, requests)
    return normalized


def read_dispatch_request(
    team_dir: Path, request_id: str
) -> TeamDispatchRequest | None:
    """Read a single dispatch request by id; returns None if absent."""
    for req in read_dispatch_requests(team_dir):
        if req.request_id == request_id:
            return req
    return None


def mark_dispatch_request_notified(
    team_dir: Path, request_id: str, reason: str | None = None
) -> bool:
    """Mark a dispatch request as notified (TS parity)."""
    return transition_dispatch_request(team_dir, request_id, "notified", reason=reason)


def mark_dispatch_request_delivered(
    team_dir: Path, request_id: str, reason: str | None = None
) -> bool:
    """Mark a dispatch request as delivered (TS parity)."""
    return transition_dispatch_request(team_dir, request_id, "delivered", reason=reason)


def transition_dispatch_request(
    team_dir: Path,
    request_id: str,
    to_status: str,
    reason: str | None = None,
) -> bool:
    """Transition a dispatch request's status.

    Args:
        team_dir: Path to team state directory.
        request_id: ID of the request to transition.
        to_status: Target status.
        reason: Optional reason for the transition.

    Returns:
        True if the transition was applied.
    """
    requests = read_dispatch_requests(team_dir)
    now = _now_iso()

    for req in requests:
        if req.request_id != request_id:
            continue
        if not can_transition_dispatch_status(req.status, to_status):
            return False
        req.status = to_status
        req.updated_at = now
        req.attempt_count += 1
        if reason:
            req.last_reason = reason
        match to_status:
            case "notified":
                req.notified_at = now
            case "delivered":
                req.delivered_at = now
            case "failed":
                req.failed_at = now
        write_dispatch_requests(team_dir, requests)
        return True

    return False
