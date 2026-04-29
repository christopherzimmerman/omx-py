"""Adapter framework contracts and data structures.

Port of src/adapt/contracts.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


ADAPT_SCHEMA_VERSION = "1.0"

ADAPT_TARGETS = ("openclaw", "hermes")


class AdaptTarget(StrEnum):
    """Supported adapt targets."""

    OPENCLAW = "openclaw"
    HERMES = "hermes"


class AdaptSubcommand(StrEnum):
    """Adapt subcommands."""

    PROBE = "probe"
    STATUS = "status"
    INIT = "init"
    ENVELOPE = "envelope"
    DOCTOR = "doctor"


class AdaptCapabilityOwnership(StrEnum):
    """Ownership level for an adapt capability."""

    OMX_OWNED = "omx-owned"
    SHARED_CONTRACT = "shared-contract"
    TARGET_OBSERVED = "target-observed"


class AdaptCapabilityStatus(StrEnum):
    """Status of an adapt capability."""

    READY = "ready"
    STUB = "stub"
    UNSUPPORTED = "unsupported"


@dataclass
class AdaptCapabilityReport:
    """Report on a single adapter capability.

    Attributes:
        id: Unique capability identifier.
        label: Human-readable label.
        ownership: Ownership level.
        status: Current status.
        summary: Description of capability state.
    """

    id: str
    label: str
    ownership: str
    status: str
    summary: str


@dataclass
class AdaptTargetDescriptor:
    """Descriptor for an adapt target.

    Attributes:
        target: Target identifier.
        display_name: Human-readable target name.
        summary: Target summary.
        followup_hint: Hint for follow-up actions.
        capabilities: List of capability reports.
    """

    target: str
    display_name: str
    summary: str
    followup_hint: str
    capabilities: list[AdaptCapabilityReport] = field(default_factory=list)


@dataclass
class AdaptPathSet:
    """Paths for adapter artifacts.

    Attributes:
        adapter_root: Root directory for the adapter.
        config_path: Path to adapter config.
        envelope_path: Path to adapter envelope.
        reports_dir: Directory for reports.
        probe_report_path: Path to probe report.
        status_report_path: Path to status report.
    """

    adapter_root: str
    config_path: str
    envelope_path: str
    reports_dir: str
    probe_report_path: str
    status_report_path: str


@dataclass
class AdaptPlanningLink:
    """Link to planning artifacts.

    Attributes:
        prd_path: Path to PRD or None.
        test_spec_paths: Paths to test specs.
        deep_interview_spec_paths: Paths to deep interview specs.
        summary: Summary of planning linkage.
    """

    prd_path: str | None
    test_spec_paths: list[str] = field(default_factory=list)
    deep_interview_spec_paths: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class AdaptOpenClawGatewayObservation:
    """Observation of an OpenClaw gateway.

    Attributes:
        name: Gateway name.
        type: Gateway type (http or command).
        configured: Whether the gateway is configured.
        command_gate_required: Whether command gate is required.
        command_gate_enabled: Whether command gate is enabled.
        timeout_ms: Timeout in milliseconds or None.
    """

    name: str
    type: str  # "http" | "command"
    configured: bool
    command_gate_required: bool
    command_gate_enabled: bool
    timeout_ms: int | None = None


@dataclass
class AdaptOpenClawHookObservation:
    """Observation of an OpenClaw hook mapping.

    Attributes:
        event: Event name.
        gateway: Gateway name or None.
        gateway_type: Gateway type or None.
        status: Hook status.
        detail: Detail description.
    """

    event: str
    gateway: str | None
    gateway_type: str | None  # "http" | "command" | None
    status: str  # "wired" | "blocked" | "unmapped"
    detail: str


@dataclass
class AdaptOpenClawMetadata:
    """OpenClaw adapter metadata.

    Attributes:
        observed_state: Current observed state.
        observed_detail: Detailed observation text.
        config: Configuration details.
        gateways: Gateway observations.
        hooks: Hook observations.
        lifecycle_bridge: Lifecycle bridge mappings.
        bootstrap: Bootstrap metadata.
    """

    observed_state: str
    observed_detail: str
    config: dict[str, Any] = field(default_factory=dict)
    gateways: list[AdaptOpenClawGatewayObservation] = field(default_factory=list)
    hooks: list[AdaptOpenClawHookObservation] = field(default_factory=list)
    lifecycle_bridge: list[dict[str, str]] = field(default_factory=list)
    bootstrap: dict[str, Any] | None = None


@dataclass
class AdaptRuntimeObservation:
    """Runtime observation for an adapt target.

    Attributes:
        state: Runtime state string.
        detail: Detail description.
        evidence: Optional evidence data.
    """

    state: str
    detail: str
    evidence: dict[str, Any] | None = None


@dataclass
class AdaptBootstrapMetadata:
    """Bootstrap metadata for an adapt target.

    Attributes:
        summary: Bootstrap summary.
        event_bridge: Event bridge mappings.
        commands: Available commands.
        next_steps: Recommended next steps.
    """

    summary: str
    event_bridge: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


@dataclass
class AdaptEnvelope:
    """Adapter envelope — full adapter metadata.

    Attributes:
        schema_version: Schema version string.
        generated_at: ISO timestamp of generation.
        target: Adapt target.
        display_name: Human-readable target name.
        summary: Envelope summary.
        adapter_paths: Paths for adapter artifacts.
        planning: Planning artifact linkage.
        capabilities: Capability reports.
        constraints: List of constraints.
        target_runtime: Runtime observation.
        bootstrap: Bootstrap metadata.
        openclaw: OpenClaw-specific metadata.
    """

    schema_version: str
    generated_at: str
    target: str
    display_name: str
    summary: str
    adapter_paths: AdaptPathSet
    planning: AdaptPlanningLink
    capabilities: list[AdaptCapabilityReport] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    target_runtime: AdaptRuntimeObservation | None = None
    bootstrap: AdaptBootstrapMetadata | None = None
    openclaw: AdaptOpenClawMetadata | None = None


@dataclass
class AdaptProbeReport:
    """Probe report for an adapt target.

    Attributes:
        schema_version: Schema version string.
        timestamp: ISO timestamp.
        target: Adapt target.
        phase: Probe phase.
        summary: Report summary.
        adapter_paths: Paths for adapter artifacts.
        planning: Planning linkage.
        capabilities: Capability reports.
        target_runtime: Runtime observation.
        openclaw: OpenClaw-specific metadata.
        next_steps: Recommended next steps.
    """

    schema_version: str
    timestamp: str
    target: str
    phase: str
    summary: str
    adapter_paths: AdaptPathSet
    planning: AdaptPlanningLink
    capabilities: list[AdaptCapabilityReport] = field(default_factory=list)
    target_runtime: AdaptRuntimeObservation | None = None
    openclaw: AdaptOpenClawMetadata | None = None
    next_steps: list[str] = field(default_factory=list)


@dataclass
class AdaptStatusReport:
    """Status report for an adapt target.

    Attributes:
        schema_version: Schema version string.
        timestamp: ISO timestamp.
        target: Adapt target.
        phase: Status phase.
        summary: Report summary.
        adapter: Adapter state info.
        target_runtime: Runtime observation.
        planning: Planning linkage.
        capabilities: Capability reports.
        openclaw: OpenClaw-specific metadata.
    """

    schema_version: str
    timestamp: str
    target: str
    phase: str
    summary: str
    adapter: dict[str, Any] = field(default_factory=dict)
    target_runtime: AdaptRuntimeObservation | None = None
    planning: AdaptPlanningLink | None = None
    capabilities: list[AdaptCapabilityReport] = field(default_factory=list)
    openclaw: AdaptOpenClawMetadata | None = None


@dataclass
class AdaptDoctorIssue:
    """A single doctor diagnostic issue.

    Attributes:
        code: Issue code.
        message: Human-readable message.
    """

    code: str
    message: str


@dataclass
class AdaptDoctorReport:
    """Doctor report for an adapt target.

    Attributes:
        schema_version: Schema version string.
        timestamp: ISO timestamp.
        target: Adapt target.
        phase: Doctor phase.
        summary: Report summary.
        issues: Diagnostic issues.
        next_steps: Recommended next steps.
    """

    schema_version: str
    timestamp: str
    target: str
    phase: str
    summary: str
    issues: list[AdaptDoctorIssue] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)


@dataclass
class AdaptInitResult:
    """Result of an adapt init operation.

    Attributes:
        schema_version: Schema version string.
        timestamp: ISO timestamp.
        target: Adapt target.
        write: Whether artifacts were written.
        summary: Result summary.
        preview_paths: Paths that would be created.
        wrote_paths: Paths that were actually written.
        envelope: The generated envelope.
    """

    schema_version: str
    timestamp: str
    target: str
    write: bool
    summary: str
    preview_paths: list[str] = field(default_factory=list)
    wrote_paths: list[str] = field(default_factory=list)
    envelope: AdaptEnvelope | None = None
