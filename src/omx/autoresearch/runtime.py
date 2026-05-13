"""Autoresearch runtime — full lifecycle port of ``src/autoresearch/runtime.ts``.

Sync-only port. The original TS code is async because Node's ``fs`` and child-
process APIs are async; the Python equivalents are blocking by design, so all
``await`` calls collapse into direct calls.

Public API (parity with TS):
    - :func:`build_autoresearch_run_tag`
    - :func:`assert_reset_safe_worktree`
    - :func:`count_trailing_autoresearch_noops`
    - :func:`run_autoresearch_evaluator`
    - :func:`decide_autoresearch_outcome`
    - :func:`build_autoresearch_instructions`
    - :func:`materialize_autoresearch_mission_to_worktree`
    - :func:`load_autoresearch_run_manifest`
    - :func:`prepare_autoresearch_runtime`
    - :func:`resume_autoresearch_runtime`
    - :func:`parse_autoresearch_candidate_artifact`
    - :func:`process_autoresearch_candidate`
    - :func:`finalize_autoresearch_run_state`
    - :func:`stop_autoresearch_runtime`

Lightweight legacy API (predates the port, retained for callers):
    - :func:`run_research_loop` — generate/evaluate loop used by simpler clients.

The mode-state lifecycle helpers ``start_mode_state`` / ``update_mode_state``
defined below are local shims that match the on-disk schema written by
``src/modes/base.ts`` (``startMode`` / ``updateModeState``). Python's
``omx.modes.base`` ships read/write helpers but no start/update API yet, so the
autoresearch runtime owns the minimal writers it needs. They are intentionally
narrow and do not perform workflow-transition reconciliation — that lives in
the deferred ``state/workflow-transition`` port.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from omx.autoresearch.contracts import (
    AutoresearchEvaluatorContract,
    AutoresearchKeepPolicy,
    AutoresearchMissionContract,
    ResearchCandidate,
    ResearchMission,
    parse_evaluator_result,
)
from omx.modes.base import cancel_mode, read_mode_state

__all__ = [
    # TS-parity exports
    "AutoresearchCandidateStatus",
    "AutoresearchDecisionStatus",
    "AutoresearchRunStatus",
    "PreparedAutoresearchRuntime",
    "AutoresearchEvaluationRecord",
    "AutoresearchCandidateArtifact",
    "AutoresearchLedgerEntry",
    "AutoresearchRunManifest",
    "AutoresearchDecision",
    "AutoresearchInstructionLedgerSummary",
    "build_autoresearch_run_tag",
    "assert_reset_safe_worktree",
    "count_trailing_autoresearch_noops",
    "run_autoresearch_evaluator",
    "decide_autoresearch_outcome",
    "build_autoresearch_instructions",
    "materialize_autoresearch_mission_to_worktree",
    "load_autoresearch_run_manifest",
    "prepare_autoresearch_runtime",
    "resume_autoresearch_runtime",
    "parse_autoresearch_candidate_artifact",
    "process_autoresearch_candidate",
    "finalize_autoresearch_run_state",
    "stop_autoresearch_runtime",
    # Lightweight legacy export
    "run_research_loop",
    # Constants
    "AUTORESEARCH_RESULTS_HEADER",
    "AUTORESEARCH_WORKTREE_EXCLUDES",
]


# --- String-literal aliases (TS union types) ---------------------------------

AutoresearchCandidateStatus = Literal["candidate", "noop", "abort", "interrupted"]
AutoresearchDecisionStatus = Literal[
    "baseline",
    "keep",
    "discard",
    "ambiguous",
    "noop",
    "abort",
    "interrupted",
    "error",
]
AutoresearchRunStatus = Literal["running", "stopped", "completed", "failed"]


# --- Constants ---------------------------------------------------------------

AUTORESEARCH_RESULTS_HEADER = "iteration\tcommit\tpass\tscore\tstatus\tdescription\n"
AUTORESEARCH_WORKTREE_EXCLUDES = ["results.tsv", "run.log", "node_modules", ".omx/"]


# --- Helpers -----------------------------------------------------------------


def _now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with millisecond precision and ``Z`` suffix.

    Matches the JS ``Date.prototype.toISOString()`` output format.
    """
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _trim_content(value: str, max_chars: int = 4000) -> str:
    """Truncate ``value`` to ``max_chars`` and append an ellipsis when truncated."""
    trimmed = value.strip()
    if len(trimmed) <= max_chars:
        return trimmed
    return f"{trimmed[:max_chars]}\n..."


def _read_git(repo_path: str, args: list[str]) -> str:
    """Run ``git <args>`` capturing stdout. Raises ``RuntimeError`` on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError) as error:
        raise RuntimeError(str(error) or f"git {' '.join(args)} failed") from error
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(stderr or f"git {' '.join(args)} failed")
    return (result.stdout or "").strip()


def _try_resolve_git_commit(worktree_path: str, ref: str) -> str | None:
    """Return resolved commit SHA, or ``None`` if ``ref`` does not resolve."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    resolved = (result.stdout or "").strip()
    return resolved or None


def _require_git_success(worktree_path: str, args: list[str]) -> None:
    """Run a git command and raise ``RuntimeError`` if it fails."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise RuntimeError(str(error) or f"git {' '.join(args)} failed") from error
    if result.returncode == 0:
        return
    raise RuntimeError((result.stderr or "").strip() or f"git {' '.join(args)} failed")


def _git_status_lines(worktree_path: str) -> list[str]:
    """Return non-empty ``git status --porcelain --untracked-files=all`` lines."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as error:
        raise RuntimeError(
            str(error) or f"git status failed for {worktree_path}"
        ) from error
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or "").strip() or f"git status failed for {worktree_path}"
        )
    return [
        line.rstrip()
        for line in re.split(r"\r?\n", result.stdout or "")
        if line.rstrip()
    ]


def _is_allowed_runtime_dirty_line(line: str) -> bool:
    """Return ``True`` for untracked-file status lines pointing at runtime excludes."""
    trimmed = line.strip()
    if len(trimmed) < 4:
        return False
    path = trimmed[3:].strip()
    if not trimmed.startswith("?? "):
        return False
    for exclude in AUTORESEARCH_WORKTREE_EXCLUDES:
        if exclude.endswith("/"):
            stripped = exclude[:-1]
            if path.startswith(exclude) or path == stripped:
                return True
        elif path == exclude:
            return True
    return False


def _read_git_short_head(worktree_path: str) -> str:
    return _read_git(worktree_path, ["rev-parse", "--short=7", "HEAD"])


def _read_git_full_head(worktree_path: str) -> str:
    return _read_git(worktree_path, ["rev-parse", "HEAD"])


# --- Path helpers ------------------------------------------------------------


def _active_run_state_file(project_root: str) -> str:
    return os.path.join(project_root, ".omx", "state", "autoresearch-state.json")


