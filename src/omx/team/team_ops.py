"""MCP-aligned team operations gateway.

Port of ``src/team/team-ops.ts``. This module is the canonical import surface
for the MCP server and the team runtime: every function below corresponds to
(or backs) an MCP tool with the same semantic name, so the runtime contract
matches the external MCP surface.

All callers (MCP server, runtime, CLI) should import from
``omx.team.team_ops``; the ``omx.team.state.*`` modules remain the private
persistence layer.

Naming and signature contract: every public function takes ``team_name`` as
the first positional argument and ``cwd`` as the last positional argument,
matching the TS convention. Internal Python helpers in ``team.state.*`` use
varied arg orders for historical reasons; wrappers here normalize them.
"""

from __future__ import annotations

from typing import Any

from omx.team.state_root import team_dir as _team_dir

# === Type re-exports ===
from omx.team.contracts import (
    TaskStatus as TaskStatus,
    TeamEvent as TeamEvent,
    TeamTask as TeamTask,
    TeamWorker as TeamWorker,
)
from omx.team.state.types import (
    ABSOLUTE_MAX_WORKERS as ABSOLUTE_MAX_WORKERS,
    DEFAULT_MAX_WORKERS as DEFAULT_MAX_WORKERS,
    TaskApprovalRecord as TaskApprovalRecord,
    TeamDispatchRequest as TeamDispatchRequest,
    TeamMailboxMessage as TeamMailboxMessage,
    TeamMonitorSnapshot as TeamMonitorSnapshot,
    TeamPhaseState as TeamPhaseState,
    TeamTaskClaim as TeamTaskClaim,
)
from omx.team.state.manifest import (
    PermissionsSnapshot as PermissionsSnapshot,
    TeamLeader as TeamLeader,
    TeamManifestV2 as TeamManifestV2,
)
from omx.team.state.policy import (
    TeamGovernance as TeamGovernance,
    TeamPolicy as TeamPolicy,
)
from omx.team.state.leader import TeamLeaderAttentionState as TeamLeaderAttentionState
from omx.team.state.shutdown import ShutdownAck as ShutdownAck
from omx.team.state.tasks import TaskReadiness as TaskReadiness

# === Direct re-exports (already (team_name|cwd, ...) shaped) ===
from omx.team.state.atomic import write_atomic as write_atomic
from omx.team.state.manifest import (
    init_team_state as team_init,
    read_team_manifest_v2 as team_read_manifest,
    write_team_manifest_v2 as team_write_manifest,
)
from omx.team.state.policy import (
    normalize_team_governance as team_normalize_governance,
    normalize_team_policy as team_normalize_policy,
)
from omx.team.state.leader import (
    mark_owned_teams_leader_session_stopped as team_mark_owned_teams_leader_session_stopped,
    mark_team_leader_session_stopped as team_mark_leader_session_stopped,
    read_team_leader_attention as team_read_leader_attention,
    write_team_leader_attention as team_write_leader_attention,
)
from omx.team.state.shutdown import (
    read_shutdown_ack as team_read_shutdown_ack,
    write_shutdown_request as team_write_shutdown_request,
)
from omx.team.state.tasks import (
    compute_task_readiness as team_compute_task_readiness,
    create_task as team_create_task,
    list_tasks as team_list_tasks,
    read_task as team_read_task,
    update_task as team_update_task,
)
from omx.team.state.dispatch import (
    resolve_dispatch_lock_timeout_ms as resolve_dispatch_lock_timeout_ms,
)
from omx.team.state.locks import with_scaling_lock as _with_scaling_lock_impl
from omx.team.state.io import (
    cleanup_team_state as team_cleanup,
    read_team_config as team_read_config,
    read_worker_heartbeat as team_read_worker_heartbeat,
    read_worker_status as team_read_worker_status,
    write_team_config as team_save_config,
    write_worker_heartbeat as team_update_worker_heartbeat,
    write_worker_identity as team_write_worker_identity,
    write_worker_inbox as team_write_worker_inbox,
    write_worker_status as team_write_worker_status,
    append_team_event as team_append_event,
)


