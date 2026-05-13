"""Tests for HUD state readers and config normalizer (Phase 8)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omx.hud.state import (
    normalize_hud_config,
    read_hud_config,
    read_hud_state,
    read_ralph_state,
    read_ultrawork_state,
    write_hud_state,
)
from omx.hud.types import (
    DEFAULT_HUD_CONFIG,
    HudConfig,
    HudGitConfig,
    RalphStateForHud,
    ResolvedHudConfig,
    UltraworkStateForHud,
)


def _write_state(cwd: Path, mode: str, payload: dict) -> None:
    """Write a mode state file at ``.omx/state/<mode>-state.json``."""
    state_dir = cwd / ".omx" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{mode}-state.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_session_state(cwd: Path, session: str, mode: str, payload: dict) -> None:
    """Write a session-scoped mode state file."""
    state_dir = cwd / ".omx" / "state" / "sessions" / session
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{mode}-state.json").write_text(json.dumps(payload), encoding="utf-8")


class ReadRalphStateTests(unittest.TestCase):
    """Tests for ``read_ralph_state``."""

    def test_returns_none_when_no_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_ralph_state(tmp))

    def test_returns_state_when_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_state(
                Path(tmp),
                "ralph",
                {"active": True, "iteration": 2, "max_iterations": 5},
            )
            state = read_ralph_state(tmp)
            self.assertIsInstance(state, RalphStateForHud)
            assert state is not None
            self.assertTrue(state.active)
            self.assertEqual(state.iteration, 2)
            self.assertEqual(state.max_iterations, 5)

    def test_returns_none_when_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_state(Path(tmp), "ralph", {"active": False})
            self.assertIsNone(read_ralph_state(tmp))

    def test_invalid_json_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp) / ".omx" / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            (state_dir / "ralph-state.json").write_text("{not-json", encoding="utf-8")
            self.assertIsNone(read_ralph_state(tmp))

    def test_session_scoped_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_state(Path(tmp), "ralph", {"active": True, "iteration": 1})
            _write_session_state(
                Path(tmp),
                "sess1",
                "ralph",
                {"active": True, "iteration": 9, "max_iterations": 10},
            )
            state = read_ralph_state(tmp, session_id="sess1")
            assert state is not None
            self.assertEqual(state.iteration, 9)

    def test_session_scoped_missing_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_state(Path(tmp), "ralph", {"active": True, "iteration": 1})
            self.assertIsNone(read_ralph_state(tmp, session_id="nope"))

    def test_non_int_iteration_coerced_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_state(
                Path(tmp), "ralph", {"active": True, "iteration": "not-a-number"}
            )
            state = read_ralph_state(tmp)
            assert state is not None
            self.assertIsNone(state.iteration)


class ReadUltraworkStateTests(unittest.TestCase):
    """Tests for ``read_ultrawork_state``."""

    def test_returns_none_when_no_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_ultrawork_state(tmp))

    def test_returns_state_when_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_state(
                Path(tmp), "ultrawork", {"active": True, "reinforcement_count": 7}
            )
            state = read_ultrawork_state(tmp)
            self.assertIsInstance(state, UltraworkStateForHud)
            assert state is not None
            self.assertEqual(state.reinforcement_count, 7)

    def test_returns_none_when_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_state(Path(tmp), "ultrawork", {"active": False})
            self.assertIsNone(read_ultrawork_state(tmp))

    def test_session_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_session_state(Path(tmp), "sess", "ultrawork", {"active": True})
            state = read_ultrawork_state(tmp, session_id="sess")
            assert state is not None
            self.assertTrue(state.active)


class NormalizeHudConfigTests(unittest.TestCase):
    """Tests for ``normalize_hud_config``."""

    def test_none_returns_defaults(self) -> None:
        result = normalize_hud_config(None)
        self.assertIsInstance(result, ResolvedHudConfig)
        self.assertEqual(result.preset, DEFAULT_HUD_CONFIG.preset)
        self.assertEqual(result.git.display, DEFAULT_HUD_CONFIG.git.display)

    def test_dict_with_valid_preset(self) -> None:
        result = normalize_hud_config({"preset": "minimal"})
        self.assertEqual(result.preset, "minimal")

    def test_dict_with_invalid_preset_keeps_default(self) -> None:
        result = normalize_hud_config({"preset": "garbage"})
        self.assertEqual(result.preset, DEFAULT_HUD_CONFIG.preset)

    def test_dict_with_valid_git_display(self) -> None:
        result = normalize_hud_config({"git": {"display": "branch"}})
        self.assertEqual(result.git.display, "branch")

    def test_dict_with_invalid_git_display_keeps_default(self) -> None:
        result = normalize_hud_config({"git": {"display": "weird"}})
        self.assertEqual(result.git.display, "repo-branch")

    def test_dict_with_remote_name_camel_case(self) -> None:
        result = normalize_hud_config({"git": {"remoteName": "upstream"}})
        self.assertEqual(result.git.remote_name, "upstream")

    def test_dict_with_remote_name_snake_case(self) -> None:
        result = normalize_hud_config({"git": {"remote_name": "snake"}})
        self.assertEqual(result.git.remote_name, "snake")

    def test_dict_with_repo_label_camel_case(self) -> None:
        result = normalize_hud_config({"git": {"repoLabel": "my-repo"}})
        self.assertEqual(result.git.repo_label, "my-repo")

    def test_dict_trims_strings(self) -> None:
        result = normalize_hud_config({"git": {"remoteName": "  origin  "}})
        self.assertEqual(result.git.remote_name, "origin")

    def test_dict_empty_strings_dropped(self) -> None:
        result = normalize_hud_config({"git": {"repoLabel": "   "}})
        self.assertIsNone(result.git.repo_label)

    def test_dict_non_object_git_returns_default(self) -> None:
        result = normalize_hud_config({"git": "not-an-object"})
        self.assertEqual(result.git.display, "repo-branch")

    def test_hud_config_dataclass_normalized(self) -> None:
        config = HudConfig(
            preset="full",
            git=HudGitConfig(display="branch", remote_name="origin"),
        )
        result = normalize_hud_config(config)
        self.assertEqual(result.preset, "full")
        self.assertEqual(result.git.display, "branch")
        self.assertEqual(result.git.remote_name, "origin")

    def test_non_dict_non_dataclass_returns_default(self) -> None:
        result = normalize_hud_config("garbage")  # type: ignore[arg-type]
        self.assertEqual(result.preset, DEFAULT_HUD_CONFIG.preset)


class ReadHudConfigTests(unittest.TestCase):
    """Tests for the on-disk HUD config reader."""

    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = read_hud_config(tmp)
            self.assertEqual(result.preset, DEFAULT_HUD_CONFIG.preset)

    def test_reads_and_normalizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / ".omx"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            (cfg_dir / "hud-config.json").write_text(
                json.dumps({"preset": "minimal", "git": {"display": "branch"}}),
                encoding="utf-8",
            )
            result = read_hud_config(tmp)
            self.assertEqual(result.preset, "minimal")
            self.assertEqual(result.git.display, "branch")

    def test_invalid_json_falls_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / ".omx"
            cfg_dir.mkdir(parents=True, exist_ok=True)
            (cfg_dir / "hud-config.json").write_text("{ invalid json", encoding="utf-8")
            result = read_hud_config(tmp)
            self.assertEqual(result.preset, DEFAULT_HUD_CONFIG.preset)


class LegacyHudStateTests(unittest.TestCase):
    """Tests for the existing hud-state.json round-trip helpers."""

    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            payload = {"tool_calls": 7, "last_turn_at": "2025-01-01T00:00:00Z"}
            write_hud_state(payload, state_dir)
            read = read_hud_state(state_dir)
            self.assertEqual(read, payload)

    def test_missing_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(read_hud_state(Path(tmp)), {})


if __name__ == "__main__":
    unittest.main()
