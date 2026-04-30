"""Tests for orchestration: overlay builder, native hook, keyword routing."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class TestBuildSessionInstructions(unittest.TestCase):
    """Tests for the overlay builder."""

    def test_produces_non_empty_content(self) -> None:
        """build_session_instructions always produces non-empty output."""
        from omx.runtime.overlay import build_session_instructions

        with tempfile.TemporaryDirectory() as tmpdir:
            path = build_session_instructions(tmpdir, "test-session-001")
            self.assertTrue(Path(path).exists())
            content = Path(path).read_text(encoding="utf-8")
            self.assertTrue(len(content) > 0)

    def test_includes_agents_md(self) -> None:
        """Instructions include AGENTS.md content when present."""
        from omx.runtime.overlay import build_session_instructions

        with tempfile.TemporaryDirectory() as tmpdir:
            agents_md = Path(tmpdir) / "AGENTS.md"
            agents_md.write_text(
                "# Test Agents\n\nThis is test AGENTS.md content.\n",
                encoding="utf-8",
            )
            path = build_session_instructions(tmpdir, "test-session-002")
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("Test Agents", content)
            self.assertIn("test AGENTS.md content", content)

    def test_includes_active_mode_states(self) -> None:
        """Instructions include active mode summaries."""
        from omx.runtime.overlay import build_session_instructions

        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / ".omx" / "state"
            state_dir.mkdir(parents=True)
            (state_dir / "ralph-state.json").write_text(
                json.dumps(
                    {
                        "active": True,
                        "current_phase": "investigate",
                    }
                ),
                encoding="utf-8",
            )
            path = build_session_instructions(tmpdir, "test-session-003")
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("ralph", content)
            self.assertIn("investigate", content)

    def test_includes_project_memory(self) -> None:
        """Instructions include project memory notes."""
        from omx.runtime.overlay import build_session_instructions

        with tempfile.TemporaryDirectory() as tmpdir:
            omx_dir = Path(tmpdir) / ".omx"
            omx_dir.mkdir(parents=True)
            (omx_dir / "project-memory.json").write_text(
                json.dumps(
                    {
                        "notes": ["Always use type hints"],
                        "directives": ["Run tests before committing"],
                    }
                ),
                encoding="utf-8",
            )
            path = build_session_instructions(tmpdir, "test-session-004")
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("Always use type hints", content)
            self.assertIn("Run tests before committing", content)

    def test_includes_priority_notes(self) -> None:
        """Instructions include priority notes from notepad."""
        from omx.runtime.overlay import build_session_instructions

        with tempfile.TemporaryDirectory() as tmpdir:
            omx_dir = Path(tmpdir) / ".omx"
            omx_dir.mkdir(parents=True)
            (omx_dir / "notepad.md").write_text(
                "# Notes\nSome general notes\n\n"
                "## PRIORITY\n- Fix the login bug\n- Deploy by Friday\n\n"
                "## Other\nSomething else\n",
                encoding="utf-8",
            )
            path = build_session_instructions(tmpdir, "test-session-005")
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("Fix the login bug", content)
            self.assertIn("Deploy by Friday", content)

    def test_includes_wiki_summary(self) -> None:
        """Instructions include wiki page listing."""
        from omx.runtime.overlay import build_session_instructions

        with tempfile.TemporaryDirectory() as tmpdir:
            wiki_dir = Path(tmpdir) / ".omx" / "wiki"
            wiki_dir.mkdir(parents=True)
            (wiki_dir / "architecture.md").write_text("# Arch", encoding="utf-8")
            (wiki_dir / "conventions.md").write_text("# Conv", encoding="utf-8")
            path = build_session_instructions(tmpdir, "test-session-006")
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("architecture", content)
            self.assertIn("conventions", content)

    def test_includes_runtime_environment(self) -> None:
        """Instructions always include runtime environment description."""
        from omx.runtime.overlay import build_session_instructions

        with tempfile.TemporaryDirectory() as tmpdir:
            path = build_session_instructions(tmpdir, "test-session-007")
            content = Path(path).read_text(encoding="utf-8")
            self.assertIn("Runtime Environment", content)
            self.assertIn("test-session-007", content)

    def test_writes_to_session_directory(self) -> None:
        """Instructions file is written to .omx/state/sessions/<id>/."""
        from omx.runtime.overlay import build_session_instructions

        with tempfile.TemporaryDirectory() as tmpdir:
            path = build_session_instructions(tmpdir, "sess-abc")
            expected = (
                Path(tmpdir)
                / ".omx"
                / "state"
                / "sessions"
                / "sess-abc"
                / "model-instructions.md"
            )
            self.assertEqual(Path(path), expected)
            self.assertTrue(expected.exists())


class TestWriteSessionModelInstructions(unittest.TestCase):
    """Tests for the write helper."""

    def test_creates_file(self) -> None:
        from omx.runtime.overlay import write_session_model_instructions

        with tempfile.TemporaryDirectory() as tmpdir:
            result = write_session_model_instructions(tmpdir, "sid-1", "test content")
            self.assertTrue(result.exists())
            self.assertEqual(result.read_text(encoding="utf-8"), "test content")


class TestKeywordDetection(unittest.TestCase):
    """Tests for skill keyword detection in prompts."""

    def test_dollar_ralph(self) -> None:
        from omx.scripts.codex_native_hook import detect_skill_from_prompt

        self.assertEqual(detect_skill_from_prompt("$ralph"), "ralph")

    def test_dollar_autopilot(self) -> None:
        from omx.scripts.codex_native_hook import detect_skill_from_prompt

        self.assertEqual(detect_skill_from_prompt("$autopilot"), "autopilot")

    def test_dollar_team(self) -> None:
        from omx.scripts.codex_native_hook import detect_skill_from_prompt

        self.assertEqual(detect_skill_from_prompt("$team"), "team")

    def test_dollar_deep_interview(self) -> None:
        from omx.scripts.codex_native_hook import detect_skill_from_prompt

        self.assertEqual(detect_skill_from_prompt("$deep-interview"), "deep-interview")

    def test_no_keyword(self) -> None:
        from omx.scripts.codex_native_hook import detect_skill_from_prompt

        self.assertIsNone(detect_skill_from_prompt("just a normal prompt"))

    def test_empty_prompt(self) -> None:
        from omx.scripts.codex_native_hook import detect_skill_from_prompt

        self.assertIsNone(detect_skill_from_prompt(""))

    def test_natural_language_trigger(self) -> None:
        """Natural language keywords should route via the registry."""
        from omx.scripts.codex_native_hook import detect_skill_from_prompt

        result = detect_skill_from_prompt("$autopilot build me a web app")
        self.assertEqual(result, "autopilot")


class TestNativeHookHandler(unittest.TestCase):
    """Tests for the native hook handler event processing."""

    def test_dispatch_unknown_event(self) -> None:
        """Unknown events should not crash."""
        from omx.scripts.codex_native_hook import _dispatch_event

        # Should not raise
        _dispatch_event("unknown-event", {}, tempfile.gettempdir(), "test-sid")

    def test_dispatch_session_start(self) -> None:
        """session-start should write session state."""
        from omx.scripts.codex_native_hook import _dispatch_event

        with tempfile.TemporaryDirectory() as tmpdir:
            _dispatch_event("session-start", {}, tmpdir, "hook-test-001")
            session_file = Path(tmpdir) / ".omx" / "session.json"
            self.assertTrue(session_file.exists())
            data = json.loads(session_file.read_text(encoding="utf-8"))
            self.assertEqual(data["session_id"], "hook-test-001")

    def test_dispatch_stop(self) -> None:
        """stop event should clean up session state."""
        from omx.scripts.codex_native_hook import _dispatch_event

        with tempfile.TemporaryDirectory() as tmpdir:
            # First create a session
            _dispatch_event("session-start", {}, tmpdir, "hook-test-002")
            session_file = Path(tmpdir) / ".omx" / "session.json"
            self.assertTrue(session_file.exists())

            # Then stop it
            _dispatch_event("stop", {}, tmpdir, "hook-test-002")
            self.assertFalse(session_file.exists())

    def test_dispatch_user_prompt_submit(self) -> None:
        """user-prompt-submit should not crash on empty payload."""
        from omx.scripts.codex_native_hook import _dispatch_event

        with tempfile.TemporaryDirectory() as tmpdir:
            # Should not raise
            _dispatch_event(
                "user-prompt-submit",
                {"prompt": "hello world"},
                tmpdir,
                "hook-test-003",
            )

    def test_extract_prompt_text(self) -> None:
        """Prompt text extraction from various payload shapes."""
        from omx.scripts.codex_native_hook import _extract_prompt_text

        self.assertEqual(_extract_prompt_text({"prompt": "hello"}), "hello")
        self.assertEqual(_extract_prompt_text({"text": "world"}), "world")
        self.assertEqual(
            _extract_prompt_text({"context": {"prompt": "nested"}}),
            "nested",
        )
        self.assertEqual(_extract_prompt_text({}), "")


class TestCodexHooksConfig(unittest.TestCase):
    """Tests for updated hooks configuration."""

    def test_build_managed_hooks_uses_python(self) -> None:
        """Managed hooks config should use python module invocation."""
        from omx.config.codex_hooks import build_managed_codex_hooks_config

        config = build_managed_codex_hooks_config("/fake/root")
        hooks = config["hooks"]
        self.assertIn("SessionStart", hooks)
        self.assertIn("UserPromptSubmit", hooks)
        self.assertIn("Stop", hooks)

        # Verify commands use python module
        for event_name, entries in hooks.items():
            for entry in entries:
                for hook in entry["hooks"]:
                    cmd = hook["command"]
                    self.assertIn("omx.scripts.codex_native_hook", cmd)

    def test_is_omx_managed_detects_python_hook(self) -> None:
        """The managed hook detector should recognize Python hook commands."""
        from omx.config.codex_hooks import _is_omx_managed_hook_command

        self.assertTrue(
            _is_omx_managed_hook_command(
                "python -u -m omx.scripts.codex_native_hook session-start"
            )
        )
        # Still detect legacy JS hooks
        self.assertTrue(
            _is_omx_managed_hook_command('node "/path/to/codex-native-hook.js"')
        )
        self.assertFalse(_is_omx_managed_hook_command("some-other-hook"))


if __name__ == "__main__":
    unittest.main()
