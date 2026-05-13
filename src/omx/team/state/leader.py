"""Team leader identity and leader-attention state.

Port of the `TeamLeader` interface plus the `leader-attention.json` family from
``src/team/state.ts`` (oh-my-codex TypeScript): ``readTeamLeaderAttention``,
``writeTeamLeaderAttention``, ``markTeamLeaderSessionStopped`` and
``markOwnedTeamsLeaderSessionStopped``.

On-disk layout (preserved from TS, but rooted at the existing Python
``.omx/team/`` location rather than the TS ``.omx/state/team/`` — see
``state_root.team_dir``):

  .omx/team/{team_name}/leader-attention.json

The attention record drives leader-attention HUD/notification surfaces. It is
written when the leader session stops (native Stop hook, native session-end
hook, or explicit notify) and read by monitor / HUD code paths.

This module is intentionally **synchronous** and **stdlib-only** per the
omx-py porting contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.team.state.atomic import write_atomic
from omx.team.state.io import read_tasks
from omx.team.state.mailbox import read_mailbox
from omx.team.state.manifest import TeamLeader as TeamLeader
from omx.team.state.monitor import read_monitor_snapshot
from omx.team.state_root import team_dir as _team_dir_path

# Re-export ``TeamLeader`` here for callers that conceptually associate it
# with the leader-attention surface. The canonical definition lives in
# ``team.state.manifest`` (since the manifest is the only persistent home for
# leader identity); this alias keeps callers importing from a single,
# discoverable module.

__all__ = [
    "TeamLeader",
    "TeamLeaderAttentionState",
    "LEADER_DECISION_STATES",
    "LEADER_ATTENTION_SOURCES",
    "TERMINAL_PHASES",
    "read_team_leader_attention",
    "write_team_leader_attention",
    "mark_team_leader_session_stopped",
    "mark_team_leader_stop_observed",
    "mark_owned_teams_leader_session_stopped",
    "mark_owned_teams_leader_stop_observed",
]


# Valid `leader_decision_state` values, mirroring the TS
# `TeamLeaderDecisionState` union.
LEADER_DECISION_STATES = frozenset(
    {"still_actionable", "done_waiting_on_leader", "stuck_waiting_on_leader"}
)

# Valid `source` values, mirroring the TS `TeamLeaderAttentionState['source']`
# union.
LEADER_ATTENTION_SOURCES = frozenset(
    {"notify_hook", "native_stop", "native_session_end"}
)

# Terminal team-phase markers — copied from `team/orchestrator.ts`
# `TERMINAL_PHASES`. Kept inline so this module does not depend on a phase
# helper that may not have been ported yet.
TERMINAL_PHASES = frozenset({"complete", "failed", "cancelled"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# === Dataclasses ===


@dataclass
class TeamLeaderAttentionState:
    """Persisted record of "does the leader need attention right now?".

    Port of the TS ``TeamLeaderAttentionState`` interface. The schema is the
    contract between the writer (Stop / session-end hooks, notify path) and
    the reader (monitor, HUD, dispatch).
    """

    team_name: str = ""
    updated_at: str = ""
    source: str = "notify_hook"  # one of LEADER_ATTENTION_SOURCES
    leader_decision_state: str = "still_actionable"  # one of LEADER_DECISION_STATES
    leader_attention_pending: bool = False
    leader_attention_reason: str | None = None
    attention_reasons: list[str] = field(default_factory=list)
    leader_stale: bool = False
    leader_session_active: bool = True
    leader_session_id: str | None = None
    leader_session_stopped_at: str | None = None
    unread_leader_message_count: int = 0
    work_remaining: bool = False
    stalled_for_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_name": self.team_name,
            "updated_at": self.updated_at,
            "source": self.source,
            "leader_decision_state": self.leader_decision_state,
            "leader_attention_pending": self.leader_attention_pending,
            "leader_attention_reason": self.leader_attention_reason,
            "attention_reasons": list(self.attention_reasons),
            "leader_stale": self.leader_stale,
            "leader_session_active": self.leader_session_active,
            "leader_session_id": self.leader_session_id,
            "leader_session_stopped_at": self.leader_session_stopped_at,
            "unread_leader_message_count": self.unread_leader_message_count,
            "work_remaining": self.work_remaining,
            "stalled_for_ms": self.stalled_for_ms,
        }

    @classmethod
    def from_dict(
        cls, d: dict[str, Any], team_name_default: str = ""
    ) -> TeamLeaderAttentionState:
        """Normalize an arbitrary dict into a well-typed attention state.

        Mirrors the TS ``normalizeTeamLeaderAttentionState`` helper: unknown
        ``source`` values fall back to ``notify_hook``, unknown
        ``leader_decision_state`` values fall back to ``still_actionable``,
        and ``attention_reasons`` is coerced to a list of non-empty strings.
        """
        source_raw = d.get("source")
        source = source_raw if source_raw in LEADER_ATTENTION_SOURCES else "notify_hook"

        decision_raw = d.get("leader_decision_state")
        leader_decision_state = (
            decision_raw
            if decision_raw in {"done_waiting_on_leader", "stuck_waiting_on_leader"}
            else "still_actionable"
        )

        attention_reasons_raw = d.get("attention_reasons")
        if isinstance(attention_reasons_raw, list):
            attention_reasons = [
                entry
                for entry in attention_reasons_raw
                if isinstance(entry, str) and entry.strip() != ""
            ]
        else:
            attention_reasons = []

        team_name = d.get("team_name")
        if not (isinstance(team_name, str) and team_name.strip() != ""):
            team_name = team_name_default

        updated_at = d.get("updated_at")
        if not (isinstance(updated_at, str) and updated_at.strip() != ""):
            updated_at = _now_iso()

        leader_attention_reason = d.get("leader_attention_reason")
        if not (
            isinstance(leader_attention_reason, str)
            and leader_attention_reason.strip() != ""
        ):
            leader_attention_reason = None

        leader_session_id = d.get("leader_session_id")
        if not (isinstance(leader_session_id, str) and leader_session_id.strip() != ""):
            leader_session_id = None

        leader_session_stopped_at = d.get("leader_session_stopped_at")
        if not (
            isinstance(leader_session_stopped_at, str)
            and leader_session_stopped_at.strip() != ""
        ):
            leader_session_stopped_at = None

        unread_raw = d.get("unread_leader_message_count")
        if isinstance(unread_raw, bool) or not isinstance(unread_raw, (int, float)):
            unread = 0
        else:
            unread = int(unread_raw)

        stalled_raw = d.get("stalled_for_ms")
        if isinstance(stalled_raw, bool) or not isinstance(stalled_raw, (int, float)):
            stalled = None
        else:
            stalled = int(stalled_raw)

        return cls(
            team_name=team_name,
            updated_at=updated_at,
            source=source,
            leader_decision_state=leader_decision_state,
            leader_attention_pending=d.get("leader_attention_pending") is True,
            leader_attention_reason=leader_attention_reason,
            attention_reasons=attention_reasons,
            leader_stale=d.get("leader_stale") is True,
            leader_session_active=d.get("leader_session_active") is not False,
            leader_session_id=leader_session_id,
            leader_session_stopped_at=leader_session_stopped_at,
            unread_leader_message_count=unread,
            work_remaining=d.get("work_remaining") is True,
            stalled_for_ms=stalled,
        )


# === Path helpers ===


def _leader_attention_path(team_name: str, cwd: str) -> Path:
    return _team_dir_path(team_name, cwd) / "leader-attention.json"


# === Public readers / writers ===


def read_team_leader_attention(
    team_name: str, cwd: str
) -> TeamLeaderAttentionState | None:
    """Read the persisted leader-attention record for a team.

    Returns ``None`` when the file does not exist or cannot be parsed —
    matching the TS reader's `try/catch → null` semantics.
    """
    path = _leader_attention_path(team_name, cwd)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    return TeamLeaderAttentionState.from_dict(raw, team_name_default=team_name)


def write_team_leader_attention(
    team_name: str, cwd: str, state: TeamLeaderAttentionState
) -> None:
    """Atomically write the leader-attention record for a team.

    The on-disk ``team_name`` is forced to match the argument so a renamed /
    misfiled record cannot silently take over a different team's slot —
    same defensive overwrite as the TS writer.
    """
    payload = state.to_dict()
    payload["team_name"] = team_name
    path = _leader_attention_path(team_name, cwd)
    write_atomic(path, json.dumps(payload, indent=2))


# === Derivation helpers (port of deriveLeaderStopAttentionState) ===


def _derive_leader_stop_attention_state(
    team_name: str,
    cwd: str,
    existing: TeamLeaderAttentionState | None,
) -> dict[str, Any]:
    """Compute the dynamic subset of attention state from current team state.

    Port of TS ``deriveLeaderStopAttentionState``. Reads tasks, the monitor
    snapshot, and the ``leader-fixed`` mailbox. Each read is best-effort:
    missing files yield empty defaults, so derivation never fails the caller.
    """
    # Tasks: pending / blocked / in_progress drive work_remaining and the
    # leader decision state. read_tasks already returns [] on missing file.
    try:
        tasks = read_tasks(cwd, team_name)
    except Exception:
        tasks = []

    pending_count = sum(1 for t in tasks if getattr(t, "status", None) == "pending")
    blocked_count = sum(1 for t in tasks if getattr(t, "status", None) == "blocked")
    in_progress_count = sum(
        1 for t in tasks if getattr(t, "status", None) == "in_progress"
    )
    work_remaining = (pending_count + blocked_count + in_progress_count) > 0

    # Monitor snapshot drives "are all workers idle?". A None snapshot or
    # empty worker map collapses cleanly to all_workers_idle = False.
    team_state_dir = _team_dir_path(team_name, cwd)
    try:
        snapshot = read_monitor_snapshot(team_state_dir)
    except Exception:
        snapshot = None

    worker_state_by_name: dict[str, str] = (
        snapshot.worker_state_by_name if snapshot is not None else {}
    )
    worker_states = [
        s
        for s in worker_state_by_name.values()
        if isinstance(s, str) and s.strip() != ""
    ]
    all_workers_idle = len(worker_states) > 0 and all(
        s == "idle" or s == "done" for s in worker_states
    )

    # Leader decision state — match the TS conditional ladder exactly:
    #   1. all empty + workers idle           → done_waiting_on_leader
    #   2. only blocked tasks, workers idle   → stuck_waiting_on_leader
    #   3. otherwise preserve existing or fall back to still_actionable
    if (
        pending_count == 0
        and blocked_count == 0
        and in_progress_count == 0
        and all_workers_idle
    ):
        leader_decision_state = "done_waiting_on_leader"
    elif (
        blocked_count > 0
        and pending_count == 0
        and in_progress_count == 0
        and all_workers_idle
    ):
        leader_decision_state = "stuck_waiting_on_leader"
    else:
        leader_decision_state = (
            existing.leader_decision_state if existing else "still_actionable"
        )

    # Unread leader-fixed mailbox messages — a message is "unread" if it has
    # not been marked delivered.
    try:
        mailbox = read_mailbox(team_state_dir, "leader-fixed")
    except Exception:
        mailbox = []

    unread_leader_message_count = sum(
        1
        for m in mailbox
        if not (isinstance(m.delivered_at, str) and m.delivered_at.strip() != "")
    )

    # Attention reasons: preserve existing reasons and add the stopped marker
    # if pending. Use dict.fromkeys to dedupe while preserving order — Python
    # equivalent of TS `new Set([...])` followed by spread to array.
    attention_reasons_in = list(existing.attention_reasons) if existing else []
    leader_attention_pending = (
        leader_decision_state != "still_actionable"
        or unread_leader_message_count > 0
        or (existing is not None and existing.leader_attention_pending is True)
    )
    if leader_attention_pending:
        attention_reasons_in.append("leader_session_stopped")
    attention_reasons = list(dict.fromkeys(attention_reasons_in))

    if leader_attention_pending:
        leader_attention_reason = (
            existing.leader_attention_reason
            if existing and existing.leader_attention_reason
            else "leader_session_stopped"
        )
    else:
        leader_attention_reason = None

    return {
        "leader_decision_state": leader_decision_state,
        "leader_attention_pending": leader_attention_pending,
        "leader_attention_reason": leader_attention_reason,
        "attention_reasons": attention_reasons,
        "unread_leader_message_count": unread_leader_message_count,
        "work_remaining": work_remaining,
    }


# === Stop-observation entry points ===


def mark_team_leader_stop_observed(
    team_name: str,
    cwd: str,
    leader_session_id: str,
    now_iso: str | None = None,
    source: str = "native_stop",
) -> TeamLeaderAttentionState:
    """Record that the leader session for a team has stopped.

    Port of TS ``markTeamLeaderStopObserved``. Idempotent: repeated calls keep
    the team in a stopped state with a refreshed ``updated_at`` /
    ``leader_session_stopped_at`` and never silently drop the stronger
    ``native_stop`` source if a later call comes in as ``native_session_end``.
    """
    if source not in LEADER_ATTENTION_SOURCES:
        source = "native_stop"
    if now_iso is None or now_iso.strip() == "":
        now_iso = _now_iso()

    existing = read_team_leader_attention(team_name, cwd)
    derived = _derive_leader_stop_attention_state(team_name, cwd, existing)

    # Preserve the stronger native_stop signal across a later native_session_end.
    if (
        existing is not None
        and existing.source == "native_stop"
        and source == "native_session_end"
    ):
        next_source = "native_stop"
    else:
        next_source = source

    next_state = TeamLeaderAttentionState(
        team_name=team_name,
        updated_at=now_iso,
        source=next_source,
        leader_decision_state=derived["leader_decision_state"],
        leader_attention_pending=derived["leader_attention_pending"],
        leader_attention_reason=derived["leader_attention_reason"],
        attention_reasons=derived["attention_reasons"],
        leader_stale=existing.leader_stale if existing else False,
        leader_session_active=False,
        leader_session_id=(
            leader_session_id
            if leader_session_id and leader_session_id.strip() != ""
            else (existing.leader_session_id if existing else None)
        ),
        leader_session_stopped_at=now_iso,
        unread_leader_message_count=derived["unread_leader_message_count"],
        work_remaining=derived["work_remaining"],
        stalled_for_ms=existing.stalled_for_ms if existing else None,
    )
    write_team_leader_attention(team_name, cwd, next_state)
    return next_state


def mark_team_leader_session_stopped(
    team_name: str,
    cwd: str,
    leader_session_id: str,
    now_iso: str | None = None,
) -> TeamLeaderAttentionState:
    """Stop-observed wrapper for the ``native_session_end`` source.

    Port of TS ``markTeamLeaderSessionStopped``. Thin wrapper around
    ``mark_team_leader_stop_observed`` that pins ``source = native_session_end``.
    """
    return mark_team_leader_stop_observed(
        team_name,
        cwd,
        leader_session_id,
        now_iso=now_iso,
        source="native_session_end",
    )


# === Owned-teams sweep ===


def _read_manifest_leader_session_id(team_name: str, cwd: str) -> str | None:
    """Read the V2 manifest's leader.session_id, or None.

    Returns ``None`` when the manifest is missing, malformed, or carries an
    empty session id. The manifest reader is owned by
    ``omx.team.state.manifest`` and already returns ``None`` for any failure
    mode, so we just unwrap the leader identity.
    """
    from omx.team.state.manifest import read_team_manifest_v2

    manifest = read_team_manifest_v2(team_name, cwd)
    if manifest is None:
        return None
    session_id = manifest.leader.session_id
    if isinstance(session_id, str) and session_id.strip() != "":
        return session_id
    return None


def _read_team_phase_current(team_name: str, cwd: str) -> str | None:
    """Read the current_phase from phase-state.json, if any."""
    phase_path = _team_dir_path(team_name, cwd) / "phase-state.json"
    if not phase_path.exists():
        return None
    try:
        raw = json.loads(phase_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None
    cp = raw.get("current_phase")
    return cp if isinstance(cp, str) else None


def mark_owned_teams_leader_stop_observed(
    cwd: str,
    leader_session_id: str,
    now_iso: str | None = None,
    source: str = "native_stop",
) -> list[str]:
    """Walk all teams under ``cwd`` and stop any owned by ``leader_session_id``.

    Port of TS ``markOwnedTeamsLeaderStopObserved``. Returns the list of team
    names that were updated. A team is considered "owned" by the leader
    session when its V2 manifest's ``leader.session_id`` matches. Teams in a
    terminal phase (``complete``, ``failed``, ``cancelled``) are skipped.
    """
    if not leader_session_id or leader_session_id.strip() == "":
        return []

    target_session_id = leader_session_id.strip()
    teams_root = Path(cwd) / ".omx" / "team"
    if not teams_root.is_dir():
        return []

    updated_teams: list[str] = []
    for entry in sorted(teams_root.iterdir()):
        if not entry.is_dir():
            continue
        team_name = entry.name.strip()
        if not team_name:
            continue

        manifest_session_id = _read_manifest_leader_session_id(team_name, cwd)
        if manifest_session_id is None:
            # No manifest, no ownership — skip silently (matches TS behavior
            # where `!manifest` causes a `continue`).
            continue
        if manifest_session_id.strip() != target_session_id:
            continue

        current_phase = _read_team_phase_current(team_name, cwd)
        if current_phase is not None and current_phase in TERMINAL_PHASES:
            continue

        mark_team_leader_stop_observed(
            team_name,
            cwd,
            target_session_id,
            now_iso=now_iso,
            source=source,
        )
        updated_teams.append(team_name)

    return updated_teams


def mark_owned_teams_leader_session_stopped(
    cwd: str,
    leader_session_id: str,
    now_iso: str | None = None,
) -> list[str]:
    """Owned-teams sweep variant for the ``native_session_end`` source.

    Port of TS ``markOwnedTeamsLeaderSessionStopped``. Thin wrapper around
    ``mark_owned_teams_leader_stop_observed``.
    """
    return mark_owned_teams_leader_stop_observed(
        cwd,
        leader_session_id,
        now_iso=now_iso,
        source="native_session_end",
    )
