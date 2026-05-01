"""Plugin event dispatch and runtime wrapper.

Port of src/hooks/extensibility/dispatcher.ts and runtime.ts.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.hooks.loader import (
    discover_hook_plugins,
    is_hook_plugins_enabled,
    resolve_hook_plugin_timeout_ms,
    sanitize_plugin_id,
)
from omx.hooks.types import HookEventEnvelope
from omx.utils.paths import omx_logs_dir

RESULT_PREFIX = "__OMX_PLUGIN_RESULT__ "


def dispatch_hook_event(
    cwd: str,
    event: HookEventEnvelope,
) -> dict[str, Any]:
    """Dispatch a hook event to all discovered plugins.

    Discovers plugins in .omx/hooks/, executes each with the event as
    JSON on stdin, collects results, and appends to the hooks log.

    Args:
        cwd: Working directory for plugin discovery.
        event: The hook event envelope to dispatch.

    Returns:
        Dict with "dispatched" bool, "plugin_count", and "results" list.
    """
    if not is_hook_plugins_enabled():
        return {"dispatched": False, "reason": "plugins_disabled"}

    plugins = discover_hook_plugins(cwd)
    if not plugins:
        return {"dispatched": True, "reason": "ok", "plugin_count": 0, "results": []}

    timeout_ms = resolve_hook_plugin_timeout_ms()
    results: list[dict[str, Any]] = []

    for plugin_path in plugins:
        result = _run_plugin(plugin_path, event, timeout_ms)
        results.append(result)

    _append_hooks_log(cwd, event, results)

    return {
        "dispatched": True,
        "reason": "ok",
        "plugin_count": len(plugins),
        "results": results,
    }


def _run_plugin(
    plugin_path: Path,
    event: HookEventEnvelope,
    timeout_ms: int,
) -> dict[str, Any]:
    """Execute a single plugin with the event envelope."""
    plugin_id = sanitize_plugin_id(plugin_path.name)
    event_json = json.dumps(event.to_dict())

    try:
        if plugin_path.suffix == ".py":
            cmd = [sys.executable, str(plugin_path)]
        elif plugin_path.suffix == ".sh":
            cmd = ["bash", str(plugin_path)]
        else:
            cmd = ["node", str(plugin_path)]

        result = subprocess.run(
            cmd,
            input=event_json,
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000.0,
            check=False,
        )

        # Parse plugin result from stdout
        plugin_result: dict[str, Any] | None = None
        for line in result.stdout.splitlines():
            if line.startswith(RESULT_PREFIX):
                try:
                    plugin_result = json.loads(line[len(RESULT_PREFIX) :])
                except json.JSONDecodeError:
                    pass

        return {
            "plugin_id": plugin_id,
            "ok": result.returncode == 0,
            "exit_code": result.returncode,
            "result": plugin_result,
        }
    except subprocess.TimeoutExpired:
        return {"plugin_id": plugin_id, "ok": False, "error": "timeout"}
    except Exception as exc:
        return {"plugin_id": plugin_id, "ok": False, "error": str(exc)}


def _append_hooks_log(
    cwd: str,
    event: HookEventEnvelope,
    results: list[dict[str, Any]],
) -> None:
    """Append dispatch event to hooks JSONL log."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = omx_logs_dir(Path(cwd)) / f"hooks-{date}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event.event,
        "plugin_count": len(results),
        "results": results,
    }

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# ── Runtime dispatch wrapper ─────────────────────────────────────────────────
# Port of src/hooks/extensibility/runtime.ts


@dataclass
class HookRuntimeDispatchResult:
    """Result of a runtime hook dispatch.

    Attributes:
        dispatched: Whether the event was dispatched to plugins.
        reason: Reason string ('ok' or 'plugins_disabled').
        result: The underlying dispatch result dict.
    """

    dispatched: bool = False
    reason: str = ""
    result: dict[str, Any] = field(default_factory=dict)


def dispatch_hook_event_runtime(
    cwd: str,
    event: HookEventEnvelope,
) -> HookRuntimeDispatchResult:
    """Runtime dispatch wrapper with source-based enable logic.

    Native and derived events are always dispatched. Other sources
    require plugins to be explicitly enabled.

    Args:
        cwd: Working directory.
        event: The hook event envelope to dispatch.

    Returns:
        HookRuntimeDispatchResult with dispatch outcome.
    """
    enabled = (
        True if event.source in ("native", "derived") else is_hook_plugins_enabled()
    )

    if not enabled:
        return HookRuntimeDispatchResult(
            dispatched=False,
            reason="plugins_disabled",
            result={
                "enabled": False,
                "reason": "disabled",
                "event": event.event,
                "source": event.source,
                "plugin_count": 0,
                "results": [],
            },
        )

    result = dispatch_hook_event(cwd, event)

    return HookRuntimeDispatchResult(
        dispatched=True,
        reason="ok",
        result=result,
    )
