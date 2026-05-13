"""Team runtime: assign / reassign task entry points.

Port of ``assignTask`` and ``reassignTask`` from ``src/team/runtime.ts``
(lines 2838-2956). Phase 2.8a — sync-only, stdlib-only.

The assignment flow mirrors TS step-for-step:

1. Sanitize ``team_name`` and read the target task / manifest.
2. Resolve governance + dispatch policy.
3. Enforce governance gates:
   * ``delegation_only`` blocks ``leader-fixed`` assignments.
   * ``plan_approval_required`` blocks unapproved tasks that
     ``requires_code_change``.
4. Look up the worker in ``config.workers``.
5. Claim the task; surface ``blocked_dependency`` cleanly.
6. Build the assignment inbox + trigger directive, then dispatch with a
   bounded retry loop (max 2 attempts). On retry, dismiss a Codex trust
   prompt when present and re-wait for the pane to become ready.
7. On any post-claim failure, roll back the claim and write a cancellation
   inbox so a stale prior inbox is not actioned.

``reassignTask`` is a thin re-target wrapper around ``assignTask``.
"""

from __future__ import annotations

import os
import time
from typing import Any

from omx.team.mcp_comm import (
    DispatchOutcome,
    DispatchTransport,
    QueueInboxParams,
    TeamNotifierTarget,
    queue_inbox_instruction,
)
from omx.team.state.approvals import read_task_approval
from omx.team.state.policy import (
    TeamGovernance,
    TeamPolicy,
    normalize_team_governance,
    normalize_team_policy,
)
from omx.team.state_root import team_dir as _team_dir
from omx.team.team_ops import (
    team_claim_task,
    team_read_config,
    team_read_manifest,
    team_read_task,
    team_release_task_claim,
    team_write_worker_inbox,
)
from omx.team.tmux_session import (
    dismiss_trust_prompt_if_present,
    sanitize_team_name,
    wait_for_worker_ready,
)
from omx.team.worker_bootstrap import (
    build_trigger_directive,
    generate_task_assignment_inbox,
)


# ---------------------------------------------------------------------------
# Private helpers (TS parity)
# ---------------------------------------------------------------------------


# Matches TS ``WORKTREE_TRIGGER_STATE_ROOT`` (runtime.ts:1321). When a worker
# runs out of a worktree we cannot embed the leader cwd in the trigger text
# because the worker pane's $cwd is different; the literal ``$OMX_TEAM_STATE_ROOT``
# is expanded by the worker shell at dispatch time.
_WORKTREE_TRIGGER_STATE_ROOT = "$OMX_TEAM_STATE_ROOT"


def _resolve_instruction_state_root(worktree_path: str | None) -> str | None:
    """Return the trigger-text state root or ``None`` for single-tree teams.

    TS source: ``resolveInstructionStateRoot`` (runtime.ts:1343-1345).
    """
    return _WORKTREE_TRIGGER_STATE_ROOT if worktree_path else None


def _resolve_worker_ready_timeout_ms(env: dict[str, str] | None = None) -> int:
    """Return the worker-ready poll timeout in milliseconds.

    TS source: ``resolveWorkerReadyTimeoutMs`` (runtime.ts:1358-1363). The
    floor is 5000 ms; the default is 45 000 ms.
    """
    effective: dict[str, str] = env if env is not None else dict(os.environ)
    raw = effective.get("OMX_TEAM_READY_TIMEOUT_MS")
    try:
        parsed = int(str(raw or "").strip(), 10)
    except (TypeError, ValueError):
        return 45_000
    if parsed >= 5_000:
        return parsed
    return 45_000


def _resolve_governance_policy(
    governance: dict[str, Any] | None,
    legacy_policy: dict[str, Any] | None = None,
) -> TeamGovernance:
    """Normalize a manifest governance dict.

    TS source: ``resolveGovernancePolicy`` (runtime.ts:1411-1416).
    """
    return normalize_team_governance(governance, legacy_policy)


def _resolve_dispatch_policy(
    manifest_policy: dict[str, Any] | None,
    worker_launch_mode: str,
) -> TeamPolicy:
    """Normalize a manifest policy dict into a canonical ``TeamPolicy``.

    TS source: ``resolveDispatchPolicy`` (runtime.ts:3599-3607). The display
    mode defaults to ``auto`` unless the manifest pins ``split_pane``.
    """
    display_mode = "auto"
    if manifest_policy and manifest_policy.get("display_mode") == "split_pane":
        display_mode = "split_pane"
    return normalize_team_policy(
        manifest_policy,
        defaults={
            "display_mode": display_mode,
            "worker_launch_mode": worker_launch_mode,
        },
    )


def _is_task_approved_for_execution(team_name: str, task_id: str, cwd: str) -> bool:
    """Return True when the task has an explicit ``approved`` record.

    TS source: ``isTaskApprovedForExecution`` (runtime.ts:3465-3468).
    """
    record = read_task_approval(_team_dir(team_name, cwd), task_id)
    return record is not None and record.status == "approved"


