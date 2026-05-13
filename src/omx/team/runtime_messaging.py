"""Team runtime messaging — direct + broadcast worker mailbox dispatch.

Port of ``sendWorkerMessage`` (runtime.ts:4507-4546) and
``broadcastWorkerMessage`` (runtime.ts:4548-4610) plus the small helper
chain they depend on (``resolveDispatchPolicy``,
``resolveWorkerMailboxTransportPreference``,
``resolveLeaderMailboxTransportPreference``,
``sendLeaderMailboxMessage``, ``sendRecipientMailboxMessage``,
``finalizeBroadcastMailboxOutcomes``, ``notifyWorkerOutcome``).

Locked decisions for Phase 2.8b:

- **Sync only.** All TS ``Promise``-returning helpers map to plain
  synchronous calls; the notifier callable supplied to
  :mod:`omx.team.mcp_comm` is synchronous (see ``TeamNotifier``).
- **Stdlib only.** No new third-party dependencies.

Simplifications vs. the TS source:

- The TS ``finalizeQueuedMailboxDispatch`` orchestrator and its
  ``logRuntimeDispatchOutcome`` / ``markDispatchRequestLeaderPaneMissingDeferred``
  cousins are not yet ported. Their responsibilities (hook-deferred
  finalization + leader-pane-missing soft-persist logging) are already
  partially folded into :func:`omx.team.mcp_comm.queue_direct_mailbox_message`,
  so we return its ``DispatchOutcome`` directly. The TS branch that
  re-stamps ``ok=True`` + ``leader_pane_missing_mailbox_persisted`` is
  preserved verbatim here when the queue layer emits that reason.
- The prompt-mode worker handle registry (``getPromptWorkerHandle``) is
  not yet ported; prompt-mode notifies return
  ``prompt_worker_handle_missing`` to match the TS failure shape.
"""

from __future__ import annotations

from typing import Any

from omx.team.mcp_comm import (
    BroadcastRecipient,
    DispatchOutcome,
    DispatchTransport,
    QueueBroadcastParams,
    QueueDirectMessageParams,
    TeamNotifierTarget,
    queue_broadcast_mailbox_message,
    queue_direct_mailbox_message,
)
from omx.team.state.policy import (
    TeamDispatchMode,
    TeamPolicy,
    TeamWorkerLaunchMode,
    normalize_team_policy,
)
from omx.team.team_ops import team_read_config, team_read_manifest
from omx.team.tmux_session import (
    is_tmux_available,
    notify_leader_status,
    sanitize_team_name,
    send_to_worker,
)
from omx.team.worker_bootstrap import (
    build_leader_mailbox_trigger_directive,
    build_mailbox_trigger_directive,
)


__all__ = [
    "send_worker_message",
    "broadcast_worker_message",
]


# ---------------------------------------------------------------------------
# Constants — mirror TS string literals exactly.
# ---------------------------------------------------------------------------

_LEADER_FIXED = "leader-fixed"
_HOOK_PREFERRED = "hook_preferred_with_fallback"
_TRANSPORT_DIRECT = "transport_direct"
_PROMPT_STDIN = "prompt_stdin"
_QUEUED_FOR_HOOK = "queued_for_hook_dispatch"
_LEADER_PERSISTED = "leader_pane_missing_mailbox_persisted"
_DEFAULT_PENDING_MAILBOX_INTENT = "pending-mailbox-review"


# ---------------------------------------------------------------------------
# Policy / transport-preference helpers (mirror runtime.ts:3599, 4170, 4179).
# ---------------------------------------------------------------------------


def _resolve_dispatch_policy(
    manifest_policy: dict[str, Any] | None, worker_launch_mode: str
) -> TeamPolicy:
    """Mirror TS ``resolveDispatchPolicy``.

    The TS helper preserves the manifest's ``display_mode`` only when it
    is the literal ``"split_pane"``; anything else collapses to ``auto``.
    ``worker_launch_mode`` is taken from the runtime config so older
    manifests still get the right transport defaults.
    """
    display_default = (
        "split_pane"
        if isinstance(manifest_policy, dict)
        and manifest_policy.get("display_mode") == "split_pane"
        else "auto"
    )
    return normalize_team_policy(
        manifest_policy,
        {
            "display_mode": display_default,
            "worker_launch_mode": worker_launch_mode,
        },
    )


def _resolve_worker_transport_preference(
    worker_launch_mode: str, dispatch_policy: TeamPolicy
) -> str:
    """Mirror TS ``resolveWorkerMailboxTransportPreference``."""
    if worker_launch_mode == TeamWorkerLaunchMode.PROMPT.value:
        return _PROMPT_STDIN
    if dispatch_policy.dispatch_mode == TeamDispatchMode.TRANSPORT_DIRECT:
        return _TRANSPORT_DIRECT
    return _HOOK_PREFERRED


