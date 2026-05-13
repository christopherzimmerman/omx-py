"""``omx catalog-contract`` — catalog expectations and headline counts.

Port of ``src/cli/catalog-contract.ts``. Sync, stdlib-only.

This CLI surface reports:

* ``--expectations`` (default) — minimum prompt/skill counts the setup
  flow should expect to install, with a small safety buffer below the
  current installable catalog size.
* ``--headlines`` — installable prompt + skill counts straight from the
  catalog manifest.
* ``--json`` — emit a machine-readable JSON envelope.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from omx.catalog.discovery import discover_prompts, discover_skills
from omx.catalog.installable import is_catalog_installable_status

SAFETY_BUFFER = 2
DEFAULT_PROMPT_MIN = 25
DEFAULT_SKILL_MIN = 30


def _count_installable(entries: list[dict[str, Any]]) -> int:
    return sum(
        1
        for entry in entries
        if isinstance(entry, dict)
        and is_catalog_installable_status(entry.get("status", "active"))
    )


def get_catalog_headline_counts() -> dict[str, int] | None:
    """Return ``{"prompts": N, "skills": N}`` or ``None`` if manifest absent.

    The Python port discovers prompts and skills from the packaged assets
    directly (no separate manifest file is required), so this returns a
    valid dict whenever discovery succeeds.
    """
    try:
        prompts = discover_prompts()
        skills = discover_skills()
    except Exception:  # noqa: BLE001
        return None
    return {
        "prompts": _count_installable(prompts),
        "skills": _count_installable(skills),
    }


def get_catalog_expectations() -> dict[str, int]:
    """Return ``{"prompt_min": N, "skill_min": N}``.

    Mirrors TS ``getCatalogExpectations``: when the catalog is empty,
    falls back to the static ``DEFAULT_PROMPT_MIN`` / ``DEFAULT_SKILL_MIN``
    constants; otherwise returns ``count - SAFETY_BUFFER`` clamped to a
    minimum of 1.
    """
    headlines = get_catalog_headline_counts()
    if not headlines:
        return {"prompt_min": DEFAULT_PROMPT_MIN, "skill_min": DEFAULT_SKILL_MIN}
    return {
        "prompt_min": max(1, headlines["prompts"] - SAFETY_BUFFER),
        "skill_min": max(1, headlines["skills"] - SAFETY_BUFFER),
    }


def handle_catalog_contract(args: list[str]) -> None:
    """Top-level handler for ``omx catalog-contract``."""
    wants_json = "--json" in args
    show_headlines = "--headlines" in args
    show_expectations = "--expectations" in args or not show_headlines

    expectations = get_catalog_expectations() if show_expectations else None
    headlines = get_catalog_headline_counts() if show_headlines else None

    if wants_json:
        payload: dict[str, Any] = {}
        if expectations is not None:
            payload["expectations"] = expectations
        if headlines is not None:
            payload["headlines"] = headlines
        print(json.dumps(payload))
        return

    if expectations is not None:
        print(f"prompt_min: {expectations['prompt_min']}")
        print(f"skill_min: {expectations['skill_min']}")
    if headlines is not None:
        print(f"prompts: {headlines['prompts']}")
        print(f"skills: {headlines['skills']}")

    if not expectations and not headlines:
        print("Catalog manifest unavailable.", file=sys.stderr)
        sys.exit(1)
