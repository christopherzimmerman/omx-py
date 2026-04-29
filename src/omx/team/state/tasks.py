"""Team task state management — claim, transition, release.

Port of src/team/state/tasks.ts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from omx.team.state.types import (
    TeamTaskClaim,
    TeamTaskStatus,
    can_transition_task_status,
    is_terminal_task_status,
)

CLAIM_LEASE_MINUTES = 15


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _lease_until() -> str:
    return (
        datetime.now(timezone.utc) + timedelta(minutes=CLAIM_LEASE_MINUTES)
    ).isoformat()


def _is_claim_expired(claim: TeamTaskClaim) -> bool:
    """Check if a task claim lease has expired."""
    if not claim.leased_until:
        return True
    try:
        expiry = datetime.fromisoformat(claim.leased_until.replace("Z", "+00:00"))
        return datetime.now(timezone.utc) > expiry
    except (ValueError, TypeError):
        return True


def claim_task(
    task_data: dict[str, Any],
    worker_name: str,
) -> dict[str, Any]:
    """Claim a task for a worker, acquiring a lease token.

    Args:
        task_data: The task dict to claim.
        worker_name: Name of the claiming worker.

    Returns:
        Dict with "ok", "task", "claim_token", and optional "error".
    """
    status = task_data.get("status", "pending")

    if is_terminal_task_status(status):
        return {"ok": False, "error": f"task is already terminal: {status}"}

    existing_claim = task_data.get("claim")
    if existing_claim and isinstance(existing_claim, dict):
        claim = TeamTaskClaim.from_dict(existing_claim)
        if claim.owner and claim.owner != worker_name and not _is_claim_expired(claim):
            return {"ok": False, "error": f"task claimed by {claim.owner}"}

    token = uuid.uuid4().hex[:16]
    task_data["status"] = TeamTaskStatus.IN_PROGRESS
    task_data["claim"] = TeamTaskClaim(
        owner=worker_name,
        token=token,
        leased_until=_lease_until(),
    ).to_dict()
    task_data["owner"] = worker_name

    return {"ok": True, "task": task_data, "claim_token": token}


def transition_task_status(
    task_data: dict[str, Any],
    from_status: str,
    to_status: str,
    claim_token: str,
    terminal_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transition a task's status, validating claim ownership.

    Args:
        task_data: The task dict.
        from_status: Expected current status.
        to_status: Target status.
        claim_token: Claim token for authorization.
        terminal_data: Optional result/error data for terminal states.

    Returns:
        Dict with "ok", "task", and optional "error".
    """
    current = task_data.get("status", "pending")
    if current != from_status:
        return {
            "ok": False,
            "error": f"status mismatch: expected {from_status}, got {current}",
        }

    if not can_transition_task_status(from_status, to_status):
        return {
            "ok": False,
            "error": f"invalid transition: {from_status} -> {to_status}",
        }

    # Validate claim token
    existing_claim = task_data.get("claim")
    if existing_claim and isinstance(existing_claim, dict):
        if existing_claim.get("token") != claim_token:
            return {"ok": False, "error": "claim token mismatch"}

    task_data["status"] = to_status

    if is_terminal_task_status(to_status):
        task_data["completed_at"] = _now_iso()
        if terminal_data:
            if "result" in terminal_data:
                task_data["result"] = terminal_data["result"]
            if "error" in terminal_data:
                task_data["error"] = terminal_data["error"]

    return {"ok": True, "task": task_data}


def release_task_claim(
    task_data: dict[str, Any],
    claim_token: str,
) -> dict[str, Any]:
    """Release a task claim, returning it to pending.

    Args:
        task_data: The task dict.
        claim_token: Claim token for authorization.

    Returns:
        Dict with "ok", "task", and optional "error".
    """
    existing_claim = task_data.get("claim")
    if existing_claim and isinstance(existing_claim, dict):
        if existing_claim.get("token") != claim_token:
            return {"ok": False, "error": "claim token mismatch"}

    task_data["status"] = TeamTaskStatus.PENDING
    task_data["claim"] = None
    task_data["owner"] = None
    return {"ok": True, "task": task_data}


def reclaim_expired_task(task_data: dict[str, Any]) -> dict[str, Any]:
    """Reclaim a task with an expired lease, returning it to pending.

    Args:
        task_data: The task dict.

    Returns:
        Dict with "ok", "task", "reclaimed", and optional "error".
    """
    existing_claim = task_data.get("claim")
    if not existing_claim or not isinstance(existing_claim, dict):
        return {"ok": True, "task": task_data, "reclaimed": False}

    claim = TeamTaskClaim.from_dict(existing_claim)
    if not _is_claim_expired(claim):
        return {"ok": True, "task": task_data, "reclaimed": False}

    task_data["status"] = TeamTaskStatus.PENDING
    task_data["claim"] = None
    task_data["owner"] = None
    return {"ok": True, "task": task_data, "reclaimed": True}
