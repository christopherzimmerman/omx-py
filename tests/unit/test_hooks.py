"""Tests for omx.hooks — plugin system, triage, keyword detection."""

import tempfile
import unittest
from pathlib import Path

from omx.hooks.keyword_detector import detect_skill_keyword, is_continuation_prompt
from omx.hooks.loader import (
    discover_hook_plugins,
    sanitize_plugin_id,
)
from omx.hooks.triage_heuristic import triage_prompt
from omx.hooks.types import (
    build_derived_hook_event,
    build_hook_event,
    build_native_hook_event,
)


class TestHookEvents(unittest.TestCase):
    def test_build_native_event(self):
        event = build_native_hook_event("stop", {"reason": "user"})
        self.assertEqual(event.event, "stop")
        self.assertEqual(event.source, "native")
        self.assertIsNone(event.confidence)
        self.assertEqual(event.context["reason"], "user")

    def test_build_derived_event(self):
        event = build_derived_hook_event(
            "needs-input", confidence=0.8, parser_reason="no prompt"
        )
        self.assertEqual(event.source, "derived")
        self.assertEqual(event.confidence, 0.8)
        self.assertEqual(event.parser_reason, "no prompt")

    def test_build_event_auto_detects_source(self):
        native = build_hook_event("stop")
        self.assertEqual(native.source, "native")
        derived = build_hook_event("needs-input")
        self.assertEqual(derived.source, "derived")

    def test_confidence_clamped(self):
        event = build_derived_hook_event("needs-input", confidence=5.0)
        self.assertEqual(event.confidence, 1.0)
        event2 = build_derived_hook_event("needs-input", confidence=-1.0)
        self.assertEqual(event2.confidence, 0.0)

    def test_event_to_dict(self):
        event = build_native_hook_event("start", session_id="s1")
        d = event.to_dict()
        self.assertEqual(d["event"], "start")
        self.assertEqual(d["session_id"], "s1")
        self.assertNotIn("confidence", d)


class TestKeywordDetector(unittest.TestCase):
    def test_continuation_prompts(self):
        self.assertTrue(is_continuation_prompt("keep going"))
        self.assertTrue(is_continuation_prompt("/continue"))
        self.assertTrue(is_continuation_prompt("  resume"))
        self.assertFalse(is_continuation_prompt("hello world"))

    def test_detect_skill_keyword(self):
        skills = ["autopilot", "team", "ralph"]
        self.assertEqual(detect_skill_keyword("$autopilot", skills), "autopilot")
        self.assertEqual(detect_skill_keyword("$team start", skills), "team")
        self.assertIsNone(detect_skill_keyword("$unknown", skills))
        self.assertIsNone(detect_skill_keyword("hello", skills))


class TestTriage(unittest.TestCase):
    def test_trivial_pass(self):
        decision = triage_prompt("hi")
        self.assertEqual(decision.lane, "PASS")
        self.assertEqual(decision.reason, "trivial_acknowledgement")

    def test_opt_out_pass(self):
        decision = triage_prompt("just chat with me about python")
        self.assertEqual(decision.lane, "PASS")
        self.assertEqual(decision.reason, "explicit_opt_out")

    def test_explore_question(self):
        decision = triage_prompt("explain how the auth middleware works")
        self.assertEqual(decision.lane, "LIGHT")
        self.assertEqual(decision.destination, "explore")
        self.assertEqual(decision.reason, "question_or_explanation")

    def test_research_signals(self):
        decision = triage_prompt("look up the official docs for the new API version")
        self.assertEqual(decision.lane, "LIGHT")
        self.assertEqual(decision.destination, "researcher")
        self.assertEqual(decision.reason, "external_reference_research")

    def test_heavy_imperative(self):
        decision = triage_prompt(
            "implement a new caching layer using Redis with proper invalidation and TTL management"
        )
        self.assertEqual(decision.lane, "HEAVY")
        self.assertEqual(decision.destination, "autopilot")

    def test_empty_prompt(self):
        decision = triage_prompt("")
        self.assertEqual(decision.lane, "PASS")
        self.assertEqual(decision.reason, "empty_input")

    def test_anchored_edit_executor(self):
        decision = triage_prompt("fix typo in src/app/auth.py")
        self.assertEqual(decision.lane, "LIGHT")
        self.assertEqual(decision.destination, "executor")
        self.assertEqual(decision.reason, "anchored_edit")


class TestLoader(unittest.TestCase):
    def test_sanitize_plugin_id(self):
        self.assertEqual(sanitize_plugin_id("my-hook.py"), "my-hook")
        self.assertEqual(sanitize_plugin_id("Some Hook.sh"), "some_hook")

    def test_discover_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins = discover_hook_plugins(tmpdir)
            self.assertEqual(plugins, [])

    def test_discover_plugins(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            hooks_dir = Path(tmpdir) / ".omx" / "hooks"
            hooks_dir.mkdir(parents=True)
            (hooks_dir / "notify.py").write_text("# plugin")
            (hooks_dir / "log.sh").write_text("# plugin")
            (hooks_dir / "readme.txt").write_text("not a plugin")

            plugins = discover_hook_plugins(tmpdir)
            self.assertEqual(len(plugins), 2)
            names = [p.name for p in plugins]
            self.assertIn("notify.py", names)
            self.assertIn("log.sh", names)


if __name__ == "__main__":
    unittest.main()
