"""Adapter target registry.

Port of src/adapt/registry.ts.
"""

from __future__ import annotations

from omx.adapt.contracts import (
    ADAPT_TARGETS,
    AdaptCapabilityReport,
    AdaptTargetDescriptor,
)


def _capability(
    id: str,
    label: str,
    ownership: str,
    status: str,
    summary: str,
) -> AdaptCapabilityReport:
    """Create a capability report entry."""
    return AdaptCapabilityReport(
        id=id,
        label=label,
        ownership=ownership,
        status=status,
        summary=summary,
    )


FOUNDATION_CAPABILITIES: list[AdaptCapabilityReport] = [
    _capability(
        "omx-adapter-paths",
        "OMX-owned adapter paths",
        "omx-owned",
        "ready",
        "Adapter artifacts stay under .omx/adapters/<target>/... rather than .omx/state or target internals.",
    ),
    _capability(
        "planning-artifact-linkage",
        "Planning artifact linkage",
        "shared-contract",
        "ready",
        "Envelope output links canonical OMX PRD/test-spec artifacts when they exist.",
    ),
    _capability(
        "foundation-reporting",
        "Foundation reporting surface",
        "shared-contract",
        "ready",
        "Probe, status, envelope, init, and doctor share a target-agnostic output contract.",
    ),
]


TARGET_DESCRIPTORS: dict[str, AdaptTargetDescriptor] = {
    "openclaw": AdaptTargetDescriptor(
        target="openclaw",
        display_name="OpenClaw",
        summary=(
            "OMX-owned adapter around existing OpenClaw notification, "
            "gateway, and lifecycle observation surfaces."
        ),
        followup_hint=(
            "Status reflects local OpenClaw config/env/gateway evidence only; "
            "remote acknowledgement remains out of scope."
        ),
        capabilities=[
            *FOUNDATION_CAPABILITIES,
            _capability(
                "gateway-observation",
                "Gateway observation",
                "target-observed",
                "ready",
                "Local OpenClaw config/env/gateway evidence is observed through existing config and gateway resolution seams.",
            ),
            _capability(
                "lifecycle-bridge",
                "Lifecycle bridge metadata",
                "shared-contract",
                "ready",
                "Probe, status, and envelope surface the existing OMX to OpenClaw lifecycle mapping without claiming remote execution health.",
            ),
        ],
    ),
    "hermes": AdaptTargetDescriptor(
        target="hermes",
        display_name="Hermes",
        summary=(
            "Foundation seam for an OMX-owned adapter around Hermes ACP, "
            "gateway, and persistent-session surfaces."
        ),
        followup_hint=(
            "Hermes adapter reads external ACP, gateway, and session-store "
            "evidence while keeping all writes under .omx/adapters/hermes/."
        ),
        capabilities=[
            *FOUNDATION_CAPABILITIES,
            _capability(
                "persistent-session-observation",
                "Persistent session observation",
                "target-observed",
                "stub",
                "Hermes session-store evidence is read from HERMES_HOME-scoped state.db when available.",
            ),
            _capability(
                "acp-envelope-bridge",
                "ACP envelope bridge",
                "shared-contract",
                "stub",
                "Hermes envelope/bootstrap metadata maps OMX lifecycle intent into ACP and gateway guidance without claiming deep control.",
            ),
        ],
    ),
}


def list_adapt_targets() -> list[AdaptTargetDescriptor]:
    """Return descriptors for all supported adapt targets.

    Returns:
        List of target descriptors in canonical order.
    """
    return [TARGET_DESCRIPTORS[t] for t in ADAPT_TARGETS if t in TARGET_DESCRIPTORS]


def get_adapt_target_descriptor(target: str) -> AdaptTargetDescriptor | None:
    """Look up a target descriptor by name.

    Args:
        target: Target name string.

    Returns:
        The descriptor if found, else None.
    """
    return TARGET_DESCRIPTORS.get(target)
