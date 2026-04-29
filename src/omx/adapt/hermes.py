"""Hermes LLM adapter.

Port of src/adapt/hermes.ts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omx.adapt.contracts import (
    AdaptBootstrapMetadata,
    AdaptCapabilityReport,
    AdaptEnvelope,
    AdaptProbeReport,
    AdaptRuntimeObservation,
    AdaptStatusReport,
)

HERMES_HOME_ENV = "HERMES_HOME"
HERMES_ROOT_ENV = "OMX_ADAPT_HERMES_ROOT"
HERMES_BOOTSTRAP_ENV = "OMX_ADAPT_HERMES_BOOTSTRAP"
HERMES_DEFAULT_HOME = str(Path.home() / ".hermes")

ACP_COMMANDS = ["hermes acp", "hermes-acp", "python -m acp_adapter"]
STATUS_COMMANDS = [
    "hermes gateway status",
    "hermes sessions list --source acp",
]
ACP_ENTRYPOINTS = [
    "acp_adapter/server.py",
    "acp_adapter/session.py",
    "acp_adapter/events.py",
    "acp_adapter/entry.py",
]
GATEWAY_ENTRYPOINTS = [
    "gateway/status.py",
    "gateway/hooks.py",
]
DOC_ENTRYPOINTS = [
    "docs/acp-setup.md",
]


@dataclass
class HermesEvidence:
    """Evidence collected from a Hermes installation.

    Attributes:
        hermes_root: Path to Hermes source root.
        hermes_home: Path to Hermes home directory.
        sources: Source resolution details.
        source_runtime: Source file presence evidence.
        installed: Whether Hermes is detected as installed.
        runtime_files: Runtime file state.
        gateway: Gateway runtime state.
        resumable: Whether sessions are resumable.
    """

    hermes_root: str
    hermes_home: str
    sources: dict[str, str] = field(default_factory=dict)
    source_runtime: dict[str, Any] = field(default_factory=dict)
    installed: bool = False
    runtime_files: dict[str, Any] = field(default_factory=dict)
    gateway: dict[str, Any] = field(default_factory=dict)
    resumable: bool = False


def _safe_json_parse(raw: str) -> Any | None:
    """Parse JSON without raising."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _resolve_relative_to_cwd(cwd: str, path_value: str) -> str:
    """Resolve a path relative to cwd if not absolute."""
    p = Path(path_value)
    if p.is_absolute():
        return str(p)
    return str(Path(cwd).resolve() / path_value)


def _resolve_default_hermes_sibling_root(cwd: str) -> str:
    """Resolve the default sibling Hermes root."""
    return str(
        Path(cwd).resolve().parent
        / "hermes-codex-skill-omx-aware-prd"
        / "external"
        / "hermes-agent"
    )


def _resolve_hermes_root(cwd: str) -> dict[str, str]:
    """Resolve the Hermes root path."""
    override = os.environ.get(HERMES_ROOT_ENV, "").strip()
    if override:
        return {"path": _resolve_relative_to_cwd(cwd, override), "source": "override"}
    return {
        "path": _resolve_default_hermes_sibling_root(cwd),
        "source": "sibling-default",
    }


def _resolve_hermes_home(cwd: str) -> dict[str, str]:
    """Resolve the Hermes home path."""
    env_value = os.environ.get(HERMES_HOME_ENV, "").strip()
    if env_value:
        return {"path": _resolve_relative_to_cwd(cwd, env_value), "source": "env"}
    return {"path": HERMES_DEFAULT_HOME, "source": "default"}


def _collect_path_evidence(
    root: str,
    relative_paths: list[str],
) -> dict[str, Any]:
    """Check which files exist under root."""
    present: list[str] = []
    missing: list[str] = []
    for rel in relative_paths:
        candidate = str(Path(root) / rel)
        if Path(candidate).exists():
            present.append(candidate)
        else:
            missing.append(candidate)
    return {"present": len(present) > 0, "files": present, "missing": missing}


def _is_readable(path: str) -> bool:
    """Check if a file is readable."""
    try:
        return os.access(path, os.R_OK)
    except OSError:
        return False


def _read_json_file(path: str) -> Any | None:
    """Read and parse a JSON file, returning None on failure."""
    try:
        if not os.access(path, os.R_OK):
            return None
        with open(path, encoding="utf-8") as f:
            return _safe_json_parse(f.read())
    except OSError:
        return None


def _sqlite_has_table(path: str, table: str) -> bool:
    """Check if a SQLite file's header bytes contain a table name."""
    if not Path(path).exists():
        return False
    try:
        with open(path, "rb") as f:
            data = f.read(256)
        return table.encode("utf-8") in data
    except OSError:
        return False


