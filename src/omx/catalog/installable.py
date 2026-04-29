"""Installable artifact tracking.

Port of src/catalog/installable.ts.
"""

from __future__ import annotations

from typing import Any

SETUP_ONLY_INSTALLABLE_SKILLS: set[str] = {"wiki"}
_INSTALLABLE_STATUSES = {"active", "internal"}


def is_catalog_installable_status(status: str | None) -> bool:
    """Check whether a catalog entry status is installable.

    Args:
        status: Status string.

    Returns:
        True if status is "active" or "internal".
    """
    return status in _INSTALLABLE_STATUSES


def get_setup_installable_skill_names(
    manifest: dict[str, Any] | None,
) -> set[str]:
    """Get the set of skill names eligible for setup installation.

    Args:
        manifest: Catalog manifest dict (may be ``None``).

    Returns:
        Set of installable skill names.
    """
    skills = (manifest or {}).get("skills", [])
    installed = {
        skill["name"]
        for skill in skills
        if isinstance(skill, dict)
        and is_catalog_installable_status(skill.get("status"))
    }
    return installed | SETUP_ONLY_INSTALLABLE_SKILLS
