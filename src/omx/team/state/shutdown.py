"""Worker shutdown request / ack handshake.

Port of ``writeShutdownRequest`` and ``readShutdownAck`` plus the
``ShutdownAck`` interface from ``src/team/state.ts`` (oh-my-codex TypeScript).

On-disk layout (preserved from TS, rooted at the existing Python
``.omx/team/`` location rather than the TS ``.omx/state/team/``):

  .omx/team/{team_name}/workers/{worker_name}/shutdown-request.json
  .omx/team/{team_name}/workers/{worker_name}/shutdown-ack.json

The request file is written by the leader / orchestrator. The ack file is
written by the worker. Files are intentionally separate so the two writers
never need to share a lock — the request side just appends a new record, and
the ack reader either sees a fully-written ack or no ack at all.

This module is synchronous and stdlib-only per the omx-py porting contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.team.state.atomic import write_atomic
from omx.team.state_root import team_dir as _team_dir_path

__all__ = [
    "ShutdownAck",
    "write_shutdown_request",
    "read_shutdown_ack",
]


# Valid ``status`` values for ``ShutdownAck`` — mirrors the TS union.
_ACK_STATUSES = frozenset({"accept", "reject"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _worker_dir(team_name: str, worker_name: str, cwd: str) -> Path:
    return _team_dir_path(team_name, cwd) / "workers" / worker_name


def _shutdown_request_path(team_name: str, worker_name: str, cwd: str) -> Path:
    return _worker_dir(team_name, worker_name, cwd) / "shutdown-request.json"


def _shutdown_ack_path(team_name: str, worker_name: str, cwd: str) -> Path:
    return _worker_dir(team_name, worker_name, cwd) / "shutdown-ack.json"


# === ShutdownAck dataclass ===


@dataclass
class ShutdownAck:
    """Worker's response to a shutdown request.

    Port of the TS ``ShutdownAck`` interface. ``status`` is one of
    ``accept`` / ``reject``. ``reason`` and ``updated_at`` are optional in the
    TS schema and are preserved as optional fields here.
    """

    status: str = "accept"  # one of "accept" | "reject"
    reason: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"status": self.status}
        if self.reason is not None:
            d["reason"] = self.reason
        if self.updated_at is not None:
            d["updated_at"] = self.updated_at
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShutdownAck | None:
        """Parse a raw dict into a ShutdownAck or return None if invalid.

        Mirrors the TS reader's "unknown status → null" gate.
        """
        if not isinstance(d, dict):
            return None
        status = d.get("status")
        if status not in _ACK_STATUSES:
            return None
        reason = d.get("reason") if isinstance(d.get("reason"), str) else None
        updated_at = (
            d.get("updated_at") if isinstance(d.get("updated_at"), str) else None
        )
        return cls(status=status, reason=reason, updated_at=updated_at)


# === Public API ===


def write_shutdown_request(
    team_name: str,
    worker_name: str,
    cwd: str,
    requested_by: str = "",
    requested_at: str | None = None,
) -> None:
    """Atomically write a worker shutdown request.

    Port of TS ``writeShutdownRequest``. The request payload preserves the
    TS-side field names ``requested_at`` and ``requested_by`` so existing
    on-disk records remain forward-compatible.

    Args:
        team_name: Team name.
        worker_name: Target worker to shut down.
        cwd: Working directory whose ``.omx/team/`` tree owns the team.
        requested_by: Identifier of the requester (leader session id, role,
            or human-readable label). Stored verbatim; empty string is allowed.
        requested_at: Optional ISO 8601 timestamp; defaults to ``utcnow()``.
    """
    if requested_at is None or requested_at.strip() == "":
        requested_at = _now_iso()
    payload = {
        "requested_at": requested_at,
        "requested_by": requested_by,
    }
    write_atomic(
        _shutdown_request_path(team_name, worker_name, cwd),
        json.dumps(payload, indent=2),
    )


def read_shutdown_ack(
    team_name: str,
    worker_name: str,
    cwd: str,
    min_updated_at: str | None = None,
) -> ShutdownAck | None:
    """Read a worker's shutdown ack, optionally requiring a minimum timestamp.

    Port of TS ``readShutdownAck``. Returns ``None`` when:
      * the ack file does not exist,
      * the ack file cannot be parsed,
      * the ``status`` is not one of ``accept`` / ``reject``,
      * a ``min_updated_at`` was supplied and the on-disk ``updated_at`` is
        either missing, unparseable, or older than the threshold.

    The threshold check uses ISO-8601 lexicographic comparison after a sanity
    parse via ``datetime.fromisoformat``. This is intentionally strict and
    mirrors the TS ``Date.parse`` gate.
    """
    path = _shutdown_ack_path(team_name, worker_name, cwd)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    ack = ShutdownAck.from_dict(raw)
    if ack is None:
        return None

    if isinstance(min_updated_at, str) and min_updated_at.strip() != "":
        min_dt = _parse_iso_or_none(min_updated_at)
        ack_dt = _parse_iso_or_none(ack.updated_at or "")
        if min_dt is None or ack_dt is None or ack_dt < min_dt:
            return None

    return ack


def _parse_iso_or_none(value: str) -> datetime | None:
    """Lenient ISO 8601 parser — accept "Z" suffix and naive timestamps.

    Returns ``None`` for the empty string or any parse failure. The TS side
    uses ``Date.parse`` which is permissive; we mirror that by normalizing
    the common trailing ``Z`` to ``+00:00`` before calling
    ``datetime.fromisoformat``.
    """
    if not value or not value.strip():
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None