def _dispatch_critical_inbox_instruction(
    *,
    team_name: str,
    config: dict[str, Any],
    worker_name: str,
    worker_index: int,
    pane_id: str | None,
    inbox: str,
    trigger_message: str,
    intent: str | None,
    cwd: str,
    dispatch_policy: TeamPolicy,
    inbox_correlation_key: str,
) -> DispatchOutcome:
    """Dispatch a critical inbox instruction via the configured transport.

    Simplified port of ``dispatchCriticalInboxInstruction``
    (runtime.ts:3645-...). The TS implementation has additional branches
    for prompt-mode startup evidence and hook receipt waits; those are
    handled by ``queue_inbox_instruction`` and the dispatch lifecycle
    inside :mod:`omx.team.mcp_comm` in the Python port. We surface the
    same three transport preferences (``prompt_stdin``,
    ``transport_direct``, ``hook_preferred_with_fallback``) so the
    contract callers see is identical.
    """
    worker_launch_mode = str(config.get("worker_launch_mode") or "interactive")

    if worker_launch_mode == "prompt":
        transport_preference = "prompt_stdin"
        fallback_allowed = False
    elif dispatch_policy.dispatch_mode.value == "transport_direct":
        transport_preference = "transport_direct"
        fallback_allowed = False
    else:
        transport_preference = "hook_preferred_with_fallback"
        fallback_allowed = True

    def _notify(
        _target: TeamNotifierTarget,
        _message: str,
        _context: dict[str, Any],
    ) -> DispatchOutcome:
        # Default notifier: report the queue acknowledgement. The Python
        # port has no separate "tmux send-keys" notifier wired through
        # this entry point yet; the dispatch lifecycle inside
        # ``queue_inbox_instruction`` performs the actual transport.
        return DispatchOutcome(
            ok=True,
            transport=DispatchTransport.HOOK.value,
            reason="queued_for_hook_dispatch",
        )

    params = QueueInboxParams(
        team_name=team_name,
        worker_name=worker_name,
        worker_index=worker_index,
        inbox=inbox,
        trigger_message=trigger_message,
        cwd=cwd,
        notify=_notify,
        pane_id=pane_id,
        intent=intent,  # type: ignore[arg-type]
        transport_preference=transport_preference,
        fallback_allowed=fallback_allowed,
        inbox_correlation_key=inbox_correlation_key,
    )
    return queue_inbox_instruction(params)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


_MAX_ASSIGN_RETRIES = 2
_ASSIGN_RETRY_DELAY_S = 2


