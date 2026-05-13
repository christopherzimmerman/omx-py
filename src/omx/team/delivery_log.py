"""Team delivery event logging.

Port of src/team/delivery-log.ts.

The TS ``appendTeamDeliveryLogForCwd`` accepts an open-shape event whose
core keys (``event``, ``source``, ``team``, ``transport``, ``result``) sit
at the top level of the JSONL entry, with caller-supplied identifiers
(``request_id``, ``message_id``, ``dispatch_kind``, ``intent``,
``transport_preference``, ``reason``) also at the top level. The Python
port mirrors that shape: TS-contract fields are explicit top-level keys,
and ``detail`` remains available as a free-form bag for any additional
metadata callers want to attach.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.utils.paths import omx_logs_dir


def _normalize_transport(transport: str | None) -> str | None:
    """Normalize transport to the on-disk shorthand used by the TS log.

    Mirrors the TS ``normalizeTransport`` helper:
    ``tmux_send_keys`` → ``send-keys``; ``prompt_stdin`` → ``prompt-stdin``;
    everything else passes through unchanged. ``None`` stays ``None`` so
    callers can omit the field.
    """
    if transport is None:
        return None
    if transport == "tmux_send_keys":
        return "send-keys"
    if transport == "prompt_stdin":
        return "prompt-stdin"
    return transport


def append_delivery_event(
    cwd: str,
    event: str,
    *,
    source: str = "",
    team: str = "",
    transport: str | None = "send-keys",
    result: str | None = "ok",
    request_id: str | None = None,
    message_id: str | None = None,
    dispatch_kind: str | None = None,
    intent: Any | None = None,
    transport_preference: str | None = None,
    reason: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Append a delivery event to the daily team delivery JSONL log.

    Top-level keys match TS ``appendTeamDeliveryLogForCwd``: ``event``,
    ``source``, ``team``, ``transport``, ``result``, ``request_id``,
    ``message_id``, ``dispatch_kind``, ``intent``, ``transport_preference``,
    and ``reason``. ``detail`` is retained for arbitrary extras callers
    want to attach without widening the schema again.

    Args:
        cwd: Working directory for log resolution.
        event: Event name (e.g. ``dispatch_result``, ``mark_delivered``).
        source: Source identifier for the event.
        team: Team session name.
        transport: Delivery transport used; normalized via
            :func:`_normalize_transport` to TS shorthand. Pass ``None`` to
            omit the field entirely.
        result: Outcome string (default ``"ok"``); pass ``None`` to omit.
        request_id: Optional dispatch request id (top-level).
        message_id: Optional mailbox message id (top-level).
        dispatch_kind: Optional dispatch kind (``inbox``/``mailbox``).
        intent: Optional intent payload (any JSON-serializable shape).
        transport_preference: Optional transport-preference string.
        reason: Optional outcome reason string.
        detail: Optional additional free-form metadata.
    """
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = omx_logs_dir(Path(cwd)) / f"team-delivery-{date}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "kind": "team_delivery",
        "event": event,
        "source": source,
        "team": team,
    }
    normalized_transport = _normalize_transport(transport)
    if normalized_transport is not None:
        entry["transport"] = normalized_transport
    if result is not None:
        entry["result"] = result
    if request_id is not None:
        entry["request_id"] = request_id
    if message_id is not None:
        entry["message_id"] = message_id
    if dispatch_kind is not None:
        entry["dispatch_kind"] = dispatch_kind
    if intent is not None:
        entry["intent"] = intent
    if transport_preference is not None:
        entry["transport_preference"] = transport_preference
    if reason is not None:
        entry["reason"] = reason
    if detail:
        entry["detail"] = detail

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
