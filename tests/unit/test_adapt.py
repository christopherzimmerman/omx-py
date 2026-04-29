"""Tests for the adapt module."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from omx.adapt.contracts import (
    ADAPT_SCHEMA_VERSION,
    AdaptCapabilityReport,
    AdaptCapabilityOwnership,
    AdaptCapabilityStatus,
    AdaptPathSet,
    AdaptPlanningLink,
    AdaptTarget,
    AdaptSubcommand,
)
from omx.adapt.registry import (
    list_adapt_targets,
    get_adapt_target_descriptor,
    FOUNDATION_CAPABILITIES,
)
from omx.adapt.paths import resolve_adapt_paths
from omx.adapt.hermes import (
    HermesEvidence,
    build_hermes_capability_overrides,
    build_hermes_bootstrap_metadata,
    build_hermes_runtime_observation,
)
from omx.adapt.openclaw import (
    build_openclaw_envelope,
    build_openclaw_probe_report,
    build_openclaw_doctor_report,
    init_openclaw_foundation,
)


class TestAdaptContracts(unittest.TestCase):
    """Tests for adapt contract dataclasses."""

    def test_adapt_schema_version(self) -> None:
        self.assertEqual(ADAPT_SCHEMA_VERSION, "1.0")

    def test_adapt_target_enum(self) -> None:
        self.assertEqual(AdaptTarget.OPENCLAW, "openclaw")
        self.assertEqual(AdaptTarget.HERMES, "hermes")

    def test_adapt_subcommand_enum(self) -> None:
        self.assertEqual(AdaptSubcommand.PROBE, "probe")
        self.assertEqual(AdaptSubcommand.DOCTOR, "doctor")

    def test_capability_ownership_enum(self) -> None:
        self.assertEqual(AdaptCapabilityOwnership.OMX_OWNED, "omx-owned")

    def test_capability_status_enum(self) -> None:
        self.assertEqual(AdaptCapabilityStatus.READY, "ready")
        self.assertEqual(AdaptCapabilityStatus.STUB, "stub")

    def test_adapt_capability_report(self) -> None:
        cap = AdaptCapabilityReport(
            id="test",
            label="Test",
            ownership="omx-owned",
            status="ready",
            summary="A test capability.",
        )
        self.assertEqual(cap.id, "test")
        self.assertEqual(cap.status, "ready")

    def test_adapt_path_set(self) -> None:
        ps = AdaptPathSet(
            adapter_root="/root",
            config_path="/root/config.json",
            envelope_path="/root/envelope.json",
            reports_dir="/root/reports",
            probe_report_path="/root/reports/probe.json",
            status_report_path="/root/reports/status.json",
        )
        self.assertEqual(ps.adapter_root, "/root")

    def test_adapt_planning_link(self) -> None:
        link = AdaptPlanningLink(prd_path=None, summary="No PRD found.")
        self.assertIsNone(link.prd_path)
        self.assertEqual(link.test_spec_paths, [])


class TestAdaptRegistry(unittest.TestCase):
    """Tests for the adapt target registry."""

    def test_list_adapt_targets(self) -> None:
        targets = list_adapt_targets()
        self.assertEqual(len(targets), 2)
        self.assertEqual(targets[0].target, "openclaw")
        self.assertEqual(targets[1].target, "hermes")

    def test_get_known_target(self) -> None:
        desc = get_adapt_target_descriptor("openclaw")
        self.assertIsNotNone(desc)
        self.assertEqual(desc.display_name, "OpenClaw")
        self.assertTrue(len(desc.capabilities) > len(FOUNDATION_CAPABILITIES))

    def test_get_unknown_target(self) -> None:
        desc = get_adapt_target_descriptor("nonexistent")
        self.assertIsNone(desc)

    def test_foundation_capabilities(self) -> None:
        self.assertEqual(len(FOUNDATION_CAPABILITIES), 3)
        ids = [c.id for c in FOUNDATION_CAPABILITIES]
        self.assertIn("omx-adapter-paths", ids)
        self.assertIn("planning-artifact-linkage", ids)


class TestAdaptPaths(unittest.TestCase):
    """Tests for adapt path resolution."""

    def test_resolve_adapt_paths(self) -> None:
        paths = resolve_adapt_paths("/project", "openclaw")
        self.assertIn("openclaw", paths.adapter_root)
        self.assertTrue(paths.config_path.endswith("adapter.json"))
        self.assertTrue(paths.envelope_path.endswith("envelope.json"))
        self.assertIn("reports", paths.reports_dir)
        self.assertTrue(paths.probe_report_path.endswith("probe.json"))
        self.assertTrue(paths.status_report_path.endswith("status.json"))


class TestHermesAdapter(unittest.TestCase):
    """Tests for the Hermes adapter."""

    def _make_evidence(self, **kwargs) -> HermesEvidence:
        defaults = {
            "hermes_root": "/fake/hermes",
            "hermes_home": "/fake/.hermes",
            "installed": False,
            "runtime_files": {
                "gatewayPidPath": "/fake/.hermes/gateway.pid",
                "gatewayStatePath": "/fake/.hermes/gateway_state.json",
                "stateDbPath": "/fake/.hermes/state.db",
                "gatewayPidReadable": False,
                "gatewayStateReadable": False,
                "stateDbReadable": False,
                "stateDbExists": False,
            },
            "gateway": {
                "pidRecord": None,
                "runtimeRecord": None,
                "live": False,
                "connectedPlatforms": [],
                "stale": False,
            },
            "source_runtime": {
                "acp": {"present": False, "files": [], "missing": []},
                "gateway": {"present": False},
                "docs": {"present": False},
                "stateStore": {"present": False},
                "acpRegistry": {"present": False},
            },
        }
        defaults.update(kwargs)
        return HermesEvidence(**defaults)

    def test_runtime_unavailable(self) -> None:
        evidence = self._make_evidence(installed=False)
        obs = build_hermes_runtime_observation(evidence)
        self.assertEqual(obs.state, "unavailable")

    def test_runtime_installed_no_files(self) -> None:
        evidence = self._make_evidence(installed=True)
        obs = build_hermes_runtime_observation(evidence)
        self.assertEqual(obs.state, "installed")

    def test_capability_overrides_not_installed(self) -> None:
        caps = [
            AdaptCapabilityReport(
                id="persistent-session-observation",
                label="",
                ownership="",
                status="stub",
                summary="",
            ),
        ]
        evidence = self._make_evidence(installed=False)
        result = build_hermes_capability_overrides(caps, evidence)
        self.assertEqual(result[0].status, "unsupported")

    def test_bootstrap_metadata(self) -> None:
        evidence = self._make_evidence()
        bootstrap = build_hermes_bootstrap_metadata(evidence)
        self.assertIn("ACP", bootstrap.summary)
        self.assertTrue(len(bootstrap.event_bridge) > 0)
        self.assertTrue(len(bootstrap.commands) > 0)


class TestOpenClawAdapter(unittest.TestCase):
    """Tests for the OpenClaw adapter."""

    def test_build_envelope(self) -> None:
        paths = resolve_adapt_paths("/project", "openclaw")
        planning = AdaptPlanningLink(prd_path=None, summary="no PRD")
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        caps = [
            AdaptCapabilityReport(
                id="test", label="T", ownership="omx-owned", status="ready", summary="s"
            )
        ]
        env = build_openclaw_envelope(paths, planning, caps, now)
        self.assertEqual(env.target, "openclaw")
        self.assertEqual(env.schema_version, ADAPT_SCHEMA_VERSION)
        self.assertIsNotNone(env.openclaw)

    def test_build_probe_report(self) -> None:
        paths = resolve_adapt_paths("/project", "openclaw")
        planning = AdaptPlanningLink(prd_path=None, summary="no PRD")
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        report = build_openclaw_probe_report(paths, planning, [], now)
        self.assertEqual(report.target, "openclaw")
        self.assertTrue(len(report.next_steps) > 0)

    def test_build_doctor_report(self) -> None:
        paths = resolve_adapt_paths("/project", "openclaw")
        planning = AdaptPlanningLink(prd_path=None, summary="no PRD")
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        report = build_openclaw_doctor_report(paths, planning, now)
        self.assertTrue(len(report.issues) > 0)
        issue_codes = [i.code for i in report.issues]
        self.assertIn("planning_artifacts_missing", issue_codes)

    def test_init_openclaw_foundation_preview(self) -> None:
        paths = resolve_adapt_paths("/project", "openclaw")
        planning = AdaptPlanningLink(prd_path=None, summary="no PRD")
        now = datetime(2025, 1, 1, tzinfo=timezone.utc)
        result = init_openclaw_foundation(paths, planning, [], False, now)
        self.assertFalse(result.write)
        self.assertEqual(result.wrote_paths, [])
        self.assertTrue(len(result.preview_paths) > 0)

    def test_init_openclaw_foundation_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            paths = resolve_adapt_paths(tmpdir, "openclaw")
            planning = AdaptPlanningLink(prd_path=None, summary="no PRD")
            now = datetime(2025, 1, 1, tzinfo=timezone.utc)
            result = init_openclaw_foundation(paths, planning, [], True, now)
            self.assertTrue(result.write)
            self.assertTrue(len(result.wrote_paths) > 0)
            self.assertTrue(Path(paths.config_path).exists())
            self.assertTrue(Path(paths.envelope_path).exists())


class TestAdaptModule(unittest.TestCase):
    """Tests for the adapt __init__ module."""

    def test_supported_adapt_targets(self) -> None:
        from omx.adapt import supported_adapt_targets

        targets = supported_adapt_targets()
        self.assertIn("openclaw", targets)
        self.assertIn("hermes", targets)

    def test_build_adapt_planning_link(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from omx.adapt import build_adapt_planning_link

            link = build_adapt_planning_link(tmpdir)
            self.assertIsNone(link.prd_path)
            self.assertIn("No canonical", link.summary)

    def test_build_adapt_envelope_unknown(self) -> None:
        from omx.adapt import build_adapt_envelope

        with self.assertRaises(ValueError):
            build_adapt_envelope("/tmp", "nonexistent")

    def test_build_adapt_envelope_openclaw(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            from omx.adapt import build_adapt_envelope

            now = datetime(2025, 1, 1, tzinfo=timezone.utc)
            env = build_adapt_envelope(tmpdir, "openclaw", now)
            self.assertEqual(env.target, "openclaw")

    def test_build_adapt_doctor_unknown(self) -> None:
        from omx.adapt import build_adapt_doctor_report

        with self.assertRaises(ValueError):
            build_adapt_doctor_report("/tmp", "nonexistent")


if __name__ == "__main__":
    unittest.main()
