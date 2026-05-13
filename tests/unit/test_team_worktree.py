"""Tests for `omx.team.worktree`.

Strategy:
  - Pure parsers (`parse_worktree_mode`, `_sanitize_path_token`,
    branch/path resolvers via `plan_worktree_target` with a stubbed
    git invoker) get direct value tests — no real git involved.
  - Git-calling functions (`is_git_repository`, `is_worktree_dirty`,
    `read_workspace_status_lines`, `ensure_worktree`,
    `rollback_provisioned_worktrees`, `remove_worktree_force`) get tests
    that monkeypatch `omx.team.worktree.subprocess.run` with a fake
    dispatcher returning `subprocess.CompletedProcess` instances.

The fake dispatcher keys on the git subcommand (`args[1:]`) so each test
states exactly the git surface it expects to see.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Callable
from unittest import mock

import pytest

from omx.team import worktree as wt
from omx.team.worktree import (
    EnsureWorktreeOptions,
    EnsureWorktreeResult,
    PlannedWorktreeTarget,
    RollbackWorktreeOptions,
    WorktreeDisabled,
    WorktreeMode,
    WorktreePlanInput,
    assert_clean_leader_workspace_for_worker_worktrees,
    ensure_worktree,
    is_git_repository,
    is_worktree_dirty,
    parse_worktree_mode,
    plan_worktree_target,
    read_workspace_status_lines,
    remove_worktree_force,
    rollback_provisioned_worktrees,
)


# ---------------------------------------------------------------------------
# Fake subprocess helpers
# ---------------------------------------------------------------------------


def _proc(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _dispatcher(
    table: dict[tuple[str, ...], subprocess.CompletedProcess[str]],
) -> Callable[..., Any]:
    """Build a fake `subprocess.run` that matches on `args[1:]` as a tuple.

    Falls back to returning exit-0 with empty output for unmatched calls so
    that incidental git probes (e.g. `_resolve_git_common_dir`) don't break
    tests that don't care about them.
    """

    def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        # `args` is a list like ['git', 'worktree', 'list', '--porcelain'].
        key = tuple(args[1:])
        if key in table:
            return table[key]
        # Default to a benign success with empty output.
        return _proc()

    return fake_run


# ---------------------------------------------------------------------------
# parse_worktree_mode — pure
# ---------------------------------------------------------------------------


class TestParseWorktreeMode:
    def test_no_flags_returns_disabled(self) -> None:
        parsed = parse_worktree_mode(["foo", "bar"])
        assert parsed.mode == WorktreeMode(enabled=False)
        assert parsed.remaining_args == ["foo", "bar"]

    def test_long_flag_alone_is_detached(self) -> None:
        parsed = parse_worktree_mode(["--worktree"])
        assert parsed.mode == WorktreeMode(enabled=True, detached=True, name=None)
        assert parsed.remaining_args == []

    def test_short_flag_alone_is_detached(self) -> None:
        parsed = parse_worktree_mode(["-w"])
        assert parsed.mode == WorktreeMode(enabled=True, detached=True, name=None)

    def test_long_flag_with_branch_consumes_next(self) -> None:
        parsed = parse_worktree_mode(["--worktree", "feature-x", "leftover"])
        assert parsed.mode == WorktreeMode(
            enabled=True, detached=False, name="feature-x"
        )
        assert parsed.remaining_args == ["leftover"]

    def test_short_flag_with_branch_consumes_next(self) -> None:
        parsed = parse_worktree_mode(["-w", "topic"])
        assert parsed.mode == WorktreeMode(enabled=True, detached=False, name="topic")
        assert parsed.remaining_args == []

    def test_short_flag_does_not_consume_team_spec_with_colon(self) -> None:
        # `3:debugger` is a team worker spec, not a branch name.
        parsed = parse_worktree_mode(["-w", "3:debugger"])
        assert parsed.mode == WorktreeMode(enabled=True, detached=True, name=None)
        assert parsed.remaining_args == ["3:debugger"]

    def test_short_flag_does_not_consume_flag_argument(self) -> None:
        parsed = parse_worktree_mode(["-w", "--other"])
        assert parsed.mode == WorktreeMode(enabled=True, detached=True, name=None)
        assert parsed.remaining_args == ["--other"]

    def test_equals_form_named(self) -> None:
        parsed = parse_worktree_mode(["--worktree=my-branch"])
        assert parsed.mode == WorktreeMode(
            enabled=True, detached=False, name="my-branch"
        )

    def test_equals_form_empty_value_is_detached(self) -> None:
        parsed = parse_worktree_mode(["--worktree="])
        assert parsed.mode == WorktreeMode(enabled=True, detached=True, name=None)

    def test_short_equals_form(self) -> None:
        parsed = parse_worktree_mode(["-w=branchy"])
        assert parsed.mode == WorktreeMode(enabled=True, detached=False, name="branchy")

    def test_short_glued_form(self) -> None:
        parsed = parse_worktree_mode(["-wstuff"])
        assert parsed.mode == WorktreeMode(enabled=True, detached=False, name="stuff")

    def test_last_flag_wins(self) -> None:
        parsed = parse_worktree_mode(["--worktree=alpha", "--worktree=beta"])
        assert parsed.mode == WorktreeMode(enabled=True, detached=False, name="beta")

    def test_unrelated_args_preserved_in_order(self) -> None:
        parsed = parse_worktree_mode(["a", "-w", "br", "b", "c"])
        assert parsed.mode.name == "br"
        assert parsed.remaining_args == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# plan_worktree_target — with stubbed git for show-toplevel / rev-parse / check-ref-format
# ---------------------------------------------------------------------------


def _plan_dispatcher(
    *,
    repo_root: str,
    head: str = "deadbeef",
    branch_valid: bool = True,
) -> Callable[..., Any]:
    """Default dispatcher for plan_worktree_target tests."""
    table: dict[tuple[str, ...], subprocess.CompletedProcess[str]] = {
        ("rev-parse", "--show-toplevel"): _proc(stdout=repo_root + "\n"),
        ("rev-parse", "HEAD"): _proc(stdout=head + "\n"),
    }

    def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        key = tuple(args[1:])
        if key in table:
            return table[key]
        if key[:2] == ("check-ref-format", "--branch"):
            return _proc(
                returncode=0 if branch_valid else 128,
                stderr="" if branch_valid else "bad branch",
            )
        return _proc()

    return fake_run


class TestPlanWorktreeTarget:
    def test_disabled_short_circuits(self, tmp_path: Path) -> None:
        result = plan_worktree_target(
            WorktreePlanInput(
                cwd=str(tmp_path), scope="launch", mode=WorktreeMode(enabled=False)
            )
        )
        assert isinstance(result, WorktreeDisabled)
        assert result.enabled is False

    def test_launch_named(self, tmp_path: Path) -> None:
        repo_root = str(tmp_path / "myrepo")
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_plan_dispatcher(repo_root=repo_root, head="abc123"),
        ):
            result = plan_worktree_target(
                WorktreePlanInput(
                    cwd=str(tmp_path),
                    scope="launch",
                    mode=WorktreeMode(enabled=True, detached=False, name="Feature/X"),
                )
            )
        assert isinstance(result, PlannedWorktreeTarget)
        assert result.repo_root == repo_root
        assert result.base_ref == "abc123"
        assert result.branch_name == "Feature/X"
        assert result.detached is False
        # path: <parent>/<basename>.omx-worktrees/launch-feature-x
        expected = os.path.join(
            os.path.dirname(repo_root), "myrepo.omx-worktrees", "launch-feature-x"
        )
        assert result.worktree_path == expected

    def test_launch_detached(self, tmp_path: Path) -> None:
        repo_root = str(tmp_path / "repo")
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_plan_dispatcher(repo_root=repo_root),
        ):
            result = plan_worktree_target(
                WorktreePlanInput(
                    cwd=str(tmp_path),
                    scope="launch",
                    mode=WorktreeMode(enabled=True, detached=True, name=None),
                )
            )
        assert isinstance(result, PlannedWorktreeTarget)
        assert result.detached is True
        assert result.branch_name is None
        assert result.worktree_path.endswith(
            os.path.join("repo.omx-worktrees", "launch-detached")
        )

    def test_autoresearch_requires_named_mode(self, tmp_path: Path) -> None:
        repo_root = str(tmp_path / "r")
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_plan_dispatcher(repo_root=repo_root),
        ):
            with pytest.raises(
                RuntimeError, match="autoresearch_worktree_requires_named_mode"
            ):
                plan_worktree_target(
                    WorktreePlanInput(
                        cwd=str(tmp_path),
                        scope="autoresearch",
                        mode=WorktreeMode(enabled=True, detached=True, name=None),
                    )
                )

    def test_autoresearch_named_path_and_branch(self, tmp_path: Path) -> None:
        repo_root = str(tmp_path / "repo")
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_plan_dispatcher(repo_root=repo_root),
        ):
            result = plan_worktree_target(
                WorktreePlanInput(
                    cwd=str(tmp_path),
                    scope="autoresearch",
                    mode=WorktreeMode(enabled=True, detached=False, name="MissionOne"),
                    worktree_tag="Run!42",
                )
            )
        assert isinstance(result, PlannedWorktreeTarget)
        assert result.branch_name == "autoresearch/missionone/run-42"
        assert result.worktree_path.endswith(
            os.path.join(".omx", "worktrees", "autoresearch-missionone-run-42")
        )

    def test_team_requires_worker_name(self, tmp_path: Path) -> None:
        repo_root = str(tmp_path / "repo")
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_plan_dispatcher(repo_root=repo_root),
        ):
            with pytest.raises(
                RuntimeError, match="team_worktree_worker_name_required"
            ):
                plan_worktree_target(
                    WorktreePlanInput(
                        cwd=str(tmp_path),
                        scope="team",
                        mode=WorktreeMode(enabled=True, detached=False, name="my-team"),
                        worker_name="",
                    )
                )

    def test_team_named_path(self, tmp_path: Path) -> None:
        repo_root = str(tmp_path / "repo")
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_plan_dispatcher(repo_root=repo_root),
        ):
            result = plan_worktree_target(
                WorktreePlanInput(
                    cwd=str(tmp_path),
                    scope="team",
                    mode=WorktreeMode(enabled=True, detached=False, name="Sprint-12"),
                    team_name="Squad A!",
                    worker_name="Worker.1",
                )
            )
        assert isinstance(result, PlannedWorktreeTarget)
        assert result.branch_name == "Sprint-12/Worker.1"
        assert result.worktree_path.endswith(
            os.path.join(".omx", "team", "squad-a", "worktrees", "worker-1")
        )

    def test_invalid_branch_raises(self, tmp_path: Path) -> None:
        repo_root = str(tmp_path / "repo")
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_plan_dispatcher(repo_root=repo_root, branch_valid=False),
        ):
            with pytest.raises(RuntimeError, match="bad branch"):
                plan_worktree_target(
                    WorktreePlanInput(
                        cwd=str(tmp_path),
                        scope="launch",
                        mode=WorktreeMode(
                            enabled=True, detached=False, name="bad..name"
                        ),
                    )
                )


# ---------------------------------------------------------------------------
# Status helpers
# ---------------------------------------------------------------------------


class TestStatusHelpers:
    def test_is_git_repository_true(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {("rev-parse", "--show-toplevel"): _proc(stdout=str(tmp_path))}
            ),
        ):
            assert is_git_repository(str(tmp_path)) is True

    def test_is_git_repository_false(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {
                    ("rev-parse", "--show-toplevel"): _proc(
                        returncode=128, stderr="not a repo"
                    )
                }
            ),
        ):
            assert is_git_repository(str(tmp_path)) is False

    def test_is_worktree_dirty_true(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {("status", "--porcelain"): _proc(stdout=" M foo.py\n")}
            ),
        ):
            assert is_worktree_dirty(str(tmp_path)) is True

    def test_is_worktree_dirty_false(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher({("status", "--porcelain"): _proc(stdout="")}),
        ):
            assert is_worktree_dirty(str(tmp_path)) is False

    def test_is_worktree_dirty_raises_on_failure(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {
                    ("status", "--porcelain"): _proc(
                        returncode=128, stderr="fatal: not a tree"
                    )
                }
            ),
        ):
            with pytest.raises(RuntimeError, match="fatal: not a tree"):
                is_worktree_dirty(str(tmp_path))

    def test_read_workspace_status_lines_strips_blank(self, tmp_path: Path) -> None:
        out = " M a.py\n?? b.py   \n\n M c.py\n"
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {("status", "--porcelain", "--untracked-files=all"): _proc(stdout=out)}
            ),
        ):
            lines = read_workspace_status_lines(str(tmp_path))
        assert lines == [" M a.py", "?? b.py", " M c.py"]

    def test_assert_clean_workspace_passes_when_empty(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {("status", "--porcelain", "--untracked-files=all"): _proc(stdout="")}
            ),
        ):
            assert_clean_leader_workspace_for_worker_worktrees(str(tmp_path))

    def test_assert_clean_workspace_raises_when_dirty(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {
                    ("status", "--porcelain", "--untracked-files=all"): _proc(
                        stdout=" M a.py\n?? b.py\n"
                    )
                }
            ),
        ):
            with pytest.raises(
                RuntimeError,
                match="leader_workspace_dirty_for_worktrees.*commit_or_stash_before_omx_team",
            ):
                assert_clean_leader_workspace_for_worker_worktrees(str(tmp_path))


# ---------------------------------------------------------------------------
# ensure_worktree
# ---------------------------------------------------------------------------


def _make_plan(
    tmp_path: Path, *, detached: bool = False, branch: str | None = "br1"
) -> PlannedWorktreeTarget:
    repo_root = str(tmp_path / "repo")
    os.makedirs(repo_root, exist_ok=True)
    return PlannedWorktreeTarget(
        enabled=True,
        scope="team",
        repo_root=repo_root,
        worktree_path=str(tmp_path / "worktrees" / "w1"),
        detached=detached,
        base_ref="basebase",
        branch_name=None if detached else branch,
    )


class TestEnsureWorktree:
    def test_disabled_returns_disabled(self) -> None:
        result = ensure_worktree(WorktreeDisabled())
        assert isinstance(result, WorktreeDisabled)

    def test_creates_branched_worktree(self, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, branch="br1")
        calls: list[list[str]] = []

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append(list(args[1:]))
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout="")
            if key[:3] == ("show-ref", "--verify", "--quiet"):
                # branch does not yet exist
                return _proc(returncode=1)
            if key[:2] == ("worktree", "add"):
                return _proc(stdout="")
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            result = ensure_worktree(plan)

        assert isinstance(result, EnsureWorktreeResult)
        assert result.created is True
        assert result.reused is False
        assert result.created_branch is True
        assert result.branch_name == "br1"
        # The `worktree add` invocation should use `-b` because branch did not exist.
        add_calls = [c for c in calls if c[:2] == ["worktree", "add"]]
        assert add_calls, "expected at least one `git worktree add` call"
        assert "-b" in add_calls[0]

    def test_creates_detached_worktree(self, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, detached=True, branch=None)
        captured: list[list[str]] = []

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(args[1:]))
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout="")
            if key[:2] == ("worktree", "add"):
                return _proc()
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            result = ensure_worktree(plan)

        assert isinstance(result, EnsureWorktreeResult)
        assert result.detached is True
        assert result.created_branch is False
        add_calls = [c for c in captured if c[:2] == ["worktree", "add"]]
        assert add_calls and "--detach" in add_calls[0]

    def test_reuses_existing_clean_worktree(self, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, branch="br1")
        # Simulate the worktree path already on disk so the list lookup matches.
        os.makedirs(plan.worktree_path, exist_ok=True)
        listing = (
            f"worktree {plan.worktree_path}\nHEAD basebase\nbranch refs/heads/br1\n"
        )

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout=listing)
            if key == ("status", "--porcelain"):
                return _proc(stdout="")  # clean
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            result = ensure_worktree(plan)

        assert isinstance(result, EnsureWorktreeResult)
        assert result.reused is True
        assert result.created is False
        assert result.dirty is None

    def test_reuses_existing_dirty_worktree_with_allow(self, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, branch="br1")
        os.makedirs(plan.worktree_path, exist_ok=True)
        listing = (
            f"worktree {plan.worktree_path}\nHEAD basebase\nbranch refs/heads/br1\n"
        )

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout=listing)
            if key == ("status", "--porcelain"):
                return _proc(stdout=" M x.py\n")
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            result = ensure_worktree(
                plan, EnsureWorktreeOptions(allow_dirty_reuse=True)
            )

        assert isinstance(result, EnsureWorktreeResult)
        assert result.reused is True
        assert result.dirty is True

    def test_reuse_dirty_worktree_without_allow_raises(self, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, branch="br1")
        os.makedirs(plan.worktree_path, exist_ok=True)
        listing = (
            f"worktree {plan.worktree_path}\nHEAD basebase\nbranch refs/heads/br1\n"
        )

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout=listing)
            if key == ("status", "--porcelain"):
                return _proc(stdout=" M x.py\n")
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="worktree_dirty"):
                ensure_worktree(plan)

    def test_branch_mismatch_raises(self, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, branch="br1")
        os.makedirs(plan.worktree_path, exist_ok=True)
        # Existing worktree is on a different branch.
        listing = (
            f"worktree {plan.worktree_path}\nHEAD basebase\nbranch refs/heads/other\n"
        )

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout=listing)
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="worktree_target_mismatch"):
                ensure_worktree(plan)

    def test_path_conflict_raises(self, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, branch="br1")
        # Path exists on disk but git knows nothing about it.
        os.makedirs(plan.worktree_path, exist_ok=True)

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout="")
            if key == ("rev-parse", "--git-common-dir"):
                # Mismatched common dir → not a sibling worktree
                return _proc(stdout="/somewhere/else/.git\n")
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="worktree_path_conflict"):
                ensure_worktree(plan)

    def test_branch_in_use_elsewhere_raises(self, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, branch="br1")
        # Path itself is NOT yet checked out, but the branch is checked out at another path.
        other_path = str(tmp_path / "other-worktree")
        listing = f"worktree {other_path}\nHEAD basebase\nbranch refs/heads/br1\n"

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout=listing)
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="branch_in_use:br1"):
                ensure_worktree(plan)

    def test_existing_branch_uses_plain_add(self, tmp_path: Path) -> None:
        """When branch already exists locally, `worktree add` runs without `-b`."""
        plan = _make_plan(tmp_path, branch="existing-branch")
        captured: list[list[str]] = []

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(args[1:]))
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout="")
            if key[:3] == ("show-ref", "--verify", "--quiet"):
                # branch exists
                return _proc(returncode=0)
            if key[:2] == ("worktree", "add"):
                return _proc()
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            result = ensure_worktree(plan)

        assert isinstance(result, EnsureWorktreeResult)
        assert result.created is True
        assert result.created_branch is False  # branch pre-existed
        add_calls = [c for c in captured if c[:2] == ["worktree", "add"]]
        assert add_calls and "-b" not in add_calls[0]

    def test_add_failure_branch_in_use_pattern(self, tmp_path: Path) -> None:
        """`git worktree add` returning a `is already checked out` stderr → branch_in_use."""
        plan = _make_plan(tmp_path, branch="br1")

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout="")
            if key[:3] == ("show-ref", "--verify", "--quiet"):
                return _proc(returncode=1)
            if key[:2] == ("worktree", "add"):
                return _proc(
                    returncode=128, stderr="fatal: 'br1' is already checked out at ..."
                )
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="branch_in_use:br1"):
                ensure_worktree(plan)

    def test_add_failure_generic(self, tmp_path: Path) -> None:
        plan = _make_plan(tmp_path, branch="br1")

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout="")
            if key[:3] == ("show-ref", "--verify", "--quiet"):
                return _proc(returncode=1)
            if key[:2] == ("worktree", "add"):
                return _proc(returncode=1, stderr="some other failure")
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            with pytest.raises(RuntimeError, match="some other failure"):
                ensure_worktree(plan)


# ---------------------------------------------------------------------------
# rollback_provisioned_worktrees / remove_worktree_force
# ---------------------------------------------------------------------------


def _ensured(
    repo_root: str,
    path: str,
    *,
    branch: str | None,
    created: bool = True,
    created_branch: bool = False,
) -> EnsureWorktreeResult:
    return EnsureWorktreeResult(
        enabled=True,
        repo_root=repo_root,
        worktree_path=path,
        detached=branch is None,
        branch_name=branch,
        created=created,
        reused=not created,
        created_branch=created_branch,
        dirty=None,
    )


class TestRollback:
    def test_rollback_no_created_is_noop(self, tmp_path: Path) -> None:
        # Only reused entries — nothing to remove.
        reused = _ensured(
            str(tmp_path), str(tmp_path / "w"), branch="br", created=False
        )
        with mock.patch("omx.team.worktree.subprocess.run") as run:
            rollback_provisioned_worktrees([reused, WorktreeDisabled()])
        run.assert_not_called()

    def test_rollback_removes_created_and_deletes_branch(self, tmp_path: Path) -> None:
        repo = str(tmp_path / "repo")
        wpath = str(tmp_path / "wt")
        ensured = _ensured(
            repo, wpath, branch="br-x", created=True, created_branch=True
        )
        captured: list[list[str]] = []

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(args[1:]))
            key = tuple(args[1:])
            if key[:3] == ("worktree", "remove", "--force"):
                return _proc()
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout="")  # branch no longer checked out anywhere
            if key[:2] == ("branch", "-D"):
                return _proc()
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            rollback_provisioned_worktrees([ensured])

        assert ["worktree", "remove", "--force", wpath] in captured
        assert ["branch", "-D", "br-x"] in captured

    def test_rollback_skip_branch_deletion(self, tmp_path: Path) -> None:
        repo = str(tmp_path / "repo")
        wpath = str(tmp_path / "wt")
        ensured = _ensured(
            repo, wpath, branch="br-x", created=True, created_branch=True
        )
        captured: list[list[str]] = []

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(args[1:]))
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            rollback_provisioned_worktrees(
                [ensured],
                RollbackWorktreeOptions(skip_branch_deletion=True),
            )

        assert ["worktree", "remove", "--force", wpath] in captured
        assert not any(c[:2] == ["branch", "-D"] for c in captured)

    def test_rollback_skips_branch_when_still_checked_out(self, tmp_path: Path) -> None:
        repo = str(tmp_path / "repo")
        wpath = str(tmp_path / "wt")
        other = str(tmp_path / "other")
        ensured = _ensured(
            repo, wpath, branch="br-x", created=True, created_branch=True
        )
        captured: list[list[str]] = []
        # Branch still checked out at another worktree → skip delete.
        listing = f"worktree {other}\nHEAD abc\nbranch refs/heads/br-x\n"

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured.append(list(args[1:]))
            key = tuple(args[1:])
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout=listing)
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            rollback_provisioned_worktrees([ensured])

        assert not any(c[:2] == ["branch", "-D"] for c in captured)

    def test_rollback_aggregates_errors(self, tmp_path: Path) -> None:
        repo = str(tmp_path / "repo")
        e1 = _ensured(
            repo, str(tmp_path / "a"), branch="b1", created=True, created_branch=True
        )
        e2 = _ensured(
            repo, str(tmp_path / "b"), branch="b2", created=True, created_branch=True
        )

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key[:3] == ("worktree", "remove", "--force"):
                # First removal in reverse order (e2 first) succeeds, e1 fails.
                if args[-1] == str(tmp_path / "a"):
                    return _proc(returncode=128, stderr="cannot remove a")
                return _proc()
            if key == ("worktree", "list", "--porcelain"):
                return _proc(stdout="")
            if key[:2] == ("branch", "-D"):
                return _proc()
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            with pytest.raises(
                RuntimeError, match="worktree_rollback_failed.*cannot remove a"
            ):
                rollback_provisioned_worktrees([e1, e2])

    def test_rollback_reverse_order(self, tmp_path: Path) -> None:
        repo = str(tmp_path / "repo")
        a = _ensured(repo, str(tmp_path / "a"), branch=None, created=True)
        b = _ensured(repo, str(tmp_path / "b"), branch=None, created=True)
        order: list[str] = []

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            if key[:3] == ("worktree", "remove", "--force"):
                order.append(args[-1])
                return _proc()
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            rollback_provisioned_worktrees([a, b])

        # `b` was provisioned last → must be removed first.
        assert order == [str(tmp_path / "b"), str(tmp_path / "a")]


class TestRemoveWorktreeForce:
    def test_success(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {("worktree", "remove", "--force", str(tmp_path)): _proc()}
            ),
        ):
            remove_worktree_force(str(tmp_path), str(tmp_path))

    def test_failure_raises(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {
                    ("worktree", "remove", "--force", str(tmp_path)): _proc(
                        returncode=128, stderr="cannot remove"
                    )
                }
            ),
        ):
            with pytest.raises(RuntimeError, match="cannot remove"):
                remove_worktree_force(str(tmp_path), str(tmp_path))


# ---------------------------------------------------------------------------
# Legacy back-compat — make sure original API still works
# ---------------------------------------------------------------------------


class TestLegacyHelpers:
    def test_list_worktrees_legacy_shape(self, tmp_path: Path) -> None:
        listing = (
            "worktree /a\nHEAD abc\nbranch refs/heads/main\n\n"
            "worktree /b\nHEAD def\nbranch refs/heads/topic\n"
        )
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher(
                {("worktree", "list", "--porcelain"): _proc(stdout=listing)}
            ),
        ):
            result = wt.list_worktrees(str(tmp_path))
        assert len(result) == 2
        assert result[0]["path"] == "/a"
        assert result[0]["branch"] == "main"
        assert result[1]["branch"] == "topic"

    def test_remove_worktree_legacy_returns_bool(self, tmp_path: Path) -> None:
        with mock.patch(
            "omx.team.worktree.subprocess.run",
            side_effect=_dispatcher({("worktree", "remove", "--force", "/x"): _proc()}),
        ):
            assert wt.remove_worktree(str(tmp_path), "/x", force=True) is True

    def test_create_worktree_legacy_fallback_path(self, tmp_path: Path) -> None:
        # First call (with -b) fails; second call (plain) succeeds.
        calls: list[tuple[str, ...]] = []

        def fake_run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            key = tuple(args[1:])
            calls.append(key)
            if "-b" in key:
                return _proc(returncode=128, stderr="branch exists")
            return _proc()

        with mock.patch("omx.team.worktree.subprocess.run", side_effect=fake_run):
            result = wt.create_worktree(str(tmp_path), "br", str(tmp_path / "x"))
        assert result["ok"] is True
        assert result["branch"] == "br"
