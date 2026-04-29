"""Regression tests for worker readiness polling: update prompts, > prompt, trust prompts."""

import unittest

from omx.team.tmux_session import (
    _pane_has_bypass_prompt,
    _pane_has_trust_prompt,
    _pane_has_update_prompt,
    _pane_looks_ready,
)


class TestCodexPromptReady(unittest.TestCase):
    def test_greater_than_prompt_is_ready(self):
        """Codex prompt line starting with > should be recognized as ready."""
        captured = "Welcome to Codex!\n\n> "
        self.assertTrue(_pane_looks_ready(captured))

    def test_greater_than_with_text_is_ready(self):
        captured = "Some output\n> "
        self.assertTrue(_pane_looks_ready(captured))

    def test_unicode_203a_prompt_is_ready(self):
        """Codex uses U+203A (›) as its prompt character."""
        captured = "Welcome to Codex!\n\n\u203a "
        self.assertTrue(_pane_looks_ready(captured))

    def test_unicode_203a_at_line_start(self):
        captured = "Some output here\n\u203a "
        self.assertTrue(_pane_looks_ready(captured))

    def test_unicode_276f_prompt_is_ready(self):
        """Some terminals use U+276F (❯) as prompt."""
        captured = "Ready\n\u276f "
        self.assertTrue(_pane_looks_ready(captured))

    def test_dollar_prompt_is_ready(self):
        captured = "user@host:~$ "
        self.assertTrue(_pane_looks_ready(captured))

    def test_empty_pane_not_ready(self):
        self.assertFalse(_pane_looks_ready(""))
        self.assertFalse(_pane_looks_ready("   \n  \n  "))

    def test_active_task_not_ready(self):
        captured = "Thinking about your request...\nGenerating response..."
        self.assertFalse(_pane_looks_ready(captured))

    def test_what_can_i_help_is_ready(self):
        captured = "Claude Code\n\nWhat can I help you with?"
        self.assertTrue(_pane_looks_ready(captured))

    def test_enter_a_prompt_is_ready(self):
        captured = "Codex CLI v1.0\nEnter a prompt to get started"
        self.assertTrue(_pane_looks_ready(captured))


class TestUpdatePromptDetection(unittest.TestCase):
    def test_detects_update_available(self):
        captured = (
            "Codex CLI v1.0\n"
            "A new version is available! update available\n"
            "Run: npm install -g @openai/codex\n"
            "1. Update now\n"
            "2. Skip\n"
            "> "
        )
        self.assertTrue(_pane_has_update_prompt(captured))

    def test_detects_upgrade_available(self):
        captured = "upgrade available: 1.0 -> 2.0\n> "
        self.assertTrue(_pane_has_update_prompt(captured))

    def test_no_update_prompt(self):
        captured = "Welcome to Codex!\n> "
        self.assertFalse(_pane_has_update_prompt(captured))

    def test_ready_with_update_prompt(self):
        """Pane showing update prompt AND > prompt should be considered ready."""
        captured = "update available: v2.0\n1. Update now\n2. Skip\n> "
        self.assertTrue(_pane_has_update_prompt(captured))
        self.assertTrue(_pane_looks_ready(captured))


class TestTrustPromptDetection(unittest.TestCase):
    def test_detects_trust_prompt(self):
        captured = (
            "Do you trust the contents of this directory?\nYes, continue\nNo, quit\n"
        )
        self.assertTrue(_pane_has_trust_prompt(captured))

    def test_no_trust_prompt(self):
        captured = "Welcome to Codex!\n> "
        self.assertFalse(_pane_has_trust_prompt(captured))


class TestBypassPromptDetection(unittest.TestCase):
    def test_detects_bypass_prompt(self):
        captured = "Bypass Permissions mode\n1. No, exit\n2. Yes, I accept\n"
        self.assertTrue(_pane_has_bypass_prompt(captured))

    def test_no_bypass_prompt(self):
        captured = "Welcome to Claude!\n> "
        self.assertFalse(_pane_has_bypass_prompt(captured))


if __name__ == "__main__":
    unittest.main()
