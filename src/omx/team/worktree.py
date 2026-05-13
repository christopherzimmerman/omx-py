"""Git worktree lifecycle management for team workers.

Port of `src/team/worktree.ts` (oh-my-codex TypeScript).

Each worker gets an isolated git worktree to avoid conflicts. This module
provides:

  - Pure planners (`parse_worktree_mode`, `plan_worktree_target`) that turn
    user input into a concrete target spec.
  - Git-driven lifecycle (`ensure_worktree`, `rollback_provisioned_worktrees`,
    `remove_worktree_force`) that invokes `git worktree …` via stdlib
    `subprocess.run`.
  - Status helpers (`is_git_repository`, `is_worktree_dirty`,
    `read_workspace_status_lines`,
    `assert_clean_leader_workspace_for_worker_worktrees`).

Locked decisions:
  - Sync only (no asyncio). TS async/await collapses to blocking
    `subprocess.run` calls.
  - Stdlib only (no `gitpython` etc).
  - Git is required; functions raise a clear `RuntimeError` when a git
    invocation fails.

Legacy helpers (`create_worktree`, `remove_worktree`, `list_worktrees`,
`prune_worktrees`) are preserved for `team.worker_bootstrap` callers that
predate the full TS port.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional, Union

__all__ = [
    # Types
    "WorktreeMode",
    "ParsedWorktreeMode",
    "WorktreePlanInput",
    "PlannedWorktreeTarget",
    "WorktreeDisabled",
    "EnsureWorktreeResult",
    "EnsureWorktreeOptions",
    "RollbackWorktreeOptions",
    # New TS-parity functions
    "is_git_repository",
    "is_worktree_dirty",
    "read_workspace_status_lines",
    "assert_clean_leader_workspace_for_worker_worktrees",
    "parse_worktree_mode",
    "plan_worktree_target",
    "ensure_worktree",
    "rollback_provisioned_worktrees",
    "remove_worktree_force",
    # Legacy helpers (kept for back-compat callers)
    "create_worktree",
    "remove_worktree",
    "list_worktrees",
    "prune_worktrees",
]


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

WorktreeScope = Literal["launch", "team", "autoresearch"]


@dataclass(frozen=True)
class WorktreeMode:
    """Worktree activation mode parsed from CLI args.

    Mirrors the TS discriminated union:

      - `{ enabled: false }`                         → `WorktreeMode(enabled=False)`
      - `{ enabled: true, detached: true, name: null }`
                                                    → `WorktreeMode(enabled=True, detached=True, name=None)`
      - `{ enabled: true, detached: false, name: str }`
                                                    → `WorktreeMode(enabled=True, detached=False, name=str)`
    """

    enabled: bool
    detached: bool = False
    name: Optional[str] = None


@dataclass(frozen=True)
class ParsedWorktreeMode:
    """Result of `parse_worktree_mode`."""

    mode: WorktreeMode
    remaining_args: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class WorktreePlanInput:
    """Input to `plan_worktree_target`."""

    cwd: str
    scope: WorktreeScope
    mode: WorktreeMode
    team_name: Optional[str] = None
    worker_name: Optional[str] = None
    worktree_tag: Optional[str] = None


@dataclass(frozen=True)
class PlannedWorktreeTarget:
    """Resolved target produced by `plan_worktree_target`.

    Always carries `enabled=True`; the disabled case is represented by
    :class:`WorktreeDisabled`.
    """

    enabled: Literal[True]
    scope: WorktreeScope
    repo_root: str
    worktree_path: str
    detached: bool
    base_ref: str
    branch_name: Optional[str]


@dataclass(frozen=True)
class WorktreeDisabled:
    """Sentinel for the disabled-mode return of plan/ensure."""

    enabled: Literal[False] = False


@dataclass(frozen=True)
class EnsureWorktreeResult:
    """Outcome of a successful `ensure_worktree` call."""

    enabled: Literal[True]
    repo_root: str
    worktree_path: str
    detached: bool
    branch_name: Optional[str]
    created: bool
    reused: bool
    created_branch: bool
    # `dirty` is only set (and only True) when the worktree had uncommitted
    # changes at launch time and `allow_dirty_reuse` was honored.
    dirty: Optional[bool] = None


@dataclass(frozen=True)
class EnsureWorktreeOptions:
    """Options for `ensure_worktree`."""

    allow_dirty_reuse: bool = False


@dataclass(frozen=True)
class RollbackWorktreeOptions:
    """Options for `rollback_provisioned_worktrees`."""

    # When True, skip `git branch -D` for branches created during provisioning
    # (ralph policy).
    skip_branch_deletion: bool = False


@dataclass(frozen=True)
class _GitWorktreeEntry:
    """Internal: parsed entry from `git worktree list --porcelain`."""

    path: str
    head: str
    branch_ref: Optional[str]
    detached: bool


_BRANCH_IN_USE_PATTERN = re.compile(
    r"already checked out|already used by worktree|is already checked out",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Low-level git helpers
# ---------------------------------------------------------------------------


def _spawn(args: list[str], cwd: str) -> subprocess.CompletedProcess[str]:
    """Run a git command, capture stdout/stderr as text. Never raises on non-zero."""
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
    )


def _read_git(repo_root: str, args: list[str]) -> str:
    """Run `git <args>` and return trimmed stdout. Raise `RuntimeError` on failure."""
    result = _spawn(["git", *args], cwd=repo_root)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"git {' '.join(args)} failed")
    return (result.stdout or "").strip()


def _sanitize_path_token(value: str) -> str:
    """Lower-case alphanumerify a string for path-safe usage."""
    normalized = value.lower()
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = re.sub(r"-+", "-", normalized)
    normalized = normalized.strip("-")
    return normalized or "default"


def _validate_branch_name(repo_root: str, branch_name: str) -> None:
    """Validate a git branch name via `git check-ref-format --branch`."""
    result = _spawn(["git", "check-ref-format", "--branch", branch_name], cwd=repo_root)
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip()
    raise RuntimeError(stderr or f"invalid_worktree_branch:{branch_name}")


def _branch_exists(repo_root: str, branch_name: str) -> bool:
    result = _spawn(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"],
        cwd=repo_root,
    )
    return result.returncode == 0


# ---------------------------------------------------------------------------
# Public status helpers
# ---------------------------------------------------------------------------


def is_git_repository(cwd: str) -> bool:
    """Return True when `cwd` is inside a git working tree."""
    result = _spawn(["git", "rev-parse", "--show-toplevel"], cwd=cwd)
    return result.returncode == 0


def is_worktree_dirty(worktree_path: str) -> bool:
    """Return True when the worktree has any tracked or untracked changes.

    Raises:
        RuntimeError: if `git status --porcelain` fails (e.g. path not a worktree).
    """
    result = _spawn(["git", "status", "--porcelain"], cwd=worktree_path)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"worktree_status_failed:{worktree_path}")
    return (result.stdout or "").strip() != ""


def read_workspace_status_lines(cwd: str) -> list[str]:
    """Return non-empty status lines from `git status --porcelain --untracked-files=all`."""
    result = _spawn(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=cwd,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"workspace_status_failed:{cwd}")

    raw = result.stdout or ""
    return [line.rstrip() for line in raw.splitlines() if line.strip() != ""]


def assert_clean_leader_workspace_for_worker_worktrees(cwd: str) -> None:
    """Raise `RuntimeError` when the leader workspace has uncommitted changes.

    Worker worktrees inherit the leader's HEAD; a dirty leader workspace would
    mean those changes are not part of the worker's baseline, so the leader
    must commit or stash first.
    """
    lines = read_workspace_status_lines(cwd)
    if not lines:
        return
    preview = " | ".join(lines[:8])
    raise RuntimeError(
        f"leader_workspace_dirty_for_worktrees:{os.path.abspath(cwd)}:"
        f"{preview}:commit_or_stash_before_omx_team"
    )


# ---------------------------------------------------------------------------
# Worktree-list parsing
# ---------------------------------------------------------------------------


def _list_worktrees(repo_root: str) -> list[_GitWorktreeEntry]:
    raw = _read_git(repo_root, ["worktree", "list", "--porcelain"])
    if not raw:
        return []

    entries: list[_GitWorktreeEntry] = []
    # `git worktree list --porcelain` separates entries with a blank line.
    chunks = [chunk.strip() for chunk in re.split(r"\n\n+", raw) if chunk.strip()]

    for chunk in chunks:
        lines = [line.strip() for line in chunk.splitlines() if line.strip()]
        worktree_line = next(
            (line for line in lines if line.startswith("worktree ")), None
        )
        head_line = next((line for line in lines if line.startswith("HEAD ")), None)
        branch_line = next((line for line in lines if line.startswith("branch ")), None)
        if not worktree_line or not head_line:
            continue

        entries.append(
            _GitWorktreeEntry(
                path=os.path.abspath(worktree_line[len("worktree ") :]),
                head=head_line[len("HEAD ") :].strip(),
                branch_ref=branch_line[len("branch ") :].strip()
                if branch_line
                else None,
                detached=("detached" in lines) or (branch_line is None),
            )
        )

    return entries


def _prune_stale_worktree_path(repo_root: str, worktree_path: str) -> None:
    result = _spawn(["git", "worktree", "prune"], cwd=repo_root)
    if result.returncode == 0:
        return
    stderr = (result.stderr or "").strip()
    raise RuntimeError(stderr or f"worktree_prune_failed:{worktree_path}")


def _find_worktree_by_path(
    entries: list[_GitWorktreeEntry],
    worktree_path: str,
) -> Optional[_GitWorktreeEntry]:
    resolved = os.path.abspath(worktree_path)
    for entry in entries:
        if os.path.abspath(entry.path) == resolved:
            return entry
    return None


def _has_branch_in_use(
    entries: list[_GitWorktreeEntry],
    branch_name: str,
    worktree_path: str,
) -> bool:
    expected_ref = f"refs/heads/{branch_name}"
    resolved = os.path.abspath(worktree_path)
    return any(
        entry.branch_ref == expected_ref and os.path.abspath(entry.path) != resolved
        for entry in entries
    )


def _resolve_git_common_dir(cwd: str) -> Optional[str]:
    result = _spawn(["git", "rev-parse", "--git-common-dir"], cwd=cwd)
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    if not value:
        return None
    # TS uses path.resolve(cwd, value) which resolves relative paths against cwd.
    if os.path.isabs(value):
        return os.path.abspath(value)
    return os.path.abspath(os.path.join(cwd, value))


def _read_worktree_entry_from_path(
    repo_root: str,
    worktree_path: str,
) -> Optional[_GitWorktreeEntry]:
    """Fallback discovery for worktrees missing from `git worktree list`."""
    if not os.path.exists(worktree_path):
        return None

    repo_common = _resolve_git_common_dir(repo_root)
    worktree_common = _resolve_git_common_dir(worktree_path)
    if not repo_common or not worktree_common or repo_common != worktree_common:
        return None

    head_result = _spawn(["git", "rev-parse", "HEAD"], cwd=worktree_path)
    if head_result.returncode != 0:
        return None
    head = (head_result.stdout or "").strip()
    if not head:
        return None

    branch_result = _spawn(["git", "symbolic-ref", "-q", "HEAD"], cwd=worktree_path)
    branch_ref = (
        (branch_result.stdout or "").strip() if branch_result.returncode == 0 else None
    )

    return _GitWorktreeEntry(
        path=os.path.abspath(worktree_path),
        head=head,
        branch_ref=branch_ref or None,
        detached=not bool(branch_ref),
    )


# ---------------------------------------------------------------------------
# Mode parsing
# ---------------------------------------------------------------------------


def parse_worktree_mode(args: list[str]) -> ParsedWorktreeMode:
    """Parse `--worktree` / `-w` flags out of `args`.

    Matches TS semantics exactly:

      - `--worktree` / `-w` alone → detached worktree.
      - `--worktree X` / `-w X`   → named worktree, **only** when `X` does not
        start with `-` and does not contain `:` (which guards against team
        worker specs like `3:debugger` being consumed as branch names).
      - `--worktree=X` / `-w=X` / `-wX` → named when X is non-empty, else
        detached.

    Anything not consumed is returned in `remaining_args` in original order.
    """
    mode: WorktreeMode = WorktreeMode(enabled=False)
    remaining: list[str] = []

    i = 0
    while i < len(args):
        raw = args[i]
        arg = str(raw or "")

        if arg in ("--worktree", "-w"):
            # Peek the next arg. Only consume it when it looks like a branch
            # name (not a flag, not a team spec containing ':').
            nxt = args[i + 1] if (i + 1) < len(args) else None
            if (
                isinstance(nxt, str)
                and len(nxt) > 0
                and not nxt.startswith("-")
                and ":" not in nxt
            ):
                mode = WorktreeMode(enabled=True, detached=False, name=nxt)
                i += 2
                continue
            mode = WorktreeMode(enabled=True, detached=True, name=None)
            i += 1
            continue

        if arg.startswith("--worktree="):
            value = arg[len("--worktree=") :].strip()
            mode = (
                WorktreeMode(enabled=True, detached=False, name=value)
                if value
                else WorktreeMode(enabled=True, detached=True, name=None)
            )
            i += 1
            continue

        if arg.startswith("-w="):
            value = arg[len("-w=") :].strip()
            mode = (
                WorktreeMode(enabled=True, detached=False, name=value)
                if value
                else WorktreeMode(enabled=True, detached=True, name=None)
            )
            i += 1
            continue

        if arg.startswith("-w") and len(arg) > 2:
            value = arg[2:].strip()
            mode = (
                WorktreeMode(enabled=True, detached=False, name=value)
                if value
                else WorktreeMode(enabled=True, detached=True, name=None)
            )
            i += 1
            continue

        remaining.append(raw)
        i += 1

    return ParsedWorktreeMode(mode=mode, remaining_args=remaining)


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


def _resolve_branch_name(input_: WorktreePlanInput) -> Optional[str]:
    if not input_.mode.enabled or input_.mode.detached:
        return None

    name = input_.mode.name
    if not name:
        return None

    if input_.scope == "launch":
        return name

    if input_.scope == "autoresearch":
        run_tag = _sanitize_path_token(input_.worktree_tag or "run")
        return f"autoresearch/{_sanitize_path_token(name)}/{run_tag}"

    # team scope
    worker_name = (input_.worker_name or "").strip()
    if not worker_name:
        raise RuntimeError("team_worktree_worker_name_required")
    return f"{name}/{worker_name}"


def _resolve_worktree_path(input_: WorktreePlanInput, repo_root: str) -> str:
    parent = os.path.dirname(repo_root)
    bucket = f"{os.path.basename(repo_root)}.omx-worktrees"

    if input_.scope == "launch":
        if not input_.mode.enabled or input_.mode.detached:
            return os.path.join(parent, bucket, "launch-detached")
        # mode.name is guaranteed non-None when enabled+not-detached
        return os.path.join(
            parent, bucket, f"launch-{_sanitize_path_token(input_.mode.name or '')}"
        )

    if input_.scope == "autoresearch":
        if not input_.mode.enabled or input_.mode.detached:
            raise RuntimeError("autoresearch_worktree_requires_named_mode")
        run_tag = _sanitize_path_token(input_.worktree_tag or "run")
        return os.path.join(
            repo_root,
            ".omx",
            "worktrees",
            f"autoresearch-{_sanitize_path_token(input_.mode.name or '')}-{run_tag}",
        )

    # team scope
    team_name = _sanitize_path_token(input_.team_name or "team")
    worker_name = _sanitize_path_token(input_.worker_name or "worker")
    return os.path.join(repo_root, ".omx", "team", team_name, "worktrees", worker_name)


def plan_worktree_target(
    input_: WorktreePlanInput,
) -> Union[PlannedWorktreeTarget, WorktreeDisabled]:
    """Resolve repo root, base ref, branch name, and target path.

    Returns :class:`WorktreeDisabled` when `input_.mode.enabled` is False.
    """
    if not input_.mode.enabled:
        return WorktreeDisabled()

    repo_root = _read_git(input_.cwd, ["rev-parse", "--show-toplevel"])
    base_ref = _read_git(repo_root, ["rev-parse", "HEAD"])
    branch_name = _resolve_branch_name(input_)

    if branch_name:
        _validate_branch_name(repo_root, branch_name)

    return PlannedWorktreeTarget(
        enabled=True,
        scope=input_.scope,
        repo_root=repo_root,
        worktree_path=_resolve_worktree_path(input_, repo_root),
        detached=input_.mode.detached,
        base_ref=base_ref,
        branch_name=branch_name,
    )


# ---------------------------------------------------------------------------
# Current-task-baseline integration (best-effort)
# ---------------------------------------------------------------------------
#
# The TS port reaches into `team/current-task-baseline.ts` for
# `upsertCurrentTaskBaseline` + `assertCurrentTaskBranchAvailable`. The Python
# `current_task_baseline` module currently exposes a narrower per-worker shape.
# Until those richer helpers are ported (tracked separately), we look them up
# dynamically and degrade to a no-op if absent — this preserves TS-equivalent
# behavior for environments where the helpers exist while avoiding a hard
# coupling to API not yet ported.


def _try_upsert_current_task_baseline(
    repo_root: str,
    *,
    branch_name: str,
    worktree_path: str,
    base_ref: str,
) -> None:
    try:
        from omx.team import current_task_baseline as _ctb  # type: ignore
    except Exception:
        return
    fn = getattr(_ctb, "upsert_current_task_baseline", None)
    if fn is None:
        return
    try:
        fn(
            repo_root,
            {
                "branch_name": branch_name,
                "worktree_path": worktree_path,
                "base_ref": base_ref,
                "status": "active",
            },
        )
    except TypeError:
        # Different signature; ignore rather than fail provisioning.
        pass


def _try_assert_current_task_branch_available(
    repo_root: str,
    branch_name: str,
    requested_worktree_path: str,
) -> None:
    try:
        from omx.team import current_task_baseline as _ctb  # type: ignore
    except Exception:
        return
    fn = getattr(_ctb, "assert_current_task_branch_available", None)
    if fn is None:
        return
    fn(repo_root, branch_name, requested_worktree_path)


# ---------------------------------------------------------------------------
# ensure_worktree
# ---------------------------------------------------------------------------


def ensure_worktree(
    plan: Union[PlannedWorktreeTarget, WorktreeDisabled],
    options: Optional[EnsureWorktreeOptions] = None,
) -> Union[EnsureWorktreeResult, WorktreeDisabled]:
    """Create or reuse a worktree matching `plan`.

    Behavior matches `ensureWorktree` in TS:

      - Disabled plan → return `WorktreeDisabled()`.
      - Existing worktree at path:
          * Validate it matches the plan (same branch / same detached HEAD).
          * If dirty, raise unless `allow_dirty_reuse=True`.
          * Return a `reused=True` result.
      - Path exists but is not a registered worktree → raise `worktree_path_conflict`.
      - Branch checked out elsewhere → raise `branch_in_use:<branch>`.
      - Otherwise create via `git worktree add` (`--detach` or `-b` or plain).
    """
    if not isinstance(plan, PlannedWorktreeTarget):
        return WorktreeDisabled()

    opts = options or EnsureWorktreeOptions()

    all_worktrees = _list_worktrees(plan.repo_root)
    stale_at_path = _find_worktree_by_path(all_worktrees, plan.worktree_path)
    if stale_at_path is not None and not os.path.exists(stale_at_path.path):
        _prune_stale_worktree_path(plan.repo_root, stale_at_path.path)
        all_worktrees = _list_worktrees(plan.repo_root)

    existing_at_path = _find_worktree_by_path(all_worktrees, plan.worktree_path)
    if existing_at_path is None:
        existing_at_path = _read_worktree_entry_from_path(
            plan.repo_root, plan.worktree_path
        )

    expected_branch_ref = f"refs/heads/{plan.branch_name}" if plan.branch_name else None

    if existing_at_path is not None:
        if plan.detached:
            if not existing_at_path.detached or existing_at_path.head != plan.base_ref:
                raise RuntimeError(f"worktree_target_mismatch:{plan.worktree_path}")
        else:
            if existing_at_path.branch_ref != expected_branch_ref:
                raise RuntimeError(f"worktree_target_mismatch:{plan.worktree_path}")

        dirty = is_worktree_dirty(plan.worktree_path)
        if dirty and not opts.allow_dirty_reuse:
            raise RuntimeError(f"worktree_dirty:{plan.worktree_path}")

        reused = EnsureWorktreeResult(
            enabled=True,
            repo_root=plan.repo_root,
            worktree_path=os.path.abspath(plan.worktree_path),
            detached=plan.detached,
            branch_name=plan.branch_name,
            created=False,
            reused=True,
            created_branch=False,
            dirty=True if dirty else None,
        )

        if plan.branch_name:
            _try_upsert_current_task_baseline(
                plan.repo_root,
                branch_name=plan.branch_name,
                worktree_path=reused.worktree_path,
                base_ref=plan.base_ref,
            )

        return reused

    if os.path.exists(plan.worktree_path):
        raise RuntimeError(f"worktree_path_conflict:{plan.worktree_path}")

    if plan.branch_name and _has_branch_in_use(
        all_worktrees, plan.branch_name, plan.worktree_path
    ):
        raise RuntimeError(f"branch_in_use:{plan.branch_name}")

    if plan.branch_name:
        _try_assert_current_task_branch_available(
            plan.repo_root, plan.branch_name, plan.worktree_path
        )

    Path(os.path.dirname(plan.worktree_path)).mkdir(parents=True, exist_ok=True)
    branch_already_existed = bool(
        plan.branch_name and _branch_exists(plan.repo_root, plan.branch_name)
    )

    add_args: list[str] = ["git", "worktree", "add"]
    if plan.detached:
        add_args += ["--detach", plan.worktree_path, plan.base_ref]
    elif branch_already_existed:
        # branch_name is non-None when we reach this branch
        add_args += [plan.worktree_path, plan.branch_name or ""]
    else:
        add_args += ["-b", plan.branch_name or "", plan.worktree_path, plan.base_ref]

    result = _spawn(add_args, cwd=plan.repo_root)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        if plan.branch_name and _BRANCH_IN_USE_PATTERN.search(stderr):
            raise RuntimeError(f"branch_in_use:{plan.branch_name}")
        raise RuntimeError(stderr or f"worktree_add_failed:{' '.join(add_args[1:])}")

    ensured = EnsureWorktreeResult(
        enabled=True,
        repo_root=plan.repo_root,
        worktree_path=os.path.abspath(plan.worktree_path),
        detached=plan.detached,
        branch_name=plan.branch_name,
        created=True,
        reused=False,
        created_branch=bool(plan.branch_name and not branch_already_existed),
        dirty=None,
    )

    if plan.branch_name:
        _try_upsert_current_task_baseline(
            plan.repo_root,
            branch_name=plan.branch_name,
            worktree_path=ensured.worktree_path,
            base_ref=plan.base_ref,
        )

    return ensured


# ---------------------------------------------------------------------------
# Rollback / removal
# ---------------------------------------------------------------------------


def rollback_provisioned_worktrees(
    results: list[Union[EnsureWorktreeResult, WorktreeDisabled]],
    options: Optional[RollbackWorktreeOptions] = None,
) -> None:
    """Remove every worktree created by a prior `ensure_worktree` call.

    Walks `results` in reverse, calling `git worktree remove --force` for each
    `created` entry and `git branch -D` for created branches that are no longer
    checked out anywhere. Accumulates per-step failures and raises a single
    `worktree_rollback_failed` error when any step failed.
    """
    opts = options or RollbackWorktreeOptions()

    created: list[EnsureWorktreeResult] = [
        r
        for r in results
        if isinstance(r, EnsureWorktreeResult) and r.enabled and r.created
    ]
    created.reverse()

    errors: list[str] = []

    for result in created:
        remove = _spawn(
            ["git", "worktree", "remove", "--force", result.worktree_path],
            cwd=result.repo_root,
        )
        if remove.returncode != 0:
            stderr = (remove.stderr or "").strip()
            errors.append(
                f"remove:{result.worktree_path}:{stderr or f'exit_{remove.returncode}'}"
            )
            continue

        if opts.skip_branch_deletion:
            continue
        if not result.created_branch or not result.branch_name:
            continue

        entries_after_remove = _list_worktrees(result.repo_root)
        still_checked_out = _has_branch_in_use(
            entries_after_remove, result.branch_name, result.worktree_path
        )
        if still_checked_out:
            continue

        delete = _spawn(
            ["git", "branch", "-D", result.branch_name],
            cwd=result.repo_root,
        )
        if delete.returncode != 0 and _branch_exists(
            result.repo_root, result.branch_name
        ):
            stderr = (delete.stderr or "").strip()
            errors.append(
                f"delete_branch:{result.branch_name}:"
                f"{stderr or f'exit_{delete.returncode}'}"
            )

    if errors:
        raise RuntimeError("worktree_rollback_failed:" + " | ".join(errors))


def remove_worktree_force(repo_root: str, worktree_path: str) -> None:
    """Force-remove a worktree via `git worktree remove --force`."""
    result = _spawn(
        ["git", "worktree", "remove", "--force", worktree_path],
        cwd=repo_root,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"worktree_remove_failed:{worktree_path}")


# ---------------------------------------------------------------------------
# Legacy helpers (preserved for back-compat callers in `team.worker_bootstrap`)
# ---------------------------------------------------------------------------


def create_worktree(
    cwd: str,
    branch_name: str,
    worktree_path: str | None = None,
) -> dict[str, Any]:
    """Create a git worktree for a worker.

    Legacy helper — predates the TS-parity `plan_worktree_target` /
    `ensure_worktree` pair. Kept for `team.worker_bootstrap` and any other
    callers that haven't migrated yet.

    Returns:
        Dict with "ok", "path", "branch", and optional "error".
    """
    if worktree_path is None:
        worktree_path = str(Path(cwd).parent / f".omx-worktree-{branch_name}")

    result = subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, worktree_path],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )

    if result.returncode != 0:
        # Try without -b if branch already exists
        result = subprocess.run(
            ["git", "worktree", "add", worktree_path, branch_name],
            capture_output=True,
            text=True,
            cwd=cwd,
            check=False,
        )

    if result.returncode == 0:
        return {"ok": True, "path": worktree_path, "branch": branch_name}
    return {
        "ok": False,
        "error": result.stderr.strip(),
        "path": worktree_path,
        "branch": branch_name,
    }


def remove_worktree(cwd: str, worktree_path: str, force: bool = False) -> bool:
    """Remove a git worktree.

    Legacy helper — returns a bool instead of raising. Prefer
    :func:`remove_worktree_force` for new code.
    """
    args = ["git", "worktree", "remove"]
    if force:
        args.append("--force")
    args.append(worktree_path)

    result = subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=False)
    return result.returncode == 0


def list_worktrees(cwd: str) -> list[dict[str, str]]:
    """List all git worktrees as a list of dicts (legacy shape).

    Prefer the internal `_list_worktrees` + `_GitWorktreeEntry` for new code.
    """
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True,
        text=True,
        cwd=cwd,
        check=False,
    )
    if result.returncode != 0:
        return []

    worktrees: list[dict[str, str]] = []
    current: dict[str, str] = {}

    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line.split(" ", 1)[1]}
        elif line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1].replace("refs/heads/", "")
        elif not line.strip() and current:
            worktrees.append(current)
            current = {}

    if current:
        worktrees.append(current)

    return worktrees


def prune_worktrees(cwd: str) -> None:
    """Prune stale worktree metadata. Best-effort; never raises."""
    subprocess.run(
        ["git", "worktree", "prune"],
        capture_output=True,
        cwd=cwd,
        check=False,
    )
