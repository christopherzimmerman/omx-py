"""Tests for omx.team.state.manifest — V2 manifest read/write/init."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

from omx.team.state.manifest import (
    PermissionsSnapshot,
    TeamLeader,
    TeamManifestV2,
    _is_team_manifest_v2,
    _manifest_v2_path,
    _team_dir,
    init_team_state,
    read_team_manifest_v2,
    write_team_manifest_v2,
)
from omx.team.state.types import WorkerInfo


def _make_manifest(name: str = "alpha", worker_count: int = 2) -> TeamManifestV2:
    workers = [
        WorkerInfo(name=f"worker-{i}", index=i, role="executor")
        for i in range(1, worker_count + 1)
    ]
    return TeamManifestV2(
        name=name,
        task="ship it",
        leader=TeamLeader(session_id="sess-1", worker_id="leader-fixed"),
        permissions_snapshot=PermissionsSnapshot(),
        tmux_session=f"omx-team-{name}",
        worker_count=worker_count,
        workers=workers,
        next_task_id=1,
        created_at="2026-05-13T00:00:00+00:00",
        policy=None,
        governance=None,
        leader_pane_id=None,
        hud_pane_id=None,
        resize_hook_name=None,
        resize_hook_target=None,
        next_worker_index=worker_count + 1,
    )


class TestManifestDataclasses(unittest.TestCase):
    def test_leader_roundtrip(self):
        leader = TeamLeader(session_id="s", worker_id="w", role="coordinator")
        restored = TeamLeader.from_dict(leader.to_dict())
        self.assertEqual(restored.session_id, "s")
        self.assertEqual(restored.worker_id, "w")
        self.assertEqual(restored.role, "coordinator")

    def test_leader_optional_thread_id(self):
        leader = TeamLeader(session_id="s", thread_id="t-1")
        d = leader.to_dict()
        self.assertEqual(d["thread_id"], "t-1")
        restored = TeamLeader.from_dict(d)
        self.assertEqual(restored.thread_id, "t-1")

    def test_permissions_snapshot_defaults(self):
        snap = PermissionsSnapshot()
        self.assertEqual(snap.approval_mode, "unknown")
        self.assertEqual(snap.sandbox_mode, "unknown")
        self.assertTrue(snap.network_access)

    def test_permissions_snapshot_roundtrip(self):
        snap = PermissionsSnapshot(
            approval_mode="on-request", sandbox_mode="offline", network_access=False
        )
        restored = PermissionsSnapshot.from_dict(snap.to_dict())
        self.assertEqual(restored.approval_mode, "on-request")
        self.assertEqual(restored.sandbox_mode, "offline")
        self.assertFalse(restored.network_access)

    def test_manifest_schema_version_defaults_to_2(self):
        m = _make_manifest()
        self.assertEqual(m.schema_version, 2)
        self.assertEqual(m.to_dict()["schema_version"], 2)

    def test_manifest_lifecycle_profile_forced_in_to_dict(self):
        m = _make_manifest()
        # Even if a caller mutated it, serialization forces 'default'
        m.lifecycle_profile = "weird"
        self.assertEqual(m.to_dict()["lifecycle_profile"], "default")

    def test_manifest_includes_default_policy_and_governance_when_none(self):
        m = _make_manifest()
        d = m.to_dict()
        self.assertIn("display_mode", d["policy"])
        self.assertIn("worker_launch_mode", d["policy"])
        self.assertEqual(d["policy"]["dispatch_mode"], "hook_preferred_with_fallback")
        self.assertIn("delegation_only", d["governance"])
        self.assertTrue(d["governance"]["one_team_per_leader_session"])


class TestIsTeamManifestV2(unittest.TestCase):
    def test_accepts_minimal_valid(self):
        m = _make_manifest()
        self.assertTrue(_is_team_manifest_v2(m.to_dict()))

    def test_rejects_wrong_schema_version(self):
        d = _make_manifest().to_dict()
        d["schema_version"] = 1
        self.assertFalse(_is_team_manifest_v2(d))

    def test_rejects_missing_required_string(self):
        d = _make_manifest().to_dict()
        d.pop("name")
        self.assertFalse(_is_team_manifest_v2(d))

    def test_rejects_non_dict(self):
        self.assertFalse(_is_team_manifest_v2(None))
        self.assertFalse(_is_team_manifest_v2(42))
        self.assertFalse(_is_team_manifest_v2("hello"))

    def test_rejects_bool_for_worker_count(self):
        d = _make_manifest().to_dict()
        d["worker_count"] = True  # bool is int in Python; explicit guard
        self.assertFalse(_is_team_manifest_v2(d))


class TestReadMissingTeam(unittest.TestCase):
    def test_read_returns_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_team_manifest_v2("does-not-exist", tmp))

    def test_read_returns_none_on_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _manifest_v2_path(tmp, "broken")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("not valid json {", encoding="utf-8")
            self.assertIsNone(read_team_manifest_v2("broken", tmp))

    def test_read_returns_none_on_wrong_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _manifest_v2_path(tmp, "stale")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"schema_version": 1, "name": "stale"}), encoding="utf-8"
            )
            self.assertIsNone(read_team_manifest_v2("stale", tmp))

    def test_read_does_not_attempt_v1_migration(self):
        """If only a V1-shape config.json exists, read_team_manifest_v2 must
        return None — no migration is attempted (locked decision)."""
        with tempfile.TemporaryDirectory() as tmp:
            team_dir = _team_dir(tmp, "v1team")
            team_dir.mkdir(parents=True, exist_ok=True)
            # Write a plausible V1 config — should be ignored.
            (team_dir / "config.json").write_text(
                json.dumps(
                    {
                        "name": "v1team",
                        "task": "old",
                        "agent_type": "executor",
                        "worker_launch_mode": "interactive",
                        "lifecycle_profile": "default",
                        "worker_count": 1,
                        "workers": [],
                        "created_at": "2025-01-01T00:00:00Z",
                        "tmux_session": "omx-team-v1team",
                        "next_task_id": 1,
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(read_team_manifest_v2("v1team", tmp))


class TestRoundTrip(unittest.TestCase):
    def test_write_then_read_preserves_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _make_manifest("alpha", worker_count=3)
            manifest.leader_cwd = tmp
            manifest.workspace_mode = "worktree"
            manifest.worktree_mode = "shared"
            write_team_manifest_v2(manifest, tmp)

            loaded = read_team_manifest_v2("alpha", tmp)
            self.assertIsNotNone(loaded)
            assert loaded is not None  # narrow for type checker
            self.assertEqual(loaded.name, "alpha")
            self.assertEqual(loaded.task, "ship it")
            self.assertEqual(loaded.worker_count, 3)
            self.assertEqual(len(loaded.workers), 3)
            self.assertEqual(loaded.workers[0].name, "worker-1")
            self.assertEqual(loaded.leader.session_id, "sess-1")
            self.assertEqual(loaded.permissions_snapshot.approval_mode, "unknown")
            self.assertEqual(loaded.tmux_session, "omx-team-alpha")
            self.assertEqual(loaded.next_task_id, 1)
            self.assertEqual(loaded.next_worker_index, 4)
            self.assertEqual(loaded.workspace_mode, "worktree")
            self.assertEqual(loaded.worktree_mode, "shared")
            self.assertEqual(loaded.leader_cwd, tmp)
            self.assertEqual(loaded.schema_version, 2)
            self.assertEqual(loaded.lifecycle_profile, "default")

    def test_write_forces_lifecycle_profile_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _make_manifest("beta")
            manifest.lifecycle_profile = "garbage"
            write_team_manifest_v2(manifest, tmp)

            raw = (_manifest_v2_path(tmp, "beta")).read_text(encoding="utf-8")
            on_disk = json.loads(raw)
            self.assertEqual(on_disk["lifecycle_profile"], "default")

    def test_write_rejects_invalid_team_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = _make_manifest("BadName!")
            with self.assertRaises(ValueError):
                write_team_manifest_v2(manifest, tmp)


class TestInitTeamState(unittest.TestCase):
    def test_init_creates_directory_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = init_team_state(
                "gamma",
                task="build a thing",
                agent_type="executor",
                worker_count=2,
                cwd=tmp,
                env={},
            )
            self.assertEqual(manifest.name, "gamma")
            self.assertEqual(manifest.worker_count, 2)
            self.assertEqual(len(manifest.workers), 2)

            team_dir = _team_dir(tmp, "gamma")
            self.assertTrue(team_dir.is_dir())
            for sub in (
                "workers",
                "tasks",
                "claims",
                "mailbox",
                "dispatch",
                "events",
                "approvals",
            ):
                self.assertTrue(
                    (team_dir / sub).is_dir(),
                    f"expected {sub}/ to exist",
                )

            # Per-worker dirs
            self.assertTrue((team_dir / "workers" / "worker-1").is_dir())
            self.assertTrue((team_dir / "workers" / "worker-2").is_dir())

            # Files
            self.assertTrue((team_dir / "manifest.v2.json").is_file())
            self.assertTrue((team_dir / "config.json").is_file())
            self.assertTrue((team_dir / "dispatch" / "requests.json").is_file())

            # config.json shape
            cfg = json.loads((team_dir / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(cfg["name"], "gamma")
            self.assertEqual(cfg["worker_count"], 2)
            self.assertEqual(cfg["tmux_session"], "omx-team-gamma")
            self.assertEqual(cfg["next_worker_index"], 3)

            # dispatch requests starts as []
            reqs = json.loads(
                (team_dir / "dispatch" / "requests.json").read_text(encoding="utf-8")
            )
            self.assertEqual(reqs, [])

    def test_init_returns_readable_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            written = init_team_state(
                "delta",
                task="t",
                agent_type="executor",
                worker_count=1,
                cwd=tmp,
                env={},
            )
            read_back = read_team_manifest_v2("delta", tmp)
            self.assertIsNotNone(read_back)
            assert read_back is not None
            self.assertEqual(read_back.name, written.name)
            self.assertEqual(read_back.task, written.task)
            self.assertEqual(read_back.worker_count, written.worker_count)
            self.assertEqual(read_back.next_worker_index, 2)
            self.assertEqual(read_back.tmux_session, "omx-team-delta")
            self.assertEqual(read_back.schema_version, 2)

    def test_init_rejects_worker_count_over_max_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                init_team_state(
                    "eps",
                    task="t",
                    agent_type="executor",
                    worker_count=5,
                    cwd=tmp,
                    max_workers=3,
                    env={},
                )

    def test_init_rejects_max_workers_over_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                init_team_state(
                    "zeta",
                    task="t",
                    agent_type="executor",
                    worker_count=1,
                    cwd=tmp,
                    max_workers=999,
                    env={},
                )

    def test_init_rejects_invalid_team_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                init_team_state(
                    "Has Spaces",
                    task="t",
                    agent_type="executor",
                    worker_count=1,
                    cwd=tmp,
                    env={},
                )

    def test_init_resolves_session_id_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = init_team_state(
                "eta",
                task="t",
                agent_type="executor",
                worker_count=1,
                cwd=tmp,
                env={"OMX_SESSION_ID": "session-xyz"},
            )
            self.assertEqual(manifest.leader.session_id, "session-xyz")

    def test_init_resolves_permissions_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = init_team_state(
                "theta",
                task="t",
                agent_type="executor",
                worker_count=1,
                cwd=tmp,
                env={
                    "OMX_APPROVAL_MODE": "on-request",
                    "OMX_SANDBOX_MODE": "offline",
                },
            )
            self.assertEqual(manifest.permissions_snapshot.approval_mode, "on-request")
            self.assertEqual(manifest.permissions_snapshot.sandbox_mode, "offline")
            # offline sandbox implies no network unless explicitly set
            self.assertFalse(manifest.permissions_snapshot.network_access)

    def test_init_network_access_explicit_overrides_sandbox_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = init_team_state(
                "iota",
                task="t",
                agent_type="executor",
                worker_count=1,
                cwd=tmp,
                env={
                    "OMX_SANDBOX_MODE": "offline",
                    "OMX_NETWORK_ACCESS": "true",
                },
            )
            self.assertTrue(manifest.permissions_snapshot.network_access)

    def test_init_invalid_worker_launch_mode_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                init_team_state(
                    "kappa",
                    task="t",
                    agent_type="executor",
                    worker_count=1,
                    cwd=tmp,
                    env={"OMX_TEAM_WORKER_LAUNCH_MODE": "bananas"},
                )

    def test_init_workspace_metadata_passthrough(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = init_team_state(
                "lam",
                task="t",
                agent_type="executor",
                worker_count=1,
                cwd=tmp,
                env={},
                workspace={
                    "leader_cwd": tmp,
                    "team_state_root": "/some/root",
                    "workspace_mode": "worktree",
                    "worktree_mode": "shared",
                },
            )
            self.assertEqual(manifest.leader_cwd, tmp)
            self.assertEqual(manifest.team_state_root, "/some/root")
            self.assertEqual(manifest.workspace_mode, "worktree")
            self.assertEqual(manifest.worktree_mode, "shared")

    def test_init_default_env_uses_process_environ(self):
        """When env=None, init reads from os.environ (smoke test)."""
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"OMX_SESSION_ID": "from-process"}, clear=False
            ):
                manifest = init_team_state(
                    "mu",
                    task="t",
                    agent_type="executor",
                    worker_count=1,
                    cwd=tmp,
                )
                self.assertEqual(manifest.leader.session_id, "from-process")


class TestAtomicWrite(unittest.TestCase):
    def test_no_partial_file_on_simulated_rename_crash(self):
        """If the rename step fails, the destination file must not appear
        and the temp file must not linger."""
        from omx.team.state.atomic import (
            reset_rename_for_tests,
            set_rename_for_tests,
        )

        with tempfile.TemporaryDirectory() as tmp:
            manifest = _make_manifest("nu")
            # First write succeeds so we have a baseline destination.
            write_team_manifest_v2(manifest, tmp)
            dest = _manifest_v2_path(tmp, "nu")
            self.assertTrue(dest.is_file())
            baseline_bytes = dest.read_bytes()

            # Now simulate a crash *during* the rename step of the second write.
            def boom(_src: str, _dst: str) -> None:
                raise OSError("simulated rename failure")

            manifest.task = "MUTATED"
            set_rename_for_tests(boom)
            try:
                with self.assertRaises(OSError):
                    write_team_manifest_v2(manifest, tmp)
            finally:
                reset_rename_for_tests()

            # Destination still has the original content (no partial overwrite).
            self.assertEqual(dest.read_bytes(), baseline_bytes)

            # No leftover temp files in the team dir.
            team_dir = _team_dir(tmp, "nu")
            leftovers = [p.name for p in team_dir.iterdir() if ".tmp." in p.name]
            self.assertEqual(leftovers, [], f"leftover tmp files: {leftovers}")


if __name__ == "__main__":
    unittest.main()
