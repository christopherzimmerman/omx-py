"""``omx tmux-hook`` — manage the tmux prompt-injection workaround.

Port of ``src/cli/tmux-hook.ts``. Sync, stdlib-only.

Subcommands:
    init        Create ``.omx/tmux-hook.json``
    status      Show config + runtime state summary
    validate    Validate config and tmux target reachability
    test        Run a synthetic notify-hook turn

This is a minimal port — only the config-file lifecycle and tmux
reachability check. The full TS-side ``test`` (synthetic notify-hook
turn) requires a port of ``scripts/tmux-hook-engine.ts`` which is out of
scope for Phase 9.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "target": {"type": "pane", "value": ""},
    "allowed_modes": ["ralph", "ultrawork", "team"],
    "cooldown_ms": 15000,
    "max_injections_per_session": 200,
    "prompt_template": "Continue from current mode state. [OMX_TMUX_INJECT]",
    "marker": "[OMX_TMUX_INJECT]",
    "dry_run": False,
    "log_level": "info",
    "skip_if_scrolling": True,
}

HELP = """\
Usage:
  omx tmux-hook init       Create .omx/tmux-hook.json
  omx tmux-hook status     Show config + runtime state summary
  omx tmux-hook validate   Validate config and tmux target reachability
  omx tmux-hook test       Run a synthetic notify-hook turn (end-to-end)\
