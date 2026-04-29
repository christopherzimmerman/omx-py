"""Tests for omx.sparkshell and omx.explore."""

import sys
import unittest

from omx.explore.allowlist import is_command_allowed
from omx.explore.harness import explore_execute
from omx.sparkshell.exec import execute_command
from omx.sparkshell.registry.generic import is_mutating_command, is_read_only_command

# echo is a shell builtin on Windows, needs cmd /c
_ECHO_CMD = (
    ["cmd", "/c", "echo", "hello"] if sys.platform == "win32" else ["echo", "hello"]
)
_ECHO_TEST_CMD = (
    ["cmd", "/c", "echo", "test"] if sys.platform == "win32" else ["echo", "test"]
)


class TestSparkshell(unittest.TestCase):
    def test_execute_echo(self):
        result = execute_command(_ECHO_CMD)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("hello", result.stdout)

    def test_execute_nonexistent(self):
        result = execute_command(["definitely_not_a_real_command_xyz"])
        self.assertEqual(result.exit_code, -1)
        self.assertIn("not found", result.stderr.lower())

    def test_is_read_only(self):
        self.assertTrue(is_read_only_command("ls"))
        self.assertTrue(is_read_only_command("cat"))
        self.assertTrue(is_read_only_command("grep"))
        self.assertFalse(is_read_only_command("rm"))

    def test_is_mutating(self):
        self.assertTrue(is_mutating_command("rm"))
        self.assertTrue(is_mutating_command("mv"))
        self.assertFalse(is_mutating_command("ls"))


class TestExplore(unittest.TestCase):
    def test_allowed_commands(self):
        self.assertTrue(is_command_allowed(["ls", "-la"]))
        self.assertTrue(is_command_allowed(["cat", "file.txt"]))
        self.assertTrue(is_command_allowed(["git", "log"]))
        self.assertFalse(is_command_allowed(["rm", "-rf", "/"]))
        self.assertFalse(is_command_allowed(["git", "push"]))
        self.assertFalse(is_command_allowed([]))

    def test_explore_execute_allowed(self):
        result = explore_execute(_ECHO_TEST_CMD)
        self.assertEqual(result.exit_code, 0)
        self.assertIn("test", result.stdout)

    def test_explore_execute_denied(self):
        result = explore_execute(["rm", "something"])
        self.assertEqual(result.exit_code, 1)
        self.assertIn("not allowed", result.stderr)


if __name__ == "__main__":
    unittest.main()
