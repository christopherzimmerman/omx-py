"""Tests for omx.cli — command dispatcher."""

import subprocess
import sys
import unittest


class TestCli(unittest.TestCase):
    def _run_omx(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "omx", *args],
            capture_output=True,
            text=True,
            env={**__import__("os").environ, "PYTHONPATH": "src"},
            cwd=str(
                __import__("pathlib").Path(__file__).resolve().parent.parent.parent
            ),
        )

    def test_version(self):
        result = self._run_omx("--version")
        self.assertEqual(result.returncode, 0)
        self.assertIn("0.15.0", result.stdout)

    def test_help(self):
        result = self._run_omx("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("setup", result.stdout)
        self.assertIn("doctor", result.stdout)

    def test_help_command(self):
        result = self._run_omx("help")
        self.assertEqual(result.returncode, 0)

    def test_no_args_shows_help(self):
        result = self._run_omx()
        self.assertEqual(result.returncode, 0)
        self.assertIn("usage", result.stdout.lower())

    def test_unknown_command_fails(self):
        result = self._run_omx("nonexistent_command")
        self.assertNotEqual(result.returncode, 0)

    def test_state_list(self):
        import tempfile

        with tempfile.TemporaryDirectory():
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from omx.cli import main; main(['state', 'list'])",
                ],
                capture_output=True,
                text=True,
                env={**__import__("os").environ, "PYTHONPATH": "src"},
                cwd=str(
                    __import__("pathlib").Path(__file__).resolve().parent.parent.parent
                ),
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("active_modes", result.stdout)


if __name__ == "__main__":
    unittest.main()
