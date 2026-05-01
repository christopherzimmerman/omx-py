"""Tests for orchestration command handlers (team, ralph, explore, resume, autoresearch)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class TestTeamWithPromptHasDispatch(unittest.TestCase):
    """Verify that team with --prompt dispatches tasks to workers."""

    @mock.patch("omx.team.tmux_session.wait_for_worker_ready", return_value=True)
    @mock.patch("omx.team.tmux_session.create_team_session")
    @mock.patch("omx.utils.platform.which")
    def test_team_with_prompt_has_dispatch(self, mock_which, mock_create, mock_wait):
        """When --prompt is provided, assign_pending_tasks dispatches to workers."""
        from dataclasses import dataclass, field

        @dataclass
        class FakeSession:
            name: str = "omx-test"
            worker_count: int = 2
            cwd: str = ""
            worker_pane_ids: list = field(default_factory=lambda: ["%1", "%2"])
            leader_pane_id: str = "%0"

        mock_which.return_value = Path("/usr/bin/tmux")

        with tempfile.TemporaryDirectory() as tmp:
            fake_session = FakeSession(cwd=tmp)
            mock_create.return_value = fake_session

            with (
                mock.patch(
                    "omx.team.runtime.assign_pending_tasks",
                    return_value=["task-1", "task-2"],
                ) as mock_assign,
                mock.patch("omx.team.state.io.write_team_config"),
                mock.patch("omx.team.state.io.write_workers"),
                mock.patch("omx.team.state.io.write_tasks"),
                mock.patch("builtins.print"),
                mock.patch(
                    "os.environ", {**os.environ, "OMX_TEAM_WORKER_CLI": "codex"}
                ),
            ):
                import argparse

                args = argparse.Namespace(spec="2:executor", prompt="do stuff")
                # Patch __import__("pathlib").Path.cwd to return tmp
                with mock.patch("pathlib.Path.cwd", return_value=Path(tmp)):
                    from omx.cli import _handle_team

                    _handle_team(args)

                mock_assign.assert_called_once()


class TestRalphWritesStateAndInstructions(unittest.TestCase):
    """Verify ralph writes state and session instructions."""

    @mock.patch("omx.utils.platform.which", return_value=Path("/usr/bin/codex"))
    @mock.patch("subprocess.run")
    def test_ralph_writes_state_and_instructions(self, mock_run, mock_which):
        """Ralph should write state and an instructions file, then launch codex."""
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch(
                    "omx.state.paths.resolve_working_directory",
                    return_value=Path(tmp),
                ),
                mock.patch(
                    "omx.state.operations.state_write", return_value={"path": "x"}
                ),
                mock.patch("omx.ralph.persistence.ensure_canonical_ralph_artifacts"),
                mock.patch("builtins.print"),
            ):
                import argparse

                from omx.cli import _handle_ralph

                args = argparse.Namespace(prompt="fix the bug")
                _handle_ralph(args)

                # Instructions file should exist
                instructions_path = Path(tmp) / ".omx" / "ralph-instructions.md"
                self.assertTrue(instructions_path.exists())

                content = instructions_path.read_text(encoding="utf-8")
                self.assertIn("Ralph persistence mode", content)
                self.assertIn("fix the bug", content)
                self.assertIn("Investigate", content)
                self.assertIn("Verify", content)

                # Codex should have been launched
                mock_run.assert_called_once()
                call_args = mock_run.call_args[0][0]
                self.assertIn("-c", call_args)
                self.assertTrue(any("model_instructions_file=" in a for a in call_args))


class TestExploreBuildInstructions(unittest.TestCase):
    """Verify explore builds instructions and launches codex."""

    @mock.patch("omx.utils.platform.which", return_value=Path("/usr/bin/codex"))
    @mock.patch("subprocess.run")
    def test_explore_builds_instructions(self, mock_run, mock_which):
        """Explore should write an instructions file with read-only constraints."""
        with tempfile.TemporaryDirectory() as tmp:
            with (
                mock.patch("pathlib.Path.cwd", return_value=Path(tmp)),
                mock.patch(
                    "omx.utils.paths.package_root",
                    return_value=Path(tmp) / "fake_pkg",
                ),
            ):
                import argparse

                from omx.cli import _handle_explore

                args = argparse.Namespace(prompt="where is the auth module?")
                _handle_explore(args)

                instructions_path = Path(tmp) / ".omx" / "explore-instructions.md"
                self.assertTrue(instructions_path.exists())

                content = instructions_path.read_text(encoding="utf-8")
                self.assertIn("read-only exploration mode", content)
                self.assertIn("where is the auth module?", content)
                self.assertIn("Do not modify any files", content)

                mock_run.assert_called_once()


class TestAutoresearchShowsDeprecation(unittest.TestCase):
    """Verify autoresearch prints deprecation message."""

    def test_autoresearch_shows_deprecation(self):
        """The autoresearch command should print a deprecation notice and exit 0."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "from omx.cli import main; main(['autoresearch'])",
            ],
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONPATH": "src"},
            cwd=str(Path(__file__).resolve().parent.parent.parent),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("deprecated", result.stdout)
        self.assertIn("$autoresearch", result.stdout)


class TestResumeNoSessionPrintsError(unittest.TestCase):
    """Verify resume with no session prints an error."""

    def test_resume_no_session_prints_error(self):
        """Resume should print an error when no session.json exists."""
        project_root = str(
            __import__("pathlib").Path(__file__).resolve().parent.parent.parent
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    f"import sys; sys.path.insert(0, {project_root + '/src'!r}); from omx.cli import main; main(['resume'])",
                ],
                capture_output=True,
                text=True,
                cwd=tmp,
            )
            self.assertNotEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertIn("no previous session", combined.lower())


if __name__ == "__main__":
    unittest.main()