def _ensure_parent_dir(file_path: str) -> None:
    parent = os.path.dirname(file_path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _write_json_file(file_path: str, value: Any) -> None:
    _ensure_parent_dir(file_path)
    payload = json.dumps(value, indent=2)
    with open(file_path, "w", encoding="utf-8") as handle:
        handle.write(payload + "\n")


def _read_json_file(file_path: str) -> Any:
    with open(file_path, encoding="utf-8") as handle:
        return json.loads(handle.read())


# --- Worktree dependency setup ----------------------------------------------


def _write_git_info_exclude(worktree_path: str, pattern: str) -> None:
    """Append ``pattern`` to the worktree's ``.git/info/exclude`` if missing."""
    exclude_path = _read_git(worktree_path, ["rev-parse", "--git-path", "info/exclude"])
    if not os.path.isabs(exclude_path):
        exclude_path = os.path.join(worktree_path, exclude_path)
    existing = ""
    if os.path.exists(exclude_path):
        existing = Path(exclude_path).read_text(encoding="utf-8")
    lines = {line for line in re.split(r"\r?\n", existing) if line}
    if pattern in lines:
        return
    needs_newline = bool(existing) and not existing.endswith("\n")
    next_content = existing + ("\n" if needs_newline else "") + pattern + "\n"
    _ensure_parent_dir(exclude_path)
    Path(exclude_path).write_text(next_content, encoding="utf-8")


def _ensure_runtime_excludes(worktree_path: str) -> None:
    for entry in AUTORESEARCH_WORKTREE_EXCLUDES:
        _write_git_info_exclude(worktree_path, entry)


def _ensure_autoresearch_worktree_dependencies(
    repo_root: str, worktree_path: str
) -> None:
    """Symlink ``node_modules`` from ``repo_root`` into ``worktree_path``.

    No-op when the source is absent or the target already exists. Mirrors TS
    ``ensureAutoresearchWorktreeDependencies``.
    """
    source = os.path.join(repo_root, "node_modules")
    target = os.path.join(worktree_path, "node_modules")
    if not os.path.exists(source) or os.path.lexists(target):
        return
    try:
        os.symlink(source, target, target_is_directory=True)
    except OSError:
        # Windows: symlink may require elevated privileges. Best-effort —
        # match TS which falls back silently when symlink creation fails.
        pass


# --- Reset-safe worktree assertion ------------------------------------------


def assert_reset_safe_worktree(worktree_path: str) -> None:
    """Raise ``RuntimeError`` when the worktree has blocking dirty entries.

    Allowed dirty entries are the runtime excludes (``results.tsv``,
    ``run.log``, ``node_modules``, ``.omx/``).
    """
    lines = _git_status_lines(worktree_path)
    blocking = [line for line in lines if not _is_allowed_runtime_dirty_line(line)]
    if not blocking:
        return
    joined = " | ".join(blocking)
    raise RuntimeError(
        f"autoresearch_reset_requires_clean_worktree:{worktree_path}:{joined}"
    )


# --- Run-tag/run-id ---------------------------------------------------------


def build_autoresearch_run_tag(date: datetime | None = None) -> str:
    """Compact ISO-8601 tag for run identifiers.

    Strips dashes/colons from the ISO date and reduces ``.\\d{3}Z`` to ``Z``.
    """
    if date is None:
        date = datetime.now(timezone.utc)
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    else:
        date = date.astimezone(timezone.utc)
    iso = date.strftime("%Y-%m-%dT%H:%M:%S.") + f"{date.microsecond // 1000:03d}Z"
    iso = iso.replace("-", "").replace(":", "")
    iso = re.sub(r"\.\d{3}Z$", "Z", iso)
    return iso


def _build_run_id(mission_slug: str, run_tag: str) -> str:
    return f"{mission_slug}-{run_tag.lower()}"


# --- Records -----------------------------------------------------------------


@dataclass
class PreparedAutoresearchRuntime:
    """Return shape of :func:`prepare_autoresearch_runtime`."""

    runId: str
    runTag: str
    runDir: str
    instructionsFile: str
    manifestFile: str
    ledgerFile: str
    latestEvaluatorFile: str
    resultsFile: str
    stateFile: str
    candidateFile: str
    repoRoot: str
    worktreePath: str
    taskDescription: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AutoresearchEvaluationRecord:
    """Evaluator invocation record stored next to the run."""

    command: str
    ran_at: str
    status: Literal["pass", "fail", "error"]
    pass_: bool | None = None
    score: float | int | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "command": self.command,
            "ran_at": self.ran_at,
            "status": self.status,
        }
        if self.pass_ is not None:
            d["pass"] = self.pass_
        if self.score is not None:
            d["score"] = self.score
        if self.exit_code is not None:
            d["exit_code"] = self.exit_code
        if self.stdout is not None:
            d["stdout"] = self.stdout
        if self.stderr is not None:
            d["stderr"] = self.stderr
        if self.parse_error is not None:
            d["parse_error"] = self.parse_error
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutoresearchEvaluationRecord:
        return cls(
            command=d["command"],
            ran_at=d["ran_at"],
            status=d["status"],
            pass_=d.get("pass"),
            score=d.get("score"),
            exit_code=d.get("exit_code"),
            stdout=d.get("stdout"),
            stderr=d.get("stderr"),
            parse_error=d.get("parse_error"),
        )


@dataclass
class AutoresearchCandidateArtifact:
    """Decoded ``candidate.json`` artifact emitted by the supervised session."""

    status: AutoresearchCandidateStatus
    candidate_commit: str | None
    base_commit: str
    description: str
    notes: list[str]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "candidate_commit": self.candidate_commit,
            "base_commit": self.base_commit,
            "description": self.description,
            "notes": list(self.notes),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutoresearchCandidateArtifact:
        return cls(
            status=d["status"],
            candidate_commit=d.get("candidate_commit"),
            base_commit=d["base_commit"],
            description=d["description"],
            notes=list(d.get("notes", [])),
            created_at=d["created_at"],
        )


@dataclass
class AutoresearchLedgerEntry:
    """Single ledger record. One entry per iteration plus the baseline row."""

    iteration: int
    kind: Literal["baseline", "iteration"]
    decision: AutoresearchDecisionStatus
    decision_reason: str
    candidate_status: AutoresearchCandidateStatus | Literal["baseline"]
    base_commit: str
    candidate_commit: str | None
    kept_commit: str
    keep_policy: AutoresearchKeepPolicy
    evaluator: AutoresearchEvaluationRecord | None
    created_at: str
    notes: list[str]
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "kind": self.kind,
            "decision": self.decision,
            "decision_reason": self.decision_reason,
            "candidate_status": self.candidate_status,
            "base_commit": self.base_commit,
            "candidate_commit": self.candidate_commit,
            "kept_commit": self.kept_commit,
            "keep_policy": self.keep_policy,
            "evaluator": self.evaluator.to_dict() if self.evaluator else None,
            "created_at": self.created_at,
            "notes": list(self.notes),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutoresearchLedgerEntry:
        ev = d.get("evaluator")
        return cls(
            iteration=int(d["iteration"]),
            kind=d["kind"],
            decision=d["decision"],
            decision_reason=d["decision_reason"],
            candidate_status=d["candidate_status"],
            base_commit=d["base_commit"],
            candidate_commit=d.get("candidate_commit"),
            kept_commit=d["kept_commit"],
            keep_policy=d.get("keep_policy", "score_improvement"),
            evaluator=AutoresearchEvaluationRecord.from_dict(ev)
            if isinstance(ev, dict)
            else None,
            created_at=d["created_at"],
            notes=list(d.get("notes", [])),
            description=d.get("description", ""),
        )


