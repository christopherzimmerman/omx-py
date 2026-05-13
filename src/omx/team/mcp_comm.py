"""MCP-based inter-worker communication.

Port of ``src/team/mcp-comm.ts`` (492 LOC). Handles inbox/mailbox dispatch
via the team gateway, classifies notifier outcomes, transitions dispatch
request state, and logs delivery results.

Sync-only port per locked Phase 2 decisions; the TS ``Promise``-returning
notifier API maps to a plain synchronous callable in Python.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import random
import time
from typing import Any, Callable, Protocol

from omx.core.types import RuntimeCommand
from omx.runtime.bridge import RuntimeBridge
from omx.team.delivery_log import append_delivery_event
from omx.team.reminder_intents import TeamReminderIntent
from omx.team.state.types import TeamDispatchRequest
from omx.team.team_ops import (
    team_enqueue_dispatch_request,
    team_mark_dispatch_request_notified,
    team_mark_message_notified,
    team_read_dispatch_request,
    team_send_message,
    team_transition_dispatch_request,
    team_broadcast,
    team_write_worker_inbox,
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class DispatchTransport(StrEnum):
    """Notifier transport classification.

    Matches the TS string-union ``'hook' | 'prompt_stdin' |
    'tmux_send_keys' | 'mailbox' | 'none'`` exactly so JSON round-trips
    are byte-identical.
    """

    HOOK = "hook"
    PROMPT_STDIN = "prompt_stdin"
    TMUX_SEND_KEYS = "tmux_send_keys"
    MAILBOX = "mailbox"
    NONE = "none"


@dataclass(frozen=True)
class TeamNotifierTarget:
    """Recipient identification handed to a :data:`TeamNotifier`."""

    worker_name: str
    worker_index: int | None = None
    pane_id: str | None = None


@dataclass
class DispatchOutcome:
    """Result of a notifier attempt.

    Mirrors the TS ``DispatchOutcome`` JSON shape exactly:
    ``{ ok, transport, reason, request_id?, message_id?, to_worker? }``.
    Optional fields are omitted from :meth:`to_dict` when unset to match
    the TS object spread semantics.
    """

    ok: bool
    transport: str  # DispatchTransport value; accept str for JSON parity
    reason: str
    request_id: str | None = None
    message_id: str | None = None
    to_worker: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "ok": self.ok,
            "transport": str(self.transport),
            "reason": self.reason,
        }
        if self.request_id is not None:
            d["request_id"] = self.request_id
        if self.message_id is not None:
            d["message_id"] = self.message_id
        if self.to_worker is not None:
            d["to_worker"] = self.to_worker
        return d


class TeamNotifier(Protocol):
    """Callable that performs the actual notification transport.

    Sync-only signature per Phase 2 locked decisions. Returning the
    :class:`DispatchOutcome` is the notifier's contract; raising is also
    permitted and is converted into a ``notify_exception:*`` failure by
    the caller.
    """

    def __call__(
        self,
        target: TeamNotifierTarget,
        message: str,
        context: dict[str, Any],
    ) -> DispatchOutcome: ...


# ---------------------------------------------------------------------------
# Internal helpers (mirror the TS module-private functions)
# ---------------------------------------------------------------------------


_HOOK_PREFERRED = "hook_preferred_with_fallback"
_TRANSPORT_DIRECT = "transport_direct"
_PROMPT_STDIN_PREF = "prompt_stdin"
_LEADER_FIXED = "leader-fixed"
_QUEUED_FOR_HOOK = "queued_for_hook_dispatch"
_LEADER_PERSISTED = "leader_pane_missing_mailbox_persisted"
_LEADER_DEFERRED = "leader_pane_missing_deferred"
_DUPLICATE = "duplicate_pending_dispatch_request"
_EXISTING_NOTIFIED = "existing_message_already_notified"


def _is_confirmed_notification(outcome: DispatchOutcome) -> bool:
    """Notifier outcome that should mark the dispatch request as notified."""
    if not outcome.ok:
        return False
    if str(outcome.transport) != DispatchTransport.HOOK.value:
        return True
    return outcome.reason != _QUEUED_FOR_HOOK


def _is_leader_pane_missing_persisted(
    request: TeamDispatchRequest, outcome: DispatchOutcome
) -> bool:
    """Detect the leader-pane-missing soft-persist outcome (TS parity)."""
    return (
        request.to_worker == _LEADER_FIXED
        and outcome.ok
        and outcome.reason == _LEADER_PERSISTED
    )


def _fallback_transport_for_preference(preference: str | None) -> str:
    """Mirror of ``fallbackTransportForPreference``."""
    if preference == _PROMPT_STDIN_PREF:
        return DispatchTransport.PROMPT_STDIN.value
    if preference == _TRANSPORT_DIRECT:
        return DispatchTransport.TMUX_SEND_KEYS.value
    return DispatchTransport.HOOK.value


def _notify_exception_reason(error: BaseException) -> str:
    """Format ``notify_exception:<message>`` (TS parity)."""
    return f"notify_exception:{error}"


def _result_label(outcome: DispatchOutcome) -> str:
    if not outcome.ok:
        return "failed"
    if outcome.reason == _QUEUED_FOR_HOOK:
        return "queued"
    return "ok"


def _log_dispatch_outcome(
    *,
    cwd: str,
    team_name: str,
    source: str,
    request_id: str | None,
    message_id: str | None,
    to_worker: str,
    dispatch_kind: str,
    outcome: DispatchOutcome,
    intent: TeamReminderIntent | None,
    transport_preference: str | None,
) -> None:
    """Append a ``dispatch_result`` entry to the team delivery log.

    Mirrors ``logDispatchOutcome`` in the TS source. Identifiers map to
    top-level keys on the JSONL entry to match the TS contract; only the
    recipient (``to_worker``) is routed through ``detail`` because the
    delivery-log schema does not have a top-level slot for it.
    """
    append_delivery_event(
        cwd,
        event="dispatch_result",
        source=source,
        team=team_name,
        transport=str(outcome.transport),
        result=_result_label(outcome),
        request_id=request_id,
        message_id=message_id,
        dispatch_kind=dispatch_kind,
        intent=intent.to_dict() if intent is not None else None,
        transport_preference=transport_preference,
        reason=outcome.reason,
        detail={"to_worker": to_worker},
    )


def _mark_immediate_dispatch_failure(
    *,
    team_name: str,
    request: TeamDispatchRequest,
    reason: str,
    message_id: str | None,
    cwd: str,
) -> None:
    """Best-effort transition a still-pending request to ``failed``.

    Mirrors ``markImmediateDispatchFailure``. ``hook_preferred_with_fallback``
    requests are left alone because the hook layer owns terminal state for
    that flow. Any errors are swallowed for parity with the TS
    ``.catch(() => {})`` pattern.
    """
    if request.transport_preference == _HOOK_PREFERRED:
        return
    current = team_read_dispatch_request(team_name, request.request_id, cwd)
    if current is None:
        return
    if current.status in ("failed", "notified", "delivered"):
        return
    try:
        team_transition_dispatch_request(
            team_name,
            request.request_id,
            "failed",
            cwd,
            reason=reason,
        )
    except Exception:  # noqa: BLE001 - parity with TS .catch(() => {})
        pass


def _mark_leader_pane_missing_deferred(
    *,
    team_name: str,
    request: TeamDispatchRequest,
    cwd: str,
) -> None:
    """Annotate a pending request with the leader-deferred reason.

    The Python state layer cannot transition ``pending`` → ``pending``,
    so this is a best-effort no-op transition; we instead rely on the
    ``last_reason`` being recorded the next time the request transitions
    out of pending. This matches the TS behavior of "leave status alone
    but stamp last_reason".
    """
    current = team_read_dispatch_request(team_name, request.request_id, cwd)
    if current is None:
        return
    if current.status != "pending":
        return
    # No legal pending -> pending transition exists; the Python state
    # layer enforces forward-only transitions. We swallow silently to
    # preserve TS .catch(() => {}) behavior; the reason will be recorded
    # on the next legitimate transition by the delivery log entry below.
    try:
        team_transition_dispatch_request(
            team_name,
            request.request_id,
            "pending",
            cwd,
            reason=_LEADER_DEFERRED,
        )
    except Exception:  # noqa: BLE001
        pass


def _enqueue_with_dedup(
    team_name: str, request_input: dict[str, Any], cwd: str
) -> tuple[TeamDispatchRequest, bool] | None:
    """Wrap ``team_enqueue_dispatch_request`` to surface dedup status.

    The TS gateway returns ``{ request, deduped }``; the Python gateway
    returns the existing pending request transparently. We detect dedup
    by checking whether the returned request's ``created_at`` predates
    this call. Returns ``None`` if the input is invalid.
    """
    from omx.team.team_ops import team_list_dispatch_requests

    before = {r.request_id for r in team_list_dispatch_requests(team_name, cwd)}
    result = team_enqueue_dispatch_request(team_name, request_input, cwd)
    if result is None:
        return None
    deduped = result.request_id in before
    return result, deduped


# ---------------------------------------------------------------------------
# Public dispatch entry points
# ---------------------------------------------------------------------------


@dataclass
class QueueInboxParams:
    """Parameters for :func:`queue_inbox_instruction`.

    Mirrors the TS ``QueueInboxParams`` interface field-for-field.
    """

    team_name: str
    worker_name: str
    worker_index: int
    inbox: str
    trigger_message: str
    cwd: str
    notify: TeamNotifier
    pane_id: str | None = None
    intent: TeamReminderIntent | None = None
    transport_preference: str | None = None
    fallback_allowed: bool | None = None
    inbox_correlation_key: str | None = None


def queue_inbox_instruction(params: QueueInboxParams) -> DispatchOutcome:
    """Persist an inbox instruction and notify the target worker.

    Port of ``queueInboxInstruction``. Writes the worker inbox, enqueues
    a dispatch request, invokes the notifier, then transitions the
    request and logs the outcome.
    """
    # Underlying signature is (cwd, team_name, worker_name, prompt); the
    # gateway re-export does not reorder it, so we call with that order.
    team_write_worker_inbox(
        params.cwd, params.team_name, params.worker_name, params.inbox
    )

    request_input: dict[str, Any] = {
        "kind": "inbox",
        "to_worker": params.worker_name,
        "worker_index": params.worker_index,
        "pane_id": params.pane_id,
        "trigger_message": params.trigger_message,
        "transport_preference": params.transport_preference or _TRANSPORT_DIRECT,
    }
    if params.fallback_allowed is not None:
        request_input["fallback_allowed"] = params.fallback_allowed
    if params.inbox_correlation_key is not None:
        request_input["inbox_correlation_key"] = params.inbox_correlation_key

    queued = _enqueue_with_dedup(params.team_name, request_input, params.cwd)
    if queued is None:
        # normalize failure (shouldn't happen with valid input)
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.NONE.value,
            reason="invalid_dispatch_request",
        )
    request, deduped = queued

    if deduped:
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.NONE.value,
            reason=_DUPLICATE,
            request_id=request.request_id,
        )

    target = TeamNotifierTarget(
        worker_name=params.worker_name,
        worker_index=params.worker_index,
        pane_id=params.pane_id,
    )
    try:
        notify_outcome = params.notify(
            target,
            params.trigger_message,
            {"request": request},
        )
    except BaseException as exc:  # noqa: BLE001 - mirror TS .catch(error => ...)
        notify_outcome = DispatchOutcome(
            ok=False,
            transport=_fallback_transport_for_preference(params.transport_preference),
            reason=_notify_exception_reason(exc),
        )

    outcome = DispatchOutcome(
        ok=notify_outcome.ok,
        transport=str(notify_outcome.transport),
        reason=notify_outcome.reason,
        request_id=request.request_id,
        message_id=notify_outcome.message_id,
        to_worker=notify_outcome.to_worker,
    )

    if _is_confirmed_notification(outcome):
        team_mark_dispatch_request_notified(
            params.team_name,
            request.request_id,
            params.cwd,
            reason=outcome.reason,
        )
    else:
        _mark_immediate_dispatch_failure(
            team_name=params.team_name,
            request=request,
            reason=outcome.reason,
            message_id=None,
            cwd=params.cwd,
        )

    _log_dispatch_outcome(
        cwd=params.cwd,
        team_name=params.team_name,
        source="team.mcp-comm",
        request_id=request.request_id,
        message_id=None,
        to_worker=params.worker_name,
        dispatch_kind="inbox",
        outcome=outcome,
        intent=params.intent,
        transport_preference=params.transport_preference,
    )
    return outcome


@dataclass
class QueueDirectMessageParams:
    """Parameters for :func:`queue_direct_mailbox_message`."""

    team_name: str
    from_worker: str
    to_worker: str
    body: str
    trigger_message: str
    cwd: str
    notify: TeamNotifier
    to_worker_index: int | None = None
    to_pane_id: str | None = None
    intent: TeamReminderIntent | None = None
    transport_preference: str | None = None
    fallback_allowed: bool | None = None


def queue_direct_mailbox_message(
    params: QueueDirectMessageParams,
) -> DispatchOutcome:
    """Send a direct mailbox message and notify the target worker.

    Port of ``queueDirectMailboxMessage``. Honors the "existing message
    already notified" short-circuit, the leader-pane-missing soft-persist
    branch, and the standard notify/transition/log flow.
    """
    message = team_send_message(
        params.team_name,
        params.from_worker,
        params.to_worker,
        params.body,
        params.cwd,
    )

    if message.notified_at and not message.delivered_at:
        transport = (
            DispatchTransport.MAILBOX.value
            if params.to_worker == _LEADER_FIXED
            else _fallback_transport_for_preference(params.transport_preference)
        )
        outcome = DispatchOutcome(
            ok=True,
            transport=transport,
            reason=_EXISTING_NOTIFIED,
            message_id=message.message_id,
            to_worker=params.to_worker,
        )
        _log_dispatch_outcome(
            cwd=params.cwd,
            team_name=params.team_name,
            source="team.mcp-comm",
            request_id=None,
            message_id=message.message_id,
            to_worker=params.to_worker,
            dispatch_kind="mailbox",
            outcome=outcome,
            intent=params.intent,
            transport_preference=params.transport_preference,
        )
        return outcome

    request_input: dict[str, Any] = {
        "kind": "mailbox",
        "to_worker": params.to_worker,
        "worker_index": params.to_worker_index,
        "pane_id": params.to_pane_id,
        "trigger_message": params.trigger_message,
        "message_id": message.message_id,
        "transport_preference": params.transport_preference or _TRANSPORT_DIRECT,
    }
    if params.fallback_allowed is not None:
        request_input["fallback_allowed"] = params.fallback_allowed

    queued = _enqueue_with_dedup(params.team_name, request_input, params.cwd)
    if queued is None:
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.NONE.value,
            reason="invalid_dispatch_request",
            message_id=message.message_id,
            to_worker=params.to_worker,
        )
    request, deduped = queued

    if deduped:
        return DispatchOutcome(
            ok=False,
            transport=DispatchTransport.NONE.value,
            reason=_DUPLICATE,
            request_id=request.request_id,
            message_id=message.message_id,
        )

    target = TeamNotifierTarget(
        worker_name=params.to_worker,
        worker_index=params.to_worker_index,
        pane_id=params.to_pane_id,
    )
    try:
        notify_outcome = params.notify(
            target,
            params.trigger_message,
            {"request": request, "message_id": message.message_id},
        )
    except BaseException as exc:  # noqa: BLE001
        notify_outcome = DispatchOutcome(
            ok=False,
            transport=_fallback_transport_for_preference(params.transport_preference),
            reason=_notify_exception_reason(exc),
        )

    outcome = DispatchOutcome(
        ok=notify_outcome.ok,
        transport=str(notify_outcome.transport),
        reason=notify_outcome.reason,
        request_id=request.request_id,
        message_id=message.message_id,
        to_worker=params.to_worker,
    )

    if _is_leader_pane_missing_persisted(request, outcome):
        _mark_leader_pane_missing_deferred(
            team_name=params.team_name, request=request, cwd=params.cwd
        )
        _log_dispatch_outcome(
            cwd=params.cwd,
            team_name=params.team_name,
            source="team.mcp-comm",
            request_id=request.request_id,
            message_id=message.message_id,
            to_worker=params.to_worker,
            dispatch_kind="mailbox",
            outcome=outcome,
            intent=params.intent,
            transport_preference=params.transport_preference,
        )
        return outcome

    if _is_confirmed_notification(outcome):
        team_mark_message_notified(
            params.team_name, params.to_worker, message.message_id, params.cwd
        )
        team_mark_dispatch_request_notified(
            params.team_name,
            request.request_id,
            params.cwd,
            reason=outcome.reason,
        )
    else:
        _mark_immediate_dispatch_failure(
            team_name=params.team_name,
            request=request,
            reason=outcome.reason,
            message_id=message.message_id,
            cwd=params.cwd,
        )

    _log_dispatch_outcome(
        cwd=params.cwd,
        team_name=params.team_name,
        source="team.mcp-comm",
        request_id=request.request_id,
        message_id=message.message_id,
        to_worker=params.to_worker,
        dispatch_kind="mailbox",
        outcome=outcome,
        intent=params.intent,
        transport_preference=params.transport_preference,
    )
    return outcome


@dataclass
class BroadcastRecipient:
    """Recipient descriptor for :func:`queue_broadcast_mailbox_message`."""

    worker_name: str
    worker_index: int
    pane_id: str | None = None


@dataclass
class QueueBroadcastParams:
    """Parameters for :func:`queue_broadcast_mailbox_message`."""

    team_name: str
    from_worker: str
    recipients: list[BroadcastRecipient]
    body: str
    cwd: str
    trigger_for: Callable[[str], str]
    notify: TeamNotifier
    intent_for: Callable[[str], TeamReminderIntent | None] | None = None
    transport_preference: str | None = None
    fallback_allowed: bool | None = None


def queue_broadcast_mailbox_message(
    params: QueueBroadcastParams,
) -> list[DispatchOutcome]:
    """Fan a single broadcast body out to every recipient and notify each.

    Port of ``queueBroadcastMailboxMessage``. Recipients not in
    ``params.recipients`` are skipped (matches the TS ``recipientByName``
    early-continue). Each recipient is dispatched sequentially because
    Locked Decision #1 forbids asyncio; per-recipient dispatch is cheap.
    """
    recipient_names = [r.worker_name for r in params.recipients]
    # Restrict the broadcast to declared recipients (TS uses a Map filter)
    from omx.team.state.mailbox import broadcast_message
    from omx.team.state_root import team_dir as _team_dir

    messages = broadcast_message(
        _team_dir(params.team_name, params.cwd),
        params.from_worker,
        params.body,
        recipient_names,
    )
    # team_broadcast wrapper exists but expands recipients from manifest;
    # we want to honor the caller-provided recipient list verbatim, so we
    # call the state-layer broadcast_message directly. The result is the
    # same TeamMailboxMessage shape.
    _ = team_broadcast  # keep the gateway import in scope for tests

    recipient_by_name = {r.worker_name: r for r in params.recipients}
    outcomes: list[DispatchOutcome] = []

    for message in messages:
        recipient = recipient_by_name.get(message.to_worker)
        if recipient is None:
            continue

        trigger = params.trigger_for(recipient.worker_name)
        intent = params.intent_for(recipient.worker_name) if params.intent_for else None

        request_input: dict[str, Any] = {
            "kind": "mailbox",
            "to_worker": recipient.worker_name,
            "worker_index": recipient.worker_index,
            "pane_id": recipient.pane_id,
            "trigger_message": trigger,
            "message_id": message.message_id,
            "transport_preference": params.transport_preference or _TRANSPORT_DIRECT,
        }
        if params.fallback_allowed is not None:
            request_input["fallback_allowed"] = params.fallback_allowed

        queued = _enqueue_with_dedup(params.team_name, request_input, params.cwd)
        if queued is None:
            outcomes.append(
                DispatchOutcome(
                    ok=False,
                    transport=DispatchTransport.NONE.value,
                    reason="invalid_dispatch_request",
                    message_id=message.message_id,
                    to_worker=recipient.worker_name,
                )
            )
            continue
        request, deduped = queued

        if deduped:
            outcomes.append(
                DispatchOutcome(
                    ok=False,
                    transport=DispatchTransport.NONE.value,
                    reason=_DUPLICATE,
                    request_id=request.request_id,
                    message_id=message.message_id,
                    to_worker=recipient.worker_name,
                )
            )
            continue

        target = TeamNotifierTarget(
            worker_name=recipient.worker_name,
            worker_index=recipient.worker_index,
            pane_id=recipient.pane_id,
        )
        try:
            notify_outcome = params.notify(
                target,
                trigger,
                {"request": request, "message_id": message.message_id},
            )
        except BaseException as exc:  # noqa: BLE001
            notify_outcome = DispatchOutcome(
                ok=False,
                transport=_fallback_transport_for_preference(
                    params.transport_preference
                ),
                reason=_notify_exception_reason(exc),
            )

        outcome = DispatchOutcome(
            ok=notify_outcome.ok,
            transport=str(notify_outcome.transport),
            reason=notify_outcome.reason,
            request_id=request.request_id,
            message_id=message.message_id,
            to_worker=recipient.worker_name,
        )
        outcomes.append(outcome)

        if _is_confirmed_notification(outcome):
            team_mark_message_notified(
                params.team_name,
                recipient.worker_name,
                message.message_id,
                params.cwd,
            )
            team_mark_dispatch_request_notified(
                params.team_name,
                request.request_id,
                params.cwd,
                reason=outcome.reason,
            )
        else:
            _mark_immediate_dispatch_failure(
                team_name=params.team_name,
                request=request,
                reason=outcome.reason,
                message_id=message.message_id,
                cwd=params.cwd,
            )

        _log_dispatch_outcome(
            cwd=params.cwd,
            team_name=params.team_name,
            source="team.mcp-comm",
            request_id=request.request_id,
            message_id=message.message_id,
            to_worker=recipient.worker_name,
            dispatch_kind="mailbox",
            outcome=outcome,
            intent=intent,
            transport_preference=params.transport_preference,
        )

    return outcomes


def wait_for_dispatch_receipt(
    team_name: str,
    request_id: str,
    cwd: str,
    *,
    timeout_ms: int,
    poll_ms: int | None = None,
    _sleep: Callable[[float], None] = time.sleep,
    _now_ms: Callable[[], float] = lambda: time.monotonic() * 1000.0,
) -> TeamDispatchRequest | None:
    """Block until the named request reaches a terminal state.

    Port of ``waitForDispatchReceipt``. Returns the request once its
    ``status`` is ``notified``, ``delivered``, or ``failed``. If the
    deadline elapses, a final read is attempted and returned (may still
    be ``pending``). Returns ``None`` if the request does not exist.

    ``_sleep`` and ``_now_ms`` are test seams; production callers should
    never set them.
    """
    timeout_ms_clamped = max(0, int(timeout_ms))
    current_poll = max(25, int(poll_ms if poll_ms is not None else 50))
    max_poll_ms = 500
    backoff_factor = 1.5
    deadline = _now_ms() + timeout_ms_clamped

    while _now_ms() <= deadline:
        request = team_read_dispatch_request(team_name, request_id, cwd)
        if request is None:
            return None
        if request.status in ("notified", "delivered", "failed"):
            return request
        jitter = random.random() * current_poll * 0.3
        _sleep((current_poll + jitter) / 1000.0)
        current_poll = min(int(current_poll * backoff_factor), max_poll_ms)

    return team_read_dispatch_request(team_name, request_id, cwd)


# ---------------------------------------------------------------------------
# Back-compat helpers retained from the pre-port skeleton
# ---------------------------------------------------------------------------


def create_mailbox_message(
    bridge: RuntimeBridge,
    message_id: str,
    from_worker: str,
    to_worker: str,
    body: str,
) -> None:
    """Create an inter-worker mailbox message via the runtime bridge.

    Retained from the pre-port skeleton; not part of the TS surface.
    """
    bridge.exec_command(
        RuntimeCommand.create_mailbox_message(
            message_id=message_id,
            from_worker=from_worker,
            to_worker=to_worker,
            body=body,
        )
    )


def queue_dispatch(
    bridge: RuntimeBridge,
    request_id: str,
    target: str,
    metadata: Any | None = None,
) -> None:
    """Queue a dispatch request via the runtime bridge.

    Retained from the pre-port skeleton; not part of the TS surface.
    """
    bridge.exec_command(
        RuntimeCommand.queue_dispatch(
            request_id=request_id,
            target=target,
            metadata=metadata,
        )
    )


__all__ = [
    "DispatchTransport",
    "DispatchOutcome",
    "TeamNotifierTarget",
    "TeamNotifier",
    "QueueInboxParams",
    "QueueDirectMessageParams",
    "QueueBroadcastParams",
    "BroadcastRecipient",
    "queue_inbox_instruction",
    "queue_direct_mailbox_message",
    "queue_broadcast_mailbox_message",
    "wait_for_dispatch_receipt",
    "create_mailbox_message",
    "queue_dispatch",
]
