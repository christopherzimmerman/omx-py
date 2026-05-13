"""``omx codex-home`` — resolve the CODEX_HOME / config path for launch.

Port of ``src/cli/codex-home.ts``. Sync, stdlib-only.

Helpers (also imported by setup tooling) read the persisted setup-scope
and decide whether the runtime should launch with a project-local
``.codex/`` or fall back to the global ``CODEX_HOME``. The CLI surface
prints the resolved values for inspection.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

SETUP_SCOPES = ("user", "project")
LEGACY_SCOPE_MIGRATION = {"project-local": "project"}


def _scope_file(cwd: Path) -> Path:
    return cwd / ".omx" / "setup-scope.json"


def read_persisted_setup_preferences(
    cwd: str | Path,
) -> dict[str, str] | None:
    """Read ``.omx/setup-scope.json``, applying legacy migrations.

    Returns:
        ``{"scope": str, "install_mode": str}`` (either may be absent) or
        ``None`` when the file is missing.
    """
    path = _scope_file(Path(cwd))
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[cli/codex-home] operation failed: {exc}", file=sys.stderr)
        return None
    if not isinstance(raw, dict):
        return None

    result: dict[str, str] = {}
    scope = raw.get("scope")
    if isinstance(scope, str):
        migrated = LEGACY_SCOPE_MIGRATION.get(scope, scope)
        if migrated in SETUP_SCOPES:
            result["scope"] = migrated

    install_mode = raw.get("installMode") or raw.get("install_mode")
    if isinstance(install_mode, str) and install_mode:
        result["install_mode"] = install_mode

    return result or None


def read_persisted_setup_scope(cwd: str | Path) -> str | None:
    """Return just the persisted scope, or ``None`` when unset."""
    prefs = read_persisted_setup_preferences(cwd)
    return prefs.get("scope") if prefs else None


def resolve_codex_home_for_launch(
    cwd: str | Path,
    env: dict[str, str] | None = None,
) -> str | None:
    """Resolve the CODEX_HOME the runtime should launch with.

    Returns ``None`` when the default global home should be used.
    """
    eff_env = env if env is not None else os.environ
    explicit = (eff_env.get("CODEX_HOME") or "").strip()
    if explicit:
        return explicit
    scope = read_persisted_setup_scope(cwd)
    if scope == "project":
        return str(Path(cwd) / ".codex")
    return None


def resolve_codex_config_path_for_launch(
    cwd: str | Path,
    env: dict[str, str] | None = None,
) -> str:
    """Resolve the full ``config.toml`` path the runtime should target."""
    override = resolve_codex_home_for_launch(cwd, env)
    if override:
        return str(Path(override) / "config.toml")
    # Default global path.
    return str(Path.home() / ".codex" / "config.toml")


def handle_codex_home(args: list[str]) -> None:
    """Top-level handler for ``omx codex-home``.

    Subcommands:
        show (default) — print the resolved CODEX_HOME and config path
        scope          — print the persisted setup-scope (if any)
    """
    sub = args[0] if args else "show"
    wants_json = "--json" in args

    if sub in ("--help", "-h", "help"):
        print(
            "Usage: omx codex-home [show|scope] [--json]\n"
            "  show   Print resolved CODEX_HOME and config path (default)\n"
            "  scope  Print persisted setup-scope.json contents"
        )
        return

    cwd = Path.cwd()

    if sub == "scope":
        prefs = read_persisted_setup_preferences(cwd) or {}
        if wants_json:
            print(json.dumps(prefs))
        else:
            scope = prefs.get("scope", "(unset)")
            install_mode = prefs.get("install_mode", "(unset)")
            print(f"scope: {scope}")
            print(f"install_mode: {install_mode}")
        return

    if sub == "show":
        home = resolve_codex_home_for_launch(cwd)
        config_path = resolve_codex_config_path_for_launch(cwd)
        if wants_json:
            payload: dict[str, Any] = {
                "codex_home": home,
                "config_path": config_path,
            }
            print(json.dumps(payload))
            return
        print(f"codex_home: {home or '(default ~/.codex)'}")
        print(f"config_path: {config_path}")
        return

    print(f"Unknown codex-home subcommand: {sub}", file=sys.stderr)
    sys.exit(1)
