"""``omx native-assets`` — report on bundled native binary state.

Port of the public-facing surface from ``src/cli/native-assets.ts``.

The TS module is largely a library (download, extract, verify checksums
for the explore-harness / sparkshell binaries). The Python port relies
on Python-native ``omx.sparkshell.exec`` instead of a packaged Rust
binary, so this CLI is mostly informational: list which native binaries
the runtime expects, where they would live, and whether they exist.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

NATIVE_PRODUCTS = ("omx-explore-harness", "omx-sparkshell")
EXPLORE_BIN_ENV = "OMX_EXPLORE_BIN"
SPARKSHELL_BIN_ENV = "OMX_SPARKSHELL_BIN"
NATIVE_CACHE_DIR_ENV = "OMX_NATIVE_CACHE_DIR"


def resolve_native_cache_root() -> Path:
    """Resolve the cache root that would hold downloaded native binaries."""
    override = os.environ.get(NATIVE_CACHE_DIR_ENV, "").strip()
    if override:
        return Path(override).resolve()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", "").strip() or str(
            Path.home() / "AppData" / "Local"
        )
        return Path(base) / "oh-my-codex" / "native"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip() or str(Path.home() / ".cache")
    return Path(xdg) / "oh-my-codex" / "native"


def _bin_filename(product: str) -> str:
    return f"{product}.exe" if sys.platform == "win32" else product


def _env_override_for_product(product: str) -> str | None:
    if product == "omx-explore-harness":
        return os.environ.get(EXPLORE_BIN_ENV) or None
    if product == "omx-sparkshell":
        return os.environ.get(SPARKSHELL_BIN_ENV) or None
    return None


def _candidate_path(product: str) -> Path:
    """Return the canonical cache path where the binary would live."""
    arch = platform.machine().lower() or "unknown"
    if arch in ("amd64", "x86_64"):
        arch = "x64"
    elif arch in ("arm64", "aarch64"):
        arch = "arm64"
    plat = sys.platform
    cache_root = resolve_native_cache_root()
    return cache_root / "current" / f"{plat}-{arch}" / product / _bin_filename(product)


def native_asset_status() -> list[dict[str, Any]]:
    """Return a per-product status row."""
    rows: list[dict[str, Any]] = []
    for product in NATIVE_PRODUCTS:
        env_override = _env_override_for_product(product)
        candidate = (
            Path(env_override).resolve() if env_override else _candidate_path(product)
        )
        rows.append(
            {
                "product": product,
                "env_override": env_override,
                "candidate_path": str(candidate),
                "exists": candidate.exists(),
            }
        )
    return rows


def handle_native_assets(args: list[str]) -> None:
    """Top-level handler for ``omx native-assets``.

    Subcommands:
        status (default) — show what binaries the runtime would look for
        cache-root       — print the cache root path
    """
    sub = args[0] if args else "status"
    wants_json = "--json" in args

    if sub in ("--help", "-h", "help"):
        print(
            "Usage: omx native-assets [status|cache-root] [--json]\n"
            "  status     Show per-product native binary candidacy (default)\n"
            "  cache-root Print the OMX native asset cache root"
        )
        return

    if sub == "cache-root":
        cache_root = resolve_native_cache_root()
        if wants_json:
            print(json.dumps({"cache_root": str(cache_root)}))
        else:
            print(cache_root)
        return

    if sub == "status":
        rows = native_asset_status()
        if wants_json:
            print(json.dumps({"products": rows}))
            return
        for row in rows:
            mark = "OK" if row["exists"] else "MISSING"
            print(f"{row['product']:24s} {mark:8s} {row['candidate_path']}")
            if row["env_override"]:
                print(f"  env_override: {row['env_override']}")
        return

    print(f"Unknown native-assets subcommand: {sub}", file=sys.stderr)
    sys.exit(1)
