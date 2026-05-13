"""Tests for the new Phase 9 top-level subcommands.

Covers ``omx star-prompt``, ``omx mcp-parity``, ``omx tmux-hook``,
``omx catalog-contract``, ``omx native-assets``, ``omx question``, and
``omx codex-home``.
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _capture(func, *args) -> tuple[int, str, str]:
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            func(*args)
    except SystemExit as exc:
        code = exc.code or 0
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------------------
# star-prompt
# ---------------------------------------------------------------------------


class TestStarPrompt(unittest.TestCase):
    def test_status_flag_emits_json(self) -> None:
        from omx.cli.star_prompt import handle_star_prompt

        with (
            mock.patch("omx.cli.star_prompt.has_been_prompted", return_value=True),
            mock.patch("omx.cli.star_prompt.is_gh_installed", return_value=True),
        ):
            code, out, _ = _capture(handle_star_prompt, ["--status"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["prompted"])
        self.assertTrue(payload["gh_installed"])
        self.assertIn("state_path", payload)

    def test_skips_when_already_prompted(self) -> None:
        from omx.cli.star_prompt import handle_star_prompt

        with (
            mock.patch("omx.cli.star_prompt.has_been_prompted", return_value=True),
            mock.patch("omx.cli.star_prompt.is_gh_installed", return_value=True),
            mock.patch("sys.stdin.isatty", return_value=True),
            mock.patch("sys.stdout.isatty", return_value=True),
        ):
            code, out, err = _capture(handle_star_prompt, [])
        self.assertEqual(code, 0)
        # No prompt should have been issued
        self.assertEqual(out, "")
        self.assertEqual(err, "")

    def test_skips_when_no_tty(self) -> None:
        from omx.cli.star_prompt import handle_star_prompt

        with mock.patch("sys.stdin.isatty", return_value=False):
            code, out, _ = _capture(handle_star_prompt, [])
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_missing_gh_warns(self) -> None:
        from omx.cli.star_prompt import handle_star_prompt

        # The handler checks sys.stdin.isatty and sys.stdout.isatty, but
        # the test rig swaps stdout to a StringIO. Use --force to bypass
        # the TTY guard so we can exercise the gh-missing branch.
        with (
            mock.patch("omx.cli.star_prompt.has_been_prompted", return_value=False),
            mock.patch("omx.cli.star_prompt.is_gh_installed", return_value=False),
        ):
            code, _, err = _capture(handle_star_prompt, ["--force"])
        self.assertEqual(code, 0)
        self.assertIn("gh CLI not installed", err)

    def test_state_path_under_home(self) -> None:
        from omx.cli.star_prompt import star_prompt_state_path

        path = star_prompt_state_path()
        self.assertTrue(str(path).endswith(os.path.join("star-prompt.json")))


# ---------------------------------------------------------------------------
# mcp-parity
# ---------------------------------------------------------------------------


class TestMcpParity(unittest.TestCase):
    def test_help_lists_servers(self) -> None:
        from omx.cli.mcp_parity import handle_mcp_parity

        code, out, _ = _capture(handle_mcp_parity, ["--help"])
        self.assertEqual(code, 0)
        self.assertIn("Supported servers", out)
        self.assertIn("state", out)
        self.assertIn("trace", out)

    def test_unknown_server_exits_one(self) -> None:
        from omx.cli.mcp_parity import handle_mcp_parity

        code, _, err = _capture(handle_mcp_parity, ["bogus"])
        self.assertEqual(code, 1)
        self.assertIn("unknown server", err)

    def test_parse_args_input_object(self) -> None:
        from omx.cli.mcp_parity import parse_mcp_parity_args

        name, input_obj, json_flag, help_flag = parse_mcp_parity_args(
            ["tool-name", "--input", '{"foo": 1}', "--json"]
        )
        self.assertEqual(name, "tool-name")
        self.assertEqual(input_obj, {"foo": 1})
        self.assertTrue(json_flag)
        self.assertFalse(help_flag)

    def test_parse_args_invalid_input(self) -> None:
        from omx.cli.mcp_parity import parse_mcp_parity_args

        with self.assertRaises(ValueError):
            parse_mcp_parity_args(["tool", "--input", "[1, 2]"])

    def test_state_read_via_handler(self) -> None:
        """End-to-end through the state MCP handler."""
        from omx.cli.mcp_parity import handle_mcp_parity

        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                code, out, _ = _capture(
                    handle_mcp_parity,
                    ["state", "list-active", "--json"],
                )
            finally:
                os.chdir(old)
        self.assertEqual(code, 0)
        # Either list of active modes or a wrapper dict
        self.assertTrue(out.strip())


# ---------------------------------------------------------------------------
# tmux-hook
# ---------------------------------------------------------------------------


class TestTmuxHook(unittest.TestCase):
    def test_help(self) -> None:
        from omx.cli.tmux_hook import handle_tmux_hook

        code, out, _ = _capture(handle_tmux_hook, ["--help"])
        self.assertEqual(code, 0)
        self.assertIn("init", out)
        self.assertIn("validate", out)

    def test_init_creates_config(self) -> None:
        from omx.cli.tmux_hook import handle_tmux_hook

        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                with mock.patch(
                    "omx.cli.tmux_hook._detect_initial_tmux_target",
                    return_value=None,
                ):
                    code, out, _ = _capture(handle_tmux_hook, ["init"])
                self.assertEqual(code, 0)
                cfg = Path(tmp) / ".omx" / "tmux-hook.json"
                self.assertTrue(cfg.exists())
                data = json.loads(cfg.read_text(encoding="utf-8"))
                self.assertEqual(data["target"]["value"], "")
            finally:
                os.chdir(old)

    def test_status_after_init(self) -> None:
        from omx.cli.tmux_hook import handle_tmux_hook

        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                cfg = Path(tmp) / ".omx" / "tmux-hook.json"
                cfg.parent.mkdir(parents=True, exist_ok=True)
                cfg.write_text(
                    json.dumps(
                        {
                            "enabled": True,
                            "target": {"type": "pane", "value": "%1"},
                            "allowed_modes": ["ralph"],
                            "cooldown_ms": 1000,
                            "max_injections_per_session": 10,
                            "prompt_template": "ping",
                            "marker": "[X]",
                            "dry_run": False,
                            "log_level": "info",
                            "skip_if_scrolling": True,
                        }
                    ),
                    encoding="utf-8",
                )
                code, out, _ = _capture(handle_tmux_hook, ["status"])
            finally:
                os.chdir(old)
        self.assertEqual(code, 0)
        self.assertIn("enabled: True", out)
        self.assertIn("target: type=pane value=%1", out)

    def test_validate_missing_config(self) -> None:
        from omx.cli.tmux_hook import handle_tmux_hook

        with tempfile.TemporaryDirectory() as tmp:
            old = os.getcwd()
            os.chdir(tmp)
            try:
                code, _, err = _capture(handle_tmux_hook, ["validate"])
            finally:
                os.chdir(old)
        self.assertEqual(code, 1)
        self.assertIn("config missing", err)

    def test_unknown_subcommand(self) -> None:
        from omx.cli.tmux_hook import handle_tmux_hook

        code, _, err = _capture(handle_tmux_hook, ["nonsense"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown tmux-hook subcommand", err)


# ---------------------------------------------------------------------------
# catalog-contract
# ---------------------------------------------------------------------------


class TestCatalogContract(unittest.TestCase):
    def test_default_expectations(self) -> None:
        from omx.cli.catalog_contract import handle_catalog_contract

        code, out, _ = _capture(handle_catalog_contract, [])
        self.assertEqual(code, 0)
        self.assertIn("prompt_min", out)
        self.assertIn("skill_min", out)

    def test_headlines_flag(self) -> None:
        from omx.cli.catalog_contract import handle_catalog_contract

        code, out, _ = _capture(handle_catalog_contract, ["--headlines"])
        self.assertEqual(code, 0)
        self.assertIn("prompts:", out)
        self.assertIn("skills:", out)

    def test_json_envelope(self) -> None:
        from omx.cli.catalog_contract import handle_catalog_contract

        code, out, _ = _capture(handle_catalog_contract, ["--headlines", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("headlines", payload)
        self.assertIn("prompts", payload["headlines"])

    def test_get_catalog_expectations_clamps_min_one(self) -> None:
        from omx.cli.catalog_contract import get_catalog_expectations

        # Patch the helper that returns the headline counts so we can force
        # both into the "below safety buffer" zone.
        with mock.patch(
            "omx.cli.catalog_contract.get_catalog_headline_counts",
            return_value={"prompts": 1, "skills": 1},
        ):
            exp = get_catalog_expectations()
        self.assertEqual(exp["prompt_min"], 1)
        self.assertEqual(exp["skill_min"], 1)


# ---------------------------------------------------------------------------
# native-assets
# ---------------------------------------------------------------------------


class TestNativeAssets(unittest.TestCase):
    def test_help(self) -> None:
        from omx.cli.native_assets import handle_native_assets

        code, out, _ = _capture(handle_native_assets, ["--help"])
        self.assertEqual(code, 0)
        self.assertIn("status", out)
        self.assertIn("cache-root", out)

    def test_cache_root_text(self) -> None:
        from omx.cli.native_assets import handle_native_assets

        code, out, _ = _capture(handle_native_assets, ["cache-root"])
        self.assertEqual(code, 0)
        self.assertIn("oh-my-codex", out)

    def test_cache_root_json(self) -> None:
        from omx.cli.native_assets import handle_native_assets

        code, out, _ = _capture(handle_native_assets, ["cache-root", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("cache_root", payload)

    def test_status_lists_known_products(self) -> None:
        from omx.cli.native_assets import handle_native_assets

        code, out, _ = _capture(handle_native_assets, ["status", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        names = {row["product"] for row in payload["products"]}
        self.assertIn("omx-explore-harness", names)
        self.assertIn("omx-sparkshell", names)

    def test_unknown_subcommand(self) -> None:
        from omx.cli.native_assets import handle_native_assets

        code, _, err = _capture(handle_native_assets, ["nonsense"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown native-assets subcommand", err)


# ---------------------------------------------------------------------------
# question
# ---------------------------------------------------------------------------


class TestQuestion(unittest.TestCase):
    def test_help_when_no_args(self) -> None:
        from omx.cli.question import handle_question

        code, out, _ = _capture(handle_question, [])
        self.assertEqual(code, 0)
        self.assertIn("--input", out)

    def test_invalid_json_input(self) -> None:
        from omx.cli.question import handle_question

        code, _, err = _capture(handle_question, ["--input", "{not json}"])
        self.assertEqual(code, 1)
        self.assertIn("--input must be valid JSON", err)

    def test_ui_requires_state_path(self) -> None:
        from omx.cli.question import handle_question

        code, _, err = _capture(handle_question, ["--ui"])
        self.assertEqual(code, 1)
        self.assertIn("--state-path", err)

    def test_parse_args_state_path_eq(self) -> None:
        from omx.cli.question import parse_question_args

        parsed = parse_question_args(["--ui", "--state-path=/tmp/x.json"])
        self.assertTrue(parsed.ui)
        self.assertEqual(parsed.state_path, "/tmp/x.json")

    def test_parse_args_input_eq(self) -> None:
        from omx.cli.question import parse_question_args

        parsed = parse_question_args(["--input={}", "--json"])
        self.assertEqual(parsed.input, "{}")
        self.assertTrue(parsed.json)

    def test_unknown_arg_raises(self) -> None:
        from omx.cli.question import parse_question_args

        with self.assertRaises(ValueError):
            parse_question_args(["--what"])


# ---------------------------------------------------------------------------
# codex-home
# ---------------------------------------------------------------------------


class TestCodexHome(unittest.TestCase):
    def test_show_text(self) -> None:
        from omx.cli.codex_home import handle_codex_home

        code, out, _ = _capture(handle_codex_home, [])
        self.assertEqual(code, 0)
        self.assertIn("codex_home:", out)
        self.assertIn("config_path:", out)

    def test_show_json(self) -> None:
        from omx.cli.codex_home import handle_codex_home

        code, out, _ = _capture(handle_codex_home, ["show", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertIn("codex_home", payload)
        self.assertIn("config_path", payload)

    def test_scope_with_persisted_value(self) -> None:
        from omx.cli.codex_home import (
            read_persisted_setup_scope,
            resolve_codex_home_for_launch,
        )

        with tempfile.TemporaryDirectory() as tmp:
            scope_file = Path(tmp) / ".omx" / "setup-scope.json"
            scope_file.parent.mkdir(parents=True, exist_ok=True)
            scope_file.write_text(json.dumps({"scope": "project"}))

            scope = read_persisted_setup_scope(tmp)
            self.assertEqual(scope, "project")

            home = resolve_codex_home_for_launch(tmp, env={})
            self.assertTrue(home.endswith(".codex"))

    def test_legacy_scope_migration(self) -> None:
        from omx.cli.codex_home import read_persisted_setup_preferences

        with tempfile.TemporaryDirectory() as tmp:
            scope_file = Path(tmp) / ".omx" / "setup-scope.json"
            scope_file.parent.mkdir(parents=True, exist_ok=True)
            scope_file.write_text(json.dumps({"scope": "project-local"}))

            prefs = read_persisted_setup_preferences(tmp)
            self.assertIsNotNone(prefs)
            self.assertEqual(prefs.get("scope"), "project")

    def test_env_override_wins(self) -> None:
        from omx.cli.codex_home import resolve_codex_home_for_launch

        home = resolve_codex_home_for_launch(
            "/tmp/cwd", env={"CODEX_HOME": "/explicit/home"}
        )
        self.assertEqual(home, "/explicit/home")

    def test_unknown_subcommand(self) -> None:
        from omx.cli.codex_home import handle_codex_home

        code, _, err = _capture(handle_codex_home, ["bogus"])
        self.assertEqual(code, 1)
        self.assertIn("Unknown codex-home subcommand", err)


# ---------------------------------------------------------------------------
# Top-level CLI routing (verifies _RAW_DISPATCH wiring)
# ---------------------------------------------------------------------------


class TestTopLevelRouting(unittest.TestCase):
    def test_main_routes_codex_home(self) -> None:
        from omx.cli import main

        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["codex-home", "show", "--json"])
        payload = json.loads(buf.getvalue())
        self.assertIn("config_path", payload)

    def test_main_routes_catalog_contract(self) -> None:
        from omx.cli import main

        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["catalog-contract", "--json"])
        payload = json.loads(buf.getvalue())
        self.assertIn("expectations", payload)

    def test_main_routes_native_assets(self) -> None:
        from omx.cli import main

        buf = io.StringIO()
        with redirect_stdout(buf):
            main(["native-assets", "cache-root", "--json"])
        payload = json.loads(buf.getvalue())
        self.assertIn("cache_root", payload)

    def test_autoresearch_deprecation_exits_one(self) -> None:
        from omx.cli import main

        buf_err = io.StringIO()
        with redirect_stderr(buf_err):
            try:
                main(["autoresearch", "anything"])
            except SystemExit as exc:
                code = exc.code
            else:
                code = 0
        self.assertEqual(code, 1)
        self.assertIn("hard-deprecated", buf_err.getvalue())


if __name__ == "__main__":
    unittest.main()
