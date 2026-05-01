"""Tests for the claude target in omx setup."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from omx.cli.setup import (
    SetupScope,
    SetupTarget,
    resolve_scope_directories,
    run_setup,
)


class TestResolveScopeDirectoriesClaude(unittest.TestCase):
    def test_user_scope_claude(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CLAUDE_HOME", None)
            dirs = resolve_scope_directories(
                SetupScope.USER, Path("/repo"), SetupTarget.CLAUDE
            )
        self.assertEqual(dirs.target, SetupTarget.CLAUDE)
        self.assertTrue(str(dirs.codex_config_file).endswith("settings.json"))
        self.assertTrue(str(dirs.skills_dir).endswith("skills"))
        self.assertEqual(dirs.main_instructions_filename, "CLAUDE.md")
        # agents == prompts dir for claude (role .md files go to agents/)
        self.assertEqual(dirs.native_agents_dir, dirs.prompts_dir)

    def test_project_scope_claude(self):
        dirs = resolve_scope_directories(
            SetupScope.PROJECT, Path("/repo"), SetupTarget.CLAUDE
        )
        self.assertEqual(dirs.codex_home_dir, Path("/repo/.claude"))
        self.assertEqual(dirs.codex_config_file, Path("/repo/.claude/settings.json"))
        self.assertEqual(dirs.skills_dir, Path("/repo/.claude/skills"))
        self.assertEqual(dirs.main_instructions_filename, "CLAUDE.md")

    def test_default_target_is_codex(self):
        dirs = resolve_scope_directories(SetupScope.USER, Path("/repo"))
        self.assertEqual(dirs.target, SetupTarget.CODEX)
        self.assertTrue(str(dirs.codex_config_file).endswith("config.toml"))
        self.assertEqual(dirs.main_instructions_filename, "AGENTS.md")


class TestRunSetupClaude(unittest.TestCase):
    def test_end_to_end_writes_settings_json_and_claude_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp) / "claude_home"
            project = Path(tmp) / "project"
            project.mkdir()
            with (
                mock.patch.dict(
                    os.environ,
                    {"CLAUDE_HOME": str(claude_home), "OMX_CLI": "claude"},
                    clear=False,
                ),
                mock.patch("pathlib.Path.cwd", return_value=project),
                mock.patch("builtins.print"),
            ):
                run_setup(target=SetupTarget.CLAUDE)

            settings = claude_home / "settings.json"
            self.assertTrue(settings.exists(), "settings.json should be written")
            data = json.loads(settings.read_text(encoding="utf-8"))
            self.assertIn("mcpServers", data)
            self.assertIn("omx_state", data["mcpServers"])

            # CLAUDE.md generated, not AGENTS.md
            self.assertTrue((claude_home / "CLAUDE.md").exists())
            self.assertFalse((claude_home / "AGENTS.md").exists())

            # Skills + agents installed; no codex-specific files
            self.assertTrue((claude_home / "skills").is_dir())
            self.assertTrue((claude_home / "agents").is_dir())
            self.assertFalse((claude_home / "config.toml").exists())
            self.assertFalse((claude_home / "hooks.json").exists())
            self.assertFalse((claude_home / "prompts").exists())

            # Agents are .md files (claude format), not .toml
            agent_files = list((claude_home / "agents").glob("*.md"))
            self.assertGreater(len(agent_files), 10)
            self.assertEqual(len(list((claude_home / "agents").glob("*.toml"))), 0)

            # Hooks wired in settings.json under claude's PascalCase event names
            self.assertIn("hooks", data)
            hooks = data["hooks"]
            for event in (
                "SessionStart",
                "UserPromptSubmit",
                "PreToolUse",
                "PostToolUse",
                "Stop",
            ):
                self.assertIn(event, hooks, f"missing claude hook event: {event}")
                entries = hooks[event]
                self.assertTrue(
                    any(
                        any(
                            "omx.scripts.codex_native_hook" in h.get("command", "")
                            for h in entry.get("hooks", [])
                        )
                        for entry in entries
                    ),
                    f"OMX hook not wired for {event}",
                )


class TestClaudeHooksPreserveAndDedupe(unittest.TestCase):
    """Verify _ensure_claude_hooks preserves user entries and dedupes OMX entries."""

    def test_preserves_user_hooks_and_dedupes_omx_on_rerun(self):
        from omx.cli.setup import _ensure_claude_hooks, CategorySummary

        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            # Pre-seed with a user hook and a stale OMX entry
            settings_path.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "UserPromptSubmit": [
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "/usr/local/bin/my-logger",
                                        }
                                    ]
                                },
                                {
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": "python -m omx.scripts.codex_native_hook user-prompt-submit",
                                        }
                                    ]
                                },
                            ]
                        }
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )

            with mock.patch("builtins.print"):
                _ensure_claude_hooks(settings_path, CategorySummary())

            data = json.loads(settings_path.read_text(encoding="utf-8"))
            ups = data["hooks"]["UserPromptSubmit"]
            # User hook still present
            user_entries = [
                e
                for e in ups
                if any("my-logger" in h.get("command", "") for h in e.get("hooks", []))
            ]
            self.assertEqual(len(user_entries), 1)
            # Exactly one OMX-managed entry (not two)
            omx_entries = [
                e
                for e in ups
                if any(
                    "omx.scripts.codex_native_hook" in h.get("command", "")
                    for h in e.get("hooks", [])
                )
            ]
            self.assertEqual(len(omx_entries), 1)


if __name__ == "__main__":
    unittest.main()