__all__ = [
    # Types
    "ABSOLUTE_MAX_WORKERS",
    "DEFAULT_MAX_WORKERS",
    "PermissionsSnapshot",
    "ShutdownAck",
    "TaskApprovalRecord",
    "TaskReadiness",
    "TaskStatus",
    "TeamDispatchRequest",
    "TeamEvent",
    "TeamGovernance",
    "TeamLeader",
    "TeamLeaderAttentionState",
    "TeamMailboxMessage",
    "TeamManifestV2",
    "TeamMonitorSnapshot",
    "TeamPhaseState",
    "TeamPolicy",
    "TeamTask",
    "TeamTaskClaim",
    "TeamWorker",
    # Direct re-exports
    "resolve_dispatch_lock_timeout_ms",
    "team_append_event",
    "team_cleanup",
    "team_compute_task_readiness",
    "team_create_task",
    "team_init",
    "team_list_tasks",
    "team_mark_leader_session_stopped",
    "team_mark_owned_teams_leader_session_stopped",
    "team_normalize_governance",
    "team_normalize_policy",
    "team_read_config",
    "team_read_leader_attention",
    "team_read_manifest",
    "team_read_shutdown_ack",
    "team_read_task",
    "team_read_worker_heartbeat",
    "team_read_worker_status",
    "team_save_config",
    "team_update_task",
    "team_update_worker_heartbeat",
    "team_write_leader_attention",
    "team_write_manifest",
    "team_write_shutdown_request",
    "team_write_worker_identity",
    "team_write_worker_inbox",
    "team_write_worker_status",
    "write_atomic",
    # Wrappers (defined below)
    "team_broadcast",
    "team_claim_task",
    "team_enqueue_dispatch_request",
    "team_get_summary",
    "team_list_dispatch_requests",
    "team_list_mailbox",
    "team_mark_dispatch_request_delivered",
    "team_mark_dispatch_request_notified",
    "team_mark_message_delivered",
    "team_mark_message_notified",
    "team_read_dispatch_request",
    "team_read_monitor_snapshot",
    "team_read_phase",
    "team_read_task_approval",
    "team_reclaim_expired_task_claim",
    "team_release_task_claim",
    "team_send_message",
    "team_transition_dispatch_request",
    "team_transition_task_status",
    "team_with_scaling_lock",
    "team_write_monitor_snapshot",
    "team_write_phase",
    "team_write_task_approval",
]


# === team_dir-based wrappers (state layer takes Path; gateway takes team_name+cwd) ===


def team_claim_task(
    team_name: str, task_id: str, worker_name: str, cwd: str
) -> dict[str, Any]:
    """Claim a task for a worker via the bulk-task store."""
    from omx.team.state.io import read_tasks, write_tasks
    from omx.team.state.tasks import claim_task

    tasks = read_tasks(cwd, team_name)
    for i, task in enumerate(tasks):
        if task.task_id != task_id:
            continue
        result = claim_task(task.to_dict(), worker_name)
        if result.get("ok"):
            tasks[i] = TeamTask.from_dict(result["task"])
            write_tasks(cwd, tasks, team_name)
        return result
    return {"ok": False, "error": f"task {task_id} not found"}


def team_release_task_claim(
    team_name: str, task_id: str, claim_token: str, cwd: str
) -> dict[str, Any]:
    """Release a task claim."""
    from omx.team.state.io import read_tasks, write_tasks
    from omx.team.state.tasks import release_task_claim

    tasks = read_tasks(cwd, team_name)
    for i, task in enumerate(tasks):
        if task.task_id != task_id:
            continue
        result = release_task_claim(task.to_dict(), claim_token)
        if result.get("ok"):
            tasks[i] = TeamTask.from_dict(result["task"])
            write_tasks(cwd, tasks, team_name)
        return result
    return {"ok": False, "error": f"task {task_id} not found"}


