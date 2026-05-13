"""Standalone team runtime helpers.

Port of four small helpers from ``src/team/runtime.ts``:

- ``apply_created_interactive_session_to_config``
  (``runtime.ts:313-330``)
- ``should_prekill_interactive_shutdown_process_trees``
  (``runtime.ts:369-379``)
- ``cleanup_team_worker_launch_orphaned_mcp_processes``
  (``runtime.ts:381-404``)
- ``resolve_worker_launch_args_from_env``
  (``runtime.ts:1914-1956``)

These functions deliberately have minimal dependencies so they can be
ported ahead of the larger ``startTeam`` / ``shutdownTeam`` blocks.

Sync conversion: TS ``Promise``-returning callbacks become synchronous
callables. ``execFileSync('ps', ...)`` is replaced with a no-op
fallback for ``cleanup_team_worker_launch_orphaned_mcp_processes`` —
the leader will inject the real cleanup function once the
``cli/cleanup`` module is ported.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Callable


# --- TeamSession local stub ------------------------------------------------


@dataclass
class TeamSession:
    """Minimal local stub for the TS ``TeamSession`` shape.

    # TODO: migrate to canonical TeamSession when team.tmux_session ports it

    Only the fields consumed by
    ``apply_created_interactive_session_to_config`` are modelled here.
    Worker pane IDs may be ``None`` in slots that failed to allocate.
    """

    name: str
    leader_pane_id: str
    hud_pane_id: str | None = None
    resize_hook_name: str | None = None
    resize_hook_target: str | None = None
    worker_pane_ids: list[str | None] = field(default_factory=list)


# --- applyCreatedInteractiveSessionToConfig -------------------------------


def apply_created_interactive_session_to_config(
    config: dict[str, Any],
    created_session: TeamSession,
    worker_pane_ids: list[str | None],
) -> None:
    """Mutate ``config`` in place with tmux IDs from a freshly created session.

    Port of ``applyCreatedInteractiveSessionToConfig`` (runtime.ts:313-330).

    The TS function takes a ``TeamConfig`` and mutates its tmux/resize-hook
    fields plus the per-worker pane IDs. The Python port accepts a loose
    ``dict[str, Any]`` for now (``TeamConfig`` is not yet a dataclass).

    Args:
        config: Mutable team config dict. Must contain a ``workers`` list of
            dicts (each with a writable ``pane_id`` slot).
        created_session: Freshly created tmux session.
        worker_pane_ids: Output list that mirrors
            ``created_session.worker_pane_ids``. Resized in-place to match
            the session's worker pane list.
    """
    config["tmux_session"] = created_session.name
    config["leader_pane_id"] = created_session.leader_pane_id
    config["hud_pane_id"] = created_session.hud_pane_id
    config["resize_hook_name"] = created_session.resize_hook_name
    config["resize_hook_target"] = created_session.resize_hook_target

    workers = config.get("workers") or []
    session_pane_ids = list(created_session.worker_pane_ids)
    # Grow the output list to match the session's pane count, mirroring the
    # TS pattern of writing index-by-index.
    while len(worker_pane_ids) < len(session_pane_ids):
        worker_pane_ids.append(None)
    for i, pane_id in enumerate(session_pane_ids):
        worker_pane_ids[i] = pane_id
        if i < len(workers) and isinstance(workers[i], dict):
            workers[i]["pane_id"] = pane_id


# --- shouldPrekillInteractiveShutdownProcessTrees --------------------------


def should_prekill_interactive_shutdown_process_trees(session_name: str) -> bool:
    """Return True when a tmux session should be prekilled by process tree.

    Port of ``shouldPrekillInteractiveShutdownProcessTrees``
    (runtime.ts:369-379).

    Shared-window sessions (those whose name embeds a ``:``-suffixed pane
    selector) overlap with the invoking client's ancestry, so pane-targeted
    teardown is preferred. Detached sessions still benefit from process-tree
    prekill — including native Windows prompt-worker ancestry.

    Args:
        session_name: Tmux session name, possibly including a
            ``session:window`` selector.

    Returns:
        ``True`` to prekill the worker process trees, ``False`` to rely on
        pane-targeted teardown only.
    """
    if ":" in session_name:
        return False
    return True


# --- cleanupTeamWorkerLaunchOrphanedMcpProcesses ---------------------------


def cleanup_team_worker_launch_orphaned_mcp_processes(
    cleanup: Callable[[], dict[str, Any]] | None = None,
    write_warning: Callable[[str], None] | None = None,
) -> None:
    """Best-effort pre-launch cleanup of orphaned MCP processes.

    Port of ``cleanupTeamWorkerLaunchOrphanedMcpProcesses``
    (runtime.ts:381-404).

    The TS implementation defaults to ``cleanupOmxMcpProcesses`` from
    ``cli/cleanup.ts`` (which uses ``execFileSync('ps', ...)``). That entire
    cleanup pipeline is not yet ported, so the default Python ``cleanup``
    callable is a no-op returning ``{"failed_pids": [], "reaped_pids": []}``.
    Callers should inject a real cleanup function once available.

    # TODO: Windows process enumeration (``tasklist`` parsing) once
    # ``cli/cleanup`` is ported

    Args:
        cleanup: Optional callable performing the actual cleanup. Must
            return a dict containing at minimum ``failed_pids`` (a list).
        write_warning: Optional sink for warnings. Defaults to stderr.
    """
    effective_cleanup = cleanup if cleanup is not None else _default_cleanup_noop
    effective_warning = (
        write_warning if write_warning is not None else _default_write_warning
    )

    try:
        result = effective_cleanup()
    except Exception as err:  # noqa: BLE001 - best-effort path mirrors TS
        effective_warning(
            f"[team/runtime] pre-launch MCP cleanup failed: {err}; "
            "continuing worker launch."
        )
        return

    failed_pids = result.get("failed_pids") if isinstance(result, dict) else None
    if isinstance(failed_pids, list) and len(failed_pids) > 0:
        effective_warning(
            f"[team/runtime] Failed to reap {len(failed_pids)} orphaned "
            "OMX MCP process(es); continuing worker launch."
        )


def _default_cleanup_noop() -> dict[str, Any]:
    return {"failed_pids": [], "reaped_pids": []}


def _default_write_warning(message: str) -> None:
    sys.stderr.write(f"{message}\n")


# --- resolveWorkerLaunchArgsFromEnv ----------------------------------------


# TS constants mirrored locally so we do not depend on the full
# ``team/model-contract.ts`` port (still in flight).
_MADMAX_FLAG = "--madmax"
_CODEX_BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"
_MODEL_FLAG = "--model"
_CONFIG_FLAG = "-c"
_REASONING_KEY = "model_reasoning_effort"
_REASONING_PATTERN = re.compile(
    rf'^\s*{re.escape(_REASONING_KEY)}\s*=\s*"?([A-Za-z0-9_-]+)"?\s*$'
)
_REASONING_VALUES = {"low", "medium", "high", "xhigh"}

# TS canonical fallback constants (config/models.ts:86-88).
_DEFAULT_FRONTIER_MODEL = "gpt-5.5"
_DEFAULT_STANDARD_MODEL = "gpt-5.4-mini"
_DEFAULT_SPARK_MODEL = "gpt-5.3-codex-spark"


@dataclass
class _ParsedLaunchArgs:
    passthrough: list[str]
    wants_bypass: bool
    reasoning_override: str | None
    model_override: str | None


def _split_worker_launch_args(raw: str | None) -> list[str]:
    """Port of TS ``splitWorkerLaunchArgs``."""
    if raw is None:
        return []
    stripped = raw.strip()
    if stripped == "":
        return []
    return [token for token in re.split(r"\s+", stripped) if token]


def _is_valid_model_value(value: str) -> bool:
    return value.strip() != "" and not value.startswith("-")


def _is_reasoning_override(value: str) -> bool:
    return _REASONING_PATTERN.match(value.strip()) is not None


def _parse_worker_launch_args(args: list[str]) -> _ParsedLaunchArgs:
    """Port of TS ``parseTeamWorkerLaunchArgs``."""
    passthrough: list[str] = []
    wants_bypass = False
    reasoning_override: str | None = None
    model_override: str | None = None

    i = 0
    while i < len(args):
        arg = args[i]
        if arg in (_CODEX_BYPASS_FLAG, _MADMAX_FLAG):
            wants_bypass = True
            i += 1
            continue

        if arg == _MODEL_FLAG:
            if i + 1 < len(args):
                maybe = args[i + 1]
                if _is_valid_model_value(maybe):
                    model_override = maybe.strip()
                    i += 2
                    continue
            # Orphan --model with no valid value is silently dropped.
            i += 1
            continue

        if arg.startswith(f"{_MODEL_FLAG}="):
            inline = arg[len(f"{_MODEL_FLAG}=") :].strip()
            if _is_valid_model_value(inline):
                model_override = inline
            # Empty/invalid --model= is silently dropped.
            i += 1
            continue

        if arg == _CONFIG_FLAG and i + 1 < len(args):
            maybe = args[i + 1]
            if _is_reasoning_override(maybe):
                reasoning_override = maybe
                i += 2
                continue

        passthrough.append(arg)
        i += 1

    return _ParsedLaunchArgs(
        passthrough=passthrough,
        wants_bypass=wants_bypass,
        reasoning_override=reasoning_override,
        model_override=model_override,
    )


def _normalize_optional_model(model: str | None) -> str | None:
    if not isinstance(model, str):
        return None
    trimmed = model.strip()
    return trimmed if trimmed else None


def _normalize_optional_reasoning(reasoning: str | None) -> str | None:
    if not isinstance(reasoning, str):
        return None
    normalized = reasoning.strip().lower()
    return normalized if normalized in _REASONING_VALUES else None


def _normalize_launch_args(
    args: list[str],
    preferred_model: str | None,
    preferred_reasoning: str | None,
) -> list[str]:
    """Port of TS ``normalizeTeamWorkerLaunchArgs``."""
    parsed = _parse_worker_launch_args(args)
    normalized = list(parsed.passthrough)

    if parsed.wants_bypass:
        normalized.append(_CODEX_BYPASS_FLAG)

    if parsed.reasoning_override is not None:
        normalized.extend([_CONFIG_FLAG, parsed.reasoning_override])
    else:
        normalized_reasoning = _normalize_optional_reasoning(preferred_reasoning)
        if normalized_reasoning is not None:
            normalized.extend(
                [_CONFIG_FLAG, f'{_REASONING_KEY}="{normalized_reasoning}"']
            )

    selected_model = _normalize_optional_model(
        preferred_model
    ) or _normalize_optional_model(parsed.model_override)
    if selected_model is not None:
        normalized.extend([_MODEL_FLAG, selected_model])

    return normalized


def _resolve_agent_default_model(
    agent_type: str | None, env: dict[str, str]
) -> str | None:
    """Port of TS ``resolveAgentDefaultModel`` (simplified for Phase 2.0).

    The TS version consults the agent registry's ``modelClass`` field. The
    Python agent registry is not wired in yet, so we implement the cases
    the runtime helper actually exercises:

    - ``"executor"`` -> frontier default
    - any name ending in ``-low`` -> spark default
    - everything else -> ``None`` (caller falls back to env/inherited)

    Env precedence matches ``config/models.ts``:
    ``OMX_DEFAULT_FRONTIER_MODEL`` > hardcoded frontier default;
    ``OMX_DEFAULT_SPARK_MODEL`` > legacy ``OMX_SPARK_MODEL`` > hardcoded
    spark default.

    # TODO: read .omx-config.json under CODEX_HOME once that loader ports
    """
    if not isinstance(agent_type, str):
        return None
    normalized = agent_type.strip().lower()
    if normalized == "":
        return None
    if normalized.endswith("-low"):
        return _resolve_spark_default(env)
    if normalized == "executor":
        return _resolve_frontier_default(env)
    return None


def _resolve_frontier_default(env: dict[str, str]) -> str:
    return (
        _normalize_optional_model(env.get("OMX_DEFAULT_FRONTIER_MODEL"))
        or _DEFAULT_FRONTIER_MODEL
    )


def _resolve_spark_default(env: dict[str, str]) -> str:
    return (
        _normalize_optional_model(env.get("OMX_DEFAULT_SPARK_MODEL"))
        or _normalize_optional_model(env.get("OMX_SPARK_MODEL"))
        or _DEFAULT_SPARK_MODEL
    )


def resolve_worker_launch_args_from_env(
    env: dict[str, str] | None = None,
    agent_type: str = "executor",
    inherited_leader_model: str | None = None,
    preferred_reasoning: str | None = None,
) -> list[str]:
    """Resolve the worker-launch argv for a team worker.

    Port of ``resolveWorkerLaunchArgsFromEnv`` (runtime.ts:1914-1956).

    Precedence (matches TS exactly):

    1. ``OMX_TEAM_WORKER_LAUNCH_ARGS`` (split on whitespace).
    2. ``inherited_leader_model`` injected as ``--model <value>`` when set.
    3. Fallback model resolved from the agent's class and env defaults.

    Reasoning override precedence:

    1. Explicit ``-c model_reasoning_effort="..."`` inside
       ``OMX_TEAM_WORKER_LAUNCH_ARGS`` (or inherited args).
    2. ``preferred_reasoning`` (a role default like ``"high"``).

    Args:
        env: Environment mapping. Defaults to ``os.environ`` when ``None``.
        agent_type: Agent role name (e.g. ``"executor"``, ``"explore-low"``).
        inherited_leader_model: Optional explicit leader model that workers
            should inherit unless overridden.
        preferred_reasoning: Optional role-default reasoning effort
            (``"low"`` | ``"medium"`` | ``"high"`` | ``"xhigh"``).

    Returns:
        Normalized argv list ready to append to a worker launch command.
    """
    effective_env: dict[str, str] = env if env is not None else dict(os.environ)

    inherited_args: list[str] = []
    if isinstance(inherited_leader_model, str) and inherited_leader_model.strip():
        inherited_args.extend([_MODEL_FLAG, inherited_leader_model.strip()])

    fallback_model = _resolve_agent_default_model(agent_type, effective_env)

    env_args = _split_worker_launch_args(
        effective_env.get("OMX_TEAM_WORKER_LAUNCH_ARGS")
    )
    all_args = env_args + inherited_args

    env_model = _normalize_optional_model(
        _parse_worker_launch_args(env_args).model_override
    )
    inherited_model = _normalize_optional_model(
        _parse_worker_launch_args(inherited_args).model_override
    )
    selected_model = (
        env_model or inherited_model or _normalize_optional_model(fallback_model)
    )

    return _normalize_launch_args(all_args, selected_model, preferred_reasoning)


__all__ = [
    "TeamSession",
    "apply_created_interactive_session_to_config",
    "cleanup_team_worker_launch_orphaned_mcp_processes",
    "resolve_worker_launch_args_from_env",
    "should_prekill_interactive_shutdown_process_trees",
]
