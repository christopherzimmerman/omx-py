"""MCP-aligned gateway for team operations.

Port of ``src/team/api-interop.ts``. This module is the canonical envelope
for the team API: it accepts a :data:`TeamApiOperation` name, validates the
input, dispatches to the matching gateway function in :mod:`omx.team.team_ops`,
and returns a typed envelope shape.

Locked decisions:
- Sync only (TS ``async``/``await`` collapsed to synchronous calls).
- Stdlib only.

Where the TS implementation depends on helpers that have not yet been ported
to Python (notably ``sendWorkerMessage`` / ``shutdownTeam`` in Phase 2.9 and
``readLatestTeamProgressEvidenceMs``), the corresponding operations return a
deterministic ``not_implemented_yet`` error envelope rather than crashing.
These branches are flagged in PARITY.md.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.team import team_ops
from omx.team.contracts import TEAM_EVENT_TYPES

# --- Regex parity with src/team/contracts.ts -------------------------------

TEAM_NAME_SAFE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")
WORKER_NAME_SAFE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
TASK_ID_SAFE_PATTERN = re.compile(r"^\d{1,20}$")

# --- TS constants ----------------------------------------------------------

TEAM_TASK_STATUSES: tuple[str, ...] = (
    "pending",
    "blocked",
    "in_progress",
    "completed",
    "failed",
)

TEAM_TASK_APPROVAL_STATUSES: tuple[str, ...] = ("pending", "approved", "rejected")

LEGACY_TEAM_MCP_TOOLS: tuple[str, ...] = (
    "team_send_message",
    "team_broadcast",
    "team_mailbox_list",
    "team_mailbox_mark_delivered",
    "team_mailbox_mark_notified",
    "team_create_task",
    "team_read_task",
    "team_list_tasks",
    "team_update_task",
    "team_claim_task",
    "team_transition_task_status",
    "team_release_task_claim",
    "team_read_config",
    "team_read_manifest",
    "team_read_worker_status",
    "team_read_worker_heartbeat",
    "team_update_worker_heartbeat",
    "team_write_worker_inbox",
    "team_write_worker_identity",
    "team_append_event",
    "team_get_summary",
    "team_cleanup",
    "team_orphan_cleanup",
    "team_write_shutdown_request",
    "team_read_shutdown_ack",
    "team_read_monitor_snapshot",
    "team_write_monitor_snapshot",
    "team_read_task_approval",
    "team_write_task_approval",
)

TEAM_API_OPERATIONS: tuple[str, ...] = (
    "send-message",
    "broadcast",
    "mailbox-list",
    "mailbox-mark-delivered",
    "mailbox-mark-notified",
    "create-task",
    "read-task",
    "list-tasks",
    "update-task",
    "claim-task",
    "transition-task-status",
    "release-task-claim",
    "read-config",
    "read-manifest",
    "read-worker-status",
    "read-worker-heartbeat",
    "update-worker-heartbeat",
    "write-worker-inbox",
    "write-worker-identity",
    "append-event",
    "read-events",
    "await-event",
    "read-idle-state",
    "read-stall-state",
    "get-summary",
    "cleanup",
    "orphan-cleanup",
    "write-shutdown-request",
    "read-shutdown-ack",
    "read-monitor-snapshot",
    "write-monitor-snapshot",
    "read-task-approval",
    "write-task-approval",
)

# ``TeamApiOperation`` is a string alias matching one of TEAM_API_OPERATIONS.
TeamApiOperation = str

# ``TeamApiEnvelope`` is one of:
#   {"ok": True,  "operation": <op>,           "data":  dict}
#   {"ok": False, "operation": <op>|"unknown", "error": {"code": str, "message": str}}
TeamApiEnvelope = dict[str, Any]

TEAM_STATE_EVENT_WINDOW = 50

_TEAM_UPDATE_TASK_MUTABLE_FIELDS = {
    "subject",
    "description",
    "blocked_by",
    "requires_code_change",
}
_TEAM_UPDATE_TASK_REQUEST_FIELDS = {
    "team_name",
    "task_id",
    "workingDirectory",
    *_TEAM_UPDATE_TASK_MUTABLE_FIELDS,
}


# --- Helpers ---------------------------------------------------------------


def _ok(operation: str, data: dict[str, Any]) -> TeamApiEnvelope:
    return {"ok": True, "operation": operation, "data": data}


def _err(operation: str, code: str, message: str) -> TeamApiEnvelope:
    return {
        "ok": False,
        "operation": operation,
        "error": {"code": code, "message": message},
    }


def _is_finite_integer(value: Any) -> bool:
    """TS parity for ``Number.isInteger && Number.isFinite``.

    Booleans are intentionally rejected (TS ``typeof === 'number'`` excludes them).
    """
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_optional_non_negative_integer(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if not _is_finite_integer(value) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer when provided")
    return int(value)


def _parse_optional_boolean(value: Any, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean when provided")
    return value


def _parse_optional_event_type(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("type must be a string when provided")
    normalized = value.strip()
    if not normalized:
        raise ValueError("type cannot be empty when provided")
    if normalized not in TEAM_EVENT_TYPES:
        joined = ", ".join(TEAM_EVENT_TYPES)
        raise ValueError(f"type must be one of: {joined}")
    return normalized


def _parse_optional_metadata(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object when provided")
    return dict(value)


def _parse_validated_task_id_array(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array of task IDs (strings)")
    task_ids: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field_name} entries must be strings")
        normalized = item.strip()
        if not TASK_ID_SAFE_PATTERN.match(normalized):
            raise ValueError(f'{field_name} contains invalid task ID: "{item}"')
        task_ids.append(normalized)
    return task_ids


def _str_field(args: dict[str, Any], key: str) -> str:
    raw = args.get(key)
    return str(raw).strip() if raw is not None else ""


def _opt_str_trim(args: dict[str, Any], key: str) -> str:
    """Return ``args[key].strip()`` only when the value is a string."""
    raw = args.get(key)
    return raw.strip() if isinstance(raw, str) else ""


def _team_state_exists(team_name: str, candidate_cwd: str) -> bool:
    if not TEAM_NAME_SAFE_PATTERN.match(team_name):
        return False
    team_root = Path(candidate_cwd) / ".omx" / "state" / "team" / team_name
    return (
        (team_root / "config.json").exists()
        or (team_root / "tasks").exists()
        or team_root.exists()
    )


def _read_team_state_root_from_manifest(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = parsed.get("team_state_root") if isinstance(parsed, dict) else None
    if isinstance(raw, str):
        trimmed = raw.strip()
        if trimmed:
            return trimmed
    return None


def _state_root_to_working_directory(state_root: str) -> str:
    absolute = Path(state_root).resolve()
    return str(absolute.parent.parent)


def _resolve_team_working_directory_from_metadata(
    team_name: str, candidate_cwd: str
) -> str | None:
    team_root = Path(candidate_cwd) / ".omx" / "state" / "team" / team_name
    if not team_root.exists():
        return None
    from_manifest = _read_team_state_root_from_manifest(team_root / "manifest.v2.json")
    if not from_manifest:
        return None
    return _state_root_to_working_directory(from_manifest)


def _resolve_team_working_directory(team_name: str, preferred_cwd: str) -> str:
    normalized = (team_name or "").strip()
    if not normalized:
        return preferred_cwd
    env_root = os.environ.get("OMX_TEAM_STATE_ROOT")
    if isinstance(env_root, str) and env_root.strip():
        return _state_root_to_working_directory(env_root.strip())

    seeds: list[str] = []
    for seed in (preferred_cwd, os.getcwd()):
        if not isinstance(seed, str) or not seed.strip():
            continue
        if seed not in seeds:
            seeds.append(seed)

    for seed in seeds:
        cursor = seed
        while cursor:
            if _team_state_exists(normalized, cursor):
                return (
                    _resolve_team_working_directory_from_metadata(normalized, cursor)
                    or cursor
                )
            parent = str(Path(cursor).parent)
            if not parent or parent == cursor:
                break
            cursor = parent
    return preferred_cwd


def _validate_common_fields(args: dict[str, Any]) -> None:
    team_name = _str_field(args, "team_name")
    if team_name and not TEAM_NAME_SAFE_PATTERN.match(team_name):
        raise ValueError(
            f'Invalid team_name: "{team_name}". Must match '
            "/^[a-z0-9][a-z0-9-]{0,29}$/ (lowercase alphanumeric + hyphens, "
            "max 30 chars)."
        )
    for field in ("worker", "from_worker", "to_worker"):
        val = _str_field(args, field)
        if val and not WORKER_NAME_SAFE_PATTERN.match(val):
            raise ValueError(
                f'Invalid {field}: "{val}". Must match '
                "/^[a-z0-9][a-z0-9-]{0,63}$/ (lowercase alphanumeric + hyphens, "
                "max 64 chars)."
            )
    raw_task_id = _str_field(args, "task_id")
    if raw_task_id and not TASK_ID_SAFE_PATTERN.match(raw_task_id):
        raise ValueError(
            f'Invalid task_id: "{raw_task_id}". Must be a positive integer '
            "(digits only, max 20 digits)."
        )


def _normalize_op_name(tool_or_operation_name: str) -> str:
    normalized = tool_or_operation_name.strip().lower()
    if normalized.startswith("team_"):
        normalized = normalized[len("team_") :]
    return normalized.replace("_", "-")


# --- Public API ------------------------------------------------------------


def resolve_team_api_operation(name: str) -> str | None:
    """Resolve a tool/operation name to a canonical :data:`TeamApiOperation`.

    Accepts both canonical hyphenated names (``send-message``) and legacy
    underscored MCP tool names (``team_send_message``). Returns ``None`` when
    the name does not resolve to a known operation.

    Port of TS ``resolveTeamApiOperation``.
    """
    if not isinstance(name, str):
        return None
    normalized = _normalize_op_name(name)
    return normalized if normalized in TEAM_API_OPERATIONS else None


def build_legacy_team_deprecation_hint(
    legacy_name: str, original_args: dict[str, Any] | None = None
) -> str:
    """Build a CLI-interop hint pointing legacy MCP tools at ``omx team api``.

    Port of TS ``buildLegacyTeamDeprecationHint``. The hint embeds the
    canonical operation slug when ``legacy_name`` resolves to a known
    operation; otherwise it embeds the raw operation slot for the user to
    correct manually.
    """
    operation = resolve_team_api_operation(legacy_name)
    payload = json.dumps(original_args if original_args is not None else {})
    if operation is None:
        return f"Use CLI interop: omx team api <operation> --input '{payload}' --json"
    return f"Use CLI interop: omx team api {operation} --input '{payload}' --json"


def execute_team_api_operation(
    operation: str,
    args: dict[str, Any],
    fallback_cwd: str,
) -> TeamApiEnvelope:
    """Dispatch a :data:`TeamApiOperation` against :mod:`omx.team.team_ops`.

    Port of TS ``executeTeamApiOperation``. Returns the canonical envelope
    shape:

        {"ok": True,  "operation": op, "data":  dict}
        {"ok": False, "operation": op, "error": {"code": str, "message": str}}

    Unknown operations short-circuit with ``code="unknown_operation"`` and
    ``operation="unknown"``. Operations that depend on yet-unported helpers
    (``send-message``, ``cleanup``, ``read-stall-state``) return a
    ``not_implemented_yet`` envelope.
    """
    if operation not in TEAM_API_OPERATIONS:
        return _err("unknown", "unknown_operation", f"Unknown operation: {operation}")

    try:
        _validate_common_fields(args)
        team_name_for_cwd = _str_field(args, "team_name")
        cwd = (
            _resolve_team_working_directory(team_name_for_cwd, fallback_cwd)
            if team_name_for_cwd
            else fallback_cwd
        )

        if operation == "send-message":
            return _op_send_message(args, cwd)
        if operation == "broadcast":
            return _op_broadcast(args, cwd)
        if operation == "mailbox-list":
            return _op_mailbox_list(args, cwd)
        if operation == "mailbox-mark-delivered":
            return _op_mailbox_mark_delivered(args, cwd)
        if operation == "mailbox-mark-notified":
            return _op_mailbox_mark_notified(args, cwd)
        if operation == "create-task":
            return _op_create_task(args, cwd)
        if operation == "read-task":
            return _op_read_task(args, cwd)
        if operation == "list-tasks":
            return _op_list_tasks(args, cwd)
        if operation == "update-task":
            return _op_update_task(args, cwd)
        if operation == "claim-task":
            return _op_claim_task(args, cwd)
        if operation == "transition-task-status":
            return _op_transition_task_status(args, cwd)
        if operation == "release-task-claim":
            return _op_release_task_claim(args, cwd)
        if operation == "read-config":
            return _op_read_config(args, cwd)
        if operation == "read-manifest":
            return _op_read_manifest(args, cwd)
        if operation == "read-worker-status":
            return _op_read_worker_status(args, cwd)
        if operation == "read-worker-heartbeat":
            return _op_read_worker_heartbeat(args, cwd)
        if operation == "update-worker-heartbeat":
            return _op_update_worker_heartbeat(args, cwd)
        if operation == "write-worker-inbox":
            return _op_write_worker_inbox(args, cwd)
        if operation == "write-worker-identity":
            return _op_write_worker_identity(args, cwd)
        if operation == "append-event":
            return _op_append_event(args, cwd)
        if operation == "read-events":
            return _op_read_events(args, cwd)
        if operation == "await-event":
            return _op_await_event(args, cwd)
        if operation == "read-idle-state":
            return _op_read_idle_state(args, cwd)
        if operation == "read-stall-state":
            return _op_read_stall_state(args, cwd)
        if operation == "get-summary":
            return _op_get_summary(args, cwd)
        if operation == "cleanup":
            return _op_cleanup(args, cwd)
        if operation == "orphan-cleanup":
            return _op_orphan_cleanup(args, cwd)
        if operation == "write-shutdown-request":
            return _op_write_shutdown_request(args, cwd)
        if operation == "read-shutdown-ack":
            return _op_read_shutdown_ack(args, cwd)
        if operation == "read-monitor-snapshot":
            return _op_read_monitor_snapshot(args, cwd)
        if operation == "write-monitor-snapshot":
            return _op_write_monitor_snapshot(args, cwd)
        if operation == "read-task-approval":
            return _op_read_task_approval(args, cwd)
        if operation == "write-task-approval":
            return _op_write_task_approval(args, cwd)

        # Defensive: TEAM_API_OPERATIONS contains an entry without a handler.
        return _err(operation, "unhandled_operation", f"No handler for {operation}")
    except ValueError as err:
        return _err(operation, "invalid_input", str(err))
    except Exception as err:  # noqa: BLE001 - mirror TS catch-all envelope
        return _err(operation, "operation_failed", str(err))


# --- Operation handlers ----------------------------------------------------


def _op_send_message(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "send-message"
    team_name = _str_field(args, "team_name")
    from_worker = _str_field(args, "from_worker")
    to_worker = _str_field(args, "to_worker")
    body = _str_field(args, "body")
    if not from_worker:
        return _err(
            operation,
            "invalid_input",
            "from_worker is required. You must identify yourself.",
        )
    if not team_name or not to_worker or not body:
        return _err(
            operation,
            "invalid_input",
            "team_name, from_worker, to_worker, body are required",
        )
    # ``sendWorkerMessage`` / ``queueDirectMailboxMessage`` interactive
    # routing depends on the tmux runtime, which is Phase 2.9.
    return _err(
        operation,
        "not_implemented_yet",
        "send-message routing depends on team runtime (Phase 2.9).",
    )


def _op_broadcast(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "broadcast"
    team_name = _str_field(args, "team_name")
    from_worker = _str_field(args, "from_worker")
    body = _str_field(args, "body")
    if not team_name or not from_worker or not body:
        return _err(
            operation,
            "invalid_input",
            "team_name, from_worker, body are required",
        )
    messages = team_ops.team_broadcast(team_name, from_worker, body, cwd)
    serialized = [_maybe_to_dict(m) for m in messages]
    return _ok(operation, {"count": len(serialized), "messages": serialized})


def _op_mailbox_list(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "mailbox-list"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    include_delivered = args.get("include_delivered") is not False
    if not team_name or not worker:
        return _err(operation, "invalid_input", "team_name and worker are required")
    all_messages = team_ops.team_list_mailbox(team_name, worker, cwd)
    if include_delivered:
        messages = all_messages
    else:
        messages = [m for m in all_messages if not _msg_delivered_at(m)]
    serialized = [_maybe_to_dict(m) for m in messages]
    return _ok(
        operation,
        {"worker": worker, "count": len(serialized), "messages": serialized},
    )


def _op_mailbox_mark_delivered(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "mailbox-mark-delivered"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    message_id = _str_field(args, "message_id")
    if not team_name or not worker or not message_id:
        return _err(
            operation,
            "invalid_input",
            "team_name, worker, message_id are required",
        )
    updated = team_ops.team_mark_message_delivered(team_name, worker, message_id, cwd)
    return _ok(
        operation,
        {
            "worker": worker,
            "message_id": message_id,
            "updated": updated,
            "dispatch_request_id": None,
            "dispatch_updated": False,
        },
    )


def _op_mailbox_mark_notified(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "mailbox-mark-notified"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    message_id = _str_field(args, "message_id")
    if not team_name or not worker or not message_id:
        return _err(
            operation,
            "invalid_input",
            "team_name, worker, message_id are required",
        )
    notified = team_ops.team_mark_message_notified(team_name, worker, message_id, cwd)
    return _ok(
        operation,
        {"worker": worker, "message_id": message_id, "notified": notified},
    )


def _op_create_task(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "create-task"
    team_name = _str_field(args, "team_name")
    subject = _str_field(args, "subject")
    description = _str_field(args, "description")
    if not team_name or not subject or not description:
        return _err(
            operation,
            "invalid_input",
            "team_name, subject, description are required",
        )
    owner = args.get("owner")
    owner_str = owner.strip() if isinstance(owner, str) and owner.strip() else None
    # Python ``team_create_task`` takes ``description`` as the primary text and
    # has no separate ``subject`` field. Combine subject + description so the
    # subject is preserved when echoed back in the task body.
    combined = f"{subject}\n\n{description}" if subject != description else description
    task = team_ops.team_create_task(
        cwd,
        team_name,
        combined,
        owner=owner_str,
    )
    return _ok(operation, {"task": _maybe_to_dict(task)})


def _op_read_task(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-task"
    team_name = _str_field(args, "team_name")
    task_id = _str_field(args, "task_id")
    if not team_name or not task_id:
        return _err(operation, "invalid_input", "team_name and task_id are required")
    task = team_ops.team_read_task(cwd, team_name, task_id)
    if task is None:
        return _err(operation, "task_not_found", "task_not_found")
    return _ok(operation, {"task": _maybe_to_dict(task)})


def _op_list_tasks(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "list-tasks"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    tasks = team_ops.team_list_tasks(cwd, team_name)
    serialized = [_maybe_to_dict(t) for t in tasks]
    return _ok(operation, {"count": len(serialized), "tasks": serialized})


def _op_update_task(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "update-task"
    team_name = _str_field(args, "team_name")
    task_id = _str_field(args, "task_id")
    if not team_name or not task_id:
        return _err(operation, "invalid_input", "team_name and task_id are required")

    lifecycle_fields = ("status", "owner", "result", "error")
    present_lifecycle = [f for f in lifecycle_fields if f in args]
    if present_lifecycle:
        joined = ", ".join(present_lifecycle)
        return _err(
            operation,
            "invalid_input",
            f"team_update_task cannot mutate lifecycle fields: {joined}",
        )
    unexpected = [k for k in args if k not in _TEAM_UPDATE_TASK_REQUEST_FIELDS]
    if unexpected:
        joined = ", ".join(unexpected)
        return _err(
            operation,
            "invalid_input",
            f"team_update_task received unsupported fields: {joined}",
        )

    updates: dict[str, Any] = {}
    if "subject" in args:
        if not isinstance(args["subject"], str):
            return _err(
                operation, "invalid_input", "subject must be a string when provided"
            )
        updates["subject"] = args["subject"].strip()
    if "description" in args:
        if not isinstance(args["description"], str):
            return _err(
                operation,
                "invalid_input",
                "description must be a string when provided",
            )
        updates["description"] = args["description"].strip()
    if "requires_code_change" in args:
        if not isinstance(args["requires_code_change"], bool):
            return _err(
                operation,
                "invalid_input",
                "requires_code_change must be a boolean when provided",
            )
        updates["requires_code_change"] = args["requires_code_change"]
    if "blocked_by" in args:
        try:
            updates["blocked_by"] = _parse_validated_task_id_array(
                args["blocked_by"], "blocked_by"
            )
        except ValueError as err:
            return _err(operation, "invalid_input", str(err))

    task = team_ops.team_update_task(cwd, team_name, task_id, updates)
    if task is None:
        return _err(operation, "task_not_found", "task_not_found")
    return _ok(operation, {"task": _maybe_to_dict(task)})


def _op_claim_task(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "claim-task"
    team_name = _str_field(args, "team_name")
    task_id = _str_field(args, "task_id")
    worker = _str_field(args, "worker")
    if not team_name or not task_id or not worker:
        return _err(
            operation,
            "invalid_input",
            "team_name, task_id, worker are required",
        )
    raw_expected = args.get("expected_version")
    if raw_expected is not None and (
        not _is_finite_integer(raw_expected) or raw_expected < 1
    ):
        return _err(
            operation,
            "invalid_input",
            "expected_version must be a positive integer when provided",
        )
    result = team_ops.team_claim_task(team_name, task_id, worker, cwd)
    return _ok(operation, _coerce_dict(result))


def _op_transition_task_status(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "transition-task-status"
    team_name = _str_field(args, "team_name")
    task_id = _str_field(args, "task_id")
    from_status = _str_field(args, "from")
    to_status = _str_field(args, "to")
    claim_token = _str_field(args, "claim_token")
    transition_result = args.get("result")
    transition_error = args.get("error")
    if not all([team_name, task_id, from_status, to_status, claim_token]):
        return _err(
            operation,
            "invalid_input",
            "team_name, task_id, from, to, claim_token are required",
        )
    if from_status not in TEAM_TASK_STATUSES or to_status not in TEAM_TASK_STATUSES:
        return _err(
            operation, "invalid_input", "from and to must be valid task statuses"
        )
    if transition_result is not None and not isinstance(transition_result, str):
        return _err(operation, "invalid_input", "result must be a string when provided")
    if transition_error is not None and not isinstance(transition_error, str):
        return _err(operation, "invalid_input", "error must be a string when provided")
    terminal: dict[str, Any] = {}
    if isinstance(transition_result, str):
        terminal["result"] = transition_result
    if isinstance(transition_error, str):
        terminal["error"] = transition_error
    result = team_ops.team_transition_task_status(
        team_name,
        task_id,
        from_status,
        to_status,
        claim_token,
        cwd,
        terminal or None,
    )
    return _ok(operation, _coerce_dict(result))


def _op_release_task_claim(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "release-task-claim"
    team_name = _str_field(args, "team_name")
    task_id = _str_field(args, "task_id")
    claim_token = _str_field(args, "claim_token")
    worker = _str_field(args, "worker")
    if not team_name or not task_id or not claim_token or not worker:
        return _err(
            operation,
            "invalid_input",
            "team_name, task_id, claim_token, worker are required",
        )
    result = team_ops.team_release_task_claim(team_name, task_id, claim_token, cwd)
    return _ok(operation, _coerce_dict(result))


def _op_read_config(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-config"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    config = team_ops.team_read_config(cwd, team_name)
    if not config:
        return _err(operation, "team_not_found", "team_not_found")
    return _ok(operation, {"config": config})


def _op_read_manifest(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-manifest"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    manifest = team_ops.team_read_manifest(cwd, team_name)
    if manifest is None:
        return _err(operation, "manifest_not_found", "manifest_not_found")
    return _ok(operation, {"manifest": _maybe_to_dict(manifest)})


def _op_read_worker_status(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-worker-status"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    if not team_name or not worker:
        return _err(operation, "invalid_input", "team_name and worker are required")
    status = team_ops.team_read_worker_status(cwd, team_name, worker)
    return _ok(operation, {"worker": worker, "status": status})


def _op_read_worker_heartbeat(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-worker-heartbeat"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    if not team_name or not worker:
        return _err(operation, "invalid_input", "team_name and worker are required")
    heartbeat = team_ops.team_read_worker_heartbeat(cwd, team_name, worker)
    return _ok(operation, {"worker": worker, "heartbeat": heartbeat})


def _op_update_worker_heartbeat(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "update-worker-heartbeat"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    pid = args.get("pid")
    turn_count = args.get("turn_count")
    alive = args.get("alive")
    if (
        not team_name
        or not worker
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or not isinstance(turn_count, int)
        or isinstance(turn_count, bool)
        or not isinstance(alive, bool)
    ):
        return _err(
            operation,
            "invalid_input",
            "team_name, worker, pid, turn_count, alive are required",
        )
    team_ops.team_update_worker_heartbeat(cwd, team_name, worker, pid, turn_count)
    return _ok(operation, {"worker": worker})


def _op_write_worker_inbox(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "write-worker-inbox"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    content = _str_field(args, "content")
    if not team_name or not worker or not content:
        return _err(
            operation,
            "invalid_input",
            "team_name, worker, content are required",
        )
    team_ops.team_write_worker_inbox(cwd, team_name, worker, content)
    return _ok(operation, {"worker": worker})


def _op_write_worker_identity(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "write-worker-identity"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    index = args.get("index")
    role = _str_field(args, "role")
    if (
        not team_name
        or not worker
        or not isinstance(index, int)
        or isinstance(index, bool)
        or not role
    ):
        return _err(
            operation,
            "invalid_input",
            "team_name, worker, index, role are required",
        )
    identity: dict[str, Any] = {
        "name": worker,
        "index": index,
        "role": role,
        "assigned_tasks": list(args.get("assigned_tasks") or []),
    }
    for field in (
        "pid",
        "pane_id",
        "working_dir",
        "worktree_path",
        "worktree_branch",
        "worktree_detached",
        "team_state_root",
    ):
        if args.get(field) is not None:
            identity[field] = args[field]
    team_ops.team_write_worker_identity(cwd, team_name, worker, identity)
    return _ok(operation, {"worker": worker})


def _op_append_event(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "append-event"
    team_name = _str_field(args, "team_name")
    event_type = _str_field(args, "type")
    worker = _str_field(args, "worker")
    if not team_name or not event_type or not worker:
        return _err(
            operation,
            "invalid_input",
            "team_name, type, worker are required",
        )
    if event_type not in TEAM_EVENT_TYPES:
        joined = ", ".join(TEAM_EVENT_TYPES)
        return _err(operation, "invalid_input", f"type must be one of: {joined}")
    metadata = _parse_optional_metadata(args.get("metadata"))
    event_dict: dict[str, Any] = {
        "event_type": event_type,
        "type": event_type,
        "worker": worker,
        "worker_id": worker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    for field in (
        "task_id",
        "message_id",
        "reason",
        "state",
        "prev_state",
        "to_worker",
        "source_type",
    ):
        if args.get(field) is not None:
            event_dict[field] = args[field]
    if isinstance(args.get("worker_count"), int) and not isinstance(
        args.get("worker_count"), bool
    ):
        event_dict["worker_count"] = args["worker_count"]
    if metadata is not None:
        event_dict["detail"] = metadata

    # team_ops.team_append_event takes a TeamEvent dataclass.
    from omx.team.contracts import TeamEvent

    detail = metadata if metadata is not None else {}
    event = TeamEvent(
        event_type=event_type,
        timestamp=event_dict["timestamp"],
        worker_id=worker,
        task_id=event_dict.get("task_id"),
        detail=detail,
    )
    team_ops.team_append_event(cwd, event, team_name)
    return _ok(operation, {"event": event.to_dict()})


def _op_read_events(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-events"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    wakeable_only = _parse_optional_boolean(args.get("wakeable_only"), "wakeable_only")
    event_type = _parse_optional_event_type(args.get("type"))
    worker = _opt_str_trim(args, "worker")
    task_id = _opt_str_trim(args, "task_id")
    after_event_id = _opt_str_trim(args, "after_event_id")

    from omx.team.state.events import read_team_events
    from omx.team.state_root import team_dir

    events = read_team_events(
        team_dir(team_name, cwd),
        after_event_id=after_event_id or None,
        event_type=event_type,
        worker=worker or None,
        task_id=task_id or None,
    )
    if wakeable_only:
        events = [e for e in events if _event_is_wakeable(e)]
    cursor = events[-1].get("event_id", "") if events else (after_event_id or "")
    return _ok(operation, {"count": len(events), "cursor": cursor, "events": events})


def _op_await_event(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "await-event"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    timeout_ms = _parse_optional_non_negative_integer(
        args.get("timeout_ms"), "timeout_ms"
    )
    poll_ms = _parse_optional_non_negative_integer(args.get("poll_ms"), "poll_ms")
    wakeable_only = _parse_optional_boolean(args.get("wakeable_only"), "wakeable_only")
    event_type = _parse_optional_event_type(args.get("type"))
    worker = _opt_str_trim(args, "worker")
    task_id = _opt_str_trim(args, "task_id")
    after_event_id = _opt_str_trim(args, "after_event_id")

    from omx.team.state.events import wait_for_team_event
    from omx.team.state_root import team_dir

    result = wait_for_team_event(
        team_dir(team_name, cwd),
        after_event_id=after_event_id or None,
        timeout_ms=timeout_ms if timeout_ms is not None else 30_000,
        poll_ms=poll_ms if poll_ms is not None else 100,
        event_type=event_type,
        worker=worker or None,
        task_id=task_id or None,
    )
    # ``wait_for_team_event`` ignores ``wakeable_only``; callers can refilter.
    _ = wakeable_only
    event = result.get("event")
    status = "event" if event is not None else "timeout"
    return _ok(
        operation,
        {
            "status": status,
            "cursor": result.get("cursor", ""),
            "event": event,
        },
    )


def _op_read_idle_state(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-idle-state"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    summary = _maybe_summary(team_name, cwd)
    if summary is None:
        return _err(operation, "team_not_found", "team_not_found")
    snapshot = team_ops.team_read_monitor_snapshot(team_name, cwd)
    from omx.team.state.events import read_team_events
    from omx.team.state_root import team_dir

    raw_events = read_team_events(team_dir(team_name, cwd))
    recent = raw_events[-TEAM_STATE_EVENT_WINDOW:] if raw_events else []
    return _ok(operation, _build_idle_state(team_name, summary, snapshot, recent))


def _op_read_stall_state(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-stall-state"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    # ``readLatestTeamProgressEvidenceMs`` + leader-attention scoring requires
    # helpers that ship with the team runtime (Phase 2.9).
    return _err(
        operation,
        "not_implemented_yet",
        "read-stall-state depends on team runtime (Phase 2.9).",
    )


def _op_get_summary(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "get-summary"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    summary = _maybe_summary(team_name, cwd)
    if summary is None:
        return _err(operation, "team_not_found", "team_not_found")
    return _ok(operation, {"summary": summary})


def _op_cleanup(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "cleanup"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    # ``shutdownTeam`` (interactive tmux teardown) is Phase 2.9.
    return _err(
        operation,
        "not_implemented_yet",
        "cleanup (full shutdown) depends on team runtime (Phase 2.9). "
        "Use orphan-cleanup for state-only removal.",
    )


def _op_orphan_cleanup(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "orphan-cleanup"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    team_ops.team_cleanup(cwd, team_name)
    return _ok(operation, {"team_name": team_name, "cleanup_mode": "orphan_cleanup"})


def _op_write_shutdown_request(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "write-shutdown-request"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    requested_by = _str_field(args, "requested_by")
    if not team_name or not worker or not requested_by:
        return _err(
            operation,
            "invalid_input",
            "team_name, worker, requested_by are required",
        )
    team_ops.team_write_shutdown_request(
        team_name, worker, cwd, requested_by=requested_by
    )
    return _ok(operation, {"worker": worker})


def _op_read_shutdown_ack(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-shutdown-ack"
    team_name = _str_field(args, "team_name")
    worker = _str_field(args, "worker")
    if not team_name or not worker:
        return _err(operation, "invalid_input", "team_name and worker are required")
    min_updated_at = args.get("min_updated_at")
    if min_updated_at is not None and not isinstance(min_updated_at, str):
        return _err(
            operation,
            "invalid_input",
            "min_updated_at must be a string when provided",
        )
    try:
        ack = team_ops.team_read_shutdown_ack(team_name, worker, cwd, min_updated_at)
    except TypeError:
        # Python signature may not accept ``min_updated_at`` yet — fall back
        # to the 3-arg call rather than failing parity callers.
        ack = team_ops.team_read_shutdown_ack(team_name, worker, cwd)
    return _ok(operation, {"worker": worker, "ack": _maybe_to_dict(ack)})


def _op_read_monitor_snapshot(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-monitor-snapshot"
    team_name = _str_field(args, "team_name")
    if not team_name:
        return _err(operation, "invalid_input", "team_name is required")
    snapshot = team_ops.team_read_monitor_snapshot(team_name, cwd)
    return _ok(operation, {"snapshot": _maybe_to_dict(snapshot)})


def _op_write_monitor_snapshot(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "write-monitor-snapshot"
    team_name = _str_field(args, "team_name")
    snapshot_arg = args.get("snapshot")
    if not team_name or snapshot_arg is None:
        return _err(operation, "invalid_input", "team_name and snapshot are required")
    from omx.team.state.types import TeamMonitorSnapshot

    snapshot = (
        snapshot_arg
        if isinstance(snapshot_arg, TeamMonitorSnapshot)
        else TeamMonitorSnapshot.from_dict(snapshot_arg)
    )
    team_ops.team_write_monitor_snapshot(team_name, snapshot, cwd)
    return _ok(operation, {})


def _op_read_task_approval(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "read-task-approval"
    team_name = _str_field(args, "team_name")
    task_id = _str_field(args, "task_id")
    if not team_name or not task_id:
        return _err(operation, "invalid_input", "team_name and task_id are required")
    approval = team_ops.team_read_task_approval(team_name, task_id, cwd)
    return _ok(operation, {"approval": _maybe_to_dict(approval)})


def _op_write_task_approval(args: dict[str, Any], cwd: str) -> TeamApiEnvelope:
    operation = "write-task-approval"
    team_name = _str_field(args, "team_name")
    task_id = _str_field(args, "task_id")
    status = _str_field(args, "status")
    reviewer = _str_field(args, "reviewer")
    decision_reason = _str_field(args, "decision_reason")
    if not all([team_name, task_id, status, reviewer, decision_reason]):
        return _err(
            operation,
            "invalid_input",
            "team_name, task_id, status, reviewer, decision_reason are required",
        )
    if status not in TEAM_TASK_APPROVAL_STATUSES:
        joined = ", ".join(TEAM_TASK_APPROVAL_STATUSES)
        return _err(operation, "invalid_input", f"status must be one of: {joined}")
    raw_required = args.get("required")
    if raw_required is not None and not isinstance(raw_required, bool):
        return _err(
            operation,
            "invalid_input",
            "required must be a boolean when provided",
        )
    from omx.team.state.types import TaskApprovalRecord

    approval = TaskApprovalRecord(
        task_id=task_id,
        required=raw_required is not False,
        status=status,
        reviewer=reviewer,
        decision_reason=decision_reason,
        decided_at=datetime.now(timezone.utc).isoformat(),
    )
    team_ops.team_write_task_approval(team_name, approval, cwd)
    return _ok(operation, {"task_id": task_id, "status": status})


# --- Idle-state helpers ----------------------------------------------------


def _maybe_summary(team_name: str, cwd: str) -> dict[str, Any] | None:
    """Return a team summary, or ``None`` when the team has no state."""
    config = team_ops.team_read_config(cwd, team_name)
    if not config:
        return None
    try:
        summary = team_ops.team_get_summary(team_name, cwd)
    except (TypeError, AttributeError):
        # ``team_get_summary`` currently calls the underlying helper with a
        # stale signature. Fall back to a minimal stub so the envelope still
        # contains a usable shape rather than crashing.
        summary = {
            "workerCount": 0,
            "tasks": {
                "total": 0,
                "pending": 0,
                "blocked": 0,
                "in_progress": 0,
                "completed": 0,
                "failed": 0,
            },
            "workers": [],
            "nonReportingWorkers": [],
        }
    return summary


def _build_idle_state(
    team_name: str,
    summary: dict[str, Any] | None,
    snapshot: Any,
    recent_events: list[dict[str, Any]],
) -> dict[str, Any]:
    worker_state_by_name = _snapshot_worker_states(snapshot)
    summary_workers = (summary or {}).get("workers") or []
    worker_names: list[str] = sorted(
        {
            *(w.get("name", "") for w in summary_workers if w.get("name")),
            *worker_state_by_name.keys(),
        }
    )
    idle_workers = [
        name for name in worker_names if worker_state_by_name.get(name) == "idle"
    ]
    non_idle_workers = [name for name in worker_names if name not in idle_workers]
    last_idle_transition = {
        name: _summarize_event(_find_latest_worker_idle_event(recent_events, name))
        for name in worker_names
    }
    last_all_idle_event = _find_latest_event_by_type(
        recent_events, ["all_workers_idle"]
    )
    return {
        "team_name": team_name,
        "worker_count": (summary or {}).get("workerCount", len(worker_names)),
        "idle_worker_count": len(idle_workers),
        "idle_workers": idle_workers,
        "non_idle_workers": non_idle_workers,
        "all_workers_idle": len(worker_names) > 0
        and len(idle_workers) == len(worker_names),
        "last_idle_transition_by_worker": last_idle_transition,
        "last_all_workers_idle_event": _summarize_event(last_all_idle_event),
        "source": {
            "summary_available": summary is not None,
            "snapshot_available": snapshot is not None,
            "recent_event_count": len(recent_events),
        },
    }


def _snapshot_worker_states(snapshot: Any) -> dict[str, str]:
    if snapshot is None:
        return {}
    if isinstance(snapshot, dict):
        states = snapshot.get("workerStateByName") or snapshot.get(
            "worker_state_by_name"
        )
    else:
        states = getattr(snapshot, "workerStateByName", None) or getattr(
            snapshot, "worker_state_by_name", None
        )
    if not isinstance(states, dict):
        return {}
    return {str(k): str(v) for k, v in states.items()}


def _summarize_event(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    return {
        "event_id": event.get("event_id"),
        "type": event.get("type") or event.get("event_type"),
        "worker": event.get("worker") or event.get("worker_id"),
        "task_id": event.get("task_id"),
        "created_at": event.get("created_at") or event.get("timestamp"),
        "reason": event.get("reason"),
        "intent": event.get("intent")
        if isinstance(event.get("intent"), str)
        else event.get("orchestration_intent"),
        "state": event.get("state"),
        "prev_state": event.get("prev_state"),
        "source_type": event.get("source_type"),
        "worker_count": event.get("worker_count"),
    }


def _find_latest_event_by_type(
    events: list[dict[str, Any]], types: list[str]
) -> dict[str, Any] | None:
    allowed = set(types)
    for event in reversed(events):
        kind = event.get("type") or event.get("event_type")
        if kind in allowed:
            return event
    return None


def _find_latest_worker_idle_event(
    events: list[dict[str, Any]], worker_name: str
) -> dict[str, Any] | None:
    for event in reversed(events):
        if (event.get("worker") or event.get("worker_id")) != worker_name:
            continue
        kind = event.get("type") or event.get("event_type")
        if kind == "worker_state_changed" and event.get("state") == "idle":
            return event
    return None


def _event_is_wakeable(event: dict[str, Any]) -> bool:
    kind = event.get("type") or event.get("event_type") or ""
    wakeable_types = {
        "task_completed",
        "task_failed",
        "worker_idle",
        "all_workers_idle",
        "shutdown_ack",
    }
    return kind in wakeable_types


# --- Serialization helpers -------------------------------------------------


def _maybe_to_dict(value: Any) -> Any:
    """Convert dataclass-like values to plain dicts for JSON envelopes."""
    if value is None:
        return None
    if isinstance(value, (dict, list, str, int, float, bool)):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return value


def _coerce_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    converted = _maybe_to_dict(value)
    if isinstance(converted, dict):
        return converted
    return {"result": converted}


def _msg_delivered_at(message: Any) -> str:
    if isinstance(message, dict):
        raw = message.get("delivered_at")
    else:
        raw = getattr(message, "delivered_at", None)
    return raw.strip() if isinstance(raw, str) else ""


# --- Back-compat surface (pre-rewrite) -------------------------------------


def handle_team_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Back-compat MCP-tool handler.

    Legacy callers go through this function; new callers should invoke
    :func:`execute_team_api_operation` directly. Tools whose names map to a
    known operation are routed through the gateway; unrecognised names emit a
    hard-deprecation error.
    """
    operation = resolve_team_api_operation(name)
    if operation is None:
        hint = build_legacy_team_deprecation_hint(name, args)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "error": (
                                f'MCP tool "{name}" is hard-deprecated. '
                                "Team mutations now require CLI interop."
                            ),
                            "code": "deprecated_cli_only",
                            "hint": hint,
                        }
                    ),
                }
            ],
            "isError": True,
        }
    envelope = execute_team_api_operation(operation, args, os.getcwd())
    return {
        "content": [{"type": "text", "text": json.dumps(envelope)}],
        "isError": not envelope.get("ok", False),
    }


__all__ = [
    "LEGACY_TEAM_MCP_TOOLS",
    "TEAM_API_OPERATIONS",
    "TEAM_TASK_APPROVAL_STATUSES",
    "TEAM_TASK_STATUSES",
    "TEAM_NAME_SAFE_PATTERN",
    "WORKER_NAME_SAFE_PATTERN",
    "TASK_ID_SAFE_PATTERN",
    "TeamApiEnvelope",
    "TeamApiOperation",
    "build_legacy_team_deprecation_hint",
    "execute_team_api_operation",
    "handle_team_tool_call",
    "resolve_team_api_operation",
]