def assign_task(
    team_name: str,
    worker_name: str,
    task_id: str,
    cwd: str,
) -> None:
    """Assign ``task_id`` to ``worker_name`` on the given team.

    Port of ``assignTask`` (runtime.ts:2838-2943). Behaviour:

    * Raises ``ValueError("Task <id> not found")`` when the task is absent.
    * Raises ``ValueError("Team <name> not found")`` when no config exists.
    * Raises ``ValueError("Worker <name> not found in team")`` when the
      worker is not in the team's worker list.
    * Raises ``ValueError("delegation_only_violation")`` when the manifest
      governance forbids leader self-execution.
    * Raises ``ValueError("plan_approval_required")`` when the task requires
      a code change but no approval record exists.
    * Raises ``ValueError("blocked_dependency:<csv>")`` when the claim fails
      because upstream dependencies are still pending.
    * Raises ``ValueError("<claim_error>")`` for any other claim failure.
    * Retries dispatch up to :data:`_MAX_ASSIGN_RETRIES` times. On retry it
      attempts to dismiss a Codex trust prompt and waits for pane readiness;
      otherwise it sleeps :data:`_ASSIGN_RETRY_DELAY_S` seconds. If every
      retry still fails the claim is rolled back, an "Assignment Cancelled"
      inbox is written, and ``worker_notify_failed`` /
      ``worker_assignment_failed:<reason>`` is raised.
    """
    sanitized = sanitize_team_name(team_name)
    # ``team_read_task`` is a direct re-export of ``state.tasks.read_task``
    # whose underlying signature is ``(cwd, team_name, task_id)`` — the
    # gateway docstring claims normalization but is not yet wired (see
    # ``team_ops.py:80``). Match the actual signature.
    task = team_read_task(cwd, sanitized, task_id)
    if task is None:
        raise ValueError(f"Task {task_id} not found")

    manifest = team_read_manifest(sanitized, cwd)
    governance = _resolve_governance_policy(
        manifest.governance if manifest is not None else None
    )

    if governance.delegation_only and worker_name == "leader-fixed":
        raise ValueError("delegation_only_violation")

    # ``requires_code_change`` is not a dataclass field on ``TeamTask``
    # (the Python contracts have not promoted it yet); fall back to
    # dynamic attribute lookup so a future schema bump or test-double
    # populates it transparently.
    requires_code_change = getattr(task, "requires_code_change", None) is True
    if governance.plan_approval_required and requires_code_change:
        if not _is_task_approved_for_execution(sanitized, task_id, cwd):
            raise ValueError("plan_approval_required")

    config = team_read_config(cwd, sanitized)
    if not config:
        raise ValueError(f"Team {sanitized} not found")

    workers_raw: list[dict[str, Any]] = list(config.get("workers") or [])
    worker_info: dict[str, Any] | None = next(
        (w for w in workers_raw if w.get("name") == worker_name),
        None,
    )
    if worker_info is None:
        raise ValueError(f"Worker {worker_name} not found in team")

    dispatch_policy = _resolve_dispatch_policy(
        manifest.policy if manifest is not None else None,
        str(config.get("worker_launch_mode") or "interactive"),
    )

    # ``team_claim_task`` does not take a version arg (the bulk-store port
    # carries no per-task version yet); it returns
    # {"ok": True, "task": ..., "claim_token": "..."} or
    # {"ok": False, "error": "<msg>"} matching the TS shape.
    claim = team_claim_task(sanitized, task_id, worker_name, cwd)
    if not claim.get("ok"):
        error = str(claim.get("error") or "claim_failed")
        if error == "blocked_dependency":
            deps = claim.get("dependencies") or []
            raise ValueError(f"blocked_dependency:{','.join(deps)}")
        raise ValueError(error)

    claim_token = str(claim.get("claim_token") or "")

    try:
        inbox = generate_task_assignment_inbox(
            worker_name, sanitized, task_id, task.description
        )
        trigger = build_trigger_directive(
            worker_name,
            sanitized,
            _resolve_instruction_state_root(worker_info.get("worktree_path"))
            or ".omx/state",
        )

        outcome: DispatchOutcome = DispatchOutcome(
            ok=False,
            transport=DispatchTransport.NONE.value,
            reason="not_attempted",
        )
        for attempt in range(1, _MAX_ASSIGN_RETRIES + 1):
            outcome = _dispatch_critical_inbox_instruction(
                team_name=sanitized,
                config=config,
                worker_name=worker_name,
                worker_index=int(worker_info.get("index") or 0),
                pane_id=worker_info.get("pane_id") or None,
                inbox=inbox,
                trigger_message=trigger.text,
                intent=trigger.intent,
                cwd=cwd,
                dispatch_policy=dispatch_policy,
                inbox_correlation_key=f"assign:{task_id}:{worker_name}",
            )
            if outcome.ok:
                break
            if (
                attempt < _MAX_ASSIGN_RETRIES
                and str(config.get("worker_launch_mode") or "") == "interactive"
                and config.get("tmux_session")
            ):
                session = str(config["tmux_session"])
                worker_index = int(worker_info.get("index") or 0)
                pane_id = worker_info.get("pane_id") or None
                if dismiss_trust_prompt_if_present(session, worker_index, pane_id):
                    wait_for_worker_ready(
                        session,
                        worker_index,
                        _resolve_worker_ready_timeout_ms(),
                        pane_id,
                    )
                else:
                    time.sleep(_ASSIGN_RETRY_DELAY_S)
        if not outcome.ok:
            raise ValueError("worker_notify_failed")
    except BaseException as error:
        # Roll back the claim so the task does not get stuck in_progress
        # on any post-claim dispatch failure.
        released = team_release_task_claim(sanitized, task_id, claim_token, cwd)

        reason_text = ""
        if isinstance(error, BaseException):
            reason_text = str(error).strip()
        reason = reason_text if reason_text else "worker_assignment_failed"

        try:
            team_write_worker_inbox(
                cwd,
                sanitized,
                worker_name,
                (
                    "# Assignment Cancelled\n\n"
                    f"Task {task_id} was not dispatched due to {reason}.\n"
                    "Do not execute this task from prior inbox content."
                ),
            )
        except BaseException as inbox_err:  # noqa: BLE001 - best-effort, mirror TS
            # TS writes to stderr; in Python we suppress because tests do
            # not assert on log output here.
            os.write(
                2,
                f"[team/runtime] operation failed: {inbox_err}\n".encode(
                    "utf-8", "replace"
                ),
            )

        if not released.get("ok"):
            release_error = str(released.get("error") or "release_failed")
            raise ValueError(f"{reason}:{release_error}") from error

        if reason == "worker_notify_failed":
            raise ValueError("worker_notify_failed") from error
        raise ValueError(f"worker_assignment_failed:{reason}") from error


def reassign_task(
    team_name: str,
    task_id: str,
    _from_worker: str,
    to_worker: str,
    cwd: str,
) -> None:
    """Reassign ``task_id`` from one worker to another.

    Thin wrapper around :func:`assign_task` that ignores the
    ``_from_worker`` argument. TS source: ``reassignTask``
    (runtime.ts:2948-2956).
    """
    assign_task(team_name, to_worker, task_id, cwd)
