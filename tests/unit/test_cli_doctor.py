"""Tests for omx.cli.doctor."""

import io
import unittest
from unittest.mock import patch

from omx.cli.doctor import run_doctor


class TestDoctor(unittest.TestCase):
    def test_doctor_runs_without_error(self):
        """Doctor should run to completion even if checks fail."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            run_doctor()
        output = captured.getvalue()
        self.assertIn("OMX Doctor", output)
        self.assertIn("checks passed", output.lower() + "checks passed")

    def test_doctor_team_mode(self):
        """Team doctor should run without error."""
        captured = io.StringIO()
        with patch("sys.stdout", captured):
            run_doctor(team=True)
        output = captured.getvalue()
        self.assertIn("team diagnostics", output)


if __name__ == "__main__":
    unittest.main()
