"""Tests for ``omx.team.scaling``.

Covers the new TS-parity surface:

* :func:`is_scaling_enabled` / :func:`assert_scaling_enabled` env gate.
* :func:`scale_up` happy paths (1/2/3 workers, with and without
  worktrees), validation failures, capacity guard, rollback paths.
* :func:`_resolve_legacy_scaled_team_worktree_mode` /
  :func:`_resolve_scale_up_worktree_mode` legacy contract reconstruction.

The existing Python-only heuristics (:func:`evaluate_scaling`,
:func:`resolve_max_workers`) are exercised in the
``TestExistingHeuristics`` block to preserve those rows in PARITY.md.

All tests stub tmux, mcp dispatch, worktree provisioning, and
``team_with_scaling_lock`` so they run hermetically without a tmux
server, git repo, or real CLI binary.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from typing import Any
from unittest import mock

import omx.team.scaling as scaling_mod
from omx.team.contracts import TaskStatus, TeamTask
from omx.team.mcp_comm import DispatchOutcome, DispatchTransport
from omx.team.scaling import (
    OMX_TEAM_SCALING_ENABLED_ENV,
    ScaleError,
    ScaleUpResult,
    _resolve_legacy_scaled_team_worktree_mode,
    _resolve_scale_up_worktree_mode,
    _resolve_worker_launch_args_for_scaling,
    _resolve_worker_ready_timeout_ms,
    assert_scaling_enabled,
    evaluate_scaling,
    is_scaling_enabled,
    resolve_max_workers,
    scale_up,
)
from omx.team.worktree import (
    EnsureWorktreeResult,
    WorktreeDisabled,
)


# ---------------------------------------------------------------------------
# Existing heuristic helpers (preserved from the pre-port skeleton)
# ---------------------------------------------------------------------------


class TestExistingHeuristics(unittest.TestCase):
    def test_evaluate_scaling_no_change_balanced(self) -> None:
        d = evaluate_scaling(2, 0, 1, 0)
        self.assertEqual(d.action, "no_change")
        self.assertEqual(d.target_count, 2)

    def test_evaluate_scaling_dead_workers(self) -> None:
        d = evaluate_scaling(3, 0, 0, 1)
        self.assertEqual(d.action, "scale_up")
        self.assertEqual(d.target_count, 3)

    def test_evaluate_scaling_up_pending(self) -> None:
        d = evaluate_scaling(2, 4, 0, 0, max_workers=6)
        self.assertEqual(d.action, "scale_up")
        # Bounded by max_workers, max-at-a-time 3, and pending count
        self.assertEqual(d.target_count, 5)

    def test_evaluate_scaling_down_idle(self) -> None:
        d = evaluate_scaling(4, 0, 3, 0)
        self.assertEqual(d.action, "scale_down")
        self.assertEqual(d.target_count, 2)

    def test_resolve_max_workers_default(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OMX_TEAM_MAX_WORKERS", None)
            self.assertGreaterEqual(resolve_max_workers(), 1)

    def test_resolve_max_workers_env_clamped(self) -> None:
        with mock.patch.dict(os.environ, {"OMX_TEAM_MAX_WORKERS": "9999"}):
            self.assertEqual(
                resolve_max_workers(),
                # ABSOLUTE_MAX_WORKERS — read indirectly to avoid hard-coding.
                scaling_mod.ABSOLUTE_MAX_WORKERS,
            )

    def test_resolve_max_workers_env_invalid(self) -> None:
        with mock.patch.dict(os.environ, {"OMX_TEAM_MAX_WORKERS": "not-a-number"}):
            self.assertGreaterEqual(resolve_max_workers(), 1)


# ---------------------------------------------------------------------------
# Env gate — is_scaling_enabled / assert_scaling_enabled
# ---------------------------------------------------------------------------


class TestScalingEnvGate(unittest.TestCase):
    def test_disabled_by_default(self) -> None:
        self.assertFalse(is_scaling_enabled({}))

    def test_enabled_truthy_values(self) -> None:
        for v in ("1", "true", "TRUE", "yes", "Yes", "on", "enabled", " 1 ", "ON"):
            with self.subTest(value=v):
                self.assertTrue(is_scaling_enabled({OMX_TEAM_SCALING_ENABLED_ENV: v}))

    def test_disabled_other_values(self) -> None:
        for v in ("0", "false", "no", "off", "disabled", "", "  "):
            with self.subTest(value=v):
                self.assertFalse(is_scaling_enabled({OMX_TEAM_SCALING_ENABLED_ENV: v}))

    def test_assert_disabled_raises(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            assert_scaling_enabled({})
        self.assertIn("OMX_TEAM_SCALING_ENABLED", str(ctx.exception))

    def test_assert_enabled_returns_none(self) -> None:
        self.assertIsNone(assert_scaling_enabled({OMX_TEAM_SCALING_ENABLED_ENV: "1"}))

    def test_uses_os_environ_when_none(self) -> None:
        with mock.patch.dict(os.environ, {OMX_TEAM_SCALING_ENABLED_ENV: "1"}):
            self.assertTrue(is_scaling_enabled(None))


# ---------------------------------------------------------------------------
# Legacy worktree-mode resolution
# ---------------------------------------------------------------------------


class TestResolveLegacyScaledTeamWorktreeMode(unittest.TestCase):
    def test_returns_existing_mode(self) -> None:
        config = {
            "name": "alpha",
            "worktree_mode": {"enabled": True, "detached": False, "name": "feature"},
            "workspace_mode": "worktree",
            "workers": [],
        }
        mode = _resolve_legacy_scaled_team_worktree_mode(config)
        self.assertTrue(mode.enabled)
        self.assertFalse(mode.detached)
        self.assertEqual(mode.name, "feature")

    def test_single_workspace_returns_disabled(self) -> None:
        config = {"name": "alpha", "workspace_mode": "single", "workers": []}
        mode = _resolve_legacy_scaled_team_worktree_mode(config)
        self.assertFalse(mode.enabled)

    def test_missing_worker_metadata_raises(self) -> None:
        config = {
            "name": "alpha",
            "workspace_mode": "worktree",
            "workers": [{"name": "worker-1", "pane_id": "%w1"}],
        }
        with self.assertRaises(RuntimeError) as ctx:
            _resolve_legacy_scaled_team_worktree_mode(config)
        self.assertIn(
            "scale_up_missing_team_worktree_contract:alpha", str(ctx.exception)
        )

    def test_detached_workers_infers_detached(self) -> None:
        config = {
            "name": "alpha",
            "workspace_mode": "worktree",
            "workers": [
                {"name": "worker-1", "worktree_path": "/x", "worktree_detached": True}
            ],
        }
        mode = _resolve_legacy_scaled_team_worktree_mode(config)
        self.assertTrue(mode.enabled)
        self.assertTrue(mode.detached)
        self.assertIsNone(mode.name)

    def test_consistent_branch_prefix_infers_named(self) -> None:
        config = {
            "name": "alpha",
            "workspace_mode": "worktree",
            "workers": [
                {"name": "worker-1", "worktree_branch": "feature/worker-1"},
                {"name": "worker-2", "worktree_branch": "feature/worker-2"},
            ],
        }
        mode = _resolve_legacy_scaled_team_worktree_mode(config)
        self.assertTrue(mode.enabled)
        self.assertFalse(mode.detached)
        self.assertEqual(mode.name, "feature")

    def test_mixed_branch_prefixes_raises(self) -> None:
        config = {
            "name": "alpha",
            "workspace_mode": "worktree",
            "workers": [
                {"name": "worker-1", "worktree_branch": "feat-a/worker-1"},
                {"name": "worker-2", "worktree_branch": "feat-b/worker-2"},
            ],
        }
        with self.assertRaises(RuntimeError):
            _resolve_legacy_scaled_team_worktree_mode(config)

    def test_resolve_scale_up_disabled_for_single_workspace(self) -> None:
        config = {"name": "alpha", "workspace_mode": "single", "workers": []}
        mode = _resolve_scale_up_worktree_mode(config)
        self.assertFalse(mode.enabled)

    def test_resolve_scale_up_falls_back_to_detached(self) -> None:
        # workspace_mode=worktree but no worker metadata → contract error,
        # which the public wrapper rescues as detached mode.
        config = {"name": "alpha", "workspace_mode": "worktree", "workers": []}
        mode = _resolve_scale_up_worktree_mode(config)
        self.assertTrue(mode.enabled)
        self.assertTrue(mode.detached)


# ---------------------------------------------------------------------------
# Env-knob helpers
# ---------------------------------------------------------------------------


class TestEnvKnobs(unittest.TestCase):
    def test_ready_timeout_default(self) -> None:
        self.assertEqual(_resolve_worker_ready_timeout_ms({}), 45_000)

    def test_ready_timeout_invalid(self) -> None:
        self.assertEqual(
            _resolve_worker_ready_timeout_ms({"OMX_TEAM_READY_TIMEOUT_MS": "x"}),
            45_000,
        )

    def test_ready_timeout_below_floor(self) -> None:
        self.assertEqual(
            _resolve_worker_ready_timeout_ms({"OMX_TEAM_READY_TIMEOUT_MS": "1000"}),
            45_000,
        )

    def test_ready_timeout_explicit(self) -> None:
        self.assertEqual(
            _resolve_worker_ready_timeout_ms({"OMX_TEAM_READY_TIMEOUT_MS": "30000"}),
            30_000,
        )

    def test_launch_args_for_scaling_returns_list(self) -> None:
        args = _resolve_worker_launch_args_for_scaling({}, "executor", None)
        self.assertIsInstance(args, list)


# ---------------------------------------------------------------------------
# scale_up — input validation
# ---------------------------------------------------------------------------


class _FakeLock:
    """Re-entrant context manager that records use."""

    def __init__(self) -> None:
        self.enters = 0
        self.exits = 0

    def __enter__(self):
        self.enters += 1
        return self

    def __exit__(self, *a):
        self.exits += 1
        return False


@contextmanager
def _scaling_enabled_env():
    saved = os.environ.get(OMX_TEAM_SCALING_ENABLED_ENV)
    os.environ[OMX_TEAM_SCALING_ENABLED_ENV] = "1"
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop(OMX_TEAM_SCALING_ENABLED_ENV, None)
        else:
            os.environ[OMX_TEAM_SCALING_ENABLED_ENV] = saved


class TestScaleUpValidation(unittest.TestCase):
    def test_raises_when_scaling_disabled(self) -> None:
        # No env set → disabled.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(OMX_TEAM_SCALING_ENABLED_ENV, None)
            with self.assertRaises(RuntimeError):
                scale_up("alpha", 1, "executor", [], "/tmp/x")

    def test_rejects_zero_count(self) -> None:
        with _scaling_enabled_env():
            result = scale_up("alpha", 0, "executor", [], "/tmp/x")
            self.assertIsInstance(result, ScaleError)
            self.assertIn("positive integer", result.error)

    def test_rejects_negative_count(self) -> None:
        with _scaling_enabled_env():
            result = scale_up("alpha", -2, "executor", [], "/tmp/x")
            self.assertIsInstance(result, ScaleError)

    def test_rejects_non_int_count(self) -> None:
        with _scaling_enabled_env():
            # bool subclasses int in Python — TS rejects via Number.isInteger;
            # we mirror by guarding against bool/non-int.
            result = scale_up("alpha", True, "executor", [], "/tmp/x")
            self.assertIsInstance(result, ScaleError)

    def test_returns_error_when_tmux_missing(self) -> None:
        with (
            _scaling_enabled_env(),
            mock.patch.object(scaling_mod, "is_tmux_available", return_value=False),
        ):
            result = scale_up("alpha", 1, "executor", [], "/tmp/x")
            self.assertIsInstance(result, ScaleError)
            self.assertIn("tmux is not available", result.error)


# ---------------------------------------------------------------------------
# scale_up — full happy / rollback flow with mocks
# ---------------------------------------------------------------------------


def _ok_dispatch() -> DispatchOutcome:
    return DispatchOutcome(
        ok=True,
        transport=DispatchTransport.HOOK.value,
        reason="queued_for_hook_dispatch",
        request_id="req-1",
    )


def _fail_dispatch(reason: str = "boom") -> DispatchOutcome:
    return DispatchOutcome(
        ok=False,
        transport=DispatchTransport.NONE.value,
        reason=reason,
    )


def _make_split_result(pane_id: str = "%new1", returncode: int = 0) -> Any:
    """Return a subprocess.CompletedProcess-shaped object."""
    proc = mock.Mock()
    proc.returncode = returncode
    proc.stdout = pane_id if returncode == 0 else ""
    proc.stderr = "" if returncode == 0 else "split failed"
    return proc


def _base_config(
    *,
    workers: list[dict[str, Any]] | None = None,
    max_workers: int = 6,
    workspace_mode: str = "single",
    worktree_mode: dict[str, Any] | None = None,
    next_worker_index: int | None = None,
) -> dict[str, Any]:
    return {
        "name": "alpha",
        "tmux_session": "omx-team-alpha:0",
        "leader_pane_id": "%leader",
        "hud_pane_id": "%hud",
        "max_workers": max_workers,
        "worker_count": len(workers or []),
        "workers": workers or [],
        "workspace_mode": workspace_mode,
        "worktree_mode": worktree_mode,
        "next_worker_index": next_worker_index,
        "worker_launch_mode": "interactive",
        "team_state_root": None,
    }


def _make_task(
    task_id: str, owner: str | None = None, role: str | None = None
) -> TeamTask:
    return TeamTask(
        task_id=task_id,
        description=f"task-{task_id}",
        role=role,
        status=TaskStatus("pending"),
        owner=owner,
    )


def _patch_scale_up_runtime(
    *,
    config: dict[str, Any],
    dispatch_outcomes: list[DispatchOutcome] | None = None,
    split_outcomes: list[Any] | None = None,
    persisted_tasks: list[TeamTask] | None = None,
    worker_cli_plan: list[str] | None = None,
    enable_worktree: bool = False,
    worktree_results: list[Any] | None = None,
    receipt: Any | None = None,
):
    """Stack of patches for a hermetic scale_up exercise."""

    saved_config = dict(config)
    saved_workers = list(config.get("workers") or [])

    def _read_config(_cwd: str, _name: str):
        # Return a fresh dict each time so the function's mutations
        # don't leak back into the caller's reference.
        result = {**saved_config}
        result["workers"] = list(saved_workers)
        return result

    saves: list[dict[str, Any]] = []

    def _save_config(
        cwd: str, cfg: dict[str, Any], team_name: str | None = None
    ) -> None:
        # Copy because the implementation re-uses the dict.
        cfg_copy = {**cfg, "workers": [dict(w) for w in cfg.get("workers", [])]}
        saves.append(cfg_copy)

    dispatch_outcomes = dispatch_outcomes if dispatch_outcomes else []

    def _queue_dispatch(_params):
        if dispatch_outcomes:
            return dispatch_outcomes.pop(0)
        return _ok_dispatch()

    split_outcomes = split_outcomes if split_outcomes else []

    def _subprocess_run(args, **_kwargs):
        # First positional arg is the command vec.
        if len(args) > 1 and args[0] == "tmux" and args[1] == "split-window":
            if split_outcomes:
                return split_outcomes.pop(0)
            return _make_split_result(pane_id=f"%new{len(split_outcomes) + 1}")
        # All other subprocess calls (e.g. kill-pane) → ok no-op
        proc = mock.Mock()
        proc.returncode = 0
        proc.stdout = ""
        proc.stderr = ""
        return proc

    persisted_tasks = persisted_tasks if persisted_tasks else []
    cli_plan = worker_cli_plan if worker_cli_plan else None

    created_tasks: list[mock.Mock] = []

    def _create_task(*_args, **_kwargs):
        task = mock.Mock()
        task.task_id = f"t{len(created_tasks) + 1}"
        created_tasks.append(task)
        return task

    worktree_results = worktree_results if worktree_results else []

    def _ensure_worktree(_plan, _options=None):
        if worktree_results:
            return worktree_results.pop(0)
        return WorktreeDisabled()

    fake_manifest = mock.Mock()
    fake_manifest.policy = {
        "display_mode": "split_pane",
        "worker_launch_mode": "interactive",
        "dispatch_mode": "hook_preferred_with_fallback",
        "dispatch_ack_timeout_ms": 1_000,
    }

    fake_lock = _FakeLock()

    patches = [
        mock.patch.object(scaling_mod, "is_tmux_available", return_value=True),
        mock.patch.object(
            scaling_mod, "team_with_scaling_lock", return_value=fake_lock
        ),
        mock.patch.object(scaling_mod, "team_read_config", side_effect=_read_config),
        mock.patch.object(scaling_mod, "team_save_config", side_effect=_save_config),
        mock.patch.object(
            scaling_mod, "team_read_manifest", return_value=fake_manifest
        ),
        mock.patch.object(scaling_mod, "team_create_task", side_effect=_create_task),
        mock.patch.object(scaling_mod, "team_list_tasks", return_value=persisted_tasks),
        mock.patch.object(scaling_mod, "team_write_worker_identity"),
        mock.patch.object(scaling_mod, "team_append_event"),
        mock.patch.object(
            scaling_mod, "subprocess", new=mock.Mock(run=_subprocess_run)
        ),
        mock.patch.object(
            scaling_mod,
            "resolve_team_worker_cli_plan",
            return_value=cli_plan if cli_plan else ["codex"] * 4,
        ),
        mock.patch.object(
            scaling_mod, "build_worker_startup_command", return_value="echo worker"
        ),
        mock.patch.object(scaling_mod, "get_worker_pane_pid", return_value=42_000),
        mock.patch.object(scaling_mod, "wait_for_worker_ready", return_value=True),
        mock.patch.object(
            scaling_mod, "dismiss_trust_prompt_if_present", return_value=False
        ),
        mock.patch.object(scaling_mod, "send_to_worker", return_value=True),
        mock.patch.object(
            scaling_mod, "queue_inbox_instruction", side_effect=_queue_dispatch
        ),
        mock.patch.object(
            scaling_mod, "wait_for_dispatch_receipt", return_value=receipt
        ),
        mock.patch.object(scaling_mod, "generate_initial_inbox", return_value="inbox"),
        mock.patch.object(
            scaling_mod,
            "build_trigger_directive",
            return_value=mock.Mock(text="GO", intent=None),
        ),
        mock.patch.object(scaling_mod, "ensure_worktree", side_effect=_ensure_worktree),
        mock.patch.object(
            scaling_mod, "plan_worktree_target", return_value=mock.Mock()
        ),
        mock.patch.object(
            scaling_mod,
            "write_worker_worktree_root_agents_file",
            return_value="/tmp/AGENTS.md",
        ),
        mock.patch.object(scaling_mod, "remove_worker_worktree_root_agents_file"),
        mock.patch.object(
            scaling_mod,
            "write_worker_role_instructions_file",
            return_value="/tmp/role-instructions.md",
        ),
        mock.patch.object(scaling_mod, "load_role_prompt", return_value=None),
        mock.patch.object(
            scaling_mod, "compose_role_instructions_for_role", return_value=""
        ),
    ]

    class _Ctx:
        def __enter__(self):
            for p in patches:
                p.start()
            return {
                "config_saves": saves,
                "created_tasks": created_tasks,
                "lock": fake_lock,
            }

        def __exit__(self, *a):
            for p in reversed(patches):
                p.stop()
            return False

    return _Ctx()


class TestScaleUpCapacity(unittest.TestCase):
    def test_capacity_exceeded_returns_error(self) -> None:
        cfg = _base_config(
            workers=[
                {"name": f"worker-{i}", "index": i, "pane_id": f"%w{i}"}
                for i in range(1, 6)
            ],
            max_workers=6,
            next_worker_index=6,
        )
        with _scaling_enabled_env(), _patch_scale_up_runtime(config=cfg):
            result = scale_up("alpha", 2, "executor", [], "/tmp/x")
            self.assertIsInstance(result, ScaleError)
            self.assertIn("max_workers", result.error)

    def test_unknown_team_returns_error(self) -> None:
        # team_read_config returns an empty dict for a missing team; scale_up
        # must detect that and short-circuit before touching tmux.
        with _scaling_enabled_env(), _patch_scale_up_runtime(config={}):
            # Override the runtime patcher's team_read_config to return {}
            # so the "missing team" branch fires.
            with mock.patch.object(scaling_mod, "team_read_config", return_value={}):
                result = scale_up("missing", 1, "executor", [], "/tmp/x")
                self.assertIsInstance(result, ScaleError)
                self.assertIn("not found", result.error)


class TestScaleUpHappyPath(unittest.TestCase):
    def setUp(self) -> None:
        self.cwd = tempfile.mkdtemp(prefix="omx-scale-")
        self.cfg = _base_config(
            workers=[
                {
                    "name": "worker-1",
                    "index": 1,
                    "pane_id": "%w1",
                    "worker_cli": "codex",
                }
            ],
            max_workers=6,
            next_worker_index=2,
        )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.cwd, ignore_errors=True)

    def test_adds_single_worker(self) -> None:
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[_make_split_result(pane_id="%w2")],
            ) as ctx,
        ):
            result = scale_up("alpha", 1, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleUpResult)
            self.assertEqual(len(result.added_workers), 1)
            self.assertEqual(result.added_workers[0]["name"], "worker-2")
            self.assertEqual(result.added_workers[0]["index"], 2)
            self.assertEqual(result.added_workers[0]["pane_id"], "%w2")
            self.assertEqual(result.new_worker_count, 2)
            self.assertEqual(result.next_worker_index, 3)
            # config persisted with new worker
            self.assertGreater(len(ctx["config_saves"]), 0)
            # lock used
            self.assertEqual(ctx["lock"].enters, 1)
            self.assertEqual(ctx["lock"].exits, 1)

    def test_adds_two_workers(self) -> None:
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[
                    _make_split_result(pane_id="%w2"),
                    _make_split_result(pane_id="%w3"),
                ],
            ),
        ):
            result = scale_up("alpha", 2, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleUpResult)
            self.assertEqual(len(result.added_workers), 2)
            self.assertEqual(
                [w["name"] for w in result.added_workers],
                ["worker-2", "worker-3"],
            )
            self.assertEqual(result.next_worker_index, 4)

    def test_adds_three_workers(self) -> None:
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[
                    _make_split_result(pane_id="%w2"),
                    _make_split_result(pane_id="%w3"),
                    _make_split_result(pane_id="%w4"),
                ],
            ),
        ):
            result = scale_up("alpha", 3, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleUpResult)
            self.assertEqual(len(result.added_workers), 3)
            self.assertEqual(result.new_worker_count, 4)
            self.assertEqual(result.next_worker_index, 5)

    def test_persists_incoming_tasks_first(self) -> None:
        tasks = [
            {"subject": "Subject A", "description": "Do A", "owner": "worker-2"},
            {"subject": "Subject B", "description": "Do B"},
        ]
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[_make_split_result(pane_id="%w2")],
            ) as ctx,
        ):
            result = scale_up("alpha", 1, "executor", tasks, self.cwd)
            self.assertIsInstance(result, ScaleUpResult)
            # Both incoming tasks should have been persisted before bootstrap.
            self.assertEqual(len(ctx["created_tasks"]), 2)


class TestScaleUpRollback(unittest.TestCase):
    def setUp(self) -> None:
        self.cwd = tempfile.mkdtemp(prefix="omx-scale-rb-")
        self.cfg = _base_config(
            workers=[{"name": "worker-1", "index": 1, "pane_id": "%w1"}],
            max_workers=6,
            next_worker_index=2,
        )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.cwd, ignore_errors=True)

    def test_split_window_failure_rolls_back(self) -> None:
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[_make_split_result(pane_id="", returncode=1)],
            ) as ctx,
        ):
            result = scale_up("alpha", 1, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleError)
            self.assertIn("Failed to create tmux pane", result.error)
            # config saved with restored next_worker_index
            self.assertTrue(ctx["config_saves"])
            last = ctx["config_saves"][-1]
            self.assertEqual(last["next_worker_index"], 2)
            # workers list unchanged
            self.assertEqual(len(last["workers"]), 1)

    def test_dispatch_failure_rolls_back(self) -> None:
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[_make_split_result(pane_id="%w2")],
                dispatch_outcomes=[_fail_dispatch("startup_no_evidence")],
                receipt=None,
            ) as ctx,
        ):
            result = scale_up("alpha", 1, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleError)
            self.assertIn("scale_up_dispatch_failed", result.error)
            last = ctx["config_saves"][-1]
            self.assertEqual(last["next_worker_index"], 2)
            self.assertEqual(len(last["workers"]), 1)

    def test_invalid_pane_id_rolls_back(self) -> None:
        # Pane stdout missing the '%' prefix → capture failure.
        bad = _make_split_result(pane_id="not-a-pane")
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[bad],
            ),
        ):
            result = scale_up("alpha", 1, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleError)
            self.assertIn("Failed to capture pane ID", result.error)

    def test_partial_failure_rolls_back_first_worker_too(self) -> None:
        # First split succeeds, second fails — both should be cleaned up.
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[
                    _make_split_result(pane_id="%w2"),
                    _make_split_result(pane_id="", returncode=1),
                ],
            ) as ctx,
        ):
            result = scale_up("alpha", 2, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleError)
            last = ctx["config_saves"][-1]
            # next_worker_index reset to the pre-scale value
            self.assertEqual(last["next_worker_index"], 2)
            # Workers list contains only the original single worker
            self.assertEqual(len(last["workers"]), 1)

    def test_dispatch_succeeds_after_trust_prompt_retry(self) -> None:
        # Outcome fails the first time, then the trust prompt is detected
        # and the retry succeeds. We layer the trust-prompt override AFTER
        # `_patch_scale_up_runtime` so the inner mock doesn't shadow it.
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[_make_split_result(pane_id="%w2")],
                dispatch_outcomes=[_fail_dispatch("blocked_by_trust_prompt")],
                receipt=None,
            ),
            mock.patch.object(
                scaling_mod, "dismiss_trust_prompt_if_present", return_value=True
            ),
        ):
            result = scale_up("alpha", 1, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleUpResult)


# ---------------------------------------------------------------------------
# Worktree mode + state-root resolution
# ---------------------------------------------------------------------------


class TestScaleUpWorktreeMode(unittest.TestCase):
    def setUp(self) -> None:
        self.cwd = tempfile.mkdtemp(prefix="omx-scale-wt-")
        self.cfg = _base_config(
            workers=[
                {
                    "name": "worker-1",
                    "index": 1,
                    "pane_id": "%w1",
                    "worktree_path": "/tmp/wt1",
                    "worktree_detached": True,
                }
            ],
            workspace_mode="worktree",
            worktree_mode=None,
            max_workers=6,
            next_worker_index=2,
        )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.cwd, ignore_errors=True)

    def test_worktree_provisioning_succeeds(self) -> None:
        ensured = EnsureWorktreeResult(
            enabled=True,
            repo_root="/tmp/repo",
            worktree_path="/tmp/wt2",
            detached=True,
            branch_name=None,
            created=True,
            reused=False,
            created_branch=False,
        )
        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[_make_split_result(pane_id="%w2")],
                worktree_results=[ensured],
                enable_worktree=True,
            ),
        ):
            result = scale_up("alpha", 1, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleUpResult)
            self.assertEqual(result.added_workers[0].get("worktree_path"), "/tmp/wt2")
            self.assertTrue(result.added_workers[0].get("worktree_detached"))

    def test_worktree_provisioning_failure_rolls_back(self) -> None:
        def _raise(_plan, _options=None):
            raise RuntimeError("worktree_dirty:/tmp/wt2")

        with (
            _scaling_enabled_env(),
            _patch_scale_up_runtime(
                config=self.cfg,
                split_outcomes=[_make_split_result(pane_id="%w2")],
                enable_worktree=True,
            ),
            mock.patch.object(scaling_mod, "ensure_worktree", side_effect=_raise),
        ):
            result = scale_up("alpha", 1, "executor", [], self.cwd)
            self.assertIsInstance(result, ScaleError)
            self.assertIn("scale_up_worktree_failed", result.error)


# ---------------------------------------------------------------------------
# Result dataclass shape
# ---------------------------------------------------------------------------


class TestResultDataclasses(unittest.TestCase):
    def test_scale_up_result_ok_default(self) -> None:
        r = ScaleUpResult(added_workers=[], new_worker_count=0, next_worker_index=1)
        self.assertTrue(r.ok)

    def test_scale_error_ok_default(self) -> None:
        e = ScaleError(error="x")
        self.assertFalse(e.ok)


if __name__ == "__main__":
    unittest.main()