def _resolve_leader_transport_preference(dispatch_policy: TeamPolicy) -> str:
    """Mirror TS ``resolveLeaderMailboxTransportPreference``.

    Leader mailboxes never use the prompt stdin path; the TS return type
    is ``Exclude<..., 'prompt_stdin'>``.
    """
    if dispatch_policy.dispatch_mode == TeamDispatchMode.TRANSPORT_DIRECT:
        return _TRANSPORT_DIRECT
    return _HOOK_PREFERRED


# ---------------------------------------------------------------------------
# Worker lookup helpers — operate on the dict shape returned by team_read_config.
# ---------------------------------------------------------------------------


def _find_worker(config: dict[str, Any], worker_name: str) -> dict[str, Any] | None:
    """Return the worker dict whose ``name`` matches, or ``None``."""
    for w in config.get("workers") or []:
        if isinstance(w, dict) and w.get("name") == worker_name:
            return w
    return None


def _worker_index(worker: dict[str, Any]) -> int | None:
    """Best-effort coerce of the worker index field."""
    raw = worker.get("index")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Notifier transport helpers (mirror runtime.ts:3565 notifyWorkerOutcome).
# ---------------------------------------------------------------------------


def _notify_worker_outcome(
    config: dict[str, Any],
    worker_index: int,
    message: str,
    pane_id: str | None,
) -> DispatchOutcome:
    """Direct-transport notify for a worker mailbox.

    Mirrors TS ``notifyWorkerOutcome`` (runtime.ts:3565-3597). The Python
    port simplifies the prompt-mode branch because the prompt worker
    handle registry is not yet ported.
    """
    worker = None
    for candidate in config.get("workers") or []:
        if isinstance(candidate, dict) and _worker_index(candidate) == worker_index:
            worker = candidate
            break
    if worker is None:
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.NONE.value,
            reason="worker_not_found",
        )

    if config.get("worker_launch_mode") == TeamWorkerLaunchMode.PROMPT.value:
        # The Python port has no prompt worker handle registry yet; surface
        # the same failure reason the TS path emits when the handle is
        # missing.
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.PROMPT_STDIN.value,
            reason="prompt_worker_handle_missing",
        )

    session_name = config.get("tmux_session")
    if not session_name or not is_tmux_available():
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.TMUX_SEND_KEYS.value,
            reason="tmux_unavailable",
        )

    worker_cli = worker.get("worker_cli") or None
    pane = pane_id or worker.get("pane_id") or None
    try:
        send_to_worker(
            session_name,
            worker_index,
            message,
            pane,
            worker_cli,
        )
    except BaseException as exc:  # noqa: BLE001 - mirror TS catch(error => ...)
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.TMUX_SEND_KEYS.value,
            reason=f"tmux_send_keys_failed:{exc}",
        )
    return DispatchOutcome(
        ok=True,
        transport=DispatchTransport.TMUX_SEND_KEYS.value,
        reason="tmux_send_keys_sent",
    )


def _notify_leader_outcome(config: dict[str, Any], message: str) -> DispatchOutcome:
    """Direct-transport notify for the leader mailbox.

    Mirrors TS ``notifyLeaderAsync`` (the leader-specific transport
    helper invoked by ``sendLeaderMailboxMessage``). The Python port uses
    ``notify_leader_status`` (tmux ``display-message``) as the on-tmux
    surface.
    """
    session_name = config.get("tmux_session")
    if not session_name or not is_tmux_available():
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.TMUX_SEND_KEYS.value,
            reason="tmux_unavailable",
        )
    try:
        ok = notify_leader_status(session_name, message)
    except BaseException as exc:  # noqa: BLE001
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.TMUX_SEND_KEYS.value,
            reason=f"tmux_send_keys_failed:{exc}",
        )
    if ok:
        return DispatchOutcome(
            ok=True,
            transport=DispatchTransport.TMUX_SEND_KEYS.value,
            reason="tmux_send_keys_sent",
        )
    return DispatchOutcome(
        ok=False,
        transport=DispatchTransport.TMUX_SEND_KEYS.value,
        reason="tmux_send_keys_failed",
    )


