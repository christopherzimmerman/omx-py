"""Tests for ``omx.team.state_root``.

Covers the env-aware resolver, the legacy ``team_dir`` shape, and integration
with the historical inline-path callers that were collapsed onto the resolver.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from omx.team.state_root import (
    OMX_TEAM_STATE_ROOT_ENV,
    resolve_team_state_root,
    team_dir,
)


class ResolveTeamStateRootDefaultTest(unittest.TestCase):
    """Default behavior when ``OMX_TEAM_STATE_ROOT`` is not set."""

    def test_default_with_empty_env(self) -> None:
        result = resolve_team_state_root("/tmp/proj", env={})
        self.assertEqual(result, Path("/tmp/proj") / ".omx" / "team")

    def test_default_with_env_unset(self) -> None:
        # Mapping that does not contain the env key behaves the same as {}.
        result = resolve_team_state_root("/tmp/proj", env={"UNRELATED": "x"})
        self.assertEqual(result, Path("/tmp/proj") / ".omx" / "team")

    def test_default_accepts_pathlike_cwd(self) -> None:
        cwd = Path("/tmp/proj")
        result = resolve_team_state_root(cwd, env={})
        self.assertEqual(result, cwd / ".omx" / "team")

    def test_default_when_env_param_is_none_reads_os_environ(self) -> None:
        # Snapshot + clear the real env var so we don't leak.
        prior = os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
        try:
            result = resolve_team_state_root("/tmp/proj")
            self.assertEqual(result, Path("/tmp/proj") / ".omx" / "team")
        finally:
            if prior is not None:
                os.environ[OMX_TEAM_STATE_ROOT_ENV] = prior


class ResolveTeamStateRootEnvOverrideTest(unittest.TestCase):
    """Behavior when ``OMX_TEAM_STATE_ROOT`` is set in the env mapping."""

    def test_absolute_override_returned_as_is(self) -> None:
        override = str(Path("/var/lib/omx-state").resolve())
        result = resolve_team_state_root(
            "/tmp/proj", env={OMX_TEAM_STATE_ROOT_ENV: override}
        )
        self.assertEqual(result, Path(override))

    def test_relative_override_anchored_at_cwd(self) -> None:
        result = resolve_team_state_root(
            "/tmp/proj", env={OMX_TEAM_STATE_ROOT_ENV: ".omx/state/team"}
        )
        self.assertEqual(result, Path("/tmp/proj") / ".omx/state/team")

    def test_override_with_surrounding_whitespace_is_trimmed(self) -> None:
        result = resolve_team_state_root(
            "/tmp/proj", env={OMX_TEAM_STATE_ROOT_ENV: "   custom/root   "}
        )
        self.assertEqual(result, Path("/tmp/proj") / "custom/root")

    def test_empty_override_falls_back_to_default(self) -> None:
        result = resolve_team_state_root("/tmp/proj", env={OMX_TEAM_STATE_ROOT_ENV: ""})
        self.assertEqual(result, Path("/tmp/proj") / ".omx" / "team")

    def test_whitespace_only_override_falls_back_to_default(self) -> None:
        result = resolve_team_state_root(
            "/tmp/proj", env={OMX_TEAM_STATE_ROOT_ENV: "   "}
        )
        self.assertEqual(result, Path("/tmp/proj") / ".omx" / "team")

    def test_override_via_os_environ_when_env_param_is_none(self) -> None:
        prior = os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
        os.environ[OMX_TEAM_STATE_ROOT_ENV] = ".omx/state/team"
        try:
            result = resolve_team_state_root("/tmp/proj")
            self.assertEqual(result, Path("/tmp/proj") / ".omx/state/team")
        finally:
            if prior is None:
                os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
            else:
                os.environ[OMX_TEAM_STATE_ROOT_ENV] = prior


class TeamDirTest(unittest.TestCase):
    """``team_dir`` parity with the historical hardcoded layout."""

    def test_team_dir_default_layout(self) -> None:
        prior = os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
        try:
            result = team_dir("alpha", "/tmp/proj")
            self.assertEqual(result, Path("/tmp/proj") / ".omx" / "team" / "alpha")
        finally:
            if prior is not None:
                os.environ[OMX_TEAM_STATE_ROOT_ENV] = prior

    def test_team_dir_honors_env_override(self) -> None:
        prior = os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
        os.environ[OMX_TEAM_STATE_ROOT_ENV] = ".omx/state/team"
        try:
            result = team_dir("alpha", "/tmp/proj")
            self.assertEqual(result, Path("/tmp/proj") / ".omx/state/team/alpha")
        finally:
            if prior is None:
                os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
            else:
                os.environ[OMX_TEAM_STATE_ROOT_ENV] = prior

    def test_team_dir_appends_verbatim_team_name(self) -> None:
        # Whitespace and unusual characters in the team name flow through as-is
        # so we surface name-sanitization issues at the caller, not here.
        prior = os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
        try:
            result = team_dir("team-with-dashes_42", "/tmp/proj")
            self.assertEqual(
                result,
                Path("/tmp/proj") / ".omx" / "team" / "team-with-dashes_42",
            )
        finally:
            if prior is not None:
                os.environ[OMX_TEAM_STATE_ROOT_ENV] = prior

    def test_team_dir_absolute_override(self) -> None:
        prior = os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
        abs_root = str(Path("/var/lib/omx-state").resolve())
        os.environ[OMX_TEAM_STATE_ROOT_ENV] = abs_root
        try:
            result = team_dir("alpha", "/tmp/proj")
            self.assertEqual(result, Path(abs_root) / "alpha")
        finally:
            if prior is None:
                os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
            else:
                os.environ[OMX_TEAM_STATE_ROOT_ENV] = prior


class StateLayerIntegrationTest(unittest.TestCase):
    """End-to-end: state/io.py and state/manifest.py share the resolver."""

    def test_io_helpers_use_resolver(self) -> None:
        # When the override is set, the io._team_dir helper must reflect the
        # new base path. This guarantees the consolidation actually happened.
        from omx.team.state import io as io_module

        prior = os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
        os.environ[OMX_TEAM_STATE_ROOT_ENV] = ".omx/state/team"
        try:
            result = io_module._team_dir("/tmp/proj", "alpha")
            self.assertEqual(result, Path("/tmp/proj") / ".omx/state/team/alpha")
        finally:
            if prior is None:
                os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
            else:
                os.environ[OMX_TEAM_STATE_ROOT_ENV] = prior

    def test_manifest_helpers_use_resolver(self) -> None:
        from omx.team.state import manifest as manifest_module

        prior = os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
        os.environ[OMX_TEAM_STATE_ROOT_ENV] = ".omx/state/team"
        try:
            result = manifest_module._team_dir("/tmp/proj", "alpha")
            self.assertEqual(result, Path("/tmp/proj") / ".omx/state/team/alpha")
        finally:
            if prior is None:
                os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
            else:
                os.environ[OMX_TEAM_STATE_ROOT_ENV] = prior

    def test_write_config_round_trip_under_default(self) -> None:
        # Behavior regression: when the env override is *not* set, the
        # historical layout must keep working byte-for-byte.
        from omx.team.state.io import read_team_config, write_team_config

        prior = os.environ.pop(OMX_TEAM_STATE_ROOT_ENV, None)
        try:
            with TemporaryDirectory() as cwd:
                write_team_config(cwd, {"name": "alpha"}, "alpha")
                expected_path = Path(cwd) / ".omx" / "team" / "alpha" / "config.json"
                self.assertTrue(expected_path.exists())
                self.assertEqual(read_team_config(cwd, "alpha"), {"name": "alpha"})
        finally:
            if prior is not None:
                os.environ[OMX_TEAM_STATE_ROOT_ENV] = prior


if __name__ == "__main__":
    unittest.main()
