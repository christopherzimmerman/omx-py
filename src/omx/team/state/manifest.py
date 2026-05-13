"""Team manifest V2 — read/write/init for the canonical team manifest.

Port of src/team/state.ts (V2 manifest portion only).

Locked decisions:
  - V2 only. No V1 fallback. See PORT_PLAN.md Locked Decision #2.
  - Sync I/O only. No asyncio.

State layout (aligned with `team.state.io`):
  .omx/team/{team_name}/
    manifest.v2.json    -- this module
    config.json          -- initial V1-shape config written by `init_team_state`
                            for parity with TS initTeamState
    workers/{worker_name}/
    tasks/
    claims/
    mailbox/
    dispatch/requests.json
    events/
    approvals/
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.team.state.atomic import write_atomic
from omx.team.state.policy import (
    normalize_team_governance,
    normalize_team_policy,
)
from omx.team.state.types import (
    ABSOLUTE_MAX_WORKERS,
    DEFAULT_MAX_WORKERS,
    WorkerInfo,
)

# --- Constants ---

TEAM_NAME_SAFE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")


# --- Dataclasses ---


@dataclass
class TeamLeader:
    """Leader identity associated with a team manifest."""

    session_id: str = ""
    worker_id: str = "leader-fixed"
    role: str = "coordinator"
    thread_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "session_id": self.session_id,
            "worker_id": self.worker_id,
            "role": self.role,
        }
        if self.thread_id is not None:
            d["thread_id"] = self.thread_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamLeader:
        return cls(
            session_id=d.get("session_id", ""),
            worker_id=d.get("worker_id", "leader-fixed"),
            role=d.get("role", "coordinator"),
            thread_id=d.get("thread_id"),
        )


@dataclass
class PermissionsSnapshot:
    """Captured approval/sandbox/network posture at team init time."""

    approval_mode: str = "unknown"
    sandbox_mode: str = "unknown"
    network_access: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "approval_mode": self.approval_mode,
            "sandbox_mode": self.sandbox_mode,
            "network_access": self.network_access,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PermissionsSnapshot:
        return cls(
            approval_mode=d.get("approval_mode", "unknown"),
            sandbox_mode=d.get("sandbox_mode", "unknown"),
            network_access=bool(d.get("network_access", True)),
        )


@dataclass
class TeamManifestV2:
    """V2 team manifest — schema_version=2.

    Policy and governance are held as loose dicts on this dataclass to keep
    the IO layer decoupled from the policy types. They are normalized via
    `normalize_team_policy` / `normalize_team_governance` on read and write.

    # TODO: tighten to TeamPolicy/TeamGovernance once policy.py is consumed
    # everywhere — kept loose here so existing callers can stay dict-shaped.
    """

    name: str
    task: str
    leader: TeamLeader
    permissions_snapshot: PermissionsSnapshot
    tmux_session: str
    worker_count: int
    workers: list[WorkerInfo]
    next_task_id: int
    created_at: str
    schema_version: int = 2
    lifecycle_profile: str = "default"
    # TODO: tighten to TeamPolicy/TeamGovernance once policy.py lands
    policy: dict[str, Any] | None = None
    # TODO: tighten to TeamPolicy/TeamGovernance once policy.py lands
    governance: dict[str, Any] | None = None
    leader_cwd: str | None = None
    team_state_root: str | None = None
    workspace_mode: str | None = None  # 'single' | 'worktree'
    worktree_mode: str | None = None
    leader_pane_id: str | None = None
    hud_pane_id: str | None = None
    resize_hook_name: str | None = None
    resize_hook_target: str | None = None
    next_worker_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        normalized_policy = normalize_team_policy(self.policy).to_dict()
        normalized_governance = normalize_team_governance(
            self.governance, legacy_policy=self.policy
        ).to_dict()
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "name": self.name,
            "task": self.task,
            "leader": self.leader.to_dict(),
            "policy": normalized_policy,
            "governance": normalized_governance,
            "lifecycle_profile": "default",
            "permissions_snapshot": self.permissions_snapshot.to_dict(),
            "tmux_session": self.tmux_session,
            "worker_count": self.worker_count,
            "workers": [w.to_dict() for w in self.workers],
            "next_task_id": self.next_task_id,
            "created_at": self.created_at,
            "leader_pane_id": self.leader_pane_id,
            "hud_pane_id": self.hud_pane_id,
            "resize_hook_name": self.resize_hook_name,
            "resize_hook_target": self.resize_hook_target,
        }
        for opt in (
            "leader_cwd",
            "team_state_root",
            "workspace_mode",
            "worktree_mode",
            "next_worker_index",
        ):
            v = getattr(self, opt)
            if v is not None:
                d[opt] = v
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamManifestV2:
        return cls(
            schema_version=int(d.get("schema_version", 2)),
            name=d["name"],
            task=d.get("task", ""),
            leader=TeamLeader.from_dict(d.get("leader", {})),
            permissions_snapshot=PermissionsSnapshot.from_dict(
                d.get("permissions_snapshot", {})
            ),
            tmux_session=d.get("tmux_session", ""),
            worker_count=int(d.get("worker_count", 0)),
            workers=[WorkerInfo.from_dict(w) for w in d.get("workers", [])],
            next_task_id=int(d.get("next_task_id", 1)),
            created_at=d.get("created_at", ""),
            lifecycle_profile=d.get("lifecycle_profile", "default"),
            policy=d.get("policy"),
            governance=d.get("governance"),
            leader_cwd=d.get("leader_cwd"),
            team_state_root=d.get("team_state_root"),
            workspace_mode=d.get("workspace_mode"),
            worktree_mode=d.get("worktree_mode"),
            leader_pane_id=d.get("leader_pane_id"),
            hud_pane_id=d.get("hud_pane_id"),
            resize_hook_name=d.get("resize_hook_name"),
            resize_hook_target=d.get("resize_hook_target"),
            next_worker_index=d.get("next_worker_index"),
        )


# --- Path helpers (aligned with team.state.io._team_dir convention) ---


def _team_dir(cwd: str, team_name: str) -> Path:
    return Path(cwd) / ".omx" / "team" / team_name


def _manifest_v2_path(cwd: str, team_name: str) -> Path:
    return _team_dir(cwd, team_name) / "manifest.v2.json"


# --- Defaults (TS parity) ---


def _default_leader() -> TeamLeader:
    return TeamLeader(session_id="", worker_id="leader-fixed", role="coordinator")


def _default_permissions_snapshot() -> PermissionsSnapshot:
    return PermissionsSnapshot(
        approval_mode="unknown", sandbox_mode="unknown", network_access=True
    )


# --- Env resolution (mirrors TS resolve* helpers) ---


def _read_env_value(env: dict[str, str] | None, keys: list[str]) -> str | None:
    if env is None:
        return None
    for key in keys:
        value = env.get(key)
        if isinstance(value, str) and value.strip() != "":
            return value.strip()
    return None


def _parse_optional_boolean(raw: str | None) -> bool | None:
    if not raw:
        return None
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on", "enabled", "allow", "allowed"):
        return True
    if normalized in ("0", "false", "no", "off", "disabled", "deny", "denied"):
        return False
    return None


def _resolve_display_mode_from_env(env: dict[str, str] | None) -> str:
    raw = _read_env_value(env, ["OMX_TEAM_DISPLAY_MODE", "OMX_TEAM_MODE"])
    if not raw:
        return "auto"
    if raw in ("in_process", "in-process", "split_pane", "tmux"):
        return "split_pane"
    return "auto"


def _resolve_worker_launch_mode_from_env(env: dict[str, str] | None) -> str:
    raw = _read_env_value(env, ["OMX_TEAM_WORKER_LAUNCH_MODE"])
    if not raw or raw == "interactive":
        return "interactive"
    if raw == "prompt":
        return "prompt"
    raise ValueError(
        f'Invalid OMX_TEAM_WORKER_LAUNCH_MODE value "{raw}". '
        "Expected: interactive, prompt"
    )


def _resolve_permissions_snapshot(env: dict[str, str] | None) -> PermissionsSnapshot:
    snapshot = _default_permissions_snapshot()

    approval_mode = _read_env_value(
        env,
        [
            "OMX_APPROVAL_MODE",
            "CODEX_APPROVAL_MODE",
            "CODEX_APPROVAL_POLICY",
            "CLAUDE_CODE_APPROVAL_MODE",
        ],
    )
    if approval_mode:
        snapshot.approval_mode = approval_mode

    sandbox_mode = _read_env_value(
        env, ["OMX_SANDBOX_MODE", "CODEX_SANDBOX_MODE", "SANDBOX_MODE"]
    )
    if sandbox_mode:
        snapshot.sandbox_mode = sandbox_mode

    network = _parse_optional_boolean(
        _read_env_value(
            env, ["OMX_NETWORK_ACCESS", "CODEX_NETWORK_ACCESS", "NETWORK_ACCESS"]
        )
    )
    if network is not None:
        snapshot.network_access = network
    elif "offline" in snapshot.sandbox_mode.lower():
        snapshot.network_access = False

    return snapshot


def _validate_team_name(name: str) -> None:
    if not TEAM_NAME_SAFE_PATTERN.match(name):
        raise ValueError(
            f'Invalid team name: "{name}". Team name must match '
            "/^[a-z0-9][a-z0-9-]{0,29}$/ "
            "(lowercase alphanumeric + hyphens, max 30 chars)."
        )


# --- Manifest validation ---


def _is_team_manifest_v2(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("schema_version") != 2:
        return False
    if not isinstance(value.get("name"), str):
        return False
    if not isinstance(value.get("task"), str):
        return False
    if not isinstance(value.get("tmux_session"), str):
        return False
    worker_count = value.get("worker_count")
    if not isinstance(worker_count, int) or isinstance(worker_count, bool):
        return False
    next_task_id = value.get("next_task_id")
    if not isinstance(next_task_id, int) or isinstance(next_task_id, bool):
        return False
    if not isinstance(value.get("created_at"), str):
        return False
    if not isinstance(value.get("workers"), list):
        return False
    for field_name in (
        "leader_pane_id",
        "hud_pane_id",
        "resize_hook_name",
        "resize_hook_target",
    ):
        v = value.get(field_name)
        if not (isinstance(v, str) or v is None):
            return False
    if not isinstance(value.get("leader"), dict):
        return False
    if not isinstance(value.get("policy"), dict):
        return False
    if not isinstance(value.get("permissions_snapshot"), dict):
        return False
    return True


# --- Public API ---


def read_team_manifest_v2(team_name: str, cwd: str) -> TeamManifestV2 | None:
    """Read the V2 manifest for `team_name`.

    Returns None if the file is missing, malformed, or fails schema validation.
    Does NOT attempt V1 migration (out of scope per locked decision).
    """
    path = _manifest_v2_path(cwd, team_name)
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not _is_team_manifest_v2(parsed):
        return None
    # Normalize policy/governance on read so callers always see a canonical
    # shape on the loose dict fields. lifecycle_profile is forced to "default"
    # to match TS readTeamManifestV2 (state.ts:978).
    parsed["policy"] = normalize_team_policy(parsed.get("policy")).to_dict()
    parsed["governance"] = normalize_team_governance(
        parsed.get("governance"), legacy_policy=parsed.get("policy")
    ).to_dict()
    parsed["lifecycle_profile"] = "default"
    try:
        return TeamManifestV2.from_dict(parsed)
    except (KeyError, ValueError, TypeError):
        return None


def write_team_manifest_v2(manifest: TeamManifestV2, cwd: str) -> None:
    """Write `manifest` to disk atomically.

    Always forces lifecycle_profile='default' on serialization, mirroring TS.
    Policy and governance are normalized via the shared helpers so the
    on-disk shape is canonical regardless of input.
    """
    _validate_team_name(manifest.name)
    manifest.lifecycle_profile = "default"
    path = _manifest_v2_path(cwd, manifest.name)
    write_atomic(path, json.dumps(manifest.to_dict(), indent=2))


def init_team_state(
    team_name: str,
    task: str,
    agent_type: str,
    worker_count: int,
    cwd: str,
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
    env: dict[str, str] | None = None,
    workspace: dict[str, Any] | None = None,
    lifecycle_profile: str = "default",
) -> TeamManifestV2:
    """Initialize team state directory + V2 manifest + initial config.json.

    Creates the standard team directory layout:
      .omx/team/{team_name}/
        workers/{worker-1}..{worker-N}/
        tasks/
        claims/
        mailbox/
        dispatch/requests.json
        events/
        approvals/
        config.json
        manifest.v2.json

    Returns the newly written `TeamManifestV2`.

    Raises:
        ValueError: if `team_name` is invalid, if `worker_count` exceeds
            `max_workers`, if `max_workers` exceeds `ABSOLUTE_MAX_WORKERS`,
            or if `lifecycle_profile` is not 'default'.
    """
    _validate_team_name(team_name)

    if max_workers > ABSOLUTE_MAX_WORKERS:
        raise ValueError(
            f"maxWorkers ({max_workers}) exceeds "
            f"ABSOLUTE_MAX_WORKERS ({ABSOLUTE_MAX_WORKERS})"
        )
    if worker_count > max_workers:
        raise ValueError(
            f"workerCount ({worker_count}) exceeds maxWorkers ({max_workers})"
        )
    if lifecycle_profile != "default":
        raise ValueError(
            f'Invalid lifecycle_profile "{lifecycle_profile}". Only "default" '
            "is supported."
        )

    env_map = env if env is not None else dict(os.environ)
    workspace_meta = workspace or {}

    root = _team_dir(cwd, team_name)
    workers_root = root / "workers"
    tasks_root = root / "tasks"
    claims_root = root / "claims"
    mailbox_root = root / "mailbox"
    dispatch_root = root / "dispatch"
    events_root = root / "events"
    approvals_root = root / "approvals"

    for d in (
        workers_root,
        tasks_root,
        claims_root,
        mailbox_root,
        dispatch_root,
        events_root,
        approvals_root,
    ):
        d.mkdir(parents=True, exist_ok=True)

    write_atomic(dispatch_root / "requests.json", json.dumps([], indent=2))

    workers: list[WorkerInfo] = []
    for i in range(1, worker_count + 1):
        name = f"worker-{i}"
        workers.append(WorkerInfo(name=name, index=i, role=agent_type))
        (workers_root / name).mkdir(parents=True, exist_ok=True)

    leader_session_id = (
        _read_env_value(env_map, ["OMX_SESSION_ID", "CODEX_SESSION_ID", "SESSION_ID"])
        or ""
    )
    leader_worker_id = _read_env_value(env_map, ["OMX_TEAM_WORKER"]) or "leader-fixed"
    display_mode = _resolve_display_mode_from_env(env_map)
    worker_launch_mode = _resolve_worker_launch_mode_from_env(env_map)
    permissions_snapshot = _resolve_permissions_snapshot(env_map)

    created_at = datetime.now(timezone.utc).isoformat()
    tmux_session = f"omx-team-{team_name}"

    # Initial V1-shape config.json — kept for parity with TS initTeamState.
    config_dict: dict[str, Any] = {
        "name": team_name,
        "task": task,
        "agent_type": agent_type,
        "worker_launch_mode": worker_launch_mode,
        "lifecycle_profile": lifecycle_profile,
        "worker_count": worker_count,
        "max_workers": max_workers,
        "workers": [w.to_dict() for w in workers],
        "created_at": created_at,
        "tmux_session": tmux_session,
        "next_task_id": 1,
        "leader_pane_id": None,
        "hud_pane_id": None,
        "resize_hook_name": None,
        "resize_hook_target": None,
        "next_worker_index": worker_count + 1,
    }
    for opt_key in (
        "leader_cwd",
        "team_state_root",
        "workspace_mode",
        "worktree_mode",
    ):
        v = workspace_meta.get(opt_key)
        if v is not None:
            config_dict[opt_key] = v
    write_atomic(root / "config.json", json.dumps(config_dict, indent=2))

    # Seed policy dict from env defaults; normalize_team_policy will canonicalize.
    policy_dict: dict[str, Any] = {
        "display_mode": display_mode,
        "worker_launch_mode": worker_launch_mode,
    }

    leader = _default_leader()
    leader.session_id = leader_session_id
    leader.worker_id = leader_worker_id

    manifest = TeamManifestV2(
        schema_version=2,
        name=team_name,
        task=task,
        leader=leader,
        policy=policy_dict,
        governance=None,  # normalized to defaults at write time
        lifecycle_profile=lifecycle_profile,
        permissions_snapshot=permissions_snapshot,
        tmux_session=tmux_session,
        worker_count=worker_count,
        workers=workers,
        next_task_id=1,
        created_at=created_at,
        leader_cwd=workspace_meta.get("leader_cwd"),
        team_state_root=workspace_meta.get("team_state_root"),
        workspace_mode=workspace_meta.get("workspace_mode"),
        worktree_mode=workspace_meta.get("worktree_mode"),
        leader_pane_id=None,
        hud_pane_id=None,
        resize_hook_name=None,
        resize_hook_target=None,
        next_worker_index=worker_count + 1,
    )
    write_team_manifest_v2(manifest, cwd)
    return manifest


__all__ = [
    "TeamLeader",
    "PermissionsSnapshot",
    "TeamManifestV2",
    "init_team_state",
    "read_team_manifest_v2",
    "write_team_manifest_v2",
]
