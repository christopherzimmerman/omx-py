"""Tests for hooks completion: agents_overlay, codebase_map, plugin_runner, sdk, runtime dispatch."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from omx.hooks.agents_overlay import (
    MAX_OVERLAY_SIZE,
    START_MARKER,
    END_MARKER,
    generate_overlay,
    has_overlay,
    strip_overlay_content,
    apply_overlay,
    strip_overlay,
    session_model_instructions_path,
    write_session_model_instructions_file,
    remove_session_model_instructions_file,
)
from omx.hooks.codebase_map import (
    _build_dir_line,
    _group_by_top_dir,
    _sort_dirs,
    generate_codebase_map,
)
from omx.hooks.plugin_runner import (
    run_plugin,
)
from omx.hooks.sdk import (
    HookPluginSdk,
    HookPluginStateApi,
    create_hook_plugin_sdk,
    clear_hook_plugin_state,
    _sanitize_plugin_name,
)
from omx.hooks.dispatcher import (
    HookRuntimeDispatchResult,
    dispatch_hook_event_runtime,
)
from omx.hooks.types import HookEventEnvelope


class TestAgentsOverlay(unittest.TestCase):
    """Tests for AGENTS.md overlay generation and manipulation."""

    def test_generate_overlay_within_size_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            overlay = generate_overlay(tmpdir, "test-session")
            self.assertLessEqual(len(overlay), MAX_OVERLAY_SIZE)
            self.assertIn(START_MARKER, overlay)
            self.assertIn(END_MARKER, overlay)
            self.assertIn("test-session", overlay)

    def test_has_overlay(self) -> None:
        self.assertTrue(
            has_overlay(f"before\n{START_MARKER}\ncontent\n{END_MARKER}\nafter")
        )
        self.assertFalse(has_overlay("no markers here"))
        self.assertFalse(has_overlay(START_MARKER))  # needs both

    def test_strip_overlay_content(self) -> None:
        content = f"before\n{START_MARKER}\noverlay stuff\n{END_MARKER}\nafter"
        stripped = strip_overlay_content(content)
        self.assertNotIn(START_MARKER, stripped)
        self.assertNotIn(END_MARKER, stripped)
        self.assertIn("before", stripped)
        self.assertIn("after", stripped)

    def test_strip_overlay_content_no_markers(self) -> None:
        content = "clean content"
        self.assertEqual(strip_overlay_content(content), content)

    def test_strip_overlay_content_malformed(self) -> None:
        content = f"before\n{START_MARKER}\nunterminated overlay"
        stripped = strip_overlay_content(content)
        self.assertNotIn(START_MARKER, stripped)
        self.assertIn("before", stripped)

    def test_apply_and_strip_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_path = os.path.join(tmpdir, "AGENTS.md")
            Path(agents_path).write_text("# Base content\n", encoding="utf-8")

            overlay = generate_overlay(tmpdir, "s1")
            apply_overlay(agents_path, overlay, tmpdir)

            content = Path(agents_path).read_text(encoding="utf-8")
            self.assertIn(START_MARKER, content)
            self.assertIn("# Base content", content)

            strip_overlay(agents_path, tmpdir)
            final = Path(agents_path).read_text(encoding="utf-8")
            self.assertNotIn(START_MARKER, final)
            self.assertIn("# Base content", final)

    def test_apply_overlay_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            agents_path = os.path.join(tmpdir, "AGENTS.md")
            Path(agents_path).write_text("base\n", encoding="utf-8")

            overlay1 = generate_overlay(tmpdir, "s1")
            apply_overlay(agents_path, overlay1, tmpdir)
            overlay2 = generate_overlay(tmpdir, "s2")
            apply_overlay(agents_path, overlay2, tmpdir)

            content = Path(agents_path).read_text(encoding="utf-8")
            # Should only have one overlay (the latest)
            self.assertEqual(content.count(START_MARKER), 1)

    def test_session_model_instructions_path(self) -> None:
        path = session_model_instructions_path("/tmp/proj", "sess1")
        self.assertTrue(str(path).endswith("AGENTS.md"))
        self.assertIn("sess1", str(path))

    def test_write_and_remove_session_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            overlay = generate_overlay(tmpdir, "test-sess")
            path = write_session_model_instructions_file(tmpdir, "test-sess", overlay)
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn(START_MARKER, content)

            remove_session_model_instructions_file(tmpdir, "test-sess")
            self.assertFalse(path.exists())

    def test_notepad_priority_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            notepad = Path(tmpdir) / ".omx" / "notepad.md"
            notepad.parent.mkdir(parents=True, exist_ok=True)
            notepad.write_text(
                "## PRIORITY\nFix the bug\n## BACKLOG\nStuff\n", encoding="utf-8"
            )

            overlay = generate_overlay(tmpdir, "s1")
            self.assertIn("Fix the bug", overlay)

    def test_project_memory_injection(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mem = Path(tmpdir) / ".omx" / "project-memory.json"
            mem.parent.mkdir(parents=True, exist_ok=True)
            mem.write_text(
                json.dumps(
                    {
                        "techStack": "Python",
                        "conventions": "PEP8",
                    }
                ),
                encoding="utf-8",
            )

            overlay = generate_overlay(tmpdir, "s1")
            self.assertIn("Python", overlay)
            self.assertIn("PEP8", overlay)


class TestCodebaseMap(unittest.TestCase):
    """Tests for codebase map generation."""

    def test_group_by_top_dir(self) -> None:
        files = ["src/a.py", "src/b.py", "tests/test_a.py", "setup.py"]
        groups = _group_by_top_dir(files)
        self.assertIn("src", groups)
        self.assertIn("tests", groups)
        self.assertIn(".", groups)
        self.assertEqual(len(groups["src"]), 2)

    def test_sort_dirs(self) -> None:
        dirs = ["tests", "src", ".git", ".", "scripts"]
        sorted_d = _sort_dirs(dirs)
        self.assertEqual(sorted_d[0], "src")
        self.assertEqual(sorted_d[1], "scripts")
        # dotfiles at end
        self.assertIn(".", sorted_d[-1:] + sorted_d[-2:-1])

    def test_build_dir_line(self) -> None:
        line = _build_dir_line(
            "src/hooks", ["src/hooks/overlay.py", "src/hooks/map.py"]
        )
        self.assertIn("src/hooks/:", line)
        self.assertIn("overlay", line)
        self.assertIn("map", line)

    def test_build_dir_line_root(self) -> None:
        line = _build_dir_line(".", ["setup.py"])
        self.assertIn("(root)", line)

    def test_build_dir_line_skip_index(self) -> None:
        line = _build_dir_line("pkg", ["pkg/index.py", "pkg/main.py"])
        self.assertNotIn("index", line)
        self.assertIn("main", line)

    def test_build_dir_line_only_index(self) -> None:
        line = _build_dir_line("pkg", ["pkg/index.py"])
        self.assertIn("index", line)

    def test_generate_codebase_map_no_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = generate_codebase_map(tmpdir)
            self.assertEqual(result, "")


class TestPluginRunner(unittest.TestCase):
    """Tests for plugin runner."""

    def test_run_plugin_missing_path(self) -> None:
        result = run_plugin({"pluginPath": "", "event": {}, "cwd": "."})
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing_path")

    def test_run_plugin_nonexistent_file(self) -> None:
        result = run_plugin(
            {
                "pluginPath": "/nonexistent/plugin.py",
                "event": {"event": "test"},
                "cwd": ".",
            }
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "runner_error")

    def test_run_plugin_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin = Path(tmpdir) / "test_plugin.py"
            plugin.write_text(
                "def on_hook_event(event, sdk):\n    pass\n",
                encoding="utf-8",
            )
            result = run_plugin(
                {
                    "pluginPath": str(plugin),
                    "event": {"event": "test"},
                    "cwd": tmpdir,
                }
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["reason"], "ok")

    def test_run_plugin_invalid_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin = Path(tmpdir) / "bad_plugin.py"
            plugin.write_text("x = 1\n", encoding="utf-8")
            result = run_plugin(
                {
                    "pluginPath": str(plugin),
                    "event": {"event": "test"},
                    "cwd": tmpdir,
                }
            )
            self.assertFalse(result["ok"])
            self.assertEqual(result["reason"], "invalid_export")


class TestHookSdk(unittest.TestCase):
    """Tests for hook plugin SDK."""

    def test_sanitize_plugin_name(self) -> None:
        self.assertEqual(_sanitize_plugin_name("my-plugin"), "my-plugin")
        self.assertEqual(_sanitize_plugin_name("bad name!"), "bad_name_")
        self.assertEqual(_sanitize_plugin_name("CamelCase"), "camelcase")

    def test_create_hook_plugin_sdk(self) -> None:
        sdk = create_hook_plugin_sdk(
            cwd="/tmp",
            plugin_name="test-plugin",
            event={"event": "start"},
        )
        self.assertIsInstance(sdk, HookPluginSdk)
        self.assertEqual(sdk.plugin_name, "test-plugin")
        self.assertTrue(sdk.side_effects_enabled)

    def test_state_api_read_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            api = HookPluginStateApi(cwd=tmpdir, plugin_name="test")
            api.write("key1", {"count": 42})
            value = api.read("key1")
            self.assertEqual(value["count"], 42)

    def test_state_api_read_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            api = HookPluginStateApi(cwd=tmpdir, plugin_name="test")
            self.assertIsNone(api.read("nonexistent"))

    def test_state_api_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            api = HookPluginStateApi(cwd=tmpdir, plugin_name="test")
            api.write("key1", "value1")
            api.clear()
            self.assertIsNone(api.read("key1"))

    def test_clear_hook_plugin_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sdk = create_hook_plugin_sdk(tmpdir, "my-plugin")
            sdk.state.write("k", "v")
            clear_hook_plugin_state(tmpdir, "my-plugin")
            self.assertIsNone(sdk.state.read("k"))


class TestRuntimeDispatch(unittest.TestCase):
    """Tests for the runtime dispatch wrapper."""

    def test_dispatch_disabled(self) -> None:
        # Force plugins disabled
        old = os.environ.get("OMX_HOOK_PLUGINS")
        os.environ["OMX_HOOK_PLUGINS"] = "0"
        try:
            event = HookEventEnvelope(event="custom", source="plugin")
            result = dispatch_hook_event_runtime("/tmp", event)
            self.assertFalse(result.dispatched)
            self.assertEqual(result.reason, "plugins_disabled")
        finally:
            if old is None:
                os.environ.pop("OMX_HOOK_PLUGINS", None)
            else:
                os.environ["OMX_HOOK_PLUGINS"] = old

    def test_dispatch_native_always_enabled(self) -> None:
        old = os.environ.get("OMX_HOOK_PLUGINS")
        os.environ["OMX_HOOK_PLUGINS"] = "0"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                event = HookEventEnvelope(event="start", source="native")
                result = dispatch_hook_event_runtime(tmpdir, event)
                self.assertTrue(result.dispatched)
                self.assertEqual(result.reason, "ok")
        finally:
            if old is None:
                os.environ.pop("OMX_HOOK_PLUGINS", None)
            else:
                os.environ["OMX_HOOK_PLUGINS"] = old

    def test_dispatch_result_dataclass(self) -> None:
        r = HookRuntimeDispatchResult(dispatched=True, reason="ok", result={"x": 1})
        self.assertTrue(r.dispatched)
        self.assertEqual(r.result["x"], 1)


if __name__ == "__main__":
    unittest.main()
