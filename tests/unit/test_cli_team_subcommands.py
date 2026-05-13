"""Tests for ``omx team`` sub-subcommands.

Phase 9 (CLI surface completion). Covers:

* Argument parsing (positional + ``--`` flags via in-process dispatch)
* JSON output (``--json``)
* Error paths exit non-zero with a stderr message
* Each sub-subcommand routes to the correct runtime function
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

# Ensure src/ is importable.
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from omx.cli.team_subcommands import (  # noqa: E402
    TEAM_SUBCOMMANDS,
    dispatch_team_subcommand,
    handle_team_broadcast,
    handle_team_reassign,
    handle_team_resume,
    handle_team_scale_down,
    handle_team_scale_up,
    handle_team_send_message,
    handle_team_shutdown,
    handle_team_status,
)


def _capture(func, *args) -> tuple[int, str, str]:
    """Call ``func(*args)`` capturing stdout/stderr and SystemExit code."""
    out = io.StringIO()
    err = io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            func(*args)
    except SystemExit as exc:
        code = exc.code or 0
    return code, out.getvalue(), err.getvalue()


class TestTeamSubcommandRegistry(unittest.TestCase):
    """Sub-subcommand registry must expose the eight Phase 9 entries."""

    def test_registry_contains_phase9_subcommands(self) -> None:
        expected = {
            "status",
            "shutdown",
            "resume",
            "scale-up",
            "scale-down",
            "reassign",
            "send-message",
            "broadcast",
        }
        self.assertTrue(expected.issubset(set(TEAM_SUBCOMMANDS)))

    def test_dispatch_returns_false_for_unknown(self) -> None:
        self.assertFalse(dispatch_team_subcommand("nonexistent", []))

    def test_dispatch_returns_true_for_known(self) -> None:
        with mock.patch.object(
            sys.modules["omx.cli.team_subcommands"],
            "handle_team_status",
        ) as m_status:
            # patch directly on TEAM_SUBCOMMANDS so the dispatch path resolves
            TEAM_SUBCOMMANDS["status"] = m_status
            try:
                self.assertTrue(dispatch_team_subcommand("status", []))
                m_status.assert_called_once_with([])
            finally:
                # restore
                TEAM_SUBCOMMANDS["status"] = handle_team_status


class TestTeamStatus(unittest.TestCase):
    """``omx team status`` happy/missing/JSON paths."""

    def test_no_teams_directory_text(self) -> None:
        with mock.patch(
            "omx.cli.team_subcommands._resolve_default_team_name",
            return_value=None,
        ):
            code, out, _ = _capture(handle_team_status, [])
        self.assertEqual(code, 0)
        self.assertIn("No teams found", out)

    def test_no_teams_directory_json(self) -> None:
        with mock.patch(
            "omx.cli.team_subcommands._resolve_default_team_name",
            return_value=None,
        ):
            code, out, _ = _capture(handle_team_status, ["--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "missing")

    def test_explicit_team_name_no_snapshot_json(self) -> None:
        with mock.patch("omx.team.runtime_monitor.monitor_team_ts", return_value=None):
            code, out, _ = _capture(handle_team_status, ["my-team", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["team_name"], "my-team")
        self.assertEqual(payload["status"], "missing")

    def test_explicit_team_name_no_snapshot_text(self) -> None:
        with mock.patch("omx.team.runtime_monitor.monitor_team_ts", return_value=None):
            code, out, _ = _capture(handle_team_status, ["my-team"])
        self.assertEqual(code, 0)
        self.assertIn("No team state found for my-team", out)

    def test_snapshot_text_output(self) -> None:
        from omx.team.runtime_types import (
            TeamSnapshot,
            TeamSnapshotTasks,
            TeamSnapshotWorker,
        )

        worker = TeamSnapshotWorker(
            name="worker-1",
            alive=True,
            status={},
            heartbeat=None,
        )
        snapshot = TeamSnapshot(
            team_name="my-team",
            phase="team-exec",
            workers=[worker],
            tasks=TeamSnapshotTasks(total=2, pending=1, in_progress=1),
            dead_workers=["worker-2"],
        )
        with mock.patch(
            "omx.team.runtime_monitor.monitor_team_ts", return_value=snapshot
        ):
            code, out, _ = _capture(handle_team_status, ["my-team"])
        self.assertEqual(code, 0)
        self.assertIn("team=my-team", out)
        self.assertIn("phase=team-exec", out)
        self.assertIn("dead_workers: worker-2", out)
        self.assertIn("tasks: total=2", out)

    def test_snapshot_json_output(self) -> None:
        from omx.team.runtime_types import TeamSnapshot, TeamSnapshotTasks

        snapshot = TeamSnapshot(
            team_name="my-team",
            phase="complete",
            tasks=TeamSnapshotTasks(total=3, completed=3),
            all_tasks_terminal=True,
        )
        with mock.patch(
            "omx.team.runtime_monitor.monitor_team_ts", return_value=snapshot
        ):
            code, out, _ = _capture(handle_team_status, ["my-team", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["team_name"], "my-team")
        self.assertEqual(payload["tasks"]["completed"], 3)
        self.assertTrue(payload["all_tasks_terminal"])

    def test_monitor_exception_exits_with_error(self) -> None:
        with mock.patch(
            "omx.team.runtime_monitor.monitor_team_ts",
            side_effect=RuntimeError("monitor boom"),
        ):
            code, _, err = _capture(handle_team_status, ["my-team"])
        self.assertEqual(code, 1)
        self.assertIn("monitor boom", err)


class TestTeamShutdown(unittest.TestCase):
    def test_missing_team_name_exits_one(self) -> None:
        with mock.patch(
            "omx.cli.team_subcommands._resolve_default_team_name",
            return_value=None,
        ):
            code, _, err = _capture(handle_team_shutdown, [])
        self.assertEqual(code, 1)
        self.assertIn("team name required", err)

    def test_shutdown_success_text(self) -> None:
        from omx.team.runtime_types import TeamShutdownSummary

        with mock.patch(
            "omx.team.runtime_shutdown.shutdown_team",
            return_value=TeamShutdownSummary(commit_hygiene_artifacts=None),
        ) as m:
            code, out, _ = _capture(handle_team_shutdown, ["my-team"])
        self.assertEqual(code, 0)
        self.assertIn("Team shutdown complete: my-team", out)
        m.assert_called_once()
        # Verify force/confirm flags are forwarded.
        args, _ = m.call_args
        self.assertEqual(args[0], "my-team")

    def test_shutdown_force_flag_forwarded(self) -> None:
        from omx.team.runtime_types import TeamShutdownSummary

        with mock.patch(
            "omx.team.runtime_shutdown.shutdown_team",
            return_value=TeamShutdownSummary(),
        ) as m:
            _capture(handle_team_shutdown, ["my-team", "--force"])
        _, kwargs = m.call_args
        # ShutdownOptions is the 3rd positional arg.
        options = m.call_args[0][2]
        self.assertTrue(options.force)

    def test_shutdown_json_envelope(self) -> None:
        from omx.team.runtime_types import TeamShutdownSummary

        with mock.patch(
            "omx.team.runtime_shutdown.shutdown_team",
            return_value=TeamShutdownSummary(),
        ):
            code, out, _ = _capture(handle_team_shutdown, ["my-team", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "omx team shutdown")

    def test_shutdown_exception_exits_one(self) -> None:
        with mock.patch(
            "omx.team.runtime_shutdown.shutdown_team",
            side_effect=RuntimeError("boom"),
        ):
            code, _, err = _capture(handle_team_shutdown, ["my-team"])
        self.assertEqual(code, 1)
        self.assertIn("boom", err)


class TestTeamResume(unittest.TestCase):
    def test_missing_team_name_exits_one(self) -> None:
        with mock.patch(
            "omx.cli.team_subcommands._resolve_default_team_name",
            return_value=None,
        ):
            code, _, err = _capture(handle_team_resume, [])
        self.assertEqual(code, 1)
        self.assertIn("team name required", err)

    def test_resume_returns_none_missing_json(self) -> None:
        with mock.patch("omx.team.runtime_resume.resume_team", return_value=None):
            code, out, _ = _capture(handle_team_resume, ["my-team", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "missing")

    def test_resume_returns_none_text(self) -> None:
        with mock.patch("omx.team.runtime_resume.resume_team", return_value=None):
            code, out, _ = _capture(handle_team_resume, ["my-team"])
        self.assertEqual(code, 0)
        self.assertIn("No resumable team found", out)

    def test_resume_success_text(self) -> None:
        from omx.team.runtime_types import TeamRuntime

        runtime = TeamRuntime(
            team_name="my-team",
            sanitized_name="my-team",
            session_name="omx-team-my-team",
            config={},
            cwd=str(Path.cwd()),
        )
        with mock.patch("omx.team.runtime_resume.resume_team", return_value=runtime):
            code, out, _ = _capture(handle_team_resume, ["my-team"])
        self.assertEqual(code, 0)
        self.assertIn("Resumed team: my-team", out)


class TestTeamScaleUp(unittest.TestCase):
    def test_missing_args(self) -> None:
        code, _, err = _capture(handle_team_scale_up, [])
        self.assertEqual(code, 1)
        self.assertIn("Usage", err)

    def test_invalid_count(self) -> None:
        code, _, err = _capture(handle_team_scale_up, ["my-team", "abc"])
        self.assertEqual(code, 1)
        self.assertIn("Invalid count", err)

    def test_scale_up_success(self) -> None:
        from omx.team.scaling import ScaleUpResult

        result = ScaleUpResult(
            added_workers=[{"name": "worker-3"}],
            new_worker_count=3,
            next_worker_index=4,
        )
        with mock.patch("omx.team.scaling.scale_up", return_value=result):
            code, out, _ = _capture(handle_team_scale_up, ["my-team", "1"])
        self.assertEqual(code, 0)
        self.assertIn("+1 workers", out)

    def test_scale_up_error_envelope(self) -> None:
        from omx.team.scaling import ScaleError

        with mock.patch(
            "omx.team.scaling.scale_up",
            return_value=ScaleError(error="max workers exceeded"),
        ):
            code, _, err = _capture(handle_team_scale_up, ["my-team", "5"])
        self.assertEqual(code, 1)
        self.assertIn("max workers exceeded", err)

    def test_scale_up_json_success(self) -> None:
        from omx.team.scaling import ScaleUpResult

        result = ScaleUpResult(
            added_workers=[{"name": "worker-2"}, {"name": "worker-3"}],
            new_worker_count=3,
            next_worker_index=4,
        )
        with mock.patch("omx.team.scaling.scale_up", return_value=result):
            code, out, _ = _capture(handle_team_scale_up, ["my-team", "2", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["new_worker_count"], 3)
        self.assertEqual(payload["added_workers"], ["worker-2", "worker-3"])

    def test_scale_up_agent_type_forwarded(self) -> None:
        from omx.team.scaling import ScaleUpResult

        with mock.patch("omx.team.scaling.scale_up") as m:
            m.return_value = ScaleUpResult(
                added_workers=[], new_worker_count=1, next_worker_index=2
            )
            _capture(
                handle_team_scale_up,
                ["my-team", "1", "--agent-type", "debugger"],
            )
        args, _ = m.call_args
        self.assertEqual(args[2], "debugger")


class TestTeamScaleDown(unittest.TestCase):
    def test_missing_team_name(self) -> None:
        code, _, err = _capture(handle_team_scale_down, [])
        self.assertEqual(code, 1)
        self.assertIn("Usage", err)

    def test_invalid_count(self) -> None:
        code, _, err = _capture(handle_team_scale_down, ["my-team", "--count=abc"])
        self.assertEqual(code, 1)
        self.assertIn("Invalid --count", err)

    def test_scale_down_success(self) -> None:
        from omx.team.scaling_down import ScaleDownResult

        with mock.patch(
            "omx.team.scaling_down.scale_down",
            return_value=ScaleDownResult(
                removed_workers=["worker-3"], new_worker_count=2
            ),
        ):
            code, out, _ = _capture(handle_team_scale_down, ["my-team"])
        self.assertEqual(code, 0)
        self.assertIn("Removed: worker-3", out)

    def test_scale_down_error(self) -> None:
        from omx.team.scaling import ScaleError

        with mock.patch(
            "omx.team.scaling_down.scale_down",
            return_value=ScaleError(error="team missing"),
        ):
            code, _, err = _capture(handle_team_scale_down, ["my-team"])
        self.assertEqual(code, 1)
        self.assertIn("team missing", err)

    def test_scale_down_worker_args_forwarded(self) -> None:
        from omx.team.scaling_down import ScaleDownResult

        with mock.patch("omx.team.scaling_down.scale_down") as m:
            m.return_value = ScaleDownResult(
                removed_workers=["worker-3"], new_worker_count=2
            )
            _capture(
                handle_team_scale_down,
                ["my-team", "--worker", "worker-3", "--force"],
            )
        args, _ = m.call_args
        options = args[2]
        self.assertEqual(options.worker_names, ["worker-3"])
        self.assertTrue(options.force)

    def test_scale_down_json_success(self) -> None:
        from omx.team.scaling_down import ScaleDownResult

        with mock.patch(
            "omx.team.scaling_down.scale_down",
            return_value=ScaleDownResult(
                removed_workers=["w1", "w2"], new_worker_count=3
            ),
        ):
            code, out, _ = _capture(
                handle_team_scale_down, ["my-team", "--count=2", "--json"]
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["removed_workers"], ["w1", "w2"])


class TestTeamReassign(unittest.TestCase):
    def test_missing_args(self) -> None:
        code, _, err = _capture(handle_team_reassign, ["my-team"])
        self.assertEqual(code, 1)
        self.assertIn("Usage", err)

    def test_reassign_success_text(self) -> None:
        with mock.patch("omx.team.runtime_assign.reassign_task") as m:
            code, out, _ = _capture(
                handle_team_reassign, ["my-team", "task-1", "worker-2"]
            )
        self.assertEqual(code, 0)
        self.assertIn("Reassigned task task-1", out)
        m.assert_called_once()

    def test_reassign_forwards_from(self) -> None:
        with mock.patch("omx.team.runtime_assign.reassign_task") as m:
            _capture(
                handle_team_reassign,
                ["my-team", "task-1", "worker-2", "--from", "worker-1"],
            )
        args, _ = m.call_args
        self.assertEqual(args[2], "worker-1")
        self.assertEqual(args[3], "worker-2")

    def test_reassign_error(self) -> None:
        with mock.patch(
            "omx.team.runtime_assign.reassign_task",
            side_effect=ValueError("Task 1 not found"),
        ):
            code, _, err = _capture(handle_team_reassign, ["my-team", "1", "worker-2"])
        self.assertEqual(code, 1)
        self.assertIn("Task 1 not found", err)

    def test_reassign_json(self) -> None:
        with mock.patch("omx.team.runtime_assign.reassign_task"):
            code, out, _ = _capture(
                handle_team_reassign,
                ["my-team", "task-1", "worker-2", "--json"],
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["task_id"], "task-1")
        self.assertTrue(payload["ok"])


class _StubOutcome:
    def __init__(self) -> None:
        self.ok = True
        self.transport = "mailbox"
        self.reason = None


class TestTeamSendMessage(unittest.TestCase):
    def test_missing_args(self) -> None:
        code, _, err = _capture(handle_team_send_message, ["my-team", "worker-1"])
        self.assertEqual(code, 1)
        self.assertIn("Usage", err)

    def test_send_message_success(self) -> None:
        with mock.patch(
            "omx.team.runtime_messaging.send_worker_message",
            return_value=_StubOutcome(),
        ) as m:
            code, out, _ = _capture(
                handle_team_send_message,
                ["my-team", "worker-1", "leader-fixed", "ACK"],
            )
        self.assertEqual(code, 0)
        self.assertIn("Sent message worker-1 -> leader-fixed", out)
        args, _ = m.call_args
        self.assertEqual(args[0], "my-team")
        self.assertEqual(args[3], "ACK")

    def test_send_message_error(self) -> None:
        with mock.patch(
            "omx.team.runtime_messaging.send_worker_message",
            side_effect=RuntimeError("mailbox_notify_failed"),
        ):
            code, _, err = _capture(
                handle_team_send_message,
                ["my-team", "worker-1", "worker-2", "hi"],
            )
        self.assertEqual(code, 1)
        self.assertIn("mailbox_notify_failed", err)

    def test_send_message_body_joining(self) -> None:
        """Multi-token body should be joined with spaces."""
        with mock.patch(
            "omx.team.runtime_messaging.send_worker_message",
            return_value=_StubOutcome(),
        ) as m:
            _capture(
                handle_team_send_message,
                ["my-team", "worker-1", "worker-2", "hello", "world"],
            )
        args, _ = m.call_args
        self.assertEqual(args[3], "hello world")

    def test_send_message_json(self) -> None:
        with mock.patch(
            "omx.team.runtime_messaging.send_worker_message",
            return_value=_StubOutcome(),
        ):
            code, out, _ = _capture(
                handle_team_send_message,
                ["my-team", "worker-1", "worker-2", "body", "--json"],
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["to_worker"], "worker-2")


class TestTeamBroadcast(unittest.TestCase):
    def test_missing_args(self) -> None:
        code, _, err = _capture(handle_team_broadcast, ["my-team"])
        self.assertEqual(code, 1)
        self.assertIn("Usage", err)

    def test_broadcast_success(self) -> None:
        with mock.patch("omx.team.runtime_messaging.broadcast_worker_message") as m:
            code, out, _ = _capture(
                handle_team_broadcast, ["my-team", "worker-1", "hello"]
            )
        self.assertEqual(code, 0)
        self.assertIn("Broadcast from worker-1", out)
        args, _ = m.call_args
        self.assertEqual(args[2], "hello")

    def test_broadcast_error(self) -> None:
        with mock.patch(
            "omx.team.runtime_messaging.broadcast_worker_message",
            side_effect=ValueError("team missing"),
        ):
            code, _, err = _capture(
                handle_team_broadcast, ["my-team", "worker-1", "hi"]
            )
        self.assertEqual(code, 1)
        self.assertIn("team missing", err)

    def test_broadcast_json(self) -> None:
        with mock.patch("omx.team.runtime_messaging.broadcast_worker_message"):
            code, out, _ = _capture(
                handle_team_broadcast,
                ["my-team", "worker-1", "hi", "--json"],
            )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["from_worker"], "worker-1")


class TestEndToEndDispatch(unittest.TestCase):
    """``omx.cli.main`` should route team sub-subcommands correctly."""

    def test_main_routes_team_status_with_json(self) -> None:
        from omx.cli import main

        with mock.patch(
            "omx.cli.team_subcommands._resolve_default_team_name",
            return_value=None,
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                main(["team", "status", "--json"])
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["status"], "missing")

    def test_main_routes_team_scale_up(self) -> None:
        from omx.cli import main
        from omx.team.scaling import ScaleUpResult

        with mock.patch(
            "omx.team.scaling.scale_up",
            return_value=ScaleUpResult(
                added_workers=[{"name": "w-2"}],
                new_worker_count=2,
                next_worker_index=3,
            ),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                main(["team", "scale-up", "my-team", "1", "--json"])
        payload = json.loads(buf.getvalue())
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["new_worker_count"], 2)


if __name__ == "__main__":
    unittest.main()