def _extract_connected_platforms(runtime: dict[str, Any] | None) -> list[str]:
    """Extract connected platform names from gateway runtime state."""
    if not runtime or not isinstance(runtime.get("platforms"), dict):
        return []
    platforms = runtime["platforms"]
    return sorted(
        name
        for name, info in platforms.items()
        if isinstance(info, dict) and info.get("state") == "connected"
    )


def _infer_gateway_live(
    pid_record: dict[str, Any] | None,
    runtime_record: dict[str, Any] | None,
) -> dict[str, bool]:
    """Infer gateway liveness from PID and runtime records."""
    gateway_state = (
        runtime_record.get("gateway_state")
        if runtime_record and isinstance(runtime_record.get("gateway_state"), str)
        else None
    )
    running_state = gateway_state in ("starting", "running", "draining")
    pid_present = (
        pid_record is not None
        and isinstance(pid_record.get("pid"), (int, float))
        and pid_record["pid"] == pid_record["pid"]  # not NaN
    )
    live = bool(pid_present and running_state)
    stale = bool(pid_present and gateway_state and not running_state)
    return {"live": live, "stale": stale}


def collect_hermes_evidence(cwd: str | None = None) -> HermesEvidence:
    """Collect evidence about a Hermes installation.

    Args:
        cwd: Working directory (defaults to os.getcwd()).

    Returns:
        HermesEvidence with all collected data.
    """
    cwd = cwd or os.getcwd()
    hermes_root = _resolve_hermes_root(cwd)
    hermes_home = _resolve_hermes_home(cwd)

    source_acp = _collect_path_evidence(hermes_root["path"], ACP_ENTRYPOINTS)
    source_gateway = _collect_path_evidence(hermes_root["path"], GATEWAY_ENTRYPOINTS)
    source_docs = _collect_path_evidence(hermes_root["path"], DOC_ENTRYPOINTS)

    state_store_path = str(Path(hermes_root["path"]) / "hermes_state.py")
    acp_registry_path = str(Path(hermes_root["path"]) / "acp_registry")
    gateway_pid_path = str(Path(hermes_home["path"]) / "gateway.pid")
    gateway_state_path = str(Path(hermes_home["path"]) / "gateway_state.json")
    state_db_path = str(Path(hermes_home["path"]) / "state.db")

    gateway_pid_readable = _is_readable(gateway_pid_path)
    gateway_state_readable = _is_readable(gateway_state_path)
    state_db_readable = _is_readable(state_db_path)
    pid_record = _read_json_file(gateway_pid_path)
    runtime_record = _read_json_file(gateway_state_path)
    sessions_table_present = _sqlite_has_table(state_db_path, "sessions")

    connected_platforms = _extract_connected_platforms(runtime_record)
    gateway_liveness = _infer_gateway_live(pid_record, runtime_record)
    installed = (
        source_acp["present"]
        or source_gateway["present"]
        or Path(state_store_path).exists()
    )
    resumable = state_db_readable and sessions_table_present

    return HermesEvidence(
        hermes_root=hermes_root["path"],
        hermes_home=hermes_home["path"],
        sources={"root": hermes_root["source"], "home": hermes_home["source"]},
        source_runtime={
            "present": installed,
            "acp": source_acp,
            "gateway": source_gateway,
            "docs": source_docs,
            "stateStore": {
                "present": Path(state_store_path).exists(),
                "path": state_store_path,
            },
            "acpRegistry": {
                "present": Path(acp_registry_path).exists(),
                "path": acp_registry_path,
            },
        },
        installed=installed,
        runtime_files={
            "gatewayPidPath": gateway_pid_path,
            "gatewayStatePath": gateway_state_path,
            "stateDbPath": state_db_path,
            "gatewayPidReadable": gateway_pid_readable,
            "gatewayStateReadable": gateway_state_readable,
            "stateDbReadable": state_db_readable,
            "stateDbExists": Path(state_db_path).exists(),
        },
        gateway={
            "pidRecord": pid_record,
            "runtimeRecord": runtime_record,
            "live": gateway_liveness["live"],
            "connectedPlatforms": connected_platforms,
            "stale": gateway_liveness["stale"],
        },
        resumable=resumable,
    )


