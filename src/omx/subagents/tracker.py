"""Subagent lifecycle tracking.

Port of src/subagents/tracker.ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SUBAGENT_TRACKING_SCHEMA_VERSION = 1
DEFAULT_SUBAGENT_ACTIVE_WINDOW_MS = 120_000


@dataclass
class TrackedSubagentThread:
    """A tracked subagent thread.

    Attributes:
        thread_id: Thread identifier.
        kind: Thread kind (leader or subagent).
        first_seen_at: ISO timestamp of first sighting.
        last_seen_at: ISO timestamp of last sighting.
        last_turn_id: Last turn identifier.
        turn_count: Number of turns.
        mode: Optional mode name.
    """

    thread_id: str = ""
    kind: str = "subagent"  # "leader" | "subagent"
    first_seen_at: str = ""
    last_seen_at: str = ""
    last_turn_id: str | None = None
    turn_count: int = 1
    mode: str | None = None


@dataclass
class TrackedSubagentSession:
    """A tracked subagent session.

    Attributes:
        session_id: Session identifier.
        leader_thread_id: Leader thread ID.
        updated_at: ISO timestamp of last update.
        threads: Threads in this session.
    """

    session_id: str = ""
    leader_thread_id: str | None = None
    updated_at: str = ""
    threads: dict[str, TrackedSubagentThread] = field(default_factory=dict)


@dataclass
class SubagentTrackingState:
    """Top-level subagent tracking state.

    Attributes:
        schema_version: Schema version number.
        sessions: Tracked sessions.
    """

    schema_version: int = SUBAGENT_TRACKING_SCHEMA_VERSION
    sessions: dict[str, TrackedSubagentSession] = field(default_factory=dict)


@dataclass
class RecordSubagentTurnInput:
    """Input for recording a subagent turn.

    Attributes:
        session_id: Session identifier.
        thread_id: Thread identifier.
        turn_id: Optional turn identifier.
        timestamp: Optional ISO timestamp.
        mode: Optional mode name.
    """

    session_id: str = ""
    thread_id: str = ""
    turn_id: str | None = None
    timestamp: str | None = None
    mode: str | None = None


@dataclass
class SubagentSessionSummary:
    """Summary of a subagent session.

    Attributes:
        session_id: Session identifier.
        leader_thread_id: Leader thread ID.
        all_thread_ids: All thread IDs.
        all_subagent_thread_ids: All subagent thread IDs.
        active_subagent_thread_ids: Currently active subagent threads.
        updated_at: Last update timestamp.
    """

    session_id: str = ""
    leader_thread_id: str | None = None
    all_thread_ids: list[str] = field(default_factory=list)
    all_subagent_thread_ids: list[str] = field(default_factory=list)
    active_subagent_thread_ids: list[str] = field(default_factory=list)
    updated_at: str | None = None


def subagent_tracking_path(cwd: str) -> Path:
    """Get the subagent tracking state file path.

    Args:
        cwd: Working directory.

    Returns:
        Path to the tracking state file.
    """
    return Path(cwd) / ".omx" / "state" / "subagent-tracking.json"


def create_subagent_tracking_state() -> SubagentTrackingState:
    """Create a fresh subagent tracking state.

    Returns:
        Empty SubagentTrackingState.
    """
    return SubagentTrackingState()


def normalize_subagent_tracking_state(data: Any) -> SubagentTrackingState:
    """Normalize raw data into a SubagentTrackingState.

    Args:
        data: Raw state data (dict or None).

    Returns:
        Normalized SubagentTrackingState.
    """
    if not isinstance(data, dict):
        return create_subagent_tracking_state()

    sessions: dict[str, TrackedSubagentSession] = {}
    epoch_iso = "1970-01-01T00:00:00.000Z"

    for session_id, raw_session in (data.get("sessions") or {}).items():
        if not isinstance(raw_session, dict):
            continue
        threads: dict[str, TrackedSubagentThread] = {}
        for thread_id, raw_thread in (raw_session.get("threads") or {}).items():
            if not isinstance(raw_thread, dict):
                continue
            normalized_tid = (
                raw_thread.get("thread_id", "").strip()
                if isinstance(raw_thread.get("thread_id"), str)
                and raw_thread["thread_id"].strip()
                else thread_id.strip()
            )
            if not normalized_tid:
                continue
            kind = "leader" if raw_thread.get("kind") == "leader" else "subagent"
            first_seen = (
                raw_thread["first_seen_at"]
                if isinstance(raw_thread.get("first_seen_at"), str)
                and raw_thread["first_seen_at"].strip()
                else (
                    raw_thread["last_seen_at"]
                    if isinstance(raw_thread.get("last_seen_at"), str)
                    and raw_thread["last_seen_at"].strip()
                    else epoch_iso
                )
            )
            last_seen = (
                raw_thread["last_seen_at"]
                if isinstance(raw_thread.get("last_seen_at"), str)
                and raw_thread["last_seen_at"].strip()
                else first_seen
            )
            turn_count = raw_thread.get("turn_count", 1)
            if not isinstance(turn_count, (int, float)) or turn_count <= 0:
                turn_count = 1
            last_turn_id = (
                raw_thread["last_turn_id"]
                if isinstance(raw_thread.get("last_turn_id"), str)
                and raw_thread["last_turn_id"].strip()
                else None
            )
            mode = (
                raw_thread["mode"]
                if isinstance(raw_thread.get("mode"), str)
                and raw_thread["mode"].strip()
                else None
            )
            threads[normalized_tid] = TrackedSubagentThread(
                thread_id=normalized_tid,
                kind=kind,
                first_seen_at=first_seen,
                last_seen_at=last_seen,
                last_turn_id=last_turn_id,
                turn_count=int(turn_count),
                mode=mode,
            )

        leader_tid = (
            raw_session["leader_thread_id"].strip()
            if isinstance(raw_session.get("leader_thread_id"), str)
            and raw_session["leader_thread_id"].strip()
            else None
        )
        updated_at = (
            raw_session["updated_at"]
            if isinstance(raw_session.get("updated_at"), str)
            and raw_session["updated_at"].strip()
            else epoch_iso
        )
        sessions[session_id] = TrackedSubagentSession(
            session_id=session_id,
            leader_thread_id=leader_tid,
            updated_at=updated_at,
            threads=threads,
        )

    return SubagentTrackingState(
        schema_version=SUBAGENT_TRACKING_SCHEMA_VERSION,
        sessions=sessions,
    )


def read_subagent_tracking_state(cwd: str) -> SubagentTrackingState:
    """Read subagent tracking state from disk.

    Args:
        cwd: Working directory.

    Returns:
        SubagentTrackingState (empty if file is missing or invalid).
    """
    path = subagent_tracking_path(cwd)
    if not path.exists():
        return create_subagent_tracking_state()
    try:
        return normalize_subagent_tracking_state(
            json.loads(path.read_text(encoding="utf-8"))
        )
    except (json.JSONDecodeError, OSError):
        return create_subagent_tracking_state()


def write_subagent_tracking_state(cwd: str, state: SubagentTrackingState) -> str:
    """Write subagent tracking state to disk.

    Args:
        cwd: Working directory.
        state: Tracking state to write.

    Returns:
        Path to the written file.
    """
    path = subagent_tracking_path(cwd)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Serialize via dict
    data = {
        "schemaVersion": state.schema_version,
        "sessions": {
            sid: {
                "session_id": s.session_id,
                "leader_thread_id": s.leader_thread_id,
                "updated_at": s.updated_at,
                "threads": {
                    tid: {
                        "thread_id": t.thread_id,
                        "kind": t.kind,
                        "first_seen_at": t.first_seen_at,
                        "last_seen_at": t.last_seen_at,
                        **({"last_turn_id": t.last_turn_id} if t.last_turn_id else {}),
                        "turn_count": t.turn_count,
                        **({"mode": t.mode} if t.mode else {}),
                    }
                    for tid, t in s.threads.items()
                },
            }
            for sid, s in state.sessions.items()
        },
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return str(path)


def record_subagent_turn(
    state: SubagentTrackingState,
    input_data: RecordSubagentTurnInput,
) -> SubagentTrackingState:
    """Record a subagent turn in tracking state.

    Args:
        state: Current tracking state.
        input_data: Turn input data.

    Returns:
        Updated tracking state.
    """
    session_id = input_data.session_id.strip()
    thread_id = input_data.thread_id.strip()
    if not session_id or not thread_id:
        return normalize_subagent_tracking_state(
            {
                "sessions": {
                    sid: {
                        "session_id": s.session_id,
                        "leader_thread_id": s.leader_thread_id,
                        "updated_at": s.updated_at,
                        "threads": {
                            tid: {
                                "thread_id": t.thread_id,
                                "kind": t.kind,
                                "first_seen_at": t.first_seen_at,
                                "last_seen_at": t.last_seen_at,
                                "turn_count": t.turn_count,
                            }
                            for tid, t in s.threads.items()
                        },
                    }
                    for sid, s in state.sessions.items()
                }
            }
        )

    timestamp = input_data.timestamp or datetime.now(timezone.utc).isoformat()
    normalized = normalize_subagent_tracking_state(
        {
            "sessions": {
                sid: {
                    "session_id": s.session_id,
                    "leader_thread_id": s.leader_thread_id,
                    "updated_at": s.updated_at,
                    "threads": {
                        tid: {
                            "thread_id": t.thread_id,
                            "kind": t.kind,
                            "first_seen_at": t.first_seen_at,
                            "last_seen_at": t.last_seen_at,
                            "last_turn_id": t.last_turn_id,
                            "turn_count": t.turn_count,
                            "mode": t.mode,
                        }
                        for tid, t in s.threads.items()
                    },
                }
                for sid, s in state.sessions.items()
            }
        }
    )

    existing_session = normalized.sessions.get(session_id)
    if not existing_session:
        existing_session = TrackedSubagentSession(
            session_id=session_id,
            updated_at=timestamp,
            threads={},
        )

    leader_tid = existing_session.leader_thread_id or thread_id
    existing_thread = existing_session.threads.get(thread_id)
    new_thread = TrackedSubagentThread(
        thread_id=thread_id,
        kind="leader" if thread_id == leader_tid else "subagent",
        first_seen_at=existing_thread.first_seen_at if existing_thread else timestamp,
        last_seen_at=timestamp,
        turn_count=(existing_thread.turn_count if existing_thread else 0) + 1,
        last_turn_id=(
            input_data.turn_id.strip()
            if input_data.turn_id and input_data.turn_id.strip()
            else (existing_thread.last_turn_id if existing_thread else None)
        ),
        mode=(
            input_data.mode.strip()
            if input_data.mode and input_data.mode.strip()
            else (existing_thread.mode if existing_thread else None)
        ),
    )

    threads = dict(existing_session.threads)
    threads[thread_id] = new_thread
    if leader_tid and thread_id != leader_tid and leader_tid in threads:
        leader = threads[leader_tid]
        threads[leader_tid] = TrackedSubagentThread(
            thread_id=leader.thread_id,
            kind="leader",
            first_seen_at=leader.first_seen_at,
            last_seen_at=leader.last_seen_at,
            last_turn_id=leader.last_turn_id,
            turn_count=leader.turn_count,
            mode=leader.mode,
        )

    normalized.sessions[session_id] = TrackedSubagentSession(
        session_id=session_id,
        leader_thread_id=leader_tid,
        updated_at=timestamp,
        threads=threads,
    )
    return normalized


def summarize_subagent_session(
    state: SubagentTrackingState,
    session_id: str,
    *,
    now: str | datetime | None = None,
    active_window_ms: int | None = None,
) -> SubagentSessionSummary | None:
    """Summarize a subagent session.

    Args:
        state: Tracking state.
        session_id: Session to summarize.
        now: Current time for activity calculation.
        active_window_ms: Activity window in milliseconds.

    Returns:
        SubagentSessionSummary or None if session not found.
    """
    session = state.sessions.get(session_id)
    if not session:
        return None

    window_ms = active_window_ms or DEFAULT_SUBAGENT_ACTIVE_WINDOW_MS
    if isinstance(now, str):
        from datetime import datetime as dt

        now_ms = int(dt.fromisoformat(now.replace("Z", "+00:00")).timestamp() * 1000)
    elif isinstance(now, datetime):
        now_ms = int(now.timestamp() * 1000)
    else:
        import time

        now_ms = int(time.time() * 1000)

    all_thread_ids = sorted(session.threads.keys())
    all_subagent_ids = [
        tid for tid in all_thread_ids if session.threads[tid].kind == "subagent"
    ]

    active_subagent_ids: list[str] = []
    for tid in all_subagent_ids:
        thread = session.threads[tid]
        try:
            from datetime import datetime as dt

            seen_ms = int(
                dt.fromisoformat(thread.last_seen_at.replace("Z", "+00:00")).timestamp()
                * 1000
            )
            if now_ms - seen_ms <= window_ms:
                active_subagent_ids.append(tid)
        except (ValueError, AttributeError):
            pass

    return SubagentSessionSummary(
        session_id=session_id,
        leader_thread_id=session.leader_thread_id,
        all_thread_ids=all_thread_ids,
        all_subagent_thread_ids=all_subagent_ids,
        active_subagent_thread_ids=active_subagent_ids,
        updated_at=session.updated_at,
    )
