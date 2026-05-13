"""Tests for omx.team.worker_bootstrap — content generation, file I/O, overlay.

Covers the deepened Phase 2.2 port of ``team/worker-bootstrap.ts``:

- Worker root AGENTS.md content + install/rollback (with git-tracked vs
  untracked branches stubbed via subprocess monkey-patching).
- Generic overlay generation + idempotent ``apply_worker_overlay`` /
  ``strip_worker_overlay`` round-trips.
- Composed team and per-worker role instruction files.
- Inbox content generators (initial / task-assignment / shutdown).
- Trigger message + directive helpers (worker inbox, worker mailbox,
  leader mailbox), both default-state-root and custom-state-root branches.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from omx.team import worker_bootstrap as wb
from omx.team.worker_bootstrap import (
    TEAM_OVERLAY_END,
    TEAM_OVERLAY_START,
    TeamReminderDirective,
    WorkerRootAgentsOptions,
    apply_worker_overlay,
    build_leader_mailbox_trigger_directive,
    build_mailbox_trigger_directive,
    build_trigger_directive,
    generate_initial_inbox,
    generate_leader_mailbox_trigger_message,
    generate_mailbox_trigger_message,
    generate_shutdown_inbox,
    generate_task_assignment_inbox,
    generate_trigger_message,
    generate_worker_overlay,
    generate_worker_root_agents_content,
    remove_team_worker_instructions_file,
    remove_worker_worktree_root_agents_file,
    strip_worker_overlay,
    write_team_worker_instructions_file,
    write_worker_role_instructions_file,
    write_worker_worktree_root_agents_file,
)


def _make_options(
    tmp: Path,
    *,
    team: str = "alpha",
    worker: str = "worker-1",
    role: str = "executor",
    role_prompt: str = "  Focus on clean refactors.  ",
) -> WorkerRootAgentsOptions:
    worktree = tmp / "wt"
    worktree.mkdir(parents=True, exist_ok=True)
    return WorkerRootAgentsOptions(
        team_name=team,
        worker_name=worker,
        worker_role=role,
        role_prompt_content=role_prompt,
        team_state_root=str(tmp / "state"),
        leader_cwd=str(tmp / "lead"),
        worktree_path=str(worktree),
    )


class TestGenerateWorkerRootAgentsContent(unittest.TestCase):
    def test_content_contains_identity_block(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            out = generate_worker_root_agents_content(options)
        self.assertIn("# Team Worker Runtime Instructions", out)
        self.assertIn("- Team: alpha", out)
        self.assertIn("- Worker: worker-1", out)
        self.assertIn("- Role: executor", out)
        self.assertIn("Focus on clean refactors.", out)

    def test_content_role_prompt_is_trimmed(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td), role_prompt="\n\n  hello\n\n")
            out = generate_worker_root_agents_content(options)
        # The trimmed prompt body sits inside the role overlay.
        self.assertIn("\nhello\n</team_worker_role>", out)

    def test_content_includes_paths(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            out = generate_worker_root_agents_content(options)
            state_root = options.team_state_root
        self.assertIn(
            f"- Inbox path: {state_root}/team/alpha/workers/worker-1/inbox.md", out
        )
        self.assertIn(
            f"- Mailbox path: {state_root}/team/alpha/mailbox/worker-1.json", out
        )
        self.assertIn(
            f"- Leader mailbox path: {state_root}/team/alpha/mailbox/leader-fixed.json",
            out,
        )

    def test_content_role_overlay_markers(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            out = generate_worker_root_agents_content(options)
        self.assertIn("<!-- OMX:TEAM:ROLE:START -->", out)
        self.assertIn("<!-- OMX:TEAM:ROLE:END -->", out)


class TestWorktreeAgentsInstallRollback(unittest.TestCase):
    """The install + remove flow drives git via subprocess. We monkey-patch
    ``subprocess.run`` within the module so we can simulate tracked /
    untracked / no-git environments without touching a real repo.
    """

    def _patched_runner(
        self, tracked: bool, *, capture_calls: list[list[str]] | None = None
    ):
        def fake_run(cmd, *args, **kwargs):  # type: ignore[override]
            if capture_calls is not None:
                capture_calls.append(list(cmd))
            argv = list(cmd)
            if argv[:2] == ["git", "rev-parse"]:
                # Tests do not exercise the alternate backup path. Force the
                # state-root fallback by reporting the git path lookup as
                # failed.
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
            if argv[:2] == ["git", "ls-files"]:
                return subprocess.CompletedProcess(
                    argv, 0 if tracked else 1, stdout="", stderr=""
                )
            if argv[:3] in (
                ["git", "update-index", "--skip-worktree"],
                ["git", "update-index", "--no-skip-worktree"],
            ):
                return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        return fake_run

    def test_install_untracked_writes_content_and_backup(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            calls: list[list[str]] = []
            with mock.patch.object(
                wb.subprocess,
                "run",
                side_effect=self._patched_runner(False, capture_calls=calls),
            ):
                out_path = write_worker_worktree_root_agents_file(options)
            self.assertTrue(Path(out_path).exists())
            content = Path(out_path).read_text(encoding="utf-8")
            self.assertIn("# Team Worker Runtime Instructions", content)

            backup_path = (
                Path(options.team_state_root)
                / "team"
                / options.team_name
                / "workers"
                / options.worker_name
                / "root-agents-backup.json"
            )
            self.assertTrue(backup_path.exists())
            backup = json.loads(backup_path.read_text(encoding="utf-8"))
            self.assertFalse(backup["existed"])
            self.assertFalse(backup["tracked"])
            self.assertFalse(backup["skipWorktreeApplied"])

        # No --skip-worktree call should have been made for the untracked branch.
        joined = [" ".join(c) for c in calls]
        self.assertFalse(any("--skip-worktree" in line for line in joined))

    def test_install_tracked_applies_skip_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            calls: list[list[str]] = []
            with mock.patch.object(
                wb.subprocess,
                "run",
                side_effect=self._patched_runner(True, capture_calls=calls),
            ):
                write_worker_worktree_root_agents_file(options)
            backup_path = (
                Path(options.team_state_root)
                / "team"
                / options.team_name
                / "workers"
                / options.worker_name
                / "root-agents-backup.json"
            )
            backup = json.loads(backup_path.read_text(encoding="utf-8"))
            self.assertTrue(backup["tracked"])
            self.assertTrue(backup["skipWorktreeApplied"])
        joined = [" ".join(c) for c in calls]
        self.assertTrue(any("--skip-worktree" in line for line in joined))

    def test_install_preserves_previous_content_in_backup(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            agents_path = Path(options.worktree_path) / "AGENTS.md"
            agents_path.write_text("# old content\n", encoding="utf-8")
            with mock.patch.object(
                wb.subprocess, "run", side_effect=self._patched_runner(False)
            ):
                write_worker_worktree_root_agents_file(options)
            backup_path = (
                Path(options.team_state_root)
                / "team"
                / options.team_name
                / "workers"
                / options.worker_name
                / "root-agents-backup.json"
            )
            backup = json.loads(backup_path.read_text(encoding="utf-8"))
            self.assertTrue(backup["existed"])
            self.assertEqual(backup["previousContent"], "# old content\n")

    def test_rollback_restores_previous_content(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            agents_path = Path(options.worktree_path) / "AGENTS.md"
            agents_path.write_text("# old content\n", encoding="utf-8")
            with mock.patch.object(
                wb.subprocess, "run", side_effect=self._patched_runner(False)
            ):
                write_worker_worktree_root_agents_file(options)
                remove_worker_worktree_root_agents_file(
                    options.team_name,
                    options.worker_name,
                    options.team_state_root,
                    options.worktree_path,
                )
            self.assertEqual(agents_path.read_text(encoding="utf-8"), "# old content\n")

    def test_rollback_removes_file_when_no_prior_content(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            agents_path = Path(options.worktree_path) / "AGENTS.md"
            self.assertFalse(agents_path.exists())
            with mock.patch.object(
                wb.subprocess, "run", side_effect=self._patched_runner(False)
            ):
                write_worker_worktree_root_agents_file(options)
                self.assertTrue(agents_path.exists())
                remove_worker_worktree_root_agents_file(
                    options.team_name,
                    options.worker_name,
                    options.team_state_root,
                    options.worktree_path,
                )
            self.assertFalse(agents_path.exists())

    def test_rollback_missing_backup_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            # No write_worker_worktree_root_agents_file call; backup does not exist.
            with mock.patch.object(
                wb.subprocess, "run", side_effect=self._patched_runner(False)
            ):
                remove_worker_worktree_root_agents_file(
                    options.team_name,
                    options.worker_name,
                    options.team_state_root,
                    options.worktree_path,
                )
        # No exception means success.

    def test_rollback_tracked_calls_no_skip_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            options = _make_options(Path(td))
            calls: list[list[str]] = []
            with mock.patch.object(
                wb.subprocess,
                "run",
                side_effect=self._patched_runner(True, capture_calls=calls),
            ):
                write_worker_worktree_root_agents_file(options)
                remove_worker_worktree_root_agents_file(
                    options.team_name,
                    options.worker_name,
                    options.team_state_root,
                    options.worktree_path,
                )
        joined = [" ".join(c) for c in calls]
        self.assertTrue(any("--no-skip-worktree" in line for line in joined))


class TestGenerateWorkerOverlay(unittest.TestCase):
    def test_overlay_contains_markers_and_team_name(self):
        overlay = generate_worker_overlay("alpha")
        self.assertTrue(overlay.startswith(TEAM_OVERLAY_START))
        self.assertTrue(overlay.endswith(TEAM_OVERLAY_END))
        self.assertIn('You are a team worker in team "alpha"', overlay)

    def test_overlay_references_team_paths(self):
        overlay = generate_worker_overlay("alpha")
        self.assertIn("<team_state_root>/team/alpha/tasks/task-<id>.json", overlay)
        self.assertIn("<team_state_root>/team/alpha/mailbox/{your-name}.json", overlay)


class TestApplyAndStripOverlay(unittest.TestCase):
    def test_apply_to_empty_file_writes_overlay(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "AGENTS.md"
            overlay = generate_worker_overlay("alpha")
            apply_worker_overlay(target, overlay)
            content = target.read_text(encoding="utf-8")
            self.assertIn(TEAM_OVERLAY_START, content)
            self.assertIn(TEAM_OVERLAY_END, content)

    def test_apply_preserves_existing_content(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "AGENTS.md"
            target.write_text("# existing\nbody\n", encoding="utf-8")
            apply_worker_overlay(target, generate_worker_overlay("alpha"))
            content = target.read_text(encoding="utf-8")
            self.assertIn("# existing", content)
            self.assertIn(TEAM_OVERLAY_START, content)

    def test_apply_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "AGENTS.md"
            overlay = generate_worker_overlay("alpha")
            apply_worker_overlay(target, overlay)
            apply_worker_overlay(target, overlay)
            content = target.read_text(encoding="utf-8")
            self.assertEqual(content.count(TEAM_OVERLAY_START), 1)
            self.assertEqual(content.count(TEAM_OVERLAY_END), 1)

    def test_strip_removes_overlay(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "AGENTS.md"
            target.write_text("# existing\n", encoding="utf-8")
            apply_worker_overlay(target, generate_worker_overlay("alpha"))
            strip_worker_overlay(target)
            content = target.read_text(encoding="utf-8")
            self.assertNotIn(TEAM_OVERLAY_START, content)
            self.assertNotIn(TEAM_OVERLAY_END, content)
            self.assertIn("# existing", content)

    def test_strip_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "AGENTS.md"
            target.write_text("# only header\n", encoding="utf-8")
            strip_worker_overlay(target)
            strip_worker_overlay(target)
            self.assertEqual(target.read_text(encoding="utf-8"), "# only header\n")

    def test_strip_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "AGENTS.md"
            # Should not raise.
            strip_worker_overlay(target)
            self.assertFalse(target.exists())

    def test_overlay_round_trip_restores_original(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "AGENTS.md"
            original = "# header\n\ntext body line\n"
            target.write_text(original, encoding="utf-8")
            apply_worker_overlay(target, generate_worker_overlay("alpha"))
            strip_worker_overlay(target)
            after = target.read_text(encoding="utf-8")
            # Stripped form preserves the original header/body. Trailing
            # whitespace normalization is acceptable.
            self.assertIn("# header", after)
            self.assertIn("text body line", after)
            self.assertNotIn(TEAM_OVERLAY_START, after)

    def test_concurrent_apply_serializes_via_lock(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "AGENTS.md"
            overlay = generate_worker_overlay("alpha")

            def worker():
                apply_worker_overlay(target, overlay)

            threads = [threading.Thread(target=worker) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            content = target.read_text(encoding="utf-8")
            self.assertEqual(content.count(TEAM_OVERLAY_START), 1)


class TestWriteTeamWorkerInstructionsFile(unittest.TestCase):
    def test_no_source_files_yields_overlay_only(self):
        with tempfile.TemporaryDirectory() as td:
            with mock.patch.object(
                wb, "codex_home", return_value=Path(td) / "codex_home"
            ):
                overlay = generate_worker_overlay("alpha")
                out_path = write_team_worker_instructions_file("alpha", td, overlay)
            out_text = Path(out_path).read_text(encoding="utf-8")
            self.assertTrue(out_text.startswith(TEAM_OVERLAY_START))
            self.assertTrue(
                out_path.endswith(
                    os.path.join(".omx", "state", "team", "alpha", "worker-agents.md")
                )
            )

    def test_user_and_project_agents_are_composed(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td) / "project"
            cwd.mkdir()
            codex_home = Path(td) / "codex_home"
            codex_home.mkdir()
            (codex_home / "AGENTS.md").write_text(
                "# User-level guidance\nUSER_LINE\n", encoding="utf-8"
            )
            (cwd / "AGENTS.md").write_text(
                "# Project guidance\nPROJECT_LINE\n", encoding="utf-8"
            )
            overlay = generate_worker_overlay("alpha")
            with mock.patch.object(wb, "codex_home", return_value=codex_home):
                out_path = write_team_worker_instructions_file(
                    "alpha", str(cwd), overlay
                )
            out_text = Path(out_path).read_text(encoding="utf-8")
        self.assertIn("USER_LINE", out_text)
        self.assertIn("PROJECT_LINE", out_text)
        self.assertIn(TEAM_OVERLAY_START, out_text)
        # User content sits before project content.
        self.assertLess(out_text.index("USER_LINE"), out_text.index("PROJECT_LINE"))

    def test_existing_overlay_in_source_is_stripped(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td) / "project"
            cwd.mkdir()
            codex_home = Path(td) / "codex_home"
            codex_home.mkdir()
            (cwd / "AGENTS.md").write_text(
                "# project\n" + generate_worker_overlay("alpha") + "\nTRAILER\n",
                encoding="utf-8",
            )
            with mock.patch.object(wb, "codex_home", return_value=codex_home):
                out_path = write_team_worker_instructions_file(
                    "alpha", str(cwd), generate_worker_overlay("alpha")
                )
            out_text = Path(out_path).read_text(encoding="utf-8")
        # Only one overlay block survives — the freshly-appended one.
        self.assertEqual(out_text.count(TEAM_OVERLAY_START), 1)
        self.assertIn("TRAILER", out_text)

    def test_shadowed_user_skill_reference_lines_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            cwd = Path(td) / "project"
            cwd.mkdir()
            codex_home = Path(td) / "codex_home"
            codex_home.mkdir()
            # Create a project-scope skill named "shadowed".
            project_skill = cwd / ".codex" / "skills" / "shadowed"
            project_skill.mkdir(parents=True)
            (project_skill / "SKILL.md").write_text("# stub", encoding="utf-8")
            # User AGENTS.md references the shadowed skill.
            (codex_home / "AGENTS.md").write_text(
                "Other line\n"
                "- See `~/.codex/skills/shadowed/SKILL.md` for help\n"
                "Tail line\n",
                encoding="utf-8",
            )
            with mock.patch.object(wb, "codex_home", return_value=codex_home):
                out_path = write_team_worker_instructions_file(
                    "alpha", str(cwd), generate_worker_overlay("alpha")
                )
            out_text = Path(out_path).read_text(encoding="utf-8")
        self.assertIn("Other line", out_text)
        self.assertIn("Tail line", out_text)
        self.assertNotIn("/skills/shadowed/SKILL.md", out_text)


class TestWriteWorkerRoleInstructionsFile(unittest.TestCase):
    def test_role_overlay_appended_to_base(self):
        with tempfile.TemporaryDirectory() as td:
            base_path = Path(td) / "base.md"
            base_path.write_text("# base\nhello\n", encoding="utf-8")
            out_path = write_worker_role_instructions_file(
                "alpha",
                "worker-1",
                td,
                str(base_path),
                "executor",
                "  Stay focused.  ",
            )
            text = Path(out_path).read_text(encoding="utf-8")
        self.assertTrue(
            out_path.endswith(
                os.path.join(
                    ".omx", "state", "team", "alpha", "workers", "worker-1", "AGENTS.md"
                )
            )
        )
        self.assertIn("# base", text)
        self.assertIn("<team_worker_role>", text)
        self.assertIn("**executor**", text)
        self.assertIn("Stay focused.", text)
        # The role prompt content is trimmed (no leading/trailing whitespace
        # inside the overlay body).
        self.assertIn("\nStay focused.\n</team_worker_role>", text)
        self.assertNotIn("  Stay focused.", text)

    def test_missing_base_yields_overlay_only(self):
        with tempfile.TemporaryDirectory() as td:
            out_path = write_worker_role_instructions_file(
                "alpha",
                "worker-1",
                td,
                str(Path(td) / "does-not-exist"),
                "executor",
                "Body.",
            )
            text = Path(out_path).read_text(encoding="utf-8")
        # No leading blank line; overlay opens with the marker.
        self.assertTrue(text.lstrip().startswith("<!-- OMX:TEAM:ROLE:START -->"))
        self.assertIn("Body.", text)


class TestRemoveTeamWorkerInstructionsFile(unittest.TestCase):
    def test_removes_file(self):
        with tempfile.TemporaryDirectory() as td:
            out_path = (
                Path(td) / ".omx" / "state" / "team" / "alpha" / "worker-agents.md"
            )
            out_path.parent.mkdir(parents=True)
            out_path.write_text("content", encoding="utf-8")
            remove_team_worker_instructions_file("alpha", td)
            self.assertFalse(out_path.exists())

    def test_missing_file_is_noop(self):
        with tempfile.TemporaryDirectory() as td:
            remove_team_worker_instructions_file("alpha", td)
        # No exception.


class TestGenerateInitialInbox(unittest.TestCase):
    def _tasks_ts_shape(self):
        return [
            {
                "id": "1",
                "subject": "Wire helper",
                "description": "Wire the helper module",
                "status": "pending",
                "role": "executor",
            },
            {
                "id": "2",
                "subject": "Add tests",
                "description": "Add coverage for helper",
                "status": "blocked",
                "blocked_by": ["1"],
            },
        ]

    def test_inbox_header_and_assignment(self):
        out = generate_initial_inbox(
            "worker-1",
            "alpha",
            "executor",
            self._tasks_ts_shape(),
        )
        self.assertIn("# Worker Assignment: worker-1", out)
        self.assertIn("**Team:** alpha", out)
        self.assertIn("**Role:** executor", out)

    def test_inbox_task_list_renders(self):
        out = generate_initial_inbox(
            "worker-1",
            "alpha",
            "executor",
            self._tasks_ts_shape(),
        )
        self.assertIn("- **Task 1**: Wire helper", out)
        self.assertIn("Description: Wire the helper module", out)
        self.assertIn("Status: pending", out)
        self.assertIn("Role: executor", out)
        self.assertIn("Blocked by: 1", out)

    def test_inbox_specialization_suppressed_when_canonical(self):
        out = generate_initial_inbox(
            "worker-1",
            "alpha",
            "executor",
            self._tasks_ts_shape(),
            worker_role="executor",
            role_prompt_content="should not appear",
            worktree_root_agents_canonical=True,
        )
        self.assertNotIn("should not appear", out)
        self.assertNotIn("## Your Specialization", out)

    def test_inbox_specialization_block_emitted(self):
        out = generate_initial_inbox(
            "worker-1",
            "alpha",
            "executor",
            self._tasks_ts_shape(),
            worker_role="executor",
            role_prompt_content="Be terse.",
        )
        self.assertIn("## Your Specialization", out)
        self.assertIn("Be terse.", out)

    def test_inbox_specialization_omitted_when_no_prompt(self):
        out = generate_initial_inbox(
            "worker-1",
            "alpha",
            "executor",
            self._tasks_ts_shape(),
            worker_role="executor",
        )
        self.assertNotIn("## Your Specialization", out)

    def test_inbox_uses_supplied_state_root_and_leader_cwd(self):
        out = generate_initial_inbox(
            "worker-1",
            "alpha",
            "executor",
            self._tasks_ts_shape(),
            team_state_root="/srv/state",
            leader_cwd="/srv/lead",
        )
        self.assertIn("/srv/state/team/alpha/tasks/task-<id>.json", out)
        self.assertIn("`/srv/lead/.codex/skills/worker/SKILL.md`", out)

    def test_inbox_default_state_root_placeholder(self):
        out = generate_initial_inbox(
            "worker-1",
            "alpha",
            "executor",
            self._tasks_ts_shape(),
        )
        self.assertIn("<team_state_root>/team/alpha/tasks/task-<id>.json", out)
        self.assertIn("<leader_cwd>/.codex/skills/worker/SKILL.md", out)

    def test_inbox_accepts_python_teamtask_shape(self):
        from omx.team.contracts import TaskStatus, TeamTask

        tasks = [
            TeamTask(
                task_id="42",
                description="Refactor module",
                status=TaskStatus.PENDING,
                role="executor",
            )
        ]
        out = generate_initial_inbox(
            "worker-1",
            "alpha",
            "executor",
            tasks,
        )
        self.assertIn("- **Task 42**: Refactor module", out)
        self.assertIn("Status: pending", out)


class TestGenerateTaskAssignmentInbox(unittest.TestCase):
    def test_contains_task_id_and_paths(self):
        out = generate_task_assignment_inbox(
            "worker-1", "alpha", "7", "Implement feature X"
        )
        self.assertIn("**Task ID:** 7", out)
        self.assertIn("Implement feature X", out)
        self.assertIn("<team_state_root>/team/alpha/tasks/task-7.json", out)
        self.assertIn('`task_id: "7"`', out)


class TestGenerateShutdownInbox(unittest.TestCase):
    def test_contains_paths_and_instructions(self):
        out = generate_shutdown_inbox("alpha", "worker-1")
        self.assertIn("# Shutdown Request", out)
        self.assertIn(
            "<team_state_root>/team/alpha/workers/worker-1/shutdown-ack.json", out
        )
        self.assertIn('"status":"accept"', out)


class TestTriggerMessages(unittest.TestCase):
    def test_default_state_root_directive(self):
        directive = build_trigger_directive("worker-1", "alpha")
        self.assertIsInstance(directive, TeamReminderDirective)
        self.assertEqual(directive.intent, "followup-relaunch")
        self.assertIn(".omx/state/team/alpha/workers/worker-1/inbox.md", directive.text)
        self.assertIn("start work now", directive.text)
        self.assertLess(len(directive.text), 200)

    def test_custom_state_root_directive_terse(self):
        directive = build_trigger_directive(
            "worker-1", "alpha", team_state_root="/srv/s"
        )
        self.assertIn("/srv/s/team/alpha/workers/worker-1/inbox.md", directive.text)
        # The custom branch uses the shorter wording.
        self.assertIn("work now, report progress", directive.text)
        self.assertNotIn("start work now", directive.text)

    def test_generate_trigger_message_delegates(self):
        text = generate_trigger_message("worker-1", "alpha")
        directive = build_trigger_directive("worker-1", "alpha")
        self.assertEqual(text, directive.text)

    def test_mailbox_directive_default(self):
        directive = build_mailbox_trigger_directive("worker-1", "alpha", 3)
        self.assertEqual(directive.intent, "pending-mailbox-review")
        self.assertIn("3 new message(s)", directive.text)
        self.assertIn(".omx/state/team/alpha/mailbox/worker-1.json", directive.text)
        self.assertLess(len(directive.text), 200)

    def test_mailbox_directive_count_clamped(self):
        d_zero = build_mailbox_trigger_directive("worker-1", "alpha", 0)
        d_neg = build_mailbox_trigger_directive("worker-1", "alpha", -5)
        d_str = build_mailbox_trigger_directive("worker-1", "alpha", "not-a-number")  # type: ignore[arg-type]
        for d in (d_zero, d_neg, d_str):
            self.assertIn(
                "1 new message(s)", d.text + d.text.replace("msg(s)", "message(s)")
            )

    def test_mailbox_directive_custom_state_root(self):
        d = build_mailbox_trigger_directive(
            "worker-1", "alpha", 2, team_state_root="/srv/s"
        )
        self.assertIn("/srv/s/team/alpha/mailbox/worker-1.json", d.text)
        self.assertIn("2 new msg(s)", d.text)

    def test_generate_mailbox_trigger_message_delegates(self):
        self.assertEqual(
            generate_mailbox_trigger_message("worker-1", "alpha", 4),
            build_mailbox_trigger_directive("worker-1", "alpha", 4).text,
        )

    def test_leader_mailbox_directive_default(self):
        d = build_leader_mailbox_trigger_directive("alpha", "worker-3")
        self.assertEqual(d.intent, "pending-mailbox-review")
        self.assertIn(".omx/state/team/alpha/mailbox/leader-fixed.json", d.text)
        self.assertIn("worker-3 sent a new message", d.text)
        self.assertLess(len(d.text), 200)

    def test_leader_mailbox_directive_custom_state_root(self):
        d = build_leader_mailbox_trigger_directive(
            "alpha", "worker-3", team_state_root="/srv/s"
        )
        self.assertIn("/srv/s/team/alpha/mailbox/leader-fixed.json", d.text)
        self.assertIn("new msg from worker-3", d.text)

    def test_generate_leader_mailbox_trigger_message_delegates(self):
        text = generate_leader_mailbox_trigger_message("alpha", "worker-3")
        d = build_leader_mailbox_trigger_directive("alpha", "worker-3")
        self.assertEqual(text, d.text)


if __name__ == "__main__":
    unittest.main()