"""


def _omx_dir(cwd: Path) -> Path:
    return cwd / ".omx"


def _config_path(cwd: Path) -> Path:
    return _omx_dir(cwd) / "tmux-hook.json"


def _state_path(cwd: Path) -> Path:
    return _omx_dir(cwd) / "state" / "tmux-hook-state.json"


def _detect_initial_tmux_target() -> tuple[str, str] | None:
    """Best-effort detection of the current tmux pane and session.

    Returns:
        ``(pane_id, session_name)`` or ``None`` when not in tmux.
    """
    if not shutil.which("tmux"):
        return None
    try:
        pane = subprocess.run(
            ["tmux", "display-message", "-p", "#{pane_id}"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        session = subprocess.run(
            ["tmux", "display-message", "-p", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

    pane_id = pane.stdout.strip() if pane.returncode == 0 else ""
    session_name = session.stdout.strip() if session.returncode == 0 else ""
    if not pane_id and not session_name:
        return None
    return pane_id, session_name


def _validate_config(raw: Any) -> dict[str, Any]:
    """Validate a parsed tmux-hook config dict. Raises ValueError on issues."""
    if not isinstance(raw, dict):
        raise ValueError("tmux-hook config must be a JSON object")

    if raw.get("enabled") not in (True, False):
        raise ValueError("`enabled` must be boolean")

    target = raw.get("target")
    if not isinstance(target, dict):
        raise ValueError("`target` is required")
    if target.get("type") not in ("session", "pane"):
        raise ValueError('`target.type` must be "session" or "pane"')
    if not isinstance(target.get("value"), str) or not target["value"].strip():
        raise ValueError("`target.value` must be a non-empty string")

    allowed = raw.get("allowed_modes")
    if (
        not isinstance(allowed, list)
        or not allowed
        or not all(isinstance(v, str) for v in allowed)
    ):
        raise ValueError("`allowed_modes` must be a non-empty string array")

    cooldown = raw.get("cooldown_ms")
    if not isinstance(cooldown, (int, float)) or cooldown < 0:
        raise ValueError("`cooldown_ms` must be a non-negative number")

    max_injections = raw.get("max_injections_per_session")
    if not isinstance(max_injections, int) or max_injections < 1:
        raise ValueError("`max_injections_per_session` must be >= 1")

    prompt_template = raw.get("prompt_template")
    if not isinstance(prompt_template, str) or not prompt_template.strip():
        raise ValueError("`prompt_template` must be a non-empty string")

    marker = raw.get("marker")
    if not isinstance(marker, str) or not marker.strip():
        raise ValueError("`marker` must be a non-empty string")

    if raw.get("dry_run") not in (True, False):
        raise ValueError("`dry_run` must be boolean")

    if raw.get("log_level") not in ("error", "info", "debug"):
        raise ValueError("`log_level` must be one of: error, info, debug")

    return raw


def init_tmux_hook_config(cwd: Path, *, silent: bool = False) -> dict[str, Any]:
    """Create ``.omx/tmux-hook.json`` if missing.

    Returns:
        ``{"config_path": str, "created": bool, "used_placeholder_target": bool,
        "detected_session": str | None}``.
    """
    cfg_path = _config_path(cwd)
    if cfg_path.exists():
        return {
            "config_path": str(cfg_path),
            "created": False,
            "used_placeholder_target": False,
            "detected_session": None,
        }

    detected = _detect_initial_tmux_target()
    detected_session: str | None = None
    used_placeholder = True
    config = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy

    if detected is not None:
        pane_id, session_name = detected
        if pane_id:
            config["target"] = {"type": "pane", "value": pane_id}
            used_placeholder = False
        elif session_name:
            config["target"] = {"type": "session", "value": session_name}
            used_placeholder = False
        if session_name:
            detected_session = session_name

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    if not silent:
        print(f"Created {cfg_path}")
        if detected_session:
            print(f"Detected tmux session: {detected_session}")
        if used_placeholder:
            print(
                "Could not auto-detect a tmux target. "
                "Edit .omx/tmux-hook.json when ready."
            )
    return {
        "config_path": str(cfg_path),
        "created": True,
        "used_placeholder_target": used_placeholder,
        "detected_session": detected_session,
    }


def _read_state(cwd: Path) -> dict[str, Any]:
    state_path = _state_path(cwd)
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def show_status(cwd: Path) -> None:
    """Print a short status summary for the current ``.omx/tmux-hook.json``."""
    cfg_path = _config_path(cwd)
    if not cfg_path.exists():
        init_tmux_hook_config(cwd, silent=True)
    try:
        config = _validate_config(json.loads(cfg_path.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error: invalid tmux-hook config: {exc}", file=sys.stderr)
        sys.exit(1)

    state = _read_state(cwd)
    print(f"config_path: {cfg_path}")
    print(f"enabled: {config['enabled']}")
    target = config["target"]
    print(f"target: type={target['type']} value={target['value']}")
    print(f"allowed_modes: {','.join(config['allowed_modes'])}")
    print(f"cooldown_ms: {config['cooldown_ms']}")
    print(f"max_injections_per_session: {config['max_injections_per_session']}")
    print(f"dry_run: {config['dry_run']}")
    print(f"log_level: {config['log_level']}")
    if state:
        total = state.get("total_injections", 0)
        last_ts = state.get("last_event_at", "?")
        last_reason = state.get("last_reason", "?")
        print(f"total_injections: {total}")
        print(f"last_event_at: {last_ts}")
        print(f"last_reason: {last_reason}")


def validate(cwd: Path) -> None:
    """Validate the config and check that the tmux target exists."""
    cfg_path = _config_path(cwd)
    if not cfg_path.exists():
        print("tmux-hook config missing. Run: omx tmux-hook init", file=sys.stderr)
        sys.exit(1)
    try:
        config = _validate_config(json.loads(cfg_path.read_text(encoding="utf-8")))
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"Error: invalid tmux-hook config: {exc}", file=sys.stderr)
        sys.exit(1)

    if not shutil.which("tmux"):
        print("Error: tmux is not installed", file=sys.stderr)
        sys.exit(1)

    target = config["target"]
    if target["type"] == "session":
        cmd = ["tmux", "has-session", "-t", target["value"]]
    else:
        cmd = ["tmux", "list-panes", "-t", target["value"], "-F", "#{pane_id}"]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3, check=False
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        print(f"Error: failed to probe tmux target: {exc}", file=sys.stderr)
        sys.exit(1)

    if result.returncode != 0:
        print(
            f"Error: target {target['type']}={target['value']} not found",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"ok: config valid; target {target['type']}={target['value']} reachable")


def synthetic_test(cwd: Path) -> None:
    """Stub for ``omx tmux-hook test`` — not yet wired in the Python port."""
    cfg_path = _config_path(cwd)
    if not cfg_path.exists():
        print("tmux-hook config missing. Run: omx tmux-hook init", file=sys.stderr)
        sys.exit(1)
    state_dir = _state_path(cwd).parent
    state_dir.mkdir(parents=True, exist_ok=True)
    # write a synthetic state row so the status command has something to read
    state = _read_state(cwd)
    state["total_injections"] = int(state.get("total_injections", 0)) + 1
    state["last_event_at"] = datetime.now(timezone.utc).isoformat()
    state["last_reason"] = "synthetic_test"
    _state_path(cwd).write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"Recorded synthetic notify-hook event in {_state_path(cwd)}")


def handle_tmux_hook(args: list[str]) -> None:
    """Top-level handler for ``omx tmux-hook``."""
    cwd = Path.cwd()
    sub = args[0] if args else "status"

    if sub in ("--help", "-h", "help"):
        print(HELP)
        return
    if sub == "init":
        init_tmux_hook_config(cwd)
        return
    if sub == "status":
        show_status(cwd)
        return
    if sub == "validate":
        validate(cwd)
        return
    if sub == "test":
        synthetic_test(cwd)
        return
    print(f"Unknown tmux-hook subcommand: {sub}", file=sys.stderr)
    print(HELP, file=sys.stderr)
    sys.exit(1)