def build_hermes_capability_overrides(
    capabilities: list[AdaptCapabilityReport],
    evidence: HermesEvidence,
) -> list[AdaptCapabilityReport]:
    """Override capability statuses based on Hermes evidence.

    Args:
        capabilities: Original capability reports.
        evidence: Collected Hermes evidence.

    Returns:
        Updated capability reports.
    """
    result: list[AdaptCapabilityReport] = []
    for cap in capabilities:
        if cap.id == "persistent-session-observation":
            if evidence.runtime_files.get("stateDbReadable"):
                new_status = "ready"
                new_summary = f"Hermes session-store evidence is readable at {evidence.runtime_files['stateDbPath']}."
            elif evidence.installed:
                new_status = "stub"
                new_summary = "Hermes source/runtime surfaces are present, but no readable session store was detected yet."
            else:
                new_status = "unsupported"
                new_summary = "Hermes external runtime was not detected from the configured root/home paths."
            result.append(
                AdaptCapabilityReport(
                    id=cap.id,
                    label=cap.label,
                    ownership=cap.ownership,
                    status=new_status,
                    summary=new_summary,
                )
            )
        elif cap.id == "acp-envelope-bridge":
            acp = evidence.source_runtime.get("acp", {})
            if acp.get("present"):
                new_status = "ready"
                new_summary = "Envelope/bootstrap metadata now includes Hermes ACP entrypoints, commands, and bridge guidance."
            elif evidence.installed:
                new_status = "stub"
                new_summary = "Hermes root is partially present, but ACP entrypoints were not fully detected."
            else:
                new_status = "unsupported"
                new_summary = (
                    "No Hermes ACP entrypoints were detected from the configured root."
                )
            result.append(
                AdaptCapabilityReport(
                    id=cap.id,
                    label=cap.label,
                    ownership=cap.ownership,
                    status=new_status,
                    summary=new_summary,
                )
            )
        else:
            result.append(cap)
    return result


def build_hermes_bootstrap_metadata(evidence: HermesEvidence) -> AdaptBootstrapMetadata:
    """Build bootstrap metadata from Hermes evidence.

    Args:
        evidence: Collected Hermes evidence.

    Returns:
        AdaptBootstrapMetadata instance.
    """
    commands = [*ACP_COMMANDS, *STATUS_COMMANDS]
    next_steps = [
        f"Set {HERMES_HOME_ENV} to the Hermes profile home you want OMX to observe.",
        f"Run {ACP_COMMANDS[0]} from {evidence.hermes_root} when validating ACP availability.",
        f"Use {STATUS_COMMANDS[0]} to confirm gateway status outside OMX if the runtime evidence looks stale.",
    ]
    bootstrap_env = os.environ.get(HERMES_BOOTSTRAP_ENV, "").strip()
    if bootstrap_env:
        next_steps.insert(
            0,
            f"Bootstrap override detected via {HERMES_BOOTSTRAP_ENV}; "
            "keep Hermes-side reads pointed at OMX-owned adapter artifacts only.",
        )

    return AdaptBootstrapMetadata(
        summary=(
            "Hermes bootstrap metadata maps OMX lifecycle intent into ACP "
            "and gateway guidance without claiming direct control over Hermes internals."
        ),
        event_bridge=[
            "session-start -> session:start",
            "session-end -> session:end",
            "session-idle -> agent:end",
            "ask-user-question -> agent:step",
            "stop -> session:end",
            "gateway-startup -> gateway:startup",
        ],
        commands=commands,
        next_steps=next_steps,
    )


def build_hermes_runtime_observation(
    evidence: HermesEvidence,
) -> AdaptRuntimeObservation:
    """Build a runtime observation from Hermes evidence.

    Args:
        evidence: Collected Hermes evidence.

    Returns:
        AdaptRuntimeObservation describing the runtime state.
    """
    if not evidence.installed:
        return AdaptRuntimeObservation(
            state="unavailable",
            detail=f"Hermes external runtime was not detected under {evidence.hermes_root}.",
            evidence={
                "hermesRoot": evidence.hermes_root,
                "expectedAcpEntry": str(
                    Path(evidence.hermes_root) / ACP_ENTRYPOINTS[0]
                ),
                "expectedGatewayEntry": str(
                    Path(evidence.hermes_root) / GATEWAY_ENTRYPOINTS[0]
                ),
                "hermesHome": evidence.hermes_home,
            },
        )

    if evidence.gateway.get("live"):
        platforms = evidence.gateway.get("connectedPlatforms", [])
        detail = (
            f"Hermes gateway appears live with connected platforms: {', '.join(platforms)}."
            if platforms
            else "Hermes gateway appears live from PID/status evidence, but no connected platforms were reported."
        )
        runtime_record = evidence.gateway.get("runtimeRecord")
        return AdaptRuntimeObservation(
            state="running",
            detail=detail,
            evidence={
                "hermesRoot": evidence.hermes_root,
                "hermesHome": evidence.hermes_home,
                "gatewayState": runtime_record.get("gateway_state")
                if runtime_record
                else None,
                "connectedPlatforms": platforms,
                "gatewayStatePath": evidence.runtime_files.get("gatewayStatePath"),
                "gatewayPidPath": evidence.runtime_files.get("gatewayPidPath"),
                "stateDbPath": evidence.runtime_files.get("stateDbPath"),
                "resumable": evidence.resumable,
            },
        )

    rf = evidence.runtime_files
    if (
        rf.get("gatewayStateReadable")
        or rf.get("gatewayPidReadable")
        or rf.get("stateDbReadable")
    ):
        reasons: list[str] = []
        if rf.get("gatewayStateReadable"):
            reasons.append(
                f"gateway status readable ({Path(rf['gatewayStatePath']).name})"
            )
        if rf.get("gatewayPidReadable"):
            reasons.append(f"gateway pid readable ({Path(rf['gatewayPidPath']).name})")
        if rf.get("stateDbReadable"):
            reasons.append(f"session store readable ({Path(rf['stateDbPath']).name})")
        if evidence.gateway.get("stale"):
            reasons.append("gateway state appears stale/non-running")

        runtime_record = evidence.gateway.get("runtimeRecord")
        return AdaptRuntimeObservation(
            state="degraded",
            detail=f"Hermes runtime evidence is present but not currently live: {'; '.join(reasons)}.",
            evidence={
                "hermesRoot": evidence.hermes_root,
                "hermesHome": evidence.hermes_home,
                "gatewayState": runtime_record.get("gateway_state")
                if runtime_record
                else None,
                "exitReason": runtime_record.get("exit_reason")
                if runtime_record
                else None,
                "connectedPlatforms": evidence.gateway.get("connectedPlatforms", []),
                "stateDbPath": rf.get("stateDbPath"),
                "resumable": evidence.resumable,
            },
        )

    return AdaptRuntimeObservation(
        state="installed",
        detail="Hermes source surfaces were detected, but no readable runtime state files were found yet.",
        evidence={
            "hermesRoot": evidence.hermes_root,
            "hermesHome": evidence.hermes_home,
            "acpFiles": evidence.source_runtime.get("acp", {}).get("files", []),
            "gatewayFiles": evidence.source_runtime.get("gateway", {}).get("files", []),
            "docs": evidence.source_runtime.get("docs", {}).get("files", []),
            "stateStoreSource": evidence.source_runtime.get("stateStore", {}).get(
                "present", False
            ),
            "acpRegistry": evidence.source_runtime.get("acpRegistry", {}).get(
                "present", False
            ),
        },
    )


