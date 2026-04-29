"""Adapter framework — module init and capability registration.

Port of src/adapt/index.ts.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from omx.adapt.contracts import (
    ADAPT_SCHEMA_VERSION,
    AdaptDoctorIssue,
    AdaptDoctorReport,
    AdaptEnvelope,
    AdaptInitResult,
    AdaptPlanningLink,
    AdaptProbeReport,
    AdaptStatusReport,
)
from omx.adapt.hermes import (
    apply_hermes_envelope,
    apply_hermes_probe,
    apply_hermes_status as apply_hermes_status,
    build_hermes_bootstrap_metadata as build_hermes_bootstrap_metadata,
    build_hermes_runtime_observation as build_hermes_runtime_observation,
    collect_hermes_evidence,
)
from omx.adapt.openclaw import (
    build_openclaw_doctor_report,
    build_openclaw_envelope,
    build_openclaw_probe_report,
    build_openclaw_status_report,
    init_openclaw_foundation,
)
from omx.adapt.paths import resolve_adapt_paths
from omx.adapt.registry import get_adapt_target_descriptor, list_adapt_targets

FOUNDATION_CONSTRAINTS = [
    "Thin adapter surface only; no bidirectional control plane is claimed in this foundation PR.",
    "No direct writes to .omx/state/... or target runtime internals.",
    "Capability reporting is asymmetric: OMX-owned, shared-contract, and target-observed surfaces are reported separately.",
]


def _to_iso_timestamp(now: datetime | None = None) -> str:
    """Format a datetime as ISO timestamp."""
    return (now or datetime.now(timezone.utc)).isoformat()


def supported_adapt_targets() -> list[str]:
    """Return list of supported adapt target names.

    Returns:
        List of target name strings.
    """
    return [d.target for d in list_adapt_targets()]


def build_adapt_planning_link(cwd: str) -> AdaptPlanningLink:
    """Build a planning artifact link for the given worktree.

    Args:
        cwd: Working directory.

    Returns:
        AdaptPlanningLink with artifact paths.
    """
    try:
        from omx.planning.artifacts import read_latest_planning_artifacts

        selection = read_latest_planning_artifacts(cwd)
    except ImportError:
        selection = type(
            "Sel",
            (),
            {
                "prd_path": None,
                "test_spec_paths": [],
                "deep_interview_spec_paths": [],
            },
        )()

    if not selection.prd_path:
        return AdaptPlanningLink(
            prd_path=None,
            test_spec_paths=[],
            deep_interview_spec_paths=[],
            summary="No canonical OMX PRD/test-spec artifacts are present in this worktree.",
        )

    test_count = len(selection.test_spec_paths)
    test_summary = (
        f"{test_count} matching test spec artifact(s) linked."
        if test_count > 0
        else "PRD detected, but no matching test spec artifact was found for its slug."
    )

    return AdaptPlanningLink(
        prd_path=selection.prd_path,
        test_spec_paths=selection.test_spec_paths,
        deep_interview_spec_paths=selection.deep_interview_spec_paths,
        summary=test_summary,
    )


def build_adapt_envelope(
    cwd: str,
    target: str,
    now: datetime | None = None,
) -> AdaptEnvelope:
    """Build an adapter envelope for a target.

    Args:
        cwd: Working directory.
        target: Adapt target name.
        now: Current datetime.

    Returns:
        AdaptEnvelope for the target.

    Raises:
        ValueError: If the target is unknown.
    """
    now = now or datetime.now(timezone.utc)
    descriptor = get_adapt_target_descriptor(target)
    if not descriptor:
        raise ValueError(f"Unknown adapt target: {target}")

    paths = resolve_adapt_paths(cwd, target)
    planning = build_adapt_planning_link(cwd)

    if target == "openclaw":
        return build_openclaw_envelope(paths, planning, descriptor.capabilities, now)

    return AdaptEnvelope(
        schema_version=ADAPT_SCHEMA_VERSION,
        generated_at=_to_iso_timestamp(now),
        target=target,
        display_name=descriptor.display_name,
        summary=descriptor.summary,
        adapter_paths=paths,
        planning=planning,
        capabilities=descriptor.capabilities,
        constraints=FOUNDATION_CONSTRAINTS,
    )


def build_adapt_envelope_for_target(
    cwd: str,
    target: str,
    now: datetime | None = None,
) -> AdaptEnvelope:
    """Build an adapter envelope with target-specific evidence.

    Args:
        cwd: Working directory.
        target: Adapt target name.
        now: Current datetime.

    Returns:
        AdaptEnvelope with target-specific data applied.
    """
    envelope = build_adapt_envelope(cwd, target, now)
    if target != "hermes":
        return envelope
    return apply_hermes_envelope(envelope, collect_hermes_evidence(cwd))


def build_adapt_probe_report(
    cwd: str,
    target: str,
    now: datetime | None = None,
) -> AdaptProbeReport:
    """Build an adapter probe report.

    Args:
        cwd: Working directory.
        target: Adapt target name.
        now: Current datetime.

    Returns:
        AdaptProbeReport for the target.

    Raises:
        ValueError: If the target is unknown.
    """
    now = now or datetime.now(timezone.utc)
    descriptor = get_adapt_target_descriptor(target)
    if not descriptor:
        raise ValueError(f"Unknown adapt target: {target}")

    paths = resolve_adapt_paths(cwd, target)
    planning = build_adapt_planning_link(cwd)

    if target == "openclaw":
        return build_openclaw_probe_report(
            paths, planning, descriptor.capabilities, now
        )

    return AdaptProbeReport(
        schema_version=ADAPT_SCHEMA_VERSION,
        timestamp=_to_iso_timestamp(now),
        target=target,
        phase="foundation",
        summary=f"{descriptor.display_name} probe foundation is available, but target-specific runtime probing is deferred.",
        adapter_paths=paths,
        planning=planning,
        capabilities=descriptor.capabilities,
        target_runtime=AdaptRuntimeObservation(
            state="not-implemented",
            detail=descriptor.followup_hint,
        ),
        next_steps=[
            f"Run omx adapt {target} init --write to materialize OMX-owned adapter artifacts.",
            descriptor.followup_hint,
        ],
    )


def build_adapt_probe_report_for_target(
    cwd: str,
    target: str,
    now: datetime | None = None,
) -> AdaptProbeReport:
    """Build probe report with target-specific evidence.

    Args:
        cwd: Working directory.
        target: Adapt target name.
        now: Current datetime.

    Returns:
        AdaptProbeReport with target-specific data.
    """
    report = build_adapt_probe_report(cwd, target, now)
    if target != "hermes":
        return report
    return apply_hermes_probe(report, collect_hermes_evidence(cwd))


def build_adapt_status_report(
    cwd: str,
    target: str,
    now: datetime | None = None,
) -> AdaptStatusReport:
    """Build an adapter status report.

    Args:
        cwd: Working directory.
        target: Adapt target name.
        now: Current datetime.

    Returns:
        AdaptStatusReport for the target.

    Raises:
        ValueError: If the target is unknown.
    """
    now = now or datetime.now(timezone.utc)
    descriptor = get_adapt_target_descriptor(target)
    if not descriptor:
        raise ValueError(f"Unknown adapt target: {target}")

    paths = resolve_adapt_paths(cwd, target)
    initialized = (
        Path(paths.config_path).exists() and Path(paths.envelope_path).exists()
    )
    planning = build_adapt_planning_link(cwd)

    if target == "openclaw":
        return build_openclaw_status_report(
            paths, planning, descriptor.capabilities, now
        )

    return AdaptStatusReport(
        schema_version=ADAPT_SCHEMA_VERSION,
        timestamp=_to_iso_timestamp(now),
        target=target,
        phase="foundation",
        summary=(
            f"{descriptor.display_name} adapter foundation is initialized under OMX-owned paths."
            if initialized
            else f"{descriptor.display_name} adapter foundation has not been initialized yet."
        ),
        adapter={
            "state": "initialized" if initialized else "not-initialized",
            "detail": (
                "Adapter foundation artifacts exist under .omx/adapters/<target>/..."
                if initialized
                else "Run init --write to create OMX-owned adapter artifacts."
            ),
            "configPath": paths.config_path,
            "envelopePath": paths.envelope_path,
        },
        target_runtime=AdaptRuntimeObservation(
            state="unknown",
            detail=descriptor.followup_hint,
        ),
        planning=planning,
        capabilities=descriptor.capabilities,
    )


def build_adapt_doctor_report(
    cwd: str,
    target: str,
    now: datetime | None = None,
) -> AdaptDoctorReport:
    """Build an adapter doctor report.

    Args:
        cwd: Working directory.
        target: Adapt target name.
        now: Current datetime.

    Returns:
        AdaptDoctorReport for the target.

    Raises:
        ValueError: If the target is unknown.
    """
    now = now or datetime.now(timezone.utc)
    descriptor = get_adapt_target_descriptor(target)
    if not descriptor:
        raise ValueError(f"Unknown adapt target: {target}")

    planning = build_adapt_planning_link(cwd)

    if target == "openclaw":
        return build_openclaw_doctor_report(
            resolve_adapt_paths(cwd, target), planning, now
        )

    status = build_adapt_status_report(cwd, target, now)
    issues: list[AdaptDoctorIssue] = []

    if status.adapter.get("state") == "not-initialized":
        issues.append(
            AdaptDoctorIssue(
                code="adapter_not_initialized",
                message=f"No adapter foundation artifacts exist for {target} under .omx/adapters/{target}.",
            )
        )

    if not planning.prd_path:
        issues.append(
            AdaptDoctorIssue(
                code="planning_artifacts_missing",
                message="No canonical OMX PRD artifact is available to link into the adapter envelope.",
            )
        )

    issues.append(
        AdaptDoctorIssue(
            code="target_specific_logic_deferred",
            message=descriptor.followup_hint,
        )
    )

    return AdaptDoctorReport(
        schema_version=ADAPT_SCHEMA_VERSION,
        timestamp=_to_iso_timestamp(now),
        target=target,
        phase="foundation",
        summary=f"Foundation doctor for {descriptor.display_name} reports only OMX-owned adapter readiness and shared planning linkage.",
        issues=issues,
        next_steps=[
            f"Run omx adapt {target} init --write.",
            "Keep follow-on integration work out of .omx/state/... and target runtime internals unless a reviewed contract exists.",
            descriptor.followup_hint,
        ],
    )


def init_adapt_foundation(
    cwd: str,
    target: str,
    write: bool = False,
    now: datetime | None = None,
) -> AdaptInitResult:
    """Initialize adapter foundation artifacts.

    Args:
        cwd: Working directory.
        target: Adapt target name.
        write: Whether to write files to disk.
        now: Current datetime.

    Returns:
        AdaptInitResult describing the operation.

    Raises:
        ValueError: If the target is unknown.
    """
    now = now or datetime.now(timezone.utc)
    descriptor = get_adapt_target_descriptor(target)
    if not descriptor:
        raise ValueError(f"Unknown adapt target: {target}")

    paths = resolve_adapt_paths(cwd, target)
    planning = build_adapt_planning_link(cwd)

    if target == "openclaw":
        return init_openclaw_foundation(
            paths, planning, descriptor.capabilities, write, now
        )

    envelope = build_adapt_envelope(cwd, target, now)
    ep = envelope.adapter_paths
    preview_paths = [
        ep.adapter_root,
        ep.config_path,
        ep.envelope_path,
        ep.reports_dir,
        ep.probe_report_path,
        ep.status_report_path,
    ]
    wrote_paths: list[str] = []

    if write:
        Path(ep.reports_dir).mkdir(parents=True, exist_ok=True)
        config = {
            "schemaVersion": ADAPT_SCHEMA_VERSION,
            "target": target,
            "createdAt": _to_iso_timestamp(now),
            "phase": "foundation",
            "summary": descriptor.summary,
            "followupHint": descriptor.followup_hint,
            "constraints": FOUNDATION_CONSTRAINTS,
        }
        Path(ep.config_path).write_text(
            json.dumps(config, indent=2) + "\n", encoding="utf-8"
        )
        # Simplified envelope serialization
        envelope_data = {
            "schemaVersion": envelope.schema_version,
            "generatedAt": envelope.generated_at,
            "target": envelope.target,
        }
        Path(ep.envelope_path).write_text(
            json.dumps(envelope_data, indent=2) + "\n", encoding="utf-8"
        )
        wrote_paths.extend([ep.config_path, ep.envelope_path])

    return AdaptInitResult(
        schema_version=ADAPT_SCHEMA_VERSION,
        timestamp=_to_iso_timestamp(now),
        target=target,
        write=write,
        summary=(
            f"{descriptor.display_name} adapter foundation was written under OMX-owned paths."
            if write
            else f"{descriptor.display_name} adapter foundation preview is ready; rerun with --write to materialize it."
        ),
        preview_paths=preview_paths,
        wrote_paths=wrote_paths,
        envelope=envelope,
    )


# Needed for the import in contracts
from omx.adapt.contracts import AdaptRuntimeObservation  # noqa: E402