def team_reclaim_expired_task_claim(
    team_name: str, task_id: str, cwd: str
) -> dict[str, Any]:
    """Reclaim a task whose lease has expired."""
    from omx.team.state.io import read_tasks, write_tasks
    from omx.team.state.tasks import reclaim_expired_task

    tasks = read_tasks(cwd, team_name)
    for i, task in enumerate(tasks):
        if task.task_id != task_id:
            continue
        result = reclaim_expired_task(task.to_dict())
        if result.get("ok") and result.get("reclaimed"):
            tasks[i] = TeamTask.from_dict(result["task"])
            write_tasks(cwd, tasks, team_name)
        return result
    return {"ok": False, "error": f"task {task_id} not found"}


def team_transition_task_status(
    team_name: str,
    task_id: str,
    from_status: str,
    to_status: str,
    claim_token: str,
    cwd: str,
    terminal_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Transition a task's status with claim-token authorization."""
    from omx.team.state.io import read_tasks, write_tasks
    from omx.team.state.tasks import transition_task_status

    tasks = read_tasks(cwd, team_name)
    for i, task in enumerate(tasks):
        if task.task_id != task_id:
            continue
        result = transition_task_status(
            task.to_dict(), from_status, to_status, claim_token, terminal_data
        )
        if result.get("ok"):
            tasks[i] = TeamTask.from_dict(result["task"])
            write_tasks(cwd, tasks, team_name)
        return result
    return {"ok": False, "error": f"task {task_id} not found"}


# --- Messaging ---


def team_send_message(
    team_name: str, from_worker: str, to_worker: str, body: str, cwd: str
) -> TeamMailboxMessage:
    """Send a direct message to one worker."""
    from omx.team.state.mailbox import send_direct_message

    return send_direct_message(_team_dir(team_name, cwd), from_worker, to_worker, body)


def team_broadcast(
    team_name: str,
    from_worker: str,
    body: str,
    cwd: str,
    worker_names: list[str] | None = None,
) -> list[TeamMailboxMessage]:
    """Broadcast a message to all workers in the team (excluding ``from_worker``).

    If ``worker_names`` is omitted, the recipient set is derived from
    ``read_workers``.
    """
    from omx.team.state.io import read_workers
    from omx.team.state.mailbox import broadcast_message

    if worker_names is None:
        worker_names = [w.worker_id for w in read_workers(cwd, team_name)]
    return broadcast_message(_team_dir(team_name, cwd), from_worker, body, worker_names)


def team_list_mailbox(
    team_name: str, worker_name: str, cwd: str
) -> list[TeamMailboxMessage]:
    """List a worker's mailbox messages."""
    from omx.team.state.mailbox import read_mailbox

    return read_mailbox(_team_dir(team_name, cwd), worker_name)


def team_mark_message_delivered(
    team_name: str, worker_name: str, message_id: str, cwd: str
) -> bool:
    """Mark a mailbox message as delivered."""
    from omx.team.state.mailbox import mark_message_delivered

    return mark_message_delivered(_team_dir(team_name, cwd), worker_name, message_id)


def team_mark_message_notified(
    team_name: str, worker_name: str, message_id: str, cwd: str
) -> bool:
    """Mark a mailbox message as notified."""
    from omx.team.state.mailbox import mark_message_notified

    return mark_message_notified(_team_dir(team_name, cwd), worker_name, message_id)


# --- Dispatch requests ---


def team_enqueue_dispatch_request(
    team_name: str, request_input: dict[str, Any], cwd: str
) -> TeamDispatchRequest | None:
    """Enqueue a dispatch request (deduplicates against pending)."""
    from omx.team.state.dispatch import enqueue_dispatch_request

    return enqueue_dispatch_request(_team_dir(team_name, cwd), team_name, request_input)


def team_list_dispatch_requests(team_name: str, cwd: str) -> list[TeamDispatchRequest]:
    """List all dispatch requests for a team."""
    from omx.team.state.dispatch import read_dispatch_requests

    return read_dispatch_requests(_team_dir(team_name, cwd))


def team_read_dispatch_request(
    team_name: str, request_id: str, cwd: str
) -> TeamDispatchRequest | None:
    """Read a single dispatch request by id."""
    from omx.team.state.dispatch import read_dispatch_request

    return read_dispatch_request(_team_dir(team_name, cwd), request_id)


def team_transition_dispatch_request(
    team_name: str, request_id: str, to_status: str, cwd: str, reason: str | None = None
) -> bool:
    """Transition a dispatch request to ``to_status``."""
    from omx.team.state.dispatch import transition_dispatch_request

    return transition_dispatch_request(
        _team_dir(team_name, cwd), request_id, to_status, reason=reason
    )


def team_mark_dispatch_request_notified(
    team_name: str, request_id: str, cwd: str, reason: str | None = None
) -> bool:
    """Mark a dispatch request as notified."""
    from omx.team.state.dispatch import mark_dispatch_request_notified

    return mark_dispatch_request_notified(
        _team_dir(team_name, cwd), request_id, reason=reason
    )


def team_mark_dispatch_request_delivered(
    team_name: str, request_id: str, cwd: str, reason: str | None = None
) -> bool:
    """Mark a dispatch request as delivered."""
    from omx.team.state.dispatch import mark_dispatch_request_delivered

    return mark_dispatch_request_delivered(
        _team_dir(team_name, cwd), request_id, reason=reason
    )


# --- Approvals ---


def team_read_task_approval(
    team_name: str, task_id: str, cwd: str
) -> TaskApprovalRecord | None:
    """Read a task approval record by task id."""
    from omx.team.state.approvals import read_task_approval

    return read_task_approval(_team_dir(team_name, cwd), task_id)


def team_write_task_approval(
    team_name: str, approval: TaskApprovalRecord, cwd: str
) -> None:
    """Write a task approval record."""
    from omx.team.state.approvals import write_task_approval

    write_task_approval(_team_dir(team_name, cwd), approval)


# --- Monitor snapshot + phase + summary ---


def team_get_summary(team_name: str, cwd: str) -> dict[str, Any]:
    """Build a team summary dict.

    Resolves workers + tasks from the state layer and dispatches to
    `team.state.monitor.get_team_summary`, which expects pre-loaded lists
    rather than (team_name, cwd).
    """
    from omx.team.state.io import read_tasks, read_workers
    from omx.team.state.monitor import get_team_summary

    workers = [w.to_dict() for w in read_workers(cwd, team_name)]
    tasks = [t.to_dict() for t in read_tasks(cwd, team_name)]
    return get_team_summary(_team_dir(team_name, cwd), workers, tasks)


def team_read_monitor_snapshot(team_name: str, cwd: str) -> TeamMonitorSnapshot | None:
    """Read the persisted monitor snapshot for a team."""
    from omx.team.state.monitor import read_monitor_snapshot

    return read_monitor_snapshot(_team_dir(team_name, cwd))


def team_write_monitor_snapshot(
    team_name: str, snapshot: TeamMonitorSnapshot, cwd: str
) -> None:
    """Persist the monitor snapshot for a team."""
    from omx.team.state.monitor import write_monitor_snapshot

    write_monitor_snapshot(_team_dir(team_name, cwd), snapshot)


def team_read_phase(team_name: str, cwd: str) -> TeamPhaseState | None:
    """Read the persisted team phase state."""
    from omx.team.state.monitor import read_phase_state

    return read_phase_state(_team_dir(team_name, cwd))


def team_write_phase(team_name: str, phase: TeamPhaseState, cwd: str) -> None:
    """Persist the team phase state."""
    from omx.team.state.monitor import write_phase_state

    write_phase_state(_team_dir(team_name, cwd), phase)


# --- Scaling lock ---


def team_with_scaling_lock(team_name: str, cwd: str, timeout_ms: int = 10_000):  # type: ignore[no-untyped-def]
    """Acquire the team's scaling lock as a context manager."""
    return _with_scaling_lock_impl(_team_dir(team_name, cwd), timeout_ms=timeout_ms)
