"""Tests for ``omx.team.runtime_assign`` (Phase 2.8a port of ``assignTask`` /
``reassignTask`` from ``src/team/runtime.ts``).

Covers:

* Happy path (single dispatch attempt succeeds).
* Governance violations (``delegation_only``, ``plan_approval_required``).
* Claim failures (``blocked_dependency``, "task claimed by ...", generic).
* Dispatch retry loop:
    - first-attempt failure + second-attempt success on the same call,
    - trust-prompt dismissal between attempts,
    - sleep fallback when no trust prompt is present.
* Total dispatch failure → claim rollback + cancellation inbox + raised
  ``worker_notify_failed`` / ``worker_assignment_failed:<reason>``.
* Missing task / config / worker.
* ``reassign_task`` re-targets through ``assign_task``.
* Private helpers (``_resolve_instruction_state_root``,
  ``_resolve_worker_ready_timeout_ms``,
  ``_is_task_approved_for_execution``).

All filesystem state is rooted under a per-test tempdir; tmux and the
``mcp_comm`` dispatcher are stubbed via ``unittest.mock``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from omx.team import runtime_assign
from omx.team.contracts import TeamTask, TaskStatus
from omx.team.mcp_comm import DispatchOutcome, DispatchTransport
from omx.team.runtime_assign import (
    _is_task_approved_for_execution,
    _resolve_dispatch_policy,
    _resolve_governance_policy,
    _resolve_instruction_state_root,
    _resolve_worker_ready_timeout_ms,
    assign_task,
    reassign_task,
)
from omx.team.state.approvals import write_task_approval
from omx.team.state.io import (
    read_worker_inbox,
    write_team_config,
    write_tasks,
)
from omx.team.state.manifest import TeamManifestV2, write_team_manifest_v2
from omx.team.state.types import TaskApprovalRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ok_outcome() -> DispatchOutcome:
    return DispatchOutcome(
        ok=True,
        transport=DispatchTransport.HOOK.value,
        reason="hook_receipt_delivered",
    )


def _fail_outcome(reason: str = "not_attempted") -> DispatchOutcome:
    return DispatchOutcome(
        ok=False,
        transport=DispatchTransport.NONE.value,
        reason=reason,
    )


def _write_manifest(
    cwd: str,
    team_name: str,
    *,
    governance: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> None:
    from omx.team.state.manifest import PermissionsSnapshot, TeamLeader

    manifest = TeamManifestV2(
        name=team_name,
        task="t",
        leader=TeamLeader(),
        permissions_snapshot=PermissionsSnapshot(),
        tmux_session=f"omx-team-{team_name}",
        worker_count=1,
        workers=[],
        next_task_id=1,
        created_at="2026-01-01T00:00:00Z",
        policy=policy,
        governance=governance,
    )
    write_team_manifest_v2(manifest, cwd)


def _seed_team(
    cwd: str,
    *,
    team_name: str = "t1",
    worker_name: str = "w1",
    worker_index: int = 1,
    pane_id: str | None = "%2",
    worker_launch_mode: str = "interactive",
    tmux_session: str | None = None,
    governance: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
    task: TeamTask | None = None,
    worktree_path: str | None = None,
) -> str:
    """Seed a minimal team state on disk and return ``team_name``."""
    base = Path(cwd) / ".omx" / "team" / team_name
    base.mkdir(parents=True, exist_ok=True)
    _write_manifest(cwd, team_name, governance=governance, policy=policy)

    worker_entry: dict[str, Any] = {
        "name": worker_name,
        "index": worker_index,
        "pane_id": pane_id,
    }
    if worker_launch_mode != "interactive":
        worker_entry["worker_launch_mode"] = worker_launch_mode
    if worktree_path:
        worker_entry["worktree_path"] = worktree_path

    config: dict[str, Any] = {
        "name": team_name,
        "workers": [worker_entry],
        "worker_launch_mode": worker_launch_mode,
        "tmux_session": tmux_session
        if tmux_session is not None
        else f"omx-team-{team_name}",
    }
    write_team_config(cwd, config, team_name)

    if task is None:
        task = TeamTask(
            task_id="1",
            description="hello world",
            status=TaskStatus.PENDING,
            created_at="2026-01-01T00:00:00Z",
        )
    write_tasks(cwd, [task], team_name)
    return team_name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResolveInstructionStateRoot(unittest.TestCase):
    def test_returns_none_for_single_tree(self) -> None:
        self.assertIsNone(_resolve_instruction_state_root(None))
        self.assertIsNone(_resolve_instruction_state_root(""))

    def test_returns_placeholder_for_worktree(self) -> None:
        self.assertEqual(
            _resolve_instruction_state_root("/some/path"),
            "$OMX_TEAM_STATE_ROOT",
        )


class TestResolveWorkerReadyTimeoutMs(unittest.TestCase):
    def test_default(self) -> None:
        self.assertEqual(_resolve_worker_ready_timeout_ms({}), 45_000)

    def test_override_above_floor(self) -> None:
        self.assertEqual(
            _resolve_worker_ready_timeout_ms({"OMX_TEAM_READY_TIMEOUT_MS": "60000"}),
            60_000,
        )

    def test_below_floor_falls_back(self) -> None:
        self.assertEqual(
            _resolve_worker_ready_timeout_ms({"OMX_TEAM_READY_TIMEOUT_MS": "1000"}),
            45_000,
        )

    def test_invalid_value_falls_back(self) -> None:
        self.assertEqual(
            _resolve_worker_ready_timeout_ms({"OMX_TEAM_READY_TIMEOUT_MS": "abc"}),
            45_000,
        )


class TestResolveGovernancePolicy(unittest.TestCase):
    def test_default(self) -> None:
        gov = _resolve_governance_policy(None)
        self.assertFalse(gov.delegation_only)
        self.assertFalse(gov.plan_approval_required)
        self.assertTrue(gov.one_team_per_leader_session)

    def test_delegation_only(self) -> None:
        gov = _resolve_governance_policy({"delegation_only": True})
        self.assertTrue(gov.delegation_only)


class TestResolveDispatchPolicy(unittest.TestCase):
    def test_default_interactive(self) -> None:
        pol = _resolve_dispatch_policy(None, "interactive")
        self.assertEqual(pol.worker_launch_mode.value, "interactive")
        self.assertEqual(pol.dispatch_mode.value, "hook_preferred_with_fallback")

    def test_transport_direct(self) -> None:
        pol = _resolve_dispatch_policy(
            {"dispatch_mode": "transport_direct"}, "interactive"
        )
        self.assertEqual(pol.dispatch_mode.value, "transport_direct")

    def test_split_pane_display(self) -> None:
        pol = _resolve_dispatch_policy({"display_mode": "split_pane"}, "interactive")
        self.assertEqual(pol.display_mode.value, "split_pane")


class TestIsTaskApprovedForExecution(unittest.TestCase):
    def test_no_record_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            base = Path(cwd) / ".omx" / "team" / "t1"
            base.mkdir(parents=True, exist_ok=True)
            self.assertFalse(_is_task_approved_for_execution("t1", "1", cwd))

    def test_pending_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            base = Path(cwd) / ".omx" / "team" / "t1"
            base.mkdir(parents=True, exist_ok=True)
            write_task_approval(base, TaskApprovalRecord(task_id="1", status="pending"))
            self.assertFalse(_is_task_approved_for_execution("t1", "1", cwd))

    def test_approved_returns_true(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            base = Path(cwd) / ".omx" / "team" / "t1"
            base.mkdir(parents=True, exist_ok=True)
            write_task_approval(
                base, TaskApprovalRecord(task_id="1", status="approved")
            )
            self.assertTrue(_is_task_approved_for_execution("t1", "1", cwd))


class TestAssignTaskHappyPath(unittest.TestCase):
    def test_single_attempt_success_writes_inbox_and_keeps_claim(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with patch.object(
                runtime_assign,
                "_dispatch_critical_inbox_instruction",
                return_value=_ok_outcome(),
            ) as dispatch:
                assign_task(team, "w1", "1", cwd)
            self.assertEqual(dispatch.call_count, 1)
            # The dispatched inbox content is written by
            # ``queue_inbox_instruction`` inside the real dispatcher; we
            # stub the dispatcher, so verify the call arguments instead.
            call_kwargs = dispatch.call_args.kwargs
            self.assertEqual(call_kwargs["team_name"], team)
            self.assertEqual(call_kwargs["worker_name"], "w1")
            self.assertEqual(call_kwargs["worker_index"], 1)
            self.assertEqual(call_kwargs["pane_id"], "%2")
            self.assertEqual(call_kwargs["inbox_correlation_key"], "assign:1:w1")
            self.assertIn("hello world", call_kwargs["inbox"])
            # Task was claimed (status moved to in_progress).
            from omx.team.state.io import read_tasks

            tasks = read_tasks(cwd, team)
            self.assertEqual(tasks[0].status, TaskStatus.IN_PROGRESS)
            self.assertEqual(tasks[0].owner, "w1")

    def test_sanitizes_team_name(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            _seed_team(cwd, team_name="t1")
            with patch.object(
                runtime_assign,
                "_dispatch_critical_inbox_instruction",
                return_value=_ok_outcome(),
            ):
                # Pass an unsanitized name; the seed used the sanitized form.
                assign_task("T1!", "w1", "1", cwd)


class TestAssignTaskGovernance(unittest.TestCase):
    def test_delegation_only_rejects_leader(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(
                cwd,
                worker_name="leader-fixed",
                governance={"delegation_only": True},
            )
            with self.assertRaises(ValueError) as ctx:
                assign_task(team, "leader-fixed", "1", cwd)
            self.assertEqual(str(ctx.exception), "delegation_only_violation")

    def test_delegation_only_allows_non_leader(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd, governance={"delegation_only": True})
            with patch.object(
                runtime_assign,
                "_dispatch_critical_inbox_instruction",
                return_value=_ok_outcome(),
            ):
                assign_task(team, "w1", "1", cwd)

    def test_plan_approval_required_blocks_when_unapproved(self) -> None:
        # ``requires_code_change`` is not a persisted ``TeamTask`` field
        # yet, so the gate uses dynamic attribute lookup. Stub
        # ``team_read_task`` so the returned object carries the attribute.
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd, governance={"plan_approval_required": True})
            stub = TeamTask(task_id="1", description="risky")
            stub.requires_code_change = True  # type: ignore[attr-defined]
            with patch.object(runtime_assign, "team_read_task", return_value=stub):
                with self.assertRaises(ValueError) as ctx:
                    assign_task(team, "w1", "1", cwd)
            self.assertEqual(str(ctx.exception), "plan_approval_required")

    def test_plan_approval_required_allows_when_approved(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd, governance={"plan_approval_required": True})
            stub = TeamTask(task_id="1", description="risky")
            stub.requires_code_change = True  # type: ignore[attr-defined]
            write_task_approval(
                Path(cwd) / ".omx" / "team" / team,
                TaskApprovalRecord(task_id="1", status="approved"),
            )
            with (
                patch.object(runtime_assign, "team_read_task", return_value=stub),
                patch.object(
                    runtime_assign,
                    "_dispatch_critical_inbox_instruction",
                    return_value=_ok_outcome(),
                ),
            ):
                assign_task(team, "w1", "1", cwd)

    def test_plan_approval_skipped_when_not_a_code_change(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            # task.requires_code_change unset → gate doesn't fire even with
            # plan_approval_required True.
            team = _seed_team(cwd, governance={"plan_approval_required": True})
            with patch.object(
                runtime_assign,
                "_dispatch_critical_inbox_instruction",
                return_value=_ok_outcome(),
            ):
                assign_task(team, "w1", "1", cwd)


class TestAssignTaskMissing(unittest.TestCase):
    def test_missing_task_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with self.assertRaises(ValueError) as ctx:
                assign_task(team, "w1", "does-not-exist", cwd)
            self.assertIn("not found", str(ctx.exception))

    def test_missing_team_config_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            # Seed only the task (no config). team_read_config will return
            # {} so we exercise the explicit "Team not found" branch.
            base = Path(cwd) / ".omx" / "team" / "t1"
            base.mkdir(parents=True, exist_ok=True)
            _write_manifest(cwd, "t1")
            task = TeamTask(task_id="1", description="x")
            write_tasks(cwd, [task], "t1")
            with self.assertRaises(ValueError) as ctx:
                assign_task("t1", "w1", "1", cwd)
            self.assertEqual(str(ctx.exception), "Team t1 not found")

    def test_unknown_worker_raises(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with self.assertRaises(ValueError) as ctx:
                assign_task(team, "ghost", "1", cwd)
            self.assertEqual(str(ctx.exception), "Worker ghost not found in team")


class TestAssignTaskClaimFailures(unittest.TestCase):
    def test_claim_failure_blocked_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            fake = {
                "ok": False,
                "error": "blocked_dependency",
                "dependencies": ["t2", "t3"],
            }
            with patch.object(runtime_assign, "team_claim_task", return_value=fake):
                with self.assertRaises(ValueError) as ctx:
                    assign_task(team, "w1", "1", cwd)
            self.assertEqual(str(ctx.exception), "blocked_dependency:t2,t3")

    def test_claim_failure_other_error_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            fake = {"ok": False, "error": "task claimed by other-worker"}
            with patch.object(runtime_assign, "team_claim_task", return_value=fake):
                with self.assertRaises(ValueError) as ctx:
                    assign_task(team, "w1", "1", cwd)
            self.assertEqual(str(ctx.exception), "task claimed by other-worker")

    def test_claim_failure_terminal_task(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            task = TeamTask(task_id="1", description="x", status=TaskStatus.COMPLETED)
            team = _seed_team(cwd, task=task)
            with self.assertRaises(ValueError) as ctx:
                assign_task(team, "w1", "1", cwd)
            self.assertIn("terminal", str(ctx.exception))


class TestAssignTaskDispatchRetries(unittest.TestCase):
    def test_dispatch_retry_succeeds_on_second_attempt_via_trust_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)

            outcomes = [_fail_outcome("not_attempted"), _ok_outcome()]

            def _dispatch(**_kwargs: Any) -> DispatchOutcome:
                return outcomes.pop(0)

            with (
                patch.object(
                    runtime_assign,
                    "_dispatch_critical_inbox_instruction",
                    side_effect=_dispatch,
                ) as dispatch,
                patch.object(
                    runtime_assign,
                    "dismiss_trust_prompt_if_present",
                    return_value=True,
                ) as dismiss,
                patch.object(
                    runtime_assign, "wait_for_worker_ready", return_value=True
                ) as wait_ready,
                patch.object(runtime_assign.time, "sleep") as sleep,
            ):
                assign_task(team, "w1", "1", cwd)

            self.assertEqual(dispatch.call_count, 2)
            self.assertEqual(dismiss.call_count, 1)
            self.assertEqual(wait_ready.call_count, 1)
            sleep.assert_not_called()

    def test_dispatch_retry_falls_back_to_sleep_when_no_trust_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            outcomes = [_fail_outcome("not_attempted"), _ok_outcome()]

            def _dispatch(**_kwargs: Any) -> DispatchOutcome:
                return outcomes.pop(0)

            with (
                patch.object(
                    runtime_assign,
                    "_dispatch_critical_inbox_instruction",
                    side_effect=_dispatch,
                ),
                patch.object(
                    runtime_assign,
                    "dismiss_trust_prompt_if_present",
                    return_value=False,
                ),
                patch.object(runtime_assign.time, "sleep") as sleep,
            ):
                assign_task(team, "w1", "1", cwd)
            sleep.assert_called_once_with(2)

    def test_total_dispatch_failure_releases_claim_and_writes_cancellation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)

            with (
                patch.object(
                    runtime_assign,
                    "_dispatch_critical_inbox_instruction",
                    return_value=_fail_outcome("notify_exception:boom"),
                ),
                patch.object(
                    runtime_assign,
                    "dismiss_trust_prompt_if_present",
                    return_value=False,
                ),
                patch.object(runtime_assign.time, "sleep"),
            ):
                with self.assertRaises(ValueError) as ctx:
                    assign_task(team, "w1", "1", cwd)
            # TS raises "worker_notify_failed" inside the try, which gets
            # caught + re-raised verbatim because reason ==
            # "worker_notify_failed".
            self.assertEqual(str(ctx.exception), "worker_notify_failed")

            # Inbox now has the cancellation marker so the worker does not
            # action a stale prior inbox.
            inbox = read_worker_inbox(cwd, team, "w1") or ""
            self.assertIn("Assignment Cancelled", inbox)
            self.assertIn("worker_notify_failed", inbox)

            # Task was rolled back to pending.
            from omx.team.state.io import read_tasks

            tasks = read_tasks(cwd, team)
            self.assertEqual(tasks[0].status, TaskStatus.PENDING)
            self.assertIsNone(tasks[0].owner)

    def test_dispatch_failure_wraps_unknown_reason(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)

            def _dispatch(**_kwargs: Any) -> DispatchOutcome:
                # Raise a custom error inside the dispatcher so the catch
                # arm sees a non-empty reason that isn't
                # ``worker_notify_failed``.
                raise RuntimeError("dispatch_layer_explosion")

            with patch.object(
                runtime_assign,
                "_dispatch_critical_inbox_instruction",
                side_effect=_dispatch,
            ):
                with self.assertRaises(ValueError) as ctx:
                    assign_task(team, "w1", "1", cwd)
            self.assertEqual(
                str(ctx.exception),
                "worker_assignment_failed:dispatch_layer_explosion",
            )

    def test_dispatch_failure_release_failure_appends_release_error(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)

            with (
                patch.object(
                    runtime_assign,
                    "_dispatch_critical_inbox_instruction",
                    return_value=_fail_outcome("notify_exception:boom"),
                ),
                patch.object(
                    runtime_assign,
                    "dismiss_trust_prompt_if_present",
                    return_value=False,
                ),
                patch.object(runtime_assign.time, "sleep"),
                patch.object(
                    runtime_assign,
                    "team_release_task_claim",
                    return_value={"ok": False, "error": "claim_token_mismatch"},
                ),
            ):
                with self.assertRaises(ValueError) as ctx:
                    assign_task(team, "w1", "1", cwd)
            self.assertEqual(
                str(ctx.exception),
                "worker_notify_failed:claim_token_mismatch",
            )

    def test_no_retry_loop_when_not_interactive(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd, worker_launch_mode="prompt")

            with (
                patch.object(
                    runtime_assign,
                    "_dispatch_critical_inbox_instruction",
                    return_value=_fail_outcome("notify_exception:boom"),
                ) as dispatch,
                patch.object(
                    runtime_assign, "dismiss_trust_prompt_if_present"
                ) as dismiss,
                patch.object(runtime_assign.time, "sleep") as sleep,
            ):
                with self.assertRaises(ValueError):
                    assign_task(team, "w1", "1", cwd)
            # In prompt mode the inter-attempt trust-prompt branch is
            # skipped, but the loop still tries _MAX_ASSIGN_RETRIES times.
            self.assertEqual(dispatch.call_count, 2)
            dismiss.assert_not_called()
            sleep.assert_not_called()

    def test_no_retry_loop_when_no_tmux_session(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd, tmux_session="")

            with (
                patch.object(
                    runtime_assign,
                    "_dispatch_critical_inbox_instruction",
                    return_value=_fail_outcome("notify_exception:boom"),
                ) as dispatch,
                patch.object(
                    runtime_assign, "dismiss_trust_prompt_if_present"
                ) as dismiss,
                patch.object(runtime_assign.time, "sleep") as sleep,
            ):
                with self.assertRaises(ValueError):
                    assign_task(team, "w1", "1", cwd)
            self.assertEqual(dispatch.call_count, 2)
            dismiss.assert_not_called()
            sleep.assert_not_called()


class TestAssignTaskWorktreeTriggerRoot(unittest.TestCase):
    def test_worktree_worker_uses_state_root_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd, worktree_path="/wt/path")
            with patch.object(
                runtime_assign,
                "_dispatch_critical_inbox_instruction",
                return_value=_ok_outcome(),
            ) as dispatch:
                assign_task(team, "w1", "1", cwd)
            trigger_text = dispatch.call_args.kwargs["trigger_message"]
            # The non-default state-root path is embedded in the trigger.
            self.assertIn("$OMX_TEAM_STATE_ROOT", trigger_text)

    def test_non_worktree_worker_uses_default_state_root(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd, worktree_path=None)
            with patch.object(
                runtime_assign,
                "_dispatch_critical_inbox_instruction",
                return_value=_ok_outcome(),
            ) as dispatch:
                assign_task(team, "w1", "1", cwd)
            trigger_text = dispatch.call_args.kwargs["trigger_message"]
            self.assertIn(".omx/state", trigger_text)


class TestReassignTask(unittest.TestCase):
    def test_reassign_delegates_to_assign(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with patch.object(runtime_assign, "assign_task") as assign:
                reassign_task(team, "1", "from-worker", "w1", cwd)
            assign.assert_called_once_with(team, "w1", "1", cwd)

    def test_reassign_end_to_end_invokes_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as cwd:
            team = _seed_team(cwd)
            with patch.object(
                runtime_assign,
                "_dispatch_critical_inbox_instruction",
                return_value=_ok_outcome(),
            ) as dispatch:
                reassign_task(team, "1", "old-worker", "w1", cwd)
            self.assertEqual(dispatch.call_count, 1)


if __name__ == "__main__":  # pragma: no cover - manual harness
    unittest.main()
