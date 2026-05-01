"""Tests for omx.utils.platform.resolve_cli."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from omx.utils.platform import UnsupportedCliError, resolve_cli


class TestResolveCli(unittest.TestCase):
    def test_default_prefers_codex(self):
        with mock.patch(
            "omx.utils.platform.which",
            side_effect=lambda name: Path(f"/bin/{name}") if name == "codex" else None,
        ):
            self.assertEqual(resolve_cli({}), (Path("/bin/codex"), "codex"))

    def test_default_falls_back_to_claude(self):
        with mock.patch(
            "omx.utils.platform.which",
            side_effect=lambda name: Path("/bin/claude") if name == "claude" else None,
        ):
            self.assertEqual(resolve_cli({}), (Path("/bin/claude"), "claude"))

    def test_default_none_when_neither_installed(self):
        with mock.patch("omx.utils.platform.which", return_value=None):
            self.assertIsNone(resolve_cli({}))

    def test_env_forces_claude_even_if_codex_installed(self):
        with mock.patch(
            "omx.utils.platform.which",
            side_effect=lambda name: Path(f"/bin/{name}"),
        ):
            self.assertEqual(
                resolve_cli({"OMX_CLI": "claude"}),
                (Path("/bin/claude"), "claude"),
            )

    def test_env_codex_returns_none_when_not_installed(self):
        with mock.patch(
            "omx.utils.platform.which",
            side_effect=lambda name: Path("/bin/claude") if name == "claude" else None,
        ):
            self.assertIsNone(resolve_cli({"OMX_CLI": "codex"}))

    def test_env_case_and_whitespace_insensitive(self):
        with mock.patch(
            "omx.utils.platform.which",
            side_effect=lambda name: Path(f"/bin/{name}"),
        ):
            self.assertEqual(
                resolve_cli({"OMX_CLI": "  CLAUDE  "}),
                (Path("/bin/claude"), "claude"),
            )

    def test_env_unsupported_value_raises(self):
        with self.assertRaises(UnsupportedCliError):
            resolve_cli({"OMX_CLI": "gemini"})


if __name__ == "__main__":
    unittest.main()