def _build_worker_notifier(
    config: dict[str, Any],
    transport_preference: str,
    worker_index: int | None,
    pane_id: str | None,
):
    """Build the per-call notifier wired into ``queue_direct_mailbox_message``.

    Mirrors the TS inline ``notify`` arrow in ``sendRecipientMailboxMessage``:
    hook-preferred preference short-circuits with
    ``queued_for_hook_dispatch``; everything else dispatches directly
    through ``notifyWorkerOutcome``.
    """

    def _notify(
        target: TeamNotifierTarget, message: str, _ctx: dict[str, Any]
    ) -> DispatchOutcome:
        if transport_preference == _HOOK_PREFERRED:
            return DispatchOutcome(
                ok=True,
                transport=DispatchTransport.HOOK.value,
                reason=_QUEUED_FOR_HOOK,
            )
        if worker_index is None:
            return DispatchOutcome(
                ok=False,
                transport=DispatchTransport.NONE.value,
                reason="missing_worker_index",
            )
        return _notify_worker_outcome(config, worker_index, message, pane_id)

    return _notify


def _build_leader_notifier(config: dict[str, Any], transport_preference: str):
    """Build the per-call notifier used by ``sendLeaderMailboxMessage``."""

    def _notify(
        _target: TeamNotifierTarget, message: str, _ctx: dict[str, Any]
    ) -> DispatchOutcome:
        if transport_preference == _HOOK_PREFERRED:
            return DispatchOutcome(
                ok=True,
                transport=DispatchTransport.HOOK.value,
                reason=_QUEUED_FOR_HOOK,
            )
        return _notify_leader_outcome(config, message)

    return _notify


def _build_broadcast_notifier(config: dict[str, Any], transport_preference: str):
    """Notifier wired into ``queue_broadcast_mailbox_message``.

    Mirrors the inline ``notify`` arrow in TS ``broadcastWorkerMessage``:
    hook-preferred preference short-circuits; direct transport routes
    through ``notifyWorkerOutcome``.
    """

    def _notify(
        target: TeamNotifierTarget, message: str, _ctx: dict[str, Any]
    ) -> DispatchOutcome:
        if transport_preference == _HOOK_PREFERRED:
            return DispatchOutcome(
                ok=True,
                transport=DispatchTransport.HOOK.value,
                reason=_QUEUED_FOR_HOOK,
            )
        if target.worker_index is None:
            return DispatchOutcome(
                ok=False,
                transport=DispatchTransport.NONE.value,
                reason="missing_worker_index",
            )
        return _notify_worker_outcome(
            config, target.worker_index, message, target.pane_id
        )

    return _notify


# ---------------------------------------------------------------------------
# Internal: leader / recipient mailbox dispatch entry points.
# ---------------------------------------------------------------------------


def _send_leader_mailbox_message(
    *,
    team_name: str,
    from_worker: str,
    body: str,
    config: dict[str, Any],
    dispatch_policy: TeamPolicy,
    cwd: str,
) -> DispatchOutcome:
    """Mirror TS ``sendLeaderMailboxMessage`` (runtime.ts:4324-4403)."""
    team_state_root = config.get("team_state_root") or ".omx/state"
    trigger = build_leader_mailbox_trigger_directive(
        team_name, from_worker, team_state_root
    )
    transport_preference = _resolve_leader_transport_preference(dispatch_policy)

    queued = queue_direct_mailbox_message(
        QueueDirectMessageParams(
            team_name=team_name,
            from_worker=from_worker,
            to_worker=_LEADER_FIXED,
            body=body,
            trigger_message=trigger.text,
            cwd=cwd,
            notify=_build_leader_notifier(config, transport_preference),
            to_pane_id=config.get("leader_pane_id") or None,
            transport_preference=transport_preference,
            fallback_allowed=(transport_preference == _HOOK_PREFERRED),
        )
    )

    # TS branch: when the leader pane is missing under hook-preferred,
    # restamp the outcome as a soft-persisted success. We replicate that
    # exact rewrite here so downstream consumers see the same shape.
    if (
        not _is_existing_mailbox_notification(queued)
        and transport_preference == _HOOK_PREFERRED
        and not config.get("leader_pane_id")
    ):
        return DispatchOutcome(
            ok=True,
            transport=DispatchTransport.MAILBOX.value,
            reason=_LEADER_PERSISTED,
            request_id=queued.request_id,
            message_id=queued.message_id,
            to_worker=_LEADER_FIXED,
        )
    return queued