@dataclass
class AutoresearchRunManifest:
    """Persistent run manifest. Schema v1.

    Field names match the TS interface verbatim (snake_case) so on-disk JSON
    is wire-compatible.
    """

    schema_version: int
    run_id: str
    run_tag: str
    mission_dir: str
    mission_file: str
    sandbox_file: str
    repo_root: str
    worktree_path: str
    mission_slug: str
    branch_name: str
    baseline_commit: str
    last_kept_commit: str
    last_kept_score: float | int | None
    latest_candidate_commit: str | None
    results_file: str
    instructions_file: str
    manifest_file: str
    ledger_file: str
    latest_evaluator_file: str
    candidate_file: str
    evaluator: AutoresearchEvaluatorContract
    keep_policy: AutoresearchKeepPolicy
    status: AutoresearchRunStatus
    stop_reason: str | None
    iteration: int
    created_at: str
    updated_at: str
    completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "run_tag": self.run_tag,
            "mission_dir": self.mission_dir,
            "mission_file": self.mission_file,
            "sandbox_file": self.sandbox_file,
            "repo_root": self.repo_root,
            "worktree_path": self.worktree_path,
            "mission_slug": self.mission_slug,
            "branch_name": self.branch_name,
            "baseline_commit": self.baseline_commit,
            "last_kept_commit": self.last_kept_commit,
            "last_kept_score": self.last_kept_score,
            "latest_candidate_commit": self.latest_candidate_commit,
            "results_file": self.results_file,
            "instructions_file": self.instructions_file,
            "manifest_file": self.manifest_file,
            "ledger_file": self.ledger_file,
            "latest_evaluator_file": self.latest_evaluator_file,
            "candidate_file": self.candidate_file,
            "evaluator": self.evaluator.to_dict(),
            "keep_policy": self.keep_policy,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "iteration": self.iteration,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutoresearchRunManifest:
        return cls(
            schema_version=int(d.get("schema_version", 1)),
            run_id=d["run_id"],
            run_tag=d["run_tag"],
            mission_dir=d["mission_dir"],
            mission_file=d["mission_file"],
            sandbox_file=d["sandbox_file"],
            repo_root=d["repo_root"],
            worktree_path=d["worktree_path"],
            mission_slug=d["mission_slug"],
            branch_name=d.get("branch_name", ""),
            baseline_commit=d["baseline_commit"],
            last_kept_commit=d["last_kept_commit"],
            last_kept_score=d.get("last_kept_score"),
            latest_candidate_commit=d.get("latest_candidate_commit"),
            results_file=d["results_file"],
            instructions_file=d["instructions_file"],
            manifest_file=d["manifest_file"],
            ledger_file=d["ledger_file"],
            latest_evaluator_file=d["latest_evaluator_file"],
            candidate_file=d["candidate_file"],
            evaluator=AutoresearchEvaluatorContract.from_dict(d["evaluator"]),
            keep_policy=d.get("keep_policy", "score_improvement"),
            status=d.get("status", "running"),
            stop_reason=d.get("stop_reason"),
            iteration=int(d.get("iteration", 0)),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            completed_at=d.get("completed_at"),
        )


@dataclass
class AutoresearchDecision:
    """Outcome decision for a single iteration."""

    decision: AutoresearchDecisionStatus
    decision_reason: str
    keep: bool
    evaluator: AutoresearchEvaluationRecord | None
    notes: list[str]


@dataclass
class AutoresearchInstructionLedgerSummary:
    """One row of the recent-ledger summary embedded in supervisor instructions."""

    iteration: int
    decision: AutoresearchDecisionStatus
    reason: str
    kept_commit: str
    candidate_commit: str | None
    evaluator_status: Literal["pass", "fail", "error"] | None
    evaluator_score: float | int | None
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "decision": self.decision,
            "reason": self.reason,
            "kept_commit": self.kept_commit,
            "candidate_commit": self.candidate_commit,
            "evaluator_status": self.evaluator_status,
            "evaluator_score": self.evaluator_score,
            "description": self.description,
        }


# --- Active-run state file ---------------------------------------------------


def _read_active_run_state(project_root: str) -> dict[str, Any] | None:
    file_path = _active_run_state_file(project_root)
    if not os.path.exists(file_path):
        return None
    try:
        return _read_json_file(file_path)
    except (OSError, json.JSONDecodeError):
        return None


def _write_active_run_state(project_root: str, value: dict[str, Any]) -> None:
    _write_json_file(_active_run_state_file(project_root), value)


def _assert_autoresearch_lock_available(project_root: str) -> None:
    state = _read_active_run_state(project_root)
    if state and state.get("active") and state.get("run_id"):
        raise RuntimeError(f"autoresearch_active_run_exists:{state['run_id']}")


def _activate_autoresearch_run(manifest: AutoresearchRunManifest) -> None:
    _write_active_run_state(
        manifest.repo_root,
        {
            "schema_version": 1,
            "active": True,
            "run_id": manifest.run_id,
            "mission_slug": manifest.mission_slug,
            "repo_root": manifest.repo_root,
            "worktree_path": manifest.worktree_path,
            "status": manifest.status,
            "updated_at": _now_iso(),
        },
    )


def _deactivate_autoresearch_run(manifest: AutoresearchRunManifest) -> None:
    previous = _read_active_run_state(manifest.repo_root)
    _write_active_run_state(
        manifest.repo_root,
        {
            "schema_version": 1,
            "active": False,
            "run_id": (previous or {}).get("run_id", manifest.run_id),
            "mission_slug": (previous or {}).get("mission_slug", manifest.mission_slug),
            "repo_root": manifest.repo_root,
            "worktree_path": (previous or {}).get(
                "worktree_path", manifest.worktree_path
            ),
            "status": manifest.status,
            "updated_at": _now_iso(),
            "completed_at": _now_iso(),
        },
    )


# --- Local mode-state shims --------------------------------------------------
#
# Python's `omx.modes.base` ships read/write/cancel but not start/update. The
# runtime owns minimal writers that mirror the on-disk schema produced by
# `src/modes/base.ts::startMode` / `updateModeState`. They are intentionally
# narrow and skip workflow-transition reconciliation — that lives in the
# deferred `state/workflow-transition` port. See the module docstring for the
# handoff note.


def _state_dir(project_root: str) -> str:
    return os.path.join(project_root, ".omx", "state")


def _mode_state_path(mode: str, project_root: str) -> str:
    return os.path.join(_state_dir(project_root), f"{mode}-state.json")


def _start_mode_state(
    mode: str, task_description: str, max_iterations: int, project_root: str
) -> dict[str, Any]:
    os.makedirs(_state_dir(project_root), exist_ok=True)
    payload: dict[str, Any] = {
        "active": True,
        "mode": mode,
        "iteration": 0,
        "max_iterations": max_iterations,
        "current_phase": "starting",
        "task_description": task_description,
        "started_at": _now_iso(),
    }
    path = _mode_state_path(mode, project_root)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, indent=2))
    return payload


def _update_mode_state(
    mode: str, updates: dict[str, Any], project_root: str
) -> dict[str, Any]:
    path = _mode_state_path(mode, project_root)
    current: dict[str, Any] = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                current = json.loads(handle.read()) or {}
        except (OSError, json.JSONDecodeError):
            current = {}
    if not current:
        raise RuntimeError(f"Mode {mode} not found")
    merged = {**current, **updates}
    if "run_outcome" not in updates:
        merged.pop("run_outcome", None)
    os.makedirs(_state_dir(project_root), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(merged, indent=2))
    return merged


# --- Results / ledger writers ------------------------------------------------


def _result_pass_value(value: bool | None) -> str:
    if value is None:
        return ""
    return "true" if value else "false"


def _result_score_value(value: float | int | None) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    return str(value)


def _initialize_autoresearch_results_file(results_file: str) -> None:
    if os.path.exists(results_file):
        return
    _ensure_parent_dir(results_file)
    Path(results_file).write_text(AUTORESEARCH_RESULTS_HEADER, encoding="utf-8")


def _append_autoresearch_results_row(
    results_file: str,
    *,
    iteration: int,
    commit: str,
    status: AutoresearchDecisionStatus,
    description: str,
    pass_: bool | None = None,
    score: float | int | None = None,
) -> None:
    if os.path.exists(results_file):
        existing = Path(results_file).read_text(encoding="utf-8")
    else:
        existing = AUTORESEARCH_RESULTS_HEADER
    row = (
        f"{iteration}\t{commit}\t{_result_pass_value(pass_)}\t"
        f"{_result_score_value(score)}\t{status}\t{description}\n"
    )
    Path(results_file).write_text(existing + row, encoding="utf-8")


def _append_autoresearch_ledger_entry(
    ledger_file: str, entry: AutoresearchLedgerEntry
) -> None:
    if os.path.exists(ledger_file):
        try:
            parsed = _read_json_file(ledger_file) or {}
        except (OSError, json.JSONDecodeError):
            parsed = {}
    else:
        parsed = {"schema_version": 1, "entries": []}
    if not isinstance(parsed, dict):
        parsed = {"schema_version": 1, "entries": []}
    entries_raw = parsed.get("entries")
    entries = list(entries_raw) if isinstance(entries_raw, list) else []
    entries.append(entry.to_dict())
    payload = {
        "schema_version": parsed.get("schema_version")
        if isinstance(parsed.get("schema_version"), int)
        else 1,
        "run_id": parsed.get("run_id"),
        "created_at": parsed.get("created_at") or _now_iso(),
        "updated_at": _now_iso(),
        "entries": entries,
    }
    _write_json_file(ledger_file, payload)


