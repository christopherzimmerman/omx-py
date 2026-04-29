"""Agent catalog and policy management.

Port of src/agents/policy.ts.
"""

from __future__ import annotations

from typing import Any

NON_NATIVE_AGENT_PROMPT_ASSETS = {
    "explore-harness",
    "sisyphus-lite",
    "team-orchestrator",
}
INSTALLABLE_STATUSES = {"active", "internal"}


def is_native_agent_installable_status(status: str) -> bool:
    """Check if an agent status string is installable ("active" or "internal")."""
    return status in INSTALLABLE_STATUSES


def get_installable_native_agent_names(manifest: dict[str, Any]) -> set[str]:
    """Get set of installable native agent names from a catalog manifest."""
    agents = manifest.get("agents", {})
    return {
        name
        for name, entry in agents.items()
        if isinstance(entry, dict) and entry.get("status") in INSTALLABLE_STATUSES
    }


def get_non_installable_native_agent_names(manifest: dict[str, Any]) -> set[str]:
    """Get set of non-installable native agent names."""
    agents = manifest.get("agents", {})
    return {
        name
        for name, entry in agents.items()
        if isinstance(entry, dict) and entry.get("status") not in INSTALLABLE_STATUSES
    }


def assert_native_agent_canonical_targets(manifest: dict[str, Any]) -> None:
    """Validate that alias/merged agents have valid canonical targets.

    Args:
        manifest: Catalog manifest with an "agents" dict.

    Raises:
        ValueError: If an alias/merged agent references an invalid target.
    """
    agents = manifest.get("agents", {})
    for name, entry in agents.items():
        if not isinstance(entry, dict):
            continue
        status = entry.get("status", "")
        if status not in ("alias", "merged"):
            continue
        canonical = entry.get("canonical")
        if not canonical:
            raise ValueError(
                f"Agent '{name}' (status={status}) must declare a 'canonical' target"
            )
        if canonical not in agents:
            raise ValueError(
                f"Agent '{name}' canonical target '{canonical}' not found in manifest"
            )
        target_entry = agents[canonical]
        if (
            not isinstance(target_entry, dict)
            or target_entry.get("status") not in INSTALLABLE_STATUSES
        ):
            raise ValueError(
                f"Agent '{name}' canonical target '{canonical}' must have installable status"
            )