def _send_recipient_mailbox_message(
    *,
    team_name: str,
    from_worker: str,
    to_worker: str,
    body: str,
    config: dict[str, Any],
    dispatch_policy: TeamPolicy,
    cwd: str,
) -> DispatchOutcome:
    """Mirror TS ``sendRecipientMailboxMessage`` (runtime.ts:4405-4458)."""
    recipient = _find_worker(config, to_worker)
    if recipient is None:
        raise ValueError(f"Worker {to_worker} not found in team")

    worker_index = _worker_index(recipient)
    pane_id = recipient.get("pane_id") or None
    worktree_path = recipient.get("worktree_path") or None
    team_state_root = _resolve_instruction_state_root(worktree_path)

    trigger = build_mailbox_trigger_directive(
        to_worker,
        team_name,
        1,
        team_state_root,
    )
    transport_preference = _resolve_worker_transport_preference(
        config.get("worker_launch_mode") or TeamWorkerLaunchMode.INTERACTIVE.value,
        dispatch_policy,
    )

    return queue_direct_mailbox_message(
        QueueDirectMessageParams(
            team_name=team_name,
            from_worker=from_worker,
            to_worker=to_worker,
            body=body,
            trigger_message=trigger.text,
            cwd=cwd,
            notify=_build_worker_notifier(
                config, transport_preference, worker_index, pane_id
            ),
            to_worker_index=worker_index,
            to_pane_id=pane_id,
            transport_preference=transport_preference,
            fallback_allowed=(transport_preference == _HOOK_PREFERRED),
        )
    )


def _resolve_instruction_state_root(worktree_path: str | None) -> str:
    """Mirror TS ``resolveInstructionStateRoot`` (runtime.ts:1343-1345).

    The TS helper returns ``WORKTREE_TRIGGER_STATE_ROOT`` when a worktree
    is in play, else ``undefined``. The Python ``build_mailbox_trigger_directive``
    helper accepts a string with a sentinel default of ``".omx/state"``,
    so we mirror the TS behavior by selecting between the worktree state
    root and the default.
    """
    if worktree_path:
        # TS WORKTREE_TRIGGER_STATE_ROOT is ``.omx-worker/state``.
        return ".omx-worker/state"
    return ".omx/state"


def _is_existing_mailbox_notification(outcome: DispatchOutcome) -> bool:
    """Mirror TS ``isExistingMailboxNotificationOutcome``."""
    return outcome.ok and outcome.reason == "existing_message_already_notified"


# ---------------------------------------------------------------------------
# Broadcast finalize helper (mirrors TS finalizeBroadcastMailboxOutcomes).
# ---------------------------------------------------------------------------


def _finalize_broadcast_outcomes(
    *,
    team_name: str,
    outcomes: list[DispatchOutcome],
    transport_preference: str,
    config: dict[str, Any],
    dispatch_policy: TeamPolicy,
    cwd: str,
) -> list[DispatchOutcome]:
    """Mirror TS ``finalizeBroadcastMailboxOutcomes`` (runtime.ts:4460-4505).

    Under hook-preferred preference, TS walks each pending outcome and
    drives ``finalizeQueuedMailboxDispatch`` per recipient. The Python
    queue-layer already wires hook outcomes through to the same terminal
    state, so we only repeat the recipient-missing fallback: any outcome
    whose ``to_worker`` is not present in ``config.workers`` is restamped
    as a ``missing_worker_index`` failure to match TS.
    """
    if transport_preference != _HOOK_PREFERRED:
        return outcomes

    finalized: list[DispatchOutcome] = []
    for outcome in outcomes:
        target_name = outcome.to_worker
        target = _find_worker(config, target_name) if target_name else None
        if target is None:
            finalized.append(
                DispatchOutcome(
                    ok=False,
                    transport=outcome.transport,
                    reason="missing_worker_index",
                    request_id=outcome.request_id,
                    message_id=outcome.message_id,
                    to_worker=target_name,
                )
            )
            continue
        finalized.append(outcome)
    # ``dispatch_policy`` is kept in the signature to preserve TS parity;
    # the simplified Python finalize does not consult it directly.
    _ = dispatch_policy
    _ = team_name
    _ = cwd
    return finalized


# ---------------------------------------------------------------------------
# Public entry points (the actual port targets).
# ---------------------------------------------------------------------------