def _read_autoresearch_ledger_entries(
    ledger_file: str,
) -> list[AutoresearchLedgerEntry]:
    if not os.path.exists(ledger_file):
        return []
    try:
        parsed = _read_json_file(ledger_file) or {}
    except (OSError, json.JSONDecodeError):
        return []
    raw = parsed.get("entries") if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[AutoresearchLedgerEntry] = []
    for item in raw:
        if isinstance(item, dict):
            try:
                out.append(AutoresearchLedgerEntry.from_dict(item))
            except (KeyError, TypeError, ValueError):
                continue
    return out


def count_trailing_autoresearch_noops(ledger_file: str) -> int:
    """Count consecutive trailing ``noop`` iteration entries."""
    entries = _read_autoresearch_ledger_entries(ledger_file)
    count = 0
    for entry in reversed(entries):
        if entry.kind != "iteration" or entry.decision != "noop":
            break
        count += 1
    return count


# --- Evaluator runner --------------------------------------------------------


def run_autoresearch_evaluator(
    contract: AutoresearchMissionContract,
    worktree_path: str,
    ledger_file: str | None = None,
    latest_evaluator_file: str | None = None,
) -> AutoresearchEvaluationRecord:
    """Run the evaluator command in ``worktree_path`` and capture its output.

    Returns a typed :class:`AutoresearchEvaluationRecord`. When
    ``latest_evaluator_file`` is provided the record is also persisted there;
    when ``ledger_file`` is provided a synthetic ``iteration=-1`` ledger row
    is appended (TS parity — "raw evaluator invocation" diagnostic shape).
    """
    ran_at = _now_iso()
    command = contract.sandbox.evaluator.command

    error: Exception | None = None
    stdout = ""
    stderr = ""
    exit_code: int | None = None
    try:
        result = subprocess.run(
            command,
            cwd=worktree_path,
            shell=True,
            capture_output=True,
            text=True,
            check=False,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        exit_code = result.returncode
    except OSError as err:
        error = err
        stderr = str(err)
        exit_code = None

    record: AutoresearchEvaluationRecord
    if error is not None or (exit_code is not None and exit_code != 0):
        combined_stderr = stderr
        if error is not None:
            combined_stderr = "\n".join(filter(None, [stderr, str(error)]))
        record = AutoresearchEvaluationRecord(
            command=command,
            ran_at=ran_at,
            status="error",
            exit_code=exit_code,
            stdout=stdout,
            stderr=combined_stderr,
        )
    else:
        try:
            parsed = parse_evaluator_result(stdout)
            record = AutoresearchEvaluationRecord(
                command=command,
                ran_at=ran_at,
                status="pass" if parsed.pass_ else "fail",
                pass_=parsed.pass_,
                score=parsed.score,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
            )
        except Exception as parse_error:  # pragma: no cover - defensive
            record = AutoresearchEvaluationRecord(
                command=command,
                ran_at=ran_at,
                status="error",
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                parse_error=str(parse_error),
            )

    if latest_evaluator_file is not None:
        _write_json_file(latest_evaluator_file, record.to_dict())
    if ledger_file is not None:
        head = _read_git_short_head(worktree_path)
        keep_policy = contract.sandbox.evaluator.keep_policy or "score_improvement"
        if record.status == "error":
            decision: AutoresearchDecisionStatus = "error"
        elif record.status == "pass":
            decision = "keep"
        else:
            decision = "discard"
        _append_autoresearch_ledger_entry(
            ledger_file,
            AutoresearchLedgerEntry(
                iteration=-1,
                kind="iteration",
                decision=decision,
                decision_reason="raw evaluator record",
                candidate_status="candidate",
                base_commit=head,
                candidate_commit=None,
                kept_commit=head,
                keep_policy=keep_policy,
                evaluator=record,
                created_at=_now_iso(),
                notes=["raw evaluator invocation"],
                description="raw evaluator record",
            ),
        )
    return record


# --- Decision logic ----------------------------------------------------------


def _comparable_score(previous: float | int | None, nxt: float | int | None) -> bool:
    return (
        isinstance(previous, (int, float))
        and not isinstance(previous, bool)
        and (isinstance(nxt, (int, float)) and not isinstance(nxt, bool))
    )


def decide_autoresearch_outcome(
    manifest: AutoresearchRunManifest | dict[str, Any],
    candidate: AutoresearchCandidateArtifact,
    evaluation: AutoresearchEvaluationRecord | None,
) -> AutoresearchDecision:
    """Decide what to do with a candidate after running the evaluator.

    Mirrors TS ``decideAutoresearchOutcome`` branch-for-branch. Accepts a
    full manifest or a dict with at least ``keep_policy`` and
    ``last_kept_score``.
    """
    if isinstance(manifest, AutoresearchRunManifest):
        keep_policy = manifest.keep_policy
        last_kept_score = manifest.last_kept_score
    else:
        keep_policy = manifest.get("keep_policy", "score_improvement")
        last_kept_score = manifest.get("last_kept_score")

    if candidate.status == "abort":
        return AutoresearchDecision(
            decision="abort",
            decision_reason="candidate requested abort",
            keep=False,
            evaluator=None,
            notes=["run stopped by candidate artifact"],
        )
    if candidate.status == "noop":
        return AutoresearchDecision(
            decision="noop",
            decision_reason="candidate reported noop",
            keep=False,
            evaluator=None,
            notes=["no code change was proposed"],
        )
    if candidate.status == "interrupted":
        return AutoresearchDecision(
            decision="interrupted",
            decision_reason="candidate session was interrupted",
            keep=False,
            evaluator=None,
            notes=["supervisor should inspect worktree cleanliness before continuing"],
        )
    if evaluation is None or evaluation.status == "error":
        return AutoresearchDecision(
            decision="discard",
            decision_reason="evaluator error",
            keep=False,
            evaluator=evaluation,
            notes=["candidate discarded because evaluator errored or crashed"],
        )
    if not evaluation.pass_:
        return AutoresearchDecision(
            decision="discard",
            decision_reason="evaluator reported failure",
            keep=False,
            evaluator=evaluation,
            notes=["candidate discarded because evaluator pass=false"],
        )
    if keep_policy == "pass_only":
        return AutoresearchDecision(
            decision="keep",
            decision_reason="pass_only keep policy accepted evaluator pass=true",
            keep=True,
            evaluator=evaluation,
            notes=["candidate kept because sandbox opted into pass_only policy"],
        )
    if not _comparable_score(last_kept_score, evaluation.score):
        return AutoresearchDecision(
            decision="ambiguous",
            decision_reason="evaluator pass without comparable score",
            keep=False,
            evaluator=evaluation,
            notes=[
                "candidate discarded because score_improvement policy "
                "requires comparable numeric scores"
            ],
        )
    if (evaluation.score or 0) > (last_kept_score or 0):
        return AutoresearchDecision(
            decision="keep",
            decision_reason="score improved over last kept score",
            keep=True,
            evaluator=evaluation,
            notes=["candidate kept because evaluator score increased"],
        )
    return AutoresearchDecision(
        decision="discard",
        decision_reason="score did not improve",
        keep=False,
        evaluator=evaluation,
        notes=[
            "candidate discarded because evaluator score was not better than the kept baseline"
        ],
    )


# --- Instruction generation --------------------------------------------------


def _format_autoresearch_instruction_summary(
    entries: list[AutoresearchLedgerEntry], max_entries: int = 3
) -> list[AutoresearchInstructionLedgerSummary]:
    out: list[AutoresearchInstructionLedgerSummary] = []
    for entry in entries[-max_entries:]:
        ev = entry.evaluator
        out.append(
            AutoresearchInstructionLedgerSummary(
                iteration=entry.iteration,
                decision=entry.decision,
                reason=_trim_content(entry.decision_reason, 160),
                kept_commit=entry.kept_commit,
                candidate_commit=entry.candidate_commit,
                evaluator_status=ev.status if ev else None,
                evaluator_score=ev.score
                if (
                    ev
                    and isinstance(ev.score, (int, float))
                    and not isinstance(ev.score, bool)
                )
                else None,
                description=_trim_content(entry.description, 120),
            )
        )
    return out


def _build_autoresearch_instruction_context(
    manifest: AutoresearchRunManifest,
) -> dict[str, Any]:
    entries = _read_autoresearch_ledger_entries(manifest.ledger_file)
    previous = entries[-1] if entries else None
    previous_iteration_outcome: str | None
    if previous is None:
        previous_iteration_outcome = None
    else:
        previous_iteration_outcome = (
            f"{previous.decision}:{_trim_content(previous.decision_reason, 160)}"
        )
    return {
        "previousIterationOutcome": previous_iteration_outcome,
        "recentLedgerSummary": _format_autoresearch_instruction_summary(entries),
    }


def build_autoresearch_instructions(
    contract: AutoresearchMissionContract,
    *,
    run_id: str,
    iteration: int,
    baseline_commit: str,
    last_kept_commit: str,
    results_file: str,
    candidate_file: str,
    keep_policy: AutoresearchKeepPolicy,
    last_kept_score: float | int | None = None,
    previous_iteration_outcome: str | None = None,
    recent_ledger_summary: list[AutoresearchInstructionLedgerSummary] | None = None,
) -> str:
    """Render the supervisor-instructions markdown for a single iteration."""
    summary_payload = [s.to_dict() for s in (recent_ledger_summary or [])]
    state_snapshot = {
        "iteration": iteration,
        "baseline_commit": baseline_commit,
        "last_kept_commit": last_kept_commit,
        "last_kept_score": last_kept_score if last_kept_score is not None else None,
        "previous_iteration_outcome": previous_iteration_outcome
        if previous_iteration_outcome
        else "none yet",
        "recent_ledger_summary": summary_payload,
        "keep_policy": keep_policy,
    }
    last_kept_score_display = (
        str(last_kept_score)
        if isinstance(last_kept_score, (int, float))
        and not isinstance(last_kept_score, bool)
        else "n/a"
    )
    lines = [
        "# OMX Autoresearch Supervisor Instructions",
        "",
        f"Run ID: {run_id}",
        f"Mission directory: {contract.missionDir}",
        f"Mission file: {contract.missionFile}",
        f"Sandbox file: {contract.sandboxFile}",
        f"Mission slug: {contract.missionSlug}",
        f"Iteration: {iteration}",
        f"Baseline commit: {baseline_commit}",
        f"Last kept commit: {last_kept_commit}",
        f"Last kept score: {last_kept_score_display}",
        f"Results file: {results_file}",
        f"Candidate artifact: {candidate_file}",
        f"Keep policy: {keep_policy}",
        "",
        "Iteration state snapshot:",
        "```json",
        json.dumps(state_snapshot, indent=2),
        "```",
        "",
        "Operate as a thin autoresearch experiment worker for exactly one experiment cycle.",
        "Do not loop forever inside this session. Make at most one candidate commit, then write the candidate artifact JSON and exit.",
        "",
        "Candidate artifact contract:",
        "- Write JSON to the exact candidate artifact path above.",
        "- status: candidate | noop | abort | interrupted",
        "- candidate_commit: string | null",
        "- base_commit: current base commit before your edits",
        "- for status=candidate, candidate_commit must resolve in git and match the worktree HEAD commit when you exit",
        "- base_commit must still match the last kept commit provided above",
        "- description: short one-line summary",
        "- notes: array of short strings",
        "- created_at: ISO timestamp",
        "",
        "Supervisor semantics after you exit:",
        "- status=candidate => evaluator runs, then supervisor keeps or discards and may reset the worktree",
        "- status=noop => supervisor logs a noop iteration and relaunches",
        "- status=abort => supervisor stops the run",
        "- status=interrupted => supervisor inspects worktree safety before deciding how to proceed",
        "",
        "Evaluator contract:",
        f"- command: {contract.sandbox.evaluator.command}",
        "- format: json",
        "- required output field: pass (boolean)",
        "- optional output field: score (number)",
        "",
        "Mission content:",
        "```md",
        _trim_content(contract.missionContent),
        "```",
        "",
        "Sandbox policy:",
        "```md",
        _trim_content(contract.sandbox.body or contract.sandboxContent),
        "```",
    ]
    return "\n".join(lines)


# --- Mission materialization -------------------------------------------------


def materialize_autoresearch_mission_to_worktree(
    contract: AutoresearchMissionContract, worktree_path: str
) -> AutoresearchMissionContract:
    """Copy mission/sandbox markdown into ``worktree_path`` and commit.

    Returns a new contract whose paths point into the worktree.
    """
    mission_dir = os.path.join(worktree_path, contract.missionRelativeDir)
    mission_file = os.path.join(mission_dir, "mission.md")
    sandbox_file = os.path.join(mission_dir, "sandbox.md")

    os.makedirs(mission_dir, exist_ok=True)
    Path(mission_file).write_text(contract.missionContent, encoding="utf-8")
    Path(sandbox_file).write_text(contract.sandboxContent, encoding="utf-8")

    # Commit the materialized files so the worktree is clean for the
    # immediately-following reset-safe assertion. Non-fatal if it fails.
    try:
        subprocess.run(
            ["git", "add", "--", mission_file, sandbox_file],
            cwd=worktree_path,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"autoresearch: materialize mission {contract.missionSlug}",
            ],
            cwd=worktree_path,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
    except OSError:
        # Reset-safe check will surface a clear diagnostic if commit fails.
        pass

    return AutoresearchMissionContract(
        missionDir=mission_dir,
        repoRoot=contract.repoRoot,
        missionFile=mission_file,
        sandboxFile=sandbox_file,
        missionRelativeDir=contract.missionRelativeDir,
        missionContent=contract.missionContent,
        sandboxContent=contract.sandboxContent,
        sandbox=contract.sandbox,
        missionSlug=contract.missionSlug,
    )


# --- Manifest persistence ----------------------------------------------------


def load_autoresearch_run_manifest(
    project_root: str, run_id: str
) -> AutoresearchRunManifest:
    """Load a previously written run manifest by id.

    Raises ``RuntimeError`` if the manifest file is missing.
    """
    manifest_file = os.path.join(
        project_root, ".omx", "logs", "autoresearch", run_id, "manifest.json"
    )
    if not os.path.exists(manifest_file):
        raise RuntimeError(f"autoresearch_resume_manifest_missing:{run_id}")
    raw = _read_json_file(manifest_file)
    return AutoresearchRunManifest.from_dict(raw)


def _write_run_manifest(manifest: AutoresearchRunManifest) -> None:
    manifest.updated_at = _now_iso()
    _write_json_file(manifest.manifest_file, manifest.to_dict())


def _write_instructions_file(
    contract: AutoresearchMissionContract, manifest: AutoresearchRunManifest
) -> None:
    ctx = _build_autoresearch_instruction_context(manifest)
    text = build_autoresearch_instructions(
        contract,
        run_id=manifest.run_id,
        iteration=manifest.iteration + 1,
        baseline_commit=manifest.baseline_commit,
        last_kept_commit=manifest.last_kept_commit,
        results_file=manifest.results_file,
        candidate_file=manifest.candidate_file,
        keep_policy=manifest.keep_policy,
        last_kept_score=manifest.last_kept_score,
        previous_iteration_outcome=ctx["previousIterationOutcome"],
        recent_ledger_summary=ctx["recentLedgerSummary"],
    )
    _ensure_parent_dir(manifest.instructions_file)
    Path(manifest.instructions_file).write_text(text + "\n", encoding="utf-8")


# --- Iteration helpers -------------------------------------------------------


def _record_autoresearch_iteration(
    manifest: AutoresearchRunManifest,
    *,
    status: AutoresearchDecisionStatus,
    decision_reason: str,
    description: str,
    candidate_status: AutoresearchCandidateStatus | Literal["baseline"],
    base_commit: str,
    candidate_commit: str | None,
    notes: list[str],
    kept_commit: str | None = None,
    evaluator: AutoresearchEvaluationRecord | None = None,
    created_at: str | None = None,
) -> None:
    commit = _read_git_short_head(manifest.worktree_path)
    _append_autoresearch_results_row(
        manifest.results_file,
        iteration=manifest.iteration,
        commit=commit,
        pass_=evaluator.pass_ if evaluator else None,
        score=evaluator.score if evaluator else None,
        status=status,
        description=description,
    )
    _append_autoresearch_ledger_entry(
        manifest.ledger_file,
        AutoresearchLedgerEntry(
            iteration=manifest.iteration,
            kind="iteration",
            decision=status,
            decision_reason=decision_reason,
            candidate_status=candidate_status,
            base_commit=base_commit,
            candidate_commit=candidate_commit,
            kept_commit=kept_commit
            if kept_commit is not None
            else manifest.last_kept_commit,
            keep_policy=manifest.keep_policy,
            evaluator=evaluator,
            created_at=created_at or _now_iso(),
            notes=list(notes),
            description=description,
        ),
    )


def _seed_baseline(
    contract: AutoresearchMissionContract, manifest: AutoresearchRunManifest
) -> AutoresearchEvaluationRecord:
    evaluation = run_autoresearch_evaluator(contract, manifest.worktree_path)
    _write_json_file(manifest.latest_evaluator_file, evaluation.to_dict())
    _append_autoresearch_results_row(
        manifest.results_file,
        iteration=0,
        commit=_read_git_short_head(manifest.worktree_path),
        pass_=evaluation.pass_,
        score=evaluation.score,
        status="error" if evaluation.status == "error" else "baseline",
        description="initial baseline evaluation",
    )
    _append_autoresearch_ledger_entry(
        manifest.ledger_file,
        AutoresearchLedgerEntry(
            iteration=0,
            kind="baseline",
            decision="error" if evaluation.status == "error" else "baseline",
            decision_reason="baseline evaluator error"
            if evaluation.status == "error"
            else "baseline established",
            candidate_status="baseline",
            base_commit=manifest.baseline_commit,
            candidate_commit=None,
            kept_commit=manifest.last_kept_commit,
            keep_policy=manifest.keep_policy,
            evaluator=evaluation,
            created_at=_now_iso(),
            notes=["baseline row is always recorded"],
            description="initial baseline evaluation",
        ),
    )
    if (
        evaluation.pass_
        and isinstance(evaluation.score, (int, float))
        and not isinstance(evaluation.score, bool)
    ):
        manifest.last_kept_score = evaluation.score
    else:
        manifest.last_kept_score = None
    _write_run_manifest(manifest)
    _write_instructions_file(contract, manifest)
    return evaluation


# --- Prepare / Resume --------------------------------------------------------


def prepare_autoresearch_runtime(
    contract: AutoresearchMissionContract,
    project_root: str,
    worktree_path: str,
    *,
    run_tag: str | None = None,
) -> PreparedAutoresearchRuntime:
    """Initialize an autoresearch run: manifest, ledger, baseline eval, mode state."""
    _assert_autoresearch_lock_available(project_root)
    _ensure_runtime_excludes(worktree_path)
    _ensure_autoresearch_worktree_dependencies(project_root, worktree_path)
    assert_reset_safe_worktree(worktree_path)

    actual_run_tag = run_tag or build_autoresearch_run_tag()
    run_id = _build_run_id(contract.missionSlug, actual_run_tag)
    baseline_commit = _read_git_short_head(worktree_path)
    try:
        branch_name = _read_git(
            worktree_path, ["symbolic-ref", "--quiet", "--short", "HEAD"]
        )
    except RuntimeError:
        branch_name = ""

    run_dir = os.path.join(project_root, ".omx", "logs", "autoresearch", run_id)
    state_file = _active_run_state_file(project_root)
    instructions_file = os.path.join(run_dir, "bootstrap-instructions.md")
    manifest_file = os.path.join(run_dir, "manifest.json")
    ledger_file = os.path.join(run_dir, "iteration-ledger.json")
    latest_evaluator_file = os.path.join(run_dir, "latest-evaluator-result.json")
    candidate_file = os.path.join(run_dir, "candidate.json")
    results_file = os.path.join(worktree_path, "results.tsv")
    task_description = f"autoresearch {contract.missionRelativeDir} ({run_id})"
    keep_policy = contract.sandbox.evaluator.keep_policy or "score_improvement"

    os.makedirs(run_dir, exist_ok=True)
    _initialize_autoresearch_results_file(results_file)
    _write_json_file(
        candidate_file,
        AutoresearchCandidateArtifact(
            status="noop",
            candidate_commit=None,
            base_commit=baseline_commit,
            description="not-yet-written",
            notes=["candidate artifact will be overwritten by the launched session"],
            created_at=_now_iso(),
        ).to_dict(),
    )

    manifest = AutoresearchRunManifest(
        schema_version=1,
        run_id=run_id,
        run_tag=actual_run_tag,
        mission_dir=contract.missionDir,
        mission_file=contract.missionFile,
        sandbox_file=contract.sandboxFile,
        repo_root=project_root,
        worktree_path=worktree_path,
        mission_slug=contract.missionSlug,
        branch_name=branch_name,
        baseline_commit=baseline_commit,
        last_kept_commit=_read_git_full_head(worktree_path),
        last_kept_score=None,
        latest_candidate_commit=None,
        results_file=results_file,
        instructions_file=instructions_file,
        manifest_file=manifest_file,
        ledger_file=ledger_file,
        latest_evaluator_file=latest_evaluator_file,
        candidate_file=candidate_file,
        evaluator=contract.sandbox.evaluator,
        keep_policy=keep_policy,
        status="running",
        stop_reason=None,
        iteration=0,
        created_at=_now_iso(),
        updated_at=_now_iso(),
        completed_at=None,
    )

    _write_instructions_file(contract, manifest)
    _write_run_manifest(manifest)
    _write_json_file(
        ledger_file,
        {
            "schema_version": 1,
            "run_id": run_id,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "entries": [],
        },
    )
    _write_json_file(
        latest_evaluator_file,
        {
            "run_id": run_id,
            "status": "not-yet-run",
            "updated_at": _now_iso(),
        },
    )

    existing_mode_state = read_mode_state("autoresearch", project_root)
    if existing_mode_state and existing_mode_state.active:
        run_id_in_state = existing_mode_state.extra.get("run_id", "unknown")
        raise RuntimeError(f"autoresearch_active_mode_exists:{run_id_in_state}")
    _start_mode_state("autoresearch", task_description, 1, project_root)
    _activate_autoresearch_run(manifest)
    _update_mode_state(
        "autoresearch",
        {
            "current_phase": "evaluating-baseline",
            "run_id": run_id,
            "run_tag": actual_run_tag,
            "mission_dir": contract.missionDir,
            "mission_file": contract.missionFile,
            "sandbox_file": contract.sandboxFile,
            "mission_slug": contract.missionSlug,
            "repo_root": project_root,
            "worktree_path": worktree_path,
            "baseline_commit": baseline_commit,
            "last_kept_commit": manifest.last_kept_commit,
            "results_file": results_file,
            "manifest_path": manifest_file,
            "iteration_ledger_path": ledger_file,
            "latest_evaluator_result_path": latest_evaluator_file,
            "bootstrap_instructions_path": instructions_file,
            "candidate_path": candidate_file,
            "keep_policy": keep_policy,
            "state_file": state_file,
        },
        project_root,
    )

    evaluation = _seed_baseline(contract, manifest)
    _update_mode_state(
        "autoresearch",
        {
            "current_phase": "running",
            "latest_evaluator_status": evaluation.status,
            "latest_evaluator_pass": evaluation.pass_,
            "latest_evaluator_score": evaluation.score,
            "latest_evaluator_ran_at": evaluation.ran_at,
            "last_kept_commit": manifest.last_kept_commit,
            "last_kept_score": manifest.last_kept_score,
        },
        project_root,
    )

    return PreparedAutoresearchRuntime(
        runId=run_id,
        runTag=actual_run_tag,
        runDir=run_dir,
        instructionsFile=instructions_file,
        manifestFile=manifest_file,
        ledgerFile=ledger_file,
        latestEvaluatorFile=latest_evaluator_file,
        resultsFile=results_file,
        stateFile=state_file,
        candidateFile=candidate_file,
        repoRoot=project_root,
        worktreePath=worktree_path,
        taskDescription=task_description,
    )


def resume_autoresearch_runtime(
    project_root: str, run_id: str
) -> PreparedAutoresearchRuntime:
    """Resume an existing, non-terminal run by id."""
    _assert_autoresearch_lock_available(project_root)
    manifest = load_autoresearch_run_manifest(project_root, run_id)
    if manifest.status != "running":
        raise RuntimeError(f"autoresearch_resume_terminal_run:{run_id}")
    if not os.path.exists(manifest.worktree_path):
        raise RuntimeError(
            f"autoresearch_resume_missing_worktree:{manifest.worktree_path}"
        )
    _ensure_runtime_excludes(manifest.worktree_path)
    _ensure_autoresearch_worktree_dependencies(project_root, manifest.worktree_path)
    assert_reset_safe_worktree(manifest.worktree_path)
    _start_mode_state("autoresearch", f"autoresearch resume {run_id}", 1, project_root)
    _activate_autoresearch_run(manifest)
    _update_mode_state(
        "autoresearch",
        {
            "current_phase": "running",
            "run_id": manifest.run_id,
            "run_tag": manifest.run_tag,
            "mission_dir": manifest.mission_dir,
            "mission_file": manifest.mission_file,
            "sandbox_file": manifest.sandbox_file,
            "mission_slug": manifest.mission_slug,
            "repo_root": manifest.repo_root,
            "worktree_path": manifest.worktree_path,
            "baseline_commit": manifest.baseline_commit,
            "last_kept_commit": manifest.last_kept_commit,
            "last_kept_score": manifest.last_kept_score,
            "results_file": manifest.results_file,
            "manifest_path": manifest.manifest_file,
            "iteration_ledger_path": manifest.ledger_file,
            "latest_evaluator_result_path": manifest.latest_evaluator_file,
            "bootstrap_instructions_path": manifest.instructions_file,
            "candidate_path": manifest.candidate_file,
            "keep_policy": manifest.keep_policy,
            "state_file": _active_run_state_file(project_root),
        },
        project_root,
    )
    return PreparedAutoresearchRuntime(
        runId=manifest.run_id,
        runTag=manifest.run_tag,
        runDir=os.path.dirname(manifest.manifest_file),
        instructionsFile=manifest.instructions_file,
        manifestFile=manifest.manifest_file,
        ledgerFile=manifest.ledger_file,
        latestEvaluatorFile=manifest.latest_evaluator_file,
        resultsFile=manifest.results_file,
        stateFile=_active_run_state_file(project_root),
        candidateFile=manifest.candidate_file,
        repoRoot=manifest.repo_root,
        worktreePath=manifest.worktree_path,
        taskDescription=f"autoresearch resume {run_id}",
    )


# --- Candidate artifact parsing/processing ----------------------------------


def parse_autoresearch_candidate_artifact(raw: str) -> AutoresearchCandidateArtifact:
    """Strict-validate JSON text into an :class:`AutoresearchCandidateArtifact`."""
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise RuntimeError(
            "autoresearch candidate artifact must be valid JSON"
        ) from error
    if not isinstance(parsed, dict):
        raise RuntimeError("autoresearch candidate artifact must be a JSON object")

    status = parsed.get("status")
    if status not in ("candidate", "noop", "abort", "interrupted"):
        raise RuntimeError(
            "autoresearch candidate artifact status must be candidate|noop|abort|interrupted"
        )
    candidate_commit = parsed.get("candidate_commit")
    if candidate_commit is not None and not isinstance(candidate_commit, str):
        raise RuntimeError(
            "autoresearch candidate artifact candidate_commit must be string|null"
        )
    base_commit = parsed.get("base_commit")
    if not isinstance(base_commit, str) or not base_commit.strip():
        raise RuntimeError("autoresearch candidate artifact base_commit is required")
    description = parsed.get("description")
    if not isinstance(description, str):
        raise RuntimeError("autoresearch candidate artifact description is required")
    notes = parsed.get("notes")
    if not isinstance(notes, list) or any(not isinstance(item, str) for item in notes):
        raise RuntimeError(
            "autoresearch candidate artifact notes must be a string array"
        )
    created_at = parsed.get("created_at")
    if not isinstance(created_at, str) or not created_at.strip():
        raise RuntimeError("autoresearch candidate artifact created_at is required")

    return AutoresearchCandidateArtifact(
        status=status,
        candidate_commit=candidate_commit,
        base_commit=base_commit,
        description=description,
        notes=list(notes),
        created_at=created_at,
    )


def _read_candidate_artifact(candidate_file: str) -> AutoresearchCandidateArtifact:
    if not os.path.exists(candidate_file):
        raise RuntimeError(f"autoresearch_candidate_missing:{candidate_file}")
    raw = Path(candidate_file).read_text(encoding="utf-8")
    return parse_autoresearch_candidate_artifact(raw)


def _finalize_run(
    manifest: AutoresearchRunManifest,
    project_root: str,
    *,
    status: AutoresearchRunStatus,
    stop_reason: str,
) -> None:
    manifest.status = status
    manifest.stop_reason = stop_reason
    manifest.completed_at = _now_iso()
    _write_run_manifest(manifest)
    _update_mode_state(
        "autoresearch",
        {
            "active": False,
            "current_phase": status,
            "completed_at": manifest.completed_at,
            "stop_reason": stop_reason,
        },
        project_root,
    )
    _deactivate_autoresearch_run(manifest)


def _reset_to_last_kept_commit(manifest: AutoresearchRunManifest) -> None:
    assert_reset_safe_worktree(manifest.worktree_path)
    _require_git_success(
        manifest.worktree_path, ["reset", "--hard", manifest.last_kept_commit]
    )


def _validate_autoresearch_candidate(
    manifest: AutoresearchRunManifest, candidate: AutoresearchCandidateArtifact
) -> tuple[AutoresearchCandidateArtifact | None, str | None]:
    """Validate a candidate artifact against the live worktree.

    Returns ``(candidate, None)`` on success or ``(None, reason)`` on failure.
    """
    resolved_base = _try_resolve_git_commit(
        manifest.worktree_path, candidate.base_commit
    )
    if not resolved_base:
        return (
            None,
            f"candidate base_commit does not resolve in git: {candidate.base_commit}",
        )
    if resolved_base != manifest.last_kept_commit:
        return None, (
            f"candidate base_commit {resolved_base} does not match last kept commit "
            f"{manifest.last_kept_commit}"
        )

    if candidate.status != "candidate":
        return AutoresearchCandidateArtifact(
            status=candidate.status,
            candidate_commit=candidate.candidate_commit,
            base_commit=resolved_base,
            description=candidate.description,
            notes=list(candidate.notes),
            created_at=candidate.created_at,
        ), None

    if not candidate.candidate_commit:
        return None, "candidate status requires a non-null candidate_commit"
    resolved_candidate = _try_resolve_git_commit(
        manifest.worktree_path, candidate.candidate_commit
    )
    if not resolved_candidate:
        return (
            None,
            f"candidate_commit does not resolve in git: {candidate.candidate_commit}",
        )
    head = _read_git_full_head(manifest.worktree_path)
    if resolved_candidate != head:
        return None, (
            f"candidate_commit {resolved_candidate} does not match worktree HEAD {head}"
        )

    return AutoresearchCandidateArtifact(
        status=candidate.status,
        candidate_commit=resolved_candidate,
        base_commit=resolved_base,
        description=candidate.description,
        notes=list(candidate.notes),
        created_at=candidate.created_at,
    ), None


def _fail_autoresearch_iteration(
    manifest: AutoresearchRunManifest,
    project_root: str,
    reason: str,
    candidate: AutoresearchCandidateArtifact | None = None,
) -> Literal["error"]:
    try:
        head_commit = _read_git_short_head(manifest.worktree_path)
    except RuntimeError:
        head_commit = manifest.baseline_commit

    _append_autoresearch_results_row(
        manifest.results_file,
        iteration=manifest.iteration,
        commit=head_commit,
        status="error",
        description=(
            candidate.description if candidate else "candidate validation failed"
        ),
    )
    _append_autoresearch_ledger_entry(
        manifest.ledger_file,
        AutoresearchLedgerEntry(
            iteration=manifest.iteration,
            kind="iteration",
            decision="error",
            decision_reason=reason,
            candidate_status=(candidate.status if candidate else "candidate"),
            base_commit=(
                candidate.base_commit if candidate else manifest.last_kept_commit
            ),
            candidate_commit=(candidate.candidate_commit if candidate else None),
            kept_commit=manifest.last_kept_commit,
            keep_policy=manifest.keep_policy,
            evaluator=None,
            created_at=_now_iso(),
            notes=list(candidate.notes if candidate else [])
            + [f"validation_error:{reason}"],
            description=(
                candidate.description if candidate else "candidate validation failed"
            ),
        ),
    )
    _finalize_run(manifest, project_root, status="failed", stop_reason=reason)
    return "error"


def _record_non_evaluated_candidate_status(
    contract: AutoresearchMissionContract,
    manifest: AutoresearchRunManifest,
    project_root: str,
    candidate: AutoresearchCandidateArtifact,
) -> AutoresearchDecisionStatus:
    shared_kwargs: dict[str, Any] = {
        "description": candidate.description,
        "candidate_status": candidate.status,
        "base_commit": candidate.base_commit,
        "candidate_commit": candidate.candidate_commit,
        "notes": list(candidate.notes),
    }
    if candidate.status == "abort":
        _record_autoresearch_iteration(
            manifest,
            status="abort",
            decision_reason="candidate requested abort",
            **shared_kwargs,
        )
        _finalize_run(
            manifest, project_root, status="stopped", stop_reason="candidate abort"
        )
        return "abort"

    if candidate.status == "interrupted":
        try:
            assert_reset_safe_worktree(manifest.worktree_path)
        except RuntimeError:
            _finalize_run(
                manifest,
                project_root,
                status="failed",
                stop_reason="interrupted dirty worktree requires operator intervention",
            )
            return "error"
        _record_autoresearch_iteration(
            manifest,
            status="interrupted",
            decision_reason="candidate session interrupted cleanly",
            **shared_kwargs,
        )
        _write_run_manifest(manifest)
        _write_instructions_file(contract, manifest)
        return "interrupted"

    _record_autoresearch_iteration(
        manifest,
        status="noop",
        decision_reason="candidate reported noop",
        **shared_kwargs,
    )
    _write_run_manifest(manifest)
    _write_instructions_file(contract, manifest)
    return "noop"


def process_autoresearch_candidate(
    contract: AutoresearchMissionContract,
    manifest: AutoresearchRunManifest,
    project_root: str,
) -> AutoresearchDecisionStatus:
    """Process the candidate artifact at ``manifest.candidate_file`` for one iteration."""
    manifest.iteration += 1
    try:
        candidate = _read_candidate_artifact(manifest.candidate_file)
    except Exception as error:
        return _fail_autoresearch_iteration(manifest, project_root, str(error))

    validated, reason = _validate_autoresearch_candidate(manifest, candidate)
    if validated is None or reason is not None:
        return _fail_autoresearch_iteration(
            manifest, project_root, reason or "candidate validation failed", candidate
        )
    candidate = validated
    manifest.latest_candidate_commit = candidate.candidate_commit

    if candidate.status != "candidate":
        return _record_non_evaluated_candidate_status(
            contract, manifest, project_root, candidate
        )

    evaluation = run_autoresearch_evaluator(contract, manifest.worktree_path)
    _write_json_file(manifest.latest_evaluator_file, evaluation.to_dict())
    decision = decide_autoresearch_outcome(manifest, candidate, evaluation)
    if decision.keep:
        manifest.last_kept_commit = _read_git_full_head(manifest.worktree_path)
        if isinstance(evaluation.score, (int, float)) and not isinstance(
            evaluation.score, bool
        ):
            manifest.last_kept_score = evaluation.score
    else:
        _reset_to_last_kept_commit(manifest)

    _record_autoresearch_iteration(
        manifest,
        status=decision.decision,
        decision_reason=decision.decision_reason,
        description=candidate.description,
        candidate_status=candidate.status,
        base_commit=candidate.base_commit,
        candidate_commit=candidate.candidate_commit,
        evaluator=evaluation,
        notes=list(candidate.notes) + list(decision.notes),
    )
    _write_run_manifest(manifest)
    _write_instructions_file(contract, manifest)
    _update_mode_state(
        "autoresearch",
        {
            "current_phase": "running",
            "iteration": manifest.iteration,
            "last_kept_commit": manifest.last_kept_commit,
            "last_kept_score": manifest.last_kept_score,
            "latest_evaluator_status": evaluation.status,
            "latest_evaluator_pass": evaluation.pass_,
            "latest_evaluator_score": evaluation.score,
            "latest_evaluator_ran_at": evaluation.ran_at,
        },
        project_root,
    )
    return decision.decision


def finalize_autoresearch_run_state(
    project_root: str,
    run_id: str,
    *,
    status: AutoresearchRunStatus,
    stop_reason: str,
) -> None:
    """Mark a non-terminal run as terminal and deactivate it."""
    manifest = load_autoresearch_run_manifest(project_root, run_id)
    if manifest.status != "running":
        return
    _finalize_run(manifest, project_root, status=status, stop_reason=stop_reason)


def stop_autoresearch_runtime(project_root: str) -> None:
    """Operator-driven stop. Finalizes if a run is active, else cancels the mode."""
    state = read_mode_state("autoresearch", project_root)
    if not state or not state.active:
        return
    run_id = state.extra.get("run_id") if state.extra else None
    if isinstance(run_id, str) and run_id:
        finalize_autoresearch_run_state(
            project_root, run_id, status="stopped", stop_reason="operator stop"
        )
        return
    cancel_mode("autoresearch", project_root)


# --- Lightweight legacy loop -------------------------------------------------


def run_research_loop(
    mission: ResearchMission,
    generate: Callable[[ResearchMission, list[ResearchCandidate]], ResearchCandidate],
    evaluate: Callable[[ResearchCandidate, ResearchMission], float],
    *,
    on_iteration: Callable[[int, ResearchCandidate], None] | None = None,
) -> list[ResearchCandidate]:
    """Run a generate/evaluate research loop until completion.

    Terminates when ``max_iterations`` is reached or a candidate scores ``>= 1.0``.
    This is the legacy lightweight loop predating the full TS port and is kept
    for callers that depend on it.
    """
    candidates: list[ResearchCandidate] = []
    for i in range(1, mission.max_iterations + 1):
        candidate = generate(mission, candidates)
        candidate.iteration = i
        candidate.score = evaluate(candidate, mission)
        candidates.append(candidate)
        if on_iteration:
            on_iteration(i, candidate)
        if candidate.score >= 1.0:
            break
    return candidates
