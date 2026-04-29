"""Plugin runner — executes a single hook plugin in a subprocess.

Port of src/hooks/extensibility/plugin-runner.ts. Reads a plugin
request from stdin, dynamically loads and executes the plugin,
and emits a structured result on stdout.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from omx.hooks.sdk import create_hook_plugin_sdk

RESULT_PREFIX = "__OMX_PLUGIN_RESULT__ "


def _read_stdin() -> str:
    """Read all of stdin as a string.

    Returns:
        Trimmed stdin content.
    """
    return sys.stdin.read().strip()


def _emit_result(result: dict[str, Any]) -> None:
    """Write a structured result line to stdout.

    Args:
        result: Result dict to serialise.
    """
    sys.stdout.write(f"{RESULT_PREFIX}{json.dumps(result)}\n")
    sys.stdout.flush()


def _load_plugin_module(plugin_path: str) -> Any:
    """Dynamically load a Python plugin module.

    Args:
        plugin_path: Path to the plugin .py file.

    Returns:
        The loaded module object.

    Raises:
        ImportError: If the module cannot be loaded.
    """
    path = Path(plugin_path)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load plugin: {plugin_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_plugin(request: dict[str, Any]) -> dict[str, Any]:
    """Execute a plugin from a parsed request.

    Args:
        request: Dict with 'pluginPath', 'event', 'cwd', optional 'pluginId'.

    Returns:
        Result dict with 'ok', 'plugin', 'reason', and optional 'error'.
    """
    plugin_id = (
        str(
            request.get("pluginId") or Path(request.get("pluginPath", "unknown")).stem
        ).strip()
        or "unknown"
    )

    try:
        plugin_path = request.get("pluginPath", "")
        if not plugin_path:
            return {"ok": False, "plugin": plugin_id, "reason": "missing_path"}

        module = _load_plugin_module(plugin_path)
        handler = getattr(module, "on_hook_event", None)
        if not callable(handler):
            return {"ok": False, "plugin": plugin_id, "reason": "invalid_export"}

        event = request.get("event", {})
        sdk = create_hook_plugin_sdk(
            cwd=request.get("cwd", "."),
            plugin_name=plugin_id,
            event=event,
            side_effects_enabled=request.get("sideEffectsEnabled", True),
        )

        handler(event, sdk)
        return {"ok": True, "plugin": plugin_id, "reason": "ok"}

    except Exception as exc:
        return {
            "ok": False,
            "plugin": plugin_id,
            "reason": "runner_error",
            "error": str(exc),
        }


def main() -> None:
    """Entry point for subprocess plugin execution."""
    raw = _read_stdin()
    if not raw:
        _emit_result({"ok": False, "plugin": "unknown", "reason": "empty_request"})
        sys.exit(1)

    try:
        request = json.loads(raw)
    except json.JSONDecodeError:
        _emit_result({"ok": False, "plugin": "unknown", "reason": "invalid_json"})
        sys.exit(1)

    result = run_plugin(request)
    _emit_result(result)
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