def send_worker_message(
    team_name: str,
    from_worker: str,
    to_worker: str,
    body: str,
    cwd: str,
) -> DispatchOutcome:
    """Send a direct mailbox message from one worker to another.

    Port of TS ``sendWorkerMessage`` (runtime.ts:4507-4546).

    Args:
        team_name: Team identifier (sanitized before lookup).
        from_worker: Sender worker name. Use ``"leader-fixed"`` from the
            leader pane.
        to_worker: Recipient worker name. ``"leader-fixed"`` routes to
            the leader mailbox via :func:`_send_leader_mailbox_message`.
        body: Message body to enqueue in the recipient's mailbox.
        cwd: Working directory containing ``.omx/team/<team>``.

    Returns:
        The terminal :class:`DispatchOutcome` (mailbox transport on
        leader-pane-missing soft-persist, otherwise whatever the notifier
        emitted).

    Raises:
        ValueError: Team config not found, or recipient worker missing.
        RuntimeError: Notifier failed (``mailbox_notify_failed:<reason>``).
    """
    sanitized = sanitize_team_name(team_name)
    config = team_read_config(cwd, sanitized)
    if not config:
        raise ValueError(f"Team {sanitized} not found")
    manifest = team_read_manifest(sanitized, cwd)
    manifest_policy = (
        manifest.policy
        if manifest is not None and hasattr(manifest, "policy")
        else None
    )
    worker_launch_mode = config.get("worker_launch_mode") or "interactive"
    dispatch_policy = _resolve_dispatch_policy(manifest_policy, worker_launch_mode)

    if to_worker == _LEADER_FIXED:
        final_outcome = _send_leader_mailbox_message(
            team_name=sanitized,
            from_worker=from_worker,
            body=body,
            config=config,
            dispatch_policy=dispatch_policy,
            cwd=cwd,
        )
        if not final_outcome.ok:
            raise RuntimeError(f"mailbox_notify_failed:{final_outcome.reason}")
        return final_outcome

    final_outcome = _send_recipient_mailbox_message(
        team_name=sanitized,
        from_worker=from_worker,
        to_worker=to_worker,
        body=body,
        config=config,
        dispatch_policy=dispatch_policy,
        cwd=cwd,
    )
    if not final_outcome.ok:
        raise RuntimeError(f"mailbox_notify_failed:{final_outcome.reason}")
    return final_outcome


def broadcast_worker_message(
    team_name: str,
    from_worker: str,
    body: str,
    cwd: str,
) -> None:
    """Broadcast a mailbox message to every worker except the sender.

    Port of TS ``broadcastWorkerMessage`` (runtime.ts:4548-4610).

    Args:
        team_name: Team identifier (sanitized before lookup).
        from_worker: Sender worker name; excluded from the recipient set
            by :func:`omx.team.state.mailbox.broadcast_message`.
        body: Message body to fan out.
        cwd: Working directory containing ``.omx/team/<team>``.

    Raises:
        ValueError: Team config not found.
        RuntimeError: One or more notifier dispatches failed
            (``mailbox_notify_failed:<reason>`` from the first failure).
    """
    sanitized = sanitize_team_name(team_name)
    config = team_read_config(cwd, sanitized)
    if not config:
        raise ValueError(f"Team {sanitized} not found")
    manifest = team_read_manifest(sanitized, cwd)
    manifest_policy = (
        manifest.policy
        if manifest is not None and hasattr(manifest, "policy")
        else None
    )
    worker_launch_mode = config.get("worker_launch_mode") or "interactive"
    dispatch_policy = _resolve_dispatch_policy(manifest_policy, worker_launch_mode)
    transport_preference = _resolve_worker_transport_preference(
        worker_launch_mode, dispatch_policy
    )

    recipients: list[BroadcastRecipient] = []
    for w in config.get("workers") or []:
        if not isinstance(w, dict):
            continue
        name = w.get("name")
        if not isinstance(name, str) or not name:
            continue
        idx = _worker_index(w)
        if idx is None:
            continue
        recipients.append(
            BroadcastRecipient(
                worker_name=name,
                worker_index=idx,
                pane_id=w.get("pane_id") or None,
            )
        )

    def _trigger_for(worker_name: str) -> str:
        worker = _find_worker(config, worker_name)
        worktree_path = worker.get("worktree_path") if worker else None
        return build_mailbox_trigger_directive(
            worker_name,
            sanitized,
            1,
            _resolve_instruction_state_root(worktree_path),
        ).text

    outcomes = queue_broadcast_mailbox_message(
        QueueBroadcastParams(
            team_name=sanitized,
            from_worker=from_worker,
            recipients=recipients,
            body=body,
            cwd=cwd,
            trigger_for=_trigger_for,
            notify=_build_broadcast_notifier(config, transport_preference),
            transport_preference=transport_preference,
            fallback_allowed=(transport_preference == _HOOK_PREFERRED),
        )
    )
    results = _finalize_broadcast_outcomes(
        team_name=sanitized,
        outcomes=outcomes,
        transport_preference=transport_preference,
        config=config,
        dispatch_policy=dispatch_policy,
        cwd=cwd,
    )

    failures = [r for r in results if not r.ok]
    if failures:
        first = failures[0]
        raise RuntimeError(f"mailbox_notify_failed:{first.reason}")