def apply_hermes_envelope(
    envelope: AdaptEnvelope,
    evidence: HermesEvidence,
) -> AdaptEnvelope:
    """Apply Hermes evidence to an envelope.

    Args:
        envelope: Base envelope.
        evidence: Collected Hermes evidence.

    Returns:
        Updated envelope with Hermes-specific data.
    """
    envelope.capabilities = build_hermes_capability_overrides(
        envelope.capabilities, evidence
    )
    envelope.target_runtime = build_hermes_runtime_observation(evidence)
    envelope.bootstrap = build_hermes_bootstrap_metadata(evidence)
    return envelope


def apply_hermes_probe(
    report: AdaptProbeReport,
    evidence: HermesEvidence,
) -> AdaptProbeReport:
    """Apply Hermes evidence to a probe report.

    Args:
        report: Base probe report.
        evidence: Collected Hermes evidence.

    Returns:
        Updated probe report.
    """
    import re

    next_steps = [
        s for s in report.next_steps if not re.search(r"follow-on PR", s, re.IGNORECASE)
    ]
    next_steps.append(f"Inspect Hermes root at {evidence.hermes_root}.")
    next_steps.append(f"Inspect Hermes home at {evidence.hermes_home}.")

    if not evidence.installed:
        next_steps.append(
            f"If Hermes lives elsewhere, set {HERMES_ROOT_ENV} and rerun the probe."
        )
    elif not evidence.runtime_files.get("stateDbReadable"):
        next_steps.append(
            f"Ensure {HERMES_HOME_ENV} points at the Hermes profile whose state.db OMX should inspect."
        )

    report.summary = "Hermes probe inspected ACP, gateway, and session-store evidence from the external runtime."
    report.capabilities = build_hermes_capability_overrides(
        report.capabilities, evidence
    )
    report.target_runtime = build_hermes_runtime_observation(evidence)
    report.next_steps = next_steps
    return report


def apply_hermes_status(
    report: AdaptStatusReport,
    evidence: HermesEvidence,
) -> AdaptStatusReport:
    """Apply Hermes evidence to a status report.

    Args:
        report: Base status report.
        evidence: Collected Hermes evidence.

    Returns:
        Updated status report.
    """
    target_runtime = build_hermes_runtime_observation(evidence)
    adapter_state = report.adapter.get("state", "not-initialized")
    if adapter_state == "initialized":
        summary = (
            f"Hermes adapter is initialized and "
            f"{'runtime evidence looks live.' if target_runtime.state == 'running' else 'runtime evidence is available for inspection.'}"
        )
    else:
        summary = f"Hermes adapter is not initialized yet; runtime evidence is still {target_runtime.state}."

    report.summary = summary
    report.capabilities = build_hermes_capability_overrides(
        report.capabilities, evidence
    )
    report.target_runtime = target_runtime
    return report
