"""OpenClaw adapter.

Port of src/adapt/openclaw.ts.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from omx.adapt.contracts import (
    ADAPT_SCHEMA_VERSION,
    AdaptCapabilityReport,
    AdaptDoctorReport,
    AdaptDoctorIssue,
    AdaptEnvelope,
    AdaptInitResult,
    AdaptOpenClawGatewayObservation,
    AdaptOpenClawHookObservation,
    AdaptOpenClawMetadata,
    AdaptPathSet,
    AdaptPlanningLink,
    AdaptProbeReport,
    AdaptRuntimeObservation,
    AdaptStatusReport,
)

OPENCLAW_HOOK_EVENTS = [
    "session-start",
    "session-end",
    "session-idle",
    "ask-user-question",
    "stop",
]

OPENCLAW_LIFECYCLE_BRIDGE = [
    {"omxEvent": "session-start", "openclawEvent": "session-start"},
    {"omxEvent": "session-end", "openclawEvent": "session-end"},
    {"omxEvent": "session-idle", "openclawEvent": "session-idle"},
    {"omxEvent": "ask-user-question", "openclawEvent": "ask-user-question"},
    {"omxEvent": "session-stop", "openclawEvent": "stop"},
]


def _summarize_observed_state(metadata: dict[str, Any]) -> str:
    """Summarize observed state of OpenClaw adapter."""
    state = metadata.get("observed_state", "not-configured")
    match state:
        case "configured":
            gateways = metadata.get("gateways", [])
            hooks = metadata.get("hooks", [])
            wired = sum(1 for h in hooks if h.get("status") == "wired")
            return (
                f"OpenClaw local adapter evidence is present with "
                f"{len(gateways)} configured gateway(s) and {wired} wired hook mapping(s)."
            )
        case "degraded":
            return "OpenClaw local adapter evidence is partial; config is present but at least one mapped hook is locally blocked."
        case "disabled":
            return "OpenClaw is disabled locally because OMX_OPENCLAW=1 is not set."
        case "missing-config":
            return "OpenClaw is enabled, but no usable local config file was found."
        case "invalid-config":
            return "OpenClaw config evidence exists, but it is invalid or incomplete."
        case _:
            return "OpenClaw has no local config or gateway evidence yet."


def _observe_openclaw(
    paths: AdaptPathSet,
    planning: AdaptPlanningLink,
) -> AdaptOpenClawMetadata:
    """Observe local OpenClaw state.

    This is a structural port; actual OpenClaw config inspection
    is deferred since it depends on the openclaw config module.
    """
    # Structural stub: returns not-configured state since
    # inspectOpenClawConfig is not yet ported
    gateways: list[AdaptOpenClawGatewayObservation] = []
    hooks: list[AdaptOpenClawHookObservation] = []

    for event in OPENCLAW_HOOK_EVENTS:
        hooks.append(
            AdaptOpenClawHookObservation(
                event=event,
                gateway=None,
                gateway_type=None,
                status="unmapped",
                detail="No enabled OpenClaw mapping exists for this event.",
            )
        )

    metadata = AdaptOpenClawMetadata(
        observed_state="not-configured",
        observed_detail="",
        config={
            "activationGateEnabled": False,
            "commandGateEnabled": False,
            "configPath": "",
            "configExists": False,
            "source": None,
            "explicitConfigPresent": False,
            "aliasConfigPresent": False,
            "aliasSources": [],
            "explicitOverridesAliases": False,
            "warnings": [],
        },
        gateways=gateways,
        hooks=hooks,
        lifecycle_bridge=[dict(e) for e in OPENCLAW_LIFECYCLE_BRIDGE],
        bootstrap={
            "adapterConfigPath": paths.config_path,
            "envelopePath": paths.envelope_path,
            "reportPaths": [paths.probe_report_path, paths.status_report_path],
            "planningArtifactPaths": [
                *([planning.prd_path] if planning.prd_path else []),
                *planning.test_spec_paths,
                *planning.deep_interview_spec_paths,
            ],
        },
    )
    summary_data = {
        "observed_state": metadata.observed_state,
        "gateways": [{"status": "unmapped"}] * len(gateways),
        "hooks": [{"status": h.status} for h in hooks],
    }
    metadata.observed_detail = _summarize_observed_state(summary_data)
    return metadata


def build_openclaw_envelope(
    paths: AdaptPathSet,
    planning: AdaptPlanningLink,
    capabilities: list[AdaptCapabilityReport],
    now: datetime,
) -> AdaptEnvelope:
    """Build an OpenClaw adapter envelope.

    Args:
        paths: Adapter path set.
        planning: Planning link.
        capabilities: Capability reports.
        now: Current datetime.

    Returns:
        AdaptEnvelope for OpenClaw.
    """
    openclaw = _observe_openclaw(paths, planning)
    return AdaptEnvelope(
        schema_version=ADAPT_SCHEMA_VERSION,
        generated_at=now.isoformat(),
        target="openclaw",
        display_name="OpenClaw",
        summary="OMX-owned OpenClaw adapter metadata built from existing local config, gateway, and lifecycle seams.",
        adapter_paths=paths,
        planning=planning,
        capabilities=capabilities,
        constraints=[
            "Status reflects local OMX/OpenClaw adapter evidence only; it does not claim downstream OpenClaw acknowledgement.",
            "Bootstrap output stays under .omx/adapters/openclaw/... and does not mutate .omx/state or upstream OpenClaw config.",
            "Command gateways remain gated by OMX_OPENCLAW_COMMAND=1 even when OMX_OPENCLAW=1 is enabled.",
        ],
        openclaw=openclaw,
    )


def build_openclaw_probe_report(
    paths: AdaptPathSet,
    planning: AdaptPlanningLink,
    capabilities: list[AdaptCapabilityReport],
    now: datetime,
) -> AdaptProbeReport:
    """Build an OpenClaw probe report.

    Args:
        paths: Adapter path set.
        planning: Planning link.
        capabilities: Capability reports.
        now: Current datetime.

    Returns:
        AdaptProbeReport for OpenClaw.
    """
    openclaw = _observe_openclaw(paths, planning)
    blocked_hooks = [h for h in openclaw.hooks if h.status == "blocked"]
    next_steps = (
        [f"{h.event}: {h.detail}" for h in blocked_hooks]
        if blocked_hooks
        else [
            "Run omx adapt openclaw init --write to materialize adapter-owned OpenClaw artifacts.",
            "Confirm downstream OpenClaw behavior separately; this probe reports local wiring evidence only.",
        ]
    )
    return AdaptProbeReport(
        schema_version=ADAPT_SCHEMA_VERSION,
        timestamp=now.isoformat(),
        target="openclaw",
        phase="foundation",
        summary=openclaw.observed_detail,
        adapter_paths=paths,
        planning=planning,
        capabilities=capabilities,
        target_runtime=AdaptRuntimeObservation(
            state="not-implemented",
            detail="Probe reports only local OpenClaw adapter evidence; remote runtime acceptance remains unobserved.",
        ),
        openclaw=openclaw,
        next_steps=next_steps,
    )


def build_openclaw_status_report(
    paths: AdaptPathSet,
    planning: AdaptPlanningLink,
    capabilities: list[AdaptCapabilityReport],
    now: datetime,
) -> AdaptStatusReport:
    """Build an OpenClaw status report.

    Args:
        paths: Adapter path set.
        planning: Planning link.
        capabilities: Capability reports.
        now: Current datetime.

    Returns:
        AdaptStatusReport for OpenClaw.
    """
    initialized = (
        Path(paths.config_path).exists() and Path(paths.envelope_path).exists()
    )
    openclaw = _observe_openclaw(paths, planning)
    return AdaptStatusReport(
        schema_version=ADAPT_SCHEMA_VERSION,
        timestamp=now.isoformat(),
        target="openclaw",
        phase="foundation",
        summary=(
            f"OpenClaw adapter artifacts exist under .omx/adapters/openclaw/... and local runtime evidence is {openclaw.observed_state}."
            if initialized
            else f"OpenClaw adapter artifacts have not been written yet; local runtime evidence is {openclaw.observed_state}."
        ),
        adapter={
            "state": "initialized" if initialized else "not-initialized",
            "detail": (
                "Adapter-owned OpenClaw artifacts are present under .omx/adapters/openclaw/..."
                if initialized
                else "Run init --write to create adapter-owned OpenClaw artifacts."
            ),
            "configPath": paths.config_path,
            "envelopePath": paths.envelope_path,
        },
        target_runtime=AdaptRuntimeObservation(
            state="unknown",
            detail="Status reflects local config/env/gateway wiring evidence only, not authoritative remote OpenClaw runtime health.",
        ),
        planning=planning,
        capabilities=capabilities,
        openclaw=openclaw,
    )


def build_openclaw_doctor_report(
    paths: AdaptPathSet,
    planning: AdaptPlanningLink,
    now: datetime,
) -> AdaptDoctorReport:
    """Build an OpenClaw doctor report.

    Args:
        paths: Adapter path set.
        planning: Planning link.
        now: Current datetime.

    Returns:
        AdaptDoctorReport for OpenClaw.
    """
    openclaw = _observe_openclaw(paths, planning)
    issues: list[AdaptDoctorIssue] = []

    if not Path(paths.config_path).exists() or not Path(paths.envelope_path).exists():
        issues.append(
            AdaptDoctorIssue(
                code="adapter_not_initialized",
                message="No OpenClaw adapter artifacts exist under .omx/adapters/openclaw.",
            )
        )

    if not openclaw.config.get("activationGateEnabled"):
        issues.append(
            AdaptDoctorIssue(
                code="openclaw_disabled",
                message="OMX_OPENCLAW=1 is required before OpenClaw local config can be observed.",
            )
        )
    elif openclaw.observed_state in ("missing-config", "not-configured"):
        issues.append(
            AdaptDoctorIssue(
                code="openclaw_config_missing",
                message=f"No usable OpenClaw config was found at {openclaw.config.get('configPath', '')}.",
            )
        )
    elif openclaw.observed_state == "invalid-config":
        issues.append(
            AdaptDoctorIssue(
                code="openclaw_config_invalid",
                message="OpenClaw config keys are present but do not form a valid runtime config.",
            )
        )

    if any(h.status == "blocked" for h in openclaw.hooks):
        issues.append(
            AdaptDoctorIssue(
                code="openclaw_hook_blocked",
                message="At least one enabled OpenClaw hook is locally blocked by missing gateway evidence or command-gateway opt-in.",
            )
        )

    if not planning.prd_path:
        issues.append(
            AdaptDoctorIssue(
                code="planning_artifacts_missing",
                message="No canonical OMX PRD artifact is available to link into the OpenClaw adapter envelope.",
            )
        )

    return AdaptDoctorReport(
        schema_version=ADAPT_SCHEMA_VERSION,
        timestamp=now.isoformat(),
        target="openclaw",
        phase="foundation",
        summary="OpenClaw doctor reports local adapter readiness and local gateway wiring evidence only.",
        issues=issues,
        next_steps=[
            "Run omx adapt openclaw init --write.",
            "Set OMX_OPENCLAW=1 and configure notifications.openclaw or compatible aliases in ~/.codex/.omx-config.json.",
            "If command gateways are configured, also set OMX_OPENCLAW_COMMAND=1 before expecting command mappings to be locally ready.",
        ],
    )


def init_openclaw_foundation(
    paths: AdaptPathSet,
    planning: AdaptPlanningLink,
    capabilities: list[AdaptCapabilityReport],
    write: bool,
    now: datetime,
) -> AdaptInitResult:
    """Initialize OpenClaw adapter foundation.

    Args:
        paths: Adapter path set.
        planning: Planning link.
        capabilities: Capability reports.
        write: Whether to write files to disk.
        now: Current datetime.

    Returns:
        AdaptInitResult describing the operation outcome.
    """
    import json

    envelope = build_openclaw_envelope(paths, planning, capabilities, now)
    preview_paths = [
        paths.adapter_root,
        paths.config_path,
        paths.envelope_path,
        paths.reports_dir,
        paths.probe_report_path,
        paths.status_report_path,
    ]
    wrote_paths: list[str] = []

    if write:
        Path(paths.reports_dir).mkdir(parents=True, exist_ok=True)
        config = {
            "schemaVersion": ADAPT_SCHEMA_VERSION,
            "target": "openclaw",
            "createdAt": now.isoformat(),
            "phase": "openclaw-local-observation",
            "observedState": envelope.openclaw.observed_state
            if envelope.openclaw
            else "not-configured",
            "summary": "OMX-owned OpenClaw adapter bootstrap metadata.",
            "lifecycleBridge": envelope.openclaw.lifecycle_bridge
            if envelope.openclaw
            else [],
            "constraints": envelope.constraints,
        }
        Path(paths.config_path).write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )
        # Simplified envelope serialization
        envelope_data = {
            "schemaVersion": envelope.schema_version,
            "generatedAt": envelope.generated_at,
            "target": envelope.target,
            "displayName": envelope.display_name,
            "summary": envelope.summary,
        }
        Path(paths.envelope_path).write_text(
            json.dumps(envelope_data, indent=2) + "\n", encoding="utf-8"
        )
        wrote_paths.extend([paths.config_path, paths.envelope_path])

    return AdaptInitResult(
        schema_version=ADAPT_SCHEMA_VERSION,
        timestamp=now.isoformat(),
        target="openclaw",
        write=write,
        summary=(
            "OpenClaw adapter metadata was written under .omx/adapters/openclaw/..."
            if write
            else "OpenClaw adapter bootstrap preview is ready; rerun with --write to materialize it."
        ),
        preview_paths=preview_paths,
        wrote_paths=wrote_paths,
        envelope=envelope,
    )
