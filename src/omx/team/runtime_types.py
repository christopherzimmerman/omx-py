"""Team runtime dataclass contracts.

Port of the type-only exports from ``src/team/runtime.ts``:

- ``TeamSnapshot`` (lines 150-181)
- ``TeamRuntime`` (lines 296-302)
- ``ShutdownOptions`` (lines 304-307, internal in TS — exposed here for tests)
- ``TeamShutdownSummary`` (lines 309-311)
- ``StaleTeamSummary`` (lines 1212-1217)
- ``TeamStartOptions`` (lines 1219-1224)

Phase 2.0 scope: type contracts only. Several fields reference yet-to-be-ported
support modules (``TeamConfig``, ``WorkerStatus``, ``WorkerHeartbeat``,
``TeamTask`` V2, ``WorktreeMode``, ``CleanupResult``, ``TeamCommitHygieneArtifactPaths``).
These are typed as ``dict[str, Any]`` or ``Any`` with ``# TODO:`` markers and will
be tightened once the supporting modules land.

All five public dataclasses provide ``to_dict()`` / ``from_dict()`` for
round-trip serialization, following the convention in ``team.contracts``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from omx.team.contracts import TeamTask


# --- TeamSnapshot ----------------------------------------------------------


@dataclass
class TeamSnapshotWorker:
    """Per-worker entry inside a ``TeamSnapshot.workers`` list.

    Mirrors the inline TS object at ``runtime.ts:153-160``.

    Attributes:
        name: Worker name.
        alive: Whether the worker pane is still alive.
        status: Worker status payload. Sourced from
            ``team.state.io.read_worker_status`` which returns
            ``dict[str, Any] | None``.
            # TODO: tighten when WorkerStatus dataclass lands
        heartbeat: Worker heartbeat payload, or ``None`` if not yet reported.
            # TODO: tighten when WorkerHeartbeat dataclass lands
        assigned_tasks: Task IDs assigned to this worker.
        turns_without_progress: Consecutive monitor cycles without observable
            progress.
    """

    name: str
    alive: bool
    status: dict[str, Any]
    heartbeat: dict[str, Any] | None
    assigned_tasks: list[str] = field(default_factory=list)
    turns_without_progress: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "alive": self.alive,
            "status": self.status,
            "heartbeat": self.heartbeat,
            "assigned_tasks": list(self.assigned_tasks),
            "turns_without_progress": self.turns_without_progress,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamSnapshotWorker:
        return cls(
            name=d["name"],
            alive=bool(d.get("alive", False)),
            status=d.get("status") or {},
            heartbeat=d.get("heartbeat"),
            assigned_tasks=list(d.get("assigned_tasks") or []),
            turns_without_progress=int(d.get("turns_without_progress", 0)),
        )


@dataclass
class TeamSnapshotTasks:
    """Task-count rollup inside a ``TeamSnapshot``.

    Mirrors the inline TS object at ``runtime.ts:161-169``.
    """

    total: int = 0
    pending: int = 0
    blocked: int = 0
    in_progress: int = 0
    completed: int = 0
    failed: int = 0
    items: list[TeamTask] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "pending": self.pending,
            "blocked": self.blocked,
            "in_progress": self.in_progress,
            "completed": self.completed,
            "failed": self.failed,
            "items": [t.to_dict() for t in self.items],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamSnapshotTasks:
        raw_items = d.get("items") or []
        items: list[TeamTask] = []
        for raw in raw_items:
            if isinstance(raw, TeamTask):
                items.append(raw)
            elif isinstance(raw, dict):
                items.append(TeamTask.from_dict(raw))
        return cls(
            total=int(d.get("total", 0)),
            pending=int(d.get("pending", 0)),
            blocked=int(d.get("blocked", 0)),
            in_progress=int(d.get("in_progress", 0)),
            completed=int(d.get("completed", 0)),
            failed=int(d.get("failed", 0)),
            items=items,
        )


@dataclass
class TeamSnapshotPerformance:
    """Optional perf section inside a ``TeamSnapshot``.

    Mirrors the optional inline TS object at ``runtime.ts:174-180``.
    """

    list_tasks_ms: float = 0.0
    worker_scan_ms: float = 0.0
    mailbox_delivery_ms: float = 0.0
    total_ms: float = 0.0
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "list_tasks_ms": self.list_tasks_ms,
            "worker_scan_ms": self.worker_scan_ms,
            "mailbox_delivery_ms": self.mailbox_delivery_ms,
            "total_ms": self.total_ms,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamSnapshotPerformance:
        return cls(
            list_tasks_ms=float(d.get("list_tasks_ms", 0.0)),
            worker_scan_ms=float(d.get("worker_scan_ms", 0.0)),
            mailbox_delivery_ms=float(d.get("mailbox_delivery_ms", 0.0)),
            total_ms=float(d.get("total_ms", 0.0)),
            updated_at=str(d.get("updated_at", "")),
        )


@dataclass
class TeamSnapshot:
    """Snapshot of a team's state at one monitor tick.

    Port of ``TeamSnapshot`` (``runtime.ts:150-181``).

    The ``phase`` field accepts any ``TeamPhase | TerminalPhase`` string. We
    keep it as ``str`` rather than an enum because terminal phases (``complete``,
    ``failed``, ``cancelled``) and pipeline phases (``team-plan``, ``team-prd``,
    ...) are not yet unified into a single Python enum.

    Attributes:
        team_name: Team identifier.
        phase: Current phase string (one of ``TEAM_PHASES`` or terminal).
        workers: Per-worker snapshots.
        tasks: Task-status rollup with item list.
        all_tasks_terminal: True iff every task is in a terminal state.
        dead_workers: Names of workers whose pane is gone.
        non_reporting_workers: Names of workers that have not emitted a
            heartbeat within the staleness window.
        recommendations: Human-readable hints rendered by the leader.
        performance: Optional perf rollup (omitted when not measured).
    """

    team_name: str
    phase: str
    workers: list[TeamSnapshotWorker] = field(default_factory=list)
    tasks: TeamSnapshotTasks = field(default_factory=TeamSnapshotTasks)
    all_tasks_terminal: bool = False
    dead_workers: list[str] = field(default_factory=list)
    non_reporting_workers: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    performance: TeamSnapshotPerformance | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "team_name": self.team_name,
            "phase": self.phase,
            "workers": [w.to_dict() for w in self.workers],
            "tasks": self.tasks.to_dict(),
            "all_tasks_terminal": self.all_tasks_terminal,
            "dead_workers": list(self.dead_workers),
            "non_reporting_workers": list(self.non_reporting_workers),
            "recommendations": list(self.recommendations),
        }
        if self.performance is not None:
            d["performance"] = self.performance.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamSnapshot:
        raw_workers = d.get("workers") or []
        workers = [
            w if isinstance(w, TeamSnapshotWorker) else TeamSnapshotWorker.from_dict(w)
            for w in raw_workers
        ]
        raw_tasks = d.get("tasks")
        tasks = (
            raw_tasks
            if isinstance(raw_tasks, TeamSnapshotTasks)
            else TeamSnapshotTasks.from_dict(raw_tasks or {})
        )
        raw_perf = d.get("performance")
        performance: TeamSnapshotPerformance | None
        if raw_perf is None:
            performance = None
        elif isinstance(raw_perf, TeamSnapshotPerformance):
            performance = raw_perf
        else:
            performance = TeamSnapshotPerformance.from_dict(raw_perf)
        return cls(
            team_name=d["team_name"],
            phase=str(d.get("phase", "")),
            workers=workers,
            tasks=tasks,
            all_tasks_terminal=bool(d.get("all_tasks_terminal", False)),
            dead_workers=list(d.get("dead_workers") or []),
            non_reporting_workers=list(d.get("non_reporting_workers") or []),
            recommendations=list(d.get("recommendations") or []),
            performance=performance,
        )


# --- TeamRuntime -----------------------------------------------------------


@dataclass
class TeamRuntime:
    """Runtime handle returned by ``startTeam``.

    Port of ``TeamRuntime`` (``runtime.ts:296-302``).

    Attributes:
        team_name: Team name as supplied by the caller.
        sanitized_name: Filesystem-safe team identifier (output of
            ``sanitize_team_name``).
        session_name: Tmux session name backing the team.
        config: TeamConfig payload.
            # TODO: tighten when TeamConfig dataclass lands
        cwd: Absolute working directory the team was launched from.
    """

    team_name: str
    sanitized_name: str
    session_name: str
    config: dict[str, Any] = field(default_factory=dict)
    cwd: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_name": self.team_name,
            "sanitized_name": self.sanitized_name,
            "session_name": self.session_name,
            "config": self.config,
            "cwd": self.cwd,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamRuntime:
        return cls(
            team_name=d["team_name"],
            sanitized_name=d["sanitized_name"],
            session_name=d["session_name"],
            config=d.get("config") or {},
            cwd=d.get("cwd", ""),
        )


# --- ShutdownOptions -------------------------------------------------------


@dataclass
class ShutdownOptions:
    """Options accepted by ``shutdownTeam``.

    Port of the internal ``ShutdownOptions`` interface (``runtime.ts:304-307``).
    Exposed publicly here so callers and tests share one shape.

    Attributes:
        force: When True, skip the shutdown-gate classification entirely.
        confirm_issues: When True, allow shutdown even though tasks are in
            ``failed`` state. Default ``False`` mirrors the TS contract.
    """

    force: bool = False
    confirm_issues: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"force": self.force, "confirm_issues": self.confirm_issues}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShutdownOptions:
        return cls(
            force=bool(d.get("force", False)),
            confirm_issues=bool(d.get("confirm_issues", False)),
        )


# --- TeamShutdownSummary ---------------------------------------------------


@dataclass
class TeamShutdownSummary:
    """Result of ``shutdownTeam``.

    Port of ``TeamShutdownSummary`` (``runtime.ts:309-311``).

    Attributes:
        commit_hygiene_artifacts: Paths to the commit-hygiene report
            artifacts written during shutdown, or ``None`` if hygiene was
            skipped. Shape mirrors TS ``TeamCommitHygieneArtifactPaths``:
            ``{"json_path": str, "markdown_path": str}``.
            # TODO: tighten when TeamCommitHygieneArtifactPaths dataclass lands
    """

    commit_hygiene_artifacts: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"commit_hygiene_artifacts": self.commit_hygiene_artifacts}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamShutdownSummary:
        raw = d.get("commit_hygiene_artifacts")
        if raw is None:
            return cls(commit_hygiene_artifacts=None)
        if not isinstance(raw, dict):
            return cls(commit_hygiene_artifacts=None)
        # Accept both camelCase TS keys and snake_case Python keys.
        json_path = raw.get("json_path") or raw.get("jsonPath")
        markdown_path = raw.get("markdown_path") or raw.get("markdownPath")
        artifacts: dict[str, str] = {}
        if isinstance(json_path, str):
            artifacts["json_path"] = json_path
        if isinstance(markdown_path, str):
            artifacts["markdown_path"] = markdown_path
        return cls(commit_hygiene_artifacts=artifacts or None)


# --- StaleTeamSummary ------------------------------------------------------


@dataclass
class StaleTeamSummary:
    """Summary of a stale-team detection.

    Port of ``StaleTeamSummary`` (``runtime.ts:1212-1217``). Passed to a
    ``confirm_stale_cleanup`` callback so the caller can decide whether to
    reap the leftover team state.

    Attributes:
        team_name: Stale team identifier.
        worktree_paths: Worktree paths still on disk for this team.
        state_path: Path to the team's state directory.
        has_dirty_worktrees: ``True`` when any worktree has uncommitted
            changes.
    """

    team_name: str
    worktree_paths: list[str] = field(default_factory=list)
    state_path: str = ""
    has_dirty_worktrees: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_name": self.team_name,
            "worktree_paths": list(self.worktree_paths),
            "state_path": self.state_path,
            "has_dirty_worktrees": self.has_dirty_worktrees,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StaleTeamSummary:
        return cls(
            team_name=d["team_name"],
            worktree_paths=list(d.get("worktree_paths") or []),
            state_path=d.get("state_path", ""),
            has_dirty_worktrees=bool(d.get("has_dirty_worktrees", False)),
        )


# --- TeamStartOptions ------------------------------------------------------


@dataclass
class TeamStartOptions:
    """Options accepted by ``startTeam``.

    Port of ``TeamStartOptions`` (``runtime.ts:1219-1224``).

    All fields are optional. Sync conversion: the TS callbacks are
    ``Promise``-returning; here they are synchronous callables.

    Attributes:
        worktree_mode: Optional worktree configuration. Modeled as a loose
            dict for now (the TS type is a discriminated union).
            # TODO: tighten when WorktreeMode dataclass lands
        confirm_stale_cleanup: Optional callback. Receives a
            ``StaleTeamSummary`` and must return ``True`` to allow cleanup.
        cleanup_launch_orphaned_mcp_processes: Optional callback to perform
            MCP-orphan cleanup before launching workers. Returns a cleanup
            result dict.
            # TODO: tighten when CleanupResult dataclass lands
        write_cleanup_warning: Optional sink for warnings emitted during
            pre-launch MCP cleanup. Defaults to stderr in the helper.
    """

    worktree_mode: dict[str, Any] | None = None
    confirm_stale_cleanup: Callable[[StaleTeamSummary], bool] | None = None
    cleanup_launch_orphaned_mcp_processes: Callable[[], dict[str, Any]] | None = None
    write_cleanup_warning: Callable[[str], None] | None = None

    def to_dict(self) -> dict[str, Any]:
        # Callables are intentionally omitted from the serialized form.
        d: dict[str, Any] = {}
        if self.worktree_mode is not None:
            d["worktree_mode"] = self.worktree_mode
        d["has_confirm_stale_cleanup"] = self.confirm_stale_cleanup is not None
        d["has_cleanup_launch_orphaned_mcp_processes"] = (
            self.cleanup_launch_orphaned_mcp_processes is not None
        )
        d["has_write_cleanup_warning"] = self.write_cleanup_warning is not None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamStartOptions:
        # Callbacks cannot be reconstructed from a dict; ignore the markers.
        return cls(worktree_mode=d.get("worktree_mode"))


__all__ = [
    "ShutdownOptions",
    "StaleTeamSummary",
    "TeamRuntime",
    "TeamShutdownSummary",
    "TeamSnapshot",
    "TeamSnapshotPerformance",
    "TeamSnapshotTasks",
    "TeamSnapshotWorker",
    "TeamStartOptions",
]
