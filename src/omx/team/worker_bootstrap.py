"""Worker process bootstrap and worktree management.

Port of src/team/worker-bootstrap.ts.

Handles:
- Worker initialization (identity, status, model instructions).
- Worktree AGENTS.md generation, install, and rollback (with git
  ``skip-worktree`` / ``info/exclude`` integration so tracked AGENTS.md
  files are not seen as dirty during a run).
- Team AGENTS.md overlay generation, idempotent apply, and strip.
- Composed team/role worker instructions files.
- Initial/task-assignment/shutdown inbox content generators.
- Short tmux send-keys trigger messages plus their structured
  :class:`TeamReminderDirective` variants for inbox and mailbox flows.

Locked decisions for this port:
- Sync only (no asyncio); the TS module is async but Python uses
  blocking I/O. The TS-level AGENTS.md mkdir lock is replaced with an
  in-process :class:`threading.Lock` per absolute AGENTS.md path,
  plus a directory-based on-disk lock so concurrent processes still
  see the lock semantics.
- Stdlib only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from omx.team.state.atomic import write_atomic
from omx.team.state.types import WorkerInfo
from omx.team.state_root import team_dir
from omx.utils.paths import codex_home, project_skills_dir, user_skills_dir
from omx.verification.verifier import (
    get_fix_loop_instructions,
    get_verification_instructions,
)

__all__ = [
    # Existing.
    "bootstrap_worker",
    "cleanup_worker",
    # Constants (overlay markers).
    "TEAM_OVERLAY_START",
    "TEAM_OVERLAY_END",
    # Worker root AGENTS.md (worktree).
    "WorkerRootAgentsOptions",
    "generate_worker_root_agents_content",
    "write_worker_worktree_root_agents_file",
    "remove_worker_worktree_root_agents_file",
    # Overlay surface.
    "generate_worker_overlay",
    "apply_worker_overlay",
    "strip_worker_overlay",
    # Team / role instruction file composition.
    "write_team_worker_instructions_file",
    "write_worker_role_instructions_file",
    "remove_team_worker_instructions_file",
    # Inbox content generators.
    "generate_initial_inbox",
    "generate_task_assignment_inbox",
    "generate_shutdown_inbox",
    # Trigger messages.
    "TeamReminderDirective",
    "generate_trigger_message",
    "build_trigger_directive",
    "generate_mailbox_trigger_message",
    "build_mailbox_trigger_directive",
    "generate_leader_mailbox_trigger_message",
    "build_leader_mailbox_trigger_directive",
]


# ---------------------------------------------------------------------------
# Constants — mirrored from TS.
# ---------------------------------------------------------------------------

TEAM_OVERLAY_START = "<!-- OMX:TEAM:WORKER:START -->"
TEAM_OVERLAY_END = "<!-- OMX:TEAM:WORKER:END -->"

_SKILL_REFERENCE_PATTERN = re.compile(r"/skills/([^/\s`]+)/SKILL\.md\b")
_AGENTS_LOCK_PATH_PARTS = (".omx", "state", "agents-md.lock")
_LOCK_OWNER_FILE = "owner.json"
_LOCK_TIMEOUT_MS = 5000
_LOCK_POLL_INTERVAL_MS = 100
_LOCK_STALE_MS = 30_000


# ---------------------------------------------------------------------------
# Existing exports — preserved verbatim from the prior port.
# ---------------------------------------------------------------------------


def bootstrap_worker(
    team_name: str,
    worker_name: str,
    worker_index: int,
    cwd: str,
    *,
    role: str = "executor",
    worker_cli: str = "codex",
    pane_id: str = "",
    use_worktree: bool = False,
) -> WorkerInfo:
    """Bootstrap a worker: create identity, state dirs, and model instructions.

    Args:
        team_name: Team name.
        worker_name: Worker identifier.
        worker_index: Zero-based worker index.
        cwd: Working directory.
        role: Agent role for this worker.
        worker_cli: CLI tool (codex, claude, gemini).
        pane_id: Tmux pane ID.
        use_worktree: Whether to create a git worktree for isolation.

    Returns:
        Populated WorkerInfo.
    """
    td = team_dir(team_name, cwd)
    worker_dir = td / "workers" / worker_name
    worker_dir.mkdir(parents=True, exist_ok=True)

    worker_cwd = cwd
    worktree_path = None
    worktree_branch = None

    if use_worktree:
        from omx.team.worktree import create_worktree

        branch = f"omx-{team_name}-{worker_name}"
        result = create_worktree(cwd, branch)
        if result["ok"]:
            worker_cwd = result["path"]
            worktree_path = result["path"]
            worktree_branch = branch

    info = WorkerInfo(
        name=worker_name,
        index=worker_index,
        role=role,
        worker_cli=worker_cli,
        pane_id=pane_id,
        working_dir=worker_cwd,
        worktree_path=worktree_path,
        worktree_branch=worktree_branch,
        pid=os.getpid(),
    )

    identity_path = worker_dir / "identity.json"
    identity = {
        **info.to_dict(),
        "team_name": team_name,
        "bootstrapped_at": datetime.now(timezone.utc).isoformat(),
    }
    identity_path.write_text(json.dumps(identity, indent=2), encoding="utf-8")

    status_path = worker_dir / "status.json"
    status_path.write_text(
        json.dumps(
            {
                "state": "idle",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    _write_model_instructions(td, worker_name, role, cwd)

    return info


def _write_model_instructions(
    td: Path,
    worker_name: str,
    role: str,
    cwd: str,
) -> None:
    """Generate the simple per-worker model instructions file."""
    instructions_path = td / "workers" / worker_name / "model-instructions.md"

    from omx.utils.paths import package_root

    prompt_file = package_root() / "assets" / "prompts" / f"{role}.md"

    lines = [
        f"# Worker: {worker_name}",
        f"## Role: {role}",
        "",
        "You are a team worker. Follow these guidelines:",
        "",
        "1. Read your inbox file when instructed",
        "2. Execute the assigned task in your working directory",
        "3. Make commits with clear messages",
        "4. Report completion via your status file",
        "",
    ]

    if prompt_file.exists():
        lines.extend(
            [
                "## Role Instructions",
                "",
                prompt_file.read_text(encoding="utf-8"),
            ]
        )

    instructions_path.write_text("\n".join(lines), encoding="utf-8")


def cleanup_worker(team_name: str, worker_name: str, cwd: str) -> None:
    """Clean up a worker's resources (worktree, state files)."""
    td = team_dir(team_name, cwd)
    worker_dir = td / "workers" / worker_name

    identity_path = worker_dir / "identity.json"
    if identity_path.exists():
        try:
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            worktree_path = identity.get("worktree_path")
            if worktree_path:
                from omx.team.worktree import remove_worktree

                remove_worktree(cwd, worktree_path, force=True)
        except (json.JSONDecodeError, OSError):
            pass


# ---------------------------------------------------------------------------
# TeamReminderDirective — small local dataclass.
#
# The Python sibling in ``omx.team.reminder_intents`` ports a different TS
# concept (``TeamReminderIntent`` metadata for dispatch). The TS
# ``worker-bootstrap`` module uses ``TeamReminderDirective`` which is a
# ``{ text, intent }`` envelope. We add the directive shape locally to keep
# the parity surface narrow and avoid disturbing the existing intent type.
# ---------------------------------------------------------------------------


_TEAM_REMINDER_INTENTS = frozenset(
    {
        "followup-reuse",
        "followup-relaunch",
        "stalled-unblock",
        "done-review-or-shutdown",
        "pending-mailbox-review",
    }
)


@dataclass(frozen=True)
class TeamReminderDirective:
    """Structured trigger directive sent to a worker/leader pane.

    Attributes:
        text: ASCII-safe, <200 char prompt the pane should act on.
        intent: One of :data:`_TEAM_REMINDER_INTENTS`.
    """

    text: str
    intent: str


# ---------------------------------------------------------------------------
# Worker root AGENTS.md (worktree-scoped) generation + install/rollback.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerRootAgentsOptions:
    """Options for worktree root AGENTS.md generation.

    Mirrors the TS ``WorkerRootAgentsOptions`` interface.
    """

    team_name: str
    worker_name: str
    worker_role: str
    role_prompt_content: str
    team_state_root: str
    leader_cwd: str
    worktree_path: str


@dataclass
class _WorkerRootAgentsBackup:
    existed: bool
    tracked: bool
    previous_content: str | None = None
    skip_worktree_applied: bool = False

    def to_json(self) -> str:
        # Field names match TS (camelCase) for cross-tool backup readability.
        payload: dict[str, Any] = {
            "existed": self.existed,
            "tracked": self.tracked,
            "skipWorktreeApplied": self.skip_worktree_applied,
        }
        if self.previous_content is not None:
            payload["previousContent"] = self.previous_content
        return json.dumps(payload, indent=2)

    @classmethod
    def from_json(cls, raw: str) -> "_WorkerRootAgentsBackup":
        data = json.loads(raw)
        return cls(
            existed=bool(data.get("existed", False)),
            tracked=bool(data.get("tracked", False)),
            previous_content=data.get("previousContent"),
            skip_worktree_applied=bool(data.get("skipWorktreeApplied", False)),
        )


def _try_read_git_value(cwd: str, args: Sequence[str]) -> str | None:
    """Run ``git <args>`` in ``cwd`` and return trimmed stdout, or ``None``."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def _is_tracked(worktree_path: str, file_name: str) -> bool:
    """Return ``True`` if ``file_name`` is tracked in the git index."""
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--error-unmatch", file_name],
            cwd=worktree_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return proc.returncode == 0


def _ensure_git_info_exclude_pattern(worktree_path: str, pattern: str) -> None:
    """Append ``pattern`` to ``$GIT_DIR/info/exclude`` if not already present."""
    exclude_path = _try_read_git_value(
        worktree_path, ["rev-parse", "--git-path", "info/exclude"]
    )
    if not exclude_path:
        return
    exclude_file = Path(exclude_path)
    existing = ""
    if exclude_file.exists():
        try:
            existing = exclude_file.read_text(encoding="utf-8")
        except OSError:
            existing = ""
    lines = {line for line in re.split(r"\r?\n", existing) if line}
    if pattern in lines:
        return
    if existing and not existing.endswith("\n"):
        suffix = f"\n{pattern}\n"
    elif existing == "":
        suffix = f"{pattern}\n"
    else:
        suffix = f"{pattern}\n"
    next_content = f"{existing}{suffix}"
    exclude_file.parent.mkdir(parents=True, exist_ok=True)
    exclude_file.write_text(next_content, encoding="utf-8")


def _build_worker_root_agents_backup_path(
    team_state_root: str,
    team_name: str,
    worker_name: str,
    worktree_path: str,
) -> Path:
    """Resolve the backup file path for the worker root AGENTS.md install."""
    git_path = _try_read_git_value(
        worktree_path,
        ["rev-parse", "--git-path", "omx/root-agents-backup.json"],
    )
    if git_path:
        return Path(git_path)
    return (
        Path(team_state_root)
        / "team"
        / team_name
        / "workers"
        / worker_name
        / "root-agents-backup.json"
    )


def generate_worker_root_agents_content(options: WorkerRootAgentsOptions) -> str:
    """Generate the worktree-scoped AGENTS.md content for a team worker.

    Snapshot-identical to TS ``generateWorkerRootAgentsContent``.
    """
    team = options.team_name
    name = options.worker_name
    role = options.worker_role
    role_prompt = options.role_prompt_content.strip()
    state_root = options.team_state_root
    inbox_dir = f"{state_root}/team/{team}/workers/{name}"
    return (
        "# Team Worker Runtime Instructions\n"
        "\n"
        "This file is generated for a live OMX team worker run and is disposable.\n"
        "\n"
        "## Worker Identity\n"
        f"- Team: {team}\n"
        f"- Worker: {name}\n"
        f"- Role: {role}\n"
        f"- Leader cwd: {options.leader_cwd}\n"
        f"- Worktree root: {options.worktree_path}\n"
        f"- Team state root: {state_root}\n"
        f"- Inbox path: {inbox_dir}/inbox.md\n"
        f"- Mailbox path: {state_root}/team/{team}/mailbox/{name}.json\n"
        f"- Leader mailbox path: {state_root}/team/{team}/mailbox/leader-fixed.json\n"
        f"- Task directory: {state_root}/team/{team}/tasks\n"
        f"- Worker status path: {inbox_dir}/status.json\n"
        f"- Worker identity path: {inbox_dir}/identity.json\n"
        "\n"
        "## Protocol\n"
        f"1. Read your inbox at `{inbox_dir}/inbox.md`.\n"
        "2. Load the worker skill from the first existing path:\n"
        "   - `${CODEX_HOME:-~/.codex}/skills/worker/SKILL.md`\n"
        f"   - `{options.leader_cwd}/.codex/skills/worker/SKILL.md`\n"
        f"   - `{options.leader_cwd}/skills/worker/SKILL.md`\n"
        "3. Send startup ACK before task work:\n"
        "\n"
        f'   `omx team api send-message --input "{{\\"team_name\\":\\"{team}\\",\\"from_worker\\":\\"{name}\\",\\"to_worker\\":\\"leader-fixed\\",\\"body\\":\\"ACK: {name} initialized\\"}}" --json`\n'
        "\n"
        "4. Resolve canonical team state root in this order: `OMX_TEAM_STATE_ROOT` env -> worker identity `team_state_root` -> config/manifest `team_state_root` -> local cwd fallback.\n"
        f"5. Read task files from `{state_root}/team/{team}/tasks/task-<id>.json` using bare `task_id` values in APIs.\n"
        "6. Use claim-safe lifecycle APIs only:\n"
        "   - `omx team api claim-task --json`\n"
        "   - `omx team api transition-task-status --json`\n"
        "   - `omx team api release-task-claim --json` only for rollback to pending\n"
        "7. Use mailbox delivery flow:\n"
        f'   - `omx team api mailbox-list --input "{{\\"team_name\\":\\"{team}\\",\\"worker\\":\\"{name}\\"}}" --json`\n'
        f'   - `omx team api mailbox-mark-delivered --input "{{\\"team_name\\":\\"{team}\\",\\"worker\\":\\"{name}\\",\\"message_id\\":\\"<MESSAGE_ID>\\"}}" --json`\n'
        "8. Preserve leader steering via inbox/mailbox nudges; task payload stays in inbox/task JSON, not this file.\n"
        "9. Do not pass `workingDirectory` to legacy team_* MCP tools; use `omx team api` CLI interop.\n"
        "\n"
        "## Message Protocol\n"
        f'- Always include `from_worker: "{name}"`\n'
        '- Send leader messages to `to_worker: "leader-fixed"`\n'
        "\n"
        "## Scope Rules\n"
        "- Follow task-specific edit scope from inbox/task JSON only.\n"
        "- If blocked on a shared file, update status with a blocked reason and report upward.\n"
        "\n"
        "<!-- OMX:TEAM:ROLE:START -->\n"
        "<team_worker_role>\n"
        f"You are operating as the **{role}** role for this team run. Apply the following role-local guidance.\n"
        "\n"
        f"{role_prompt}\n"
        "</team_worker_role>\n"
        "<!-- OMX:TEAM:ROLE:END -->\n"
    )


def write_worker_worktree_root_agents_file(
    options: WorkerRootAgentsOptions,
) -> str:
    """Install the worktree AGENTS.md, recording a backup for rollback.

    Returns the absolute AGENTS.md path. If the file is tracked in git the
    function applies ``--skip-worktree`` so the synthetic content is not seen
    as a dirty edit; otherwise it appends an ``info/exclude`` rule.
    """
    worktree_path = options.worktree_path
    agents_path = Path(worktree_path) / "AGENTS.md"
    tracked = _is_tracked(worktree_path, "AGENTS.md")
    existed = agents_path.exists()
    previous_content: str | None = None
    if existed:
        try:
            previous_content = agents_path.read_text(encoding="utf-8")
        except OSError:
            previous_content = None
    skip_worktree_applied = False

    if tracked:
        try:
            proc = subprocess.run(
                ["git", "update-index", "--skip-worktree", "AGENTS.md"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
            skip_worktree_applied = proc.returncode == 0
        except (FileNotFoundError, OSError):
            skip_worktree_applied = False
    else:
        _ensure_git_info_exclude_pattern(worktree_path, "AGENTS.md")

    backup = _WorkerRootAgentsBackup(
        existed=existed,
        tracked=tracked,
        previous_content=previous_content,
        skip_worktree_applied=skip_worktree_applied,
    )
    backup_path = _build_worker_root_agents_backup_path(
        options.team_state_root,
        options.team_name,
        options.worker_name,
        worktree_path,
    )
    write_atomic(backup_path, backup.to_json())
    write_atomic(agents_path, generate_worker_root_agents_content(options))
    return str(agents_path)


def remove_worker_worktree_root_agents_file(
    team_name: str,
    worker_name: str,
    team_state_root: str,
    worktree_path: str,
) -> None:
    """Roll back the worktree AGENTS.md install using the recorded backup.

    Safe to call repeatedly. A missing backup is treated as a no-op.
    """
    agents_path = Path(worktree_path) / "AGENTS.md"
    backup_path = _build_worker_root_agents_backup_path(
        team_state_root, team_name, worker_name, worktree_path
    )

    backup: _WorkerRootAgentsBackup | None = None
    try:
        backup = _WorkerRootAgentsBackup.from_json(
            backup_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError, ValueError):
        backup = None

    if backup is None:
        return

    if backup.tracked and backup.skip_worktree_applied:
        try:
            subprocess.run(
                ["git", "update-index", "--no-skip-worktree", "AGENTS.md"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=False,
            )
        except (FileNotFoundError, OSError):
            pass

    if backup.existed:
        write_atomic(agents_path, backup.previous_content or "")
    else:
        try:
            agents_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    try:
        backup_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Worker overlay generation and idempotent apply/strip.
# ---------------------------------------------------------------------------


def generate_worker_overlay(team_name: str) -> str:
    """Generate the generic team worker AGENTS.md overlay block.

    The overlay is identical for every worker in a team; per-worker identity
    lives in the inbox file. Bounded by ``TEAM_OVERLAY_START`` /
    ``TEAM_OVERLAY_END`` markers so :func:`strip_worker_overlay` can remove it
    later.
    """
    codex_home_token = "${CODEX_HOME:-~/.codex}"
    return (
        f"{TEAM_OVERLAY_START}\n"
        "<team_worker_protocol>\n"
        f'You are a team worker in team "{team_name}". Your identity and assigned tasks are in your inbox file.\n'
        "\n"
        "## Protocol\n"
        "1. Read your inbox file at the path provided in your first instruction\n"
        "2. Load the worker skill instructions from the first path that exists:\n"
        f"   - `{codex_home_token}/skills/worker/SKILL.md`\n"
        "   - `<leader_cwd>/.codex/skills/worker/SKILL.md`\n"
        "   - `<leader_cwd>/skills/worker/SKILL.md` (repo fallback)\n"
        '3. Send an ACK to the lead using CLI interop `omx team api send-message --json` (to_worker="leader-fixed") once initialized\n'
        "4. Resolve canonical team state root in this order:\n"
        "   - OMX_TEAM_STATE_ROOT env\n"
        "   - worker identity team_state_root\n"
        "   - team config/manifest team_state_root\n"
        "   - local cwd fallback (.omx/state)\n"
        f"5. Read your task from <team_state_root>/team/{team_name}/tasks/task-<id>.json (example: task-1.json)\n"
        "6. Task id format:\n"
        '   - State/MCP APIs use task_id: "<id>" (example: "1"), never "task-1"\n'
        "7. Request a claim via CLI interop (`omx team api claim-task --json`); do not directly set lifecycle fields in the task file\n"
        "8. Do the work using your tools\n"
        "9. After completing work, commit your changes before reporting completion:\n"
        '   `git add -A && git commit -m "task: <task-subject>"`\n'
        "   This ensures your changes are available for incremental integration into the leader branch.\n"
        "10. On completion/failure, use lifecycle transition APIs:\n"
        '   - `omx team api transition-task-status --json` with from `"in_progress"` to `"completed"` or `"failed"`\n'
        "   - Include `result` (for completed) or `error` (for failed) in the transition patch\n"
        "11. Use `omx team api release-task-claim --json` only for rollback/requeue to `pending` (not for completion)\n"
        f'12. Update your status: write {{"state": "idle", "updated_at": "<current ISO timestamp>"}} to <team_state_root>/team/{team_name}/workers/{{your-name}}/status.json\n'
        "13. Wait for new instructions (the lead will send them via your terminal)\n"
        f"14. Check your mailbox for messages at <team_state_root>/team/{team_name}/mailbox/{{your-name}}.json\n"
        "15. For legacy team_* MCP tools (hard-deprecated), switch to `omx team api` CLI interop; do not pass workingDirectory unless the lead explicitly tells you to\n"
        "\n"
        "## Message Protocol\n"
        "When calling `omx team api send-message`, you MUST always include:\n"
        '- from_worker: "<your-worker-name>" (your identity — check your inbox file for your worker name, never omit this)\n'
        '- to_worker: "leader-fixed" (to message the leader) or "worker-N" (for peers)\n'
        "\n"
        "## Startup Handshake (Required)\n"
        "Before doing any task work, send exactly one startup ACK to the leader.\n"
        "Keep the body short and deterministic so all worker CLIs (Codex/Claude) behave consistently.\n"
        "\n"
        "Example:\n"
        f'omx team api send-message --input "{{\\"team_name\\":\\"{team_name}\\",\\"from_worker\\":\\"<your-worker-name>\\",\\"to_worker\\":\\"leader-fixed\\",\\"body\\":\\"ACK: <your-worker-name> initialized\\"}}" --json\n'
        "\n"
        "CRITICAL: Never omit from_worker. The MCP server cannot auto-detect your identity.\n"
        "\n"
        "When your mailbox receives a message, process delivery explicitly:\n"
        f'1. Read: `omx team api mailbox-list --input "{{\\"team_name\\":\\"{team_name}\\",\\"worker\\":\\"<your-worker-name>\\"}}" --json`\n'
        f'2. Mark delivered: `omx team api mailbox-mark-delivered --input "{{\\"team_name\\":\\"{team_name}\\",\\"worker\\":\\"<your-worker-name>\\",\\"message_id\\":\\"<MESSAGE_ID>\\"}}" --json`\n'
        "3. If you reply, include concrete progress and keep executing your assigned work or the next feasible task after replying.\n"
        "\n"
        "## Rules\n"
        "- Do NOT edit files outside the paths listed in your task description\n"
        '- If you need to modify a shared file, report to the lead by writing to your status file with state "blocked"\n'
        "- Do NOT write lifecycle fields (`status`, `owner`, `result`, `error`) directly in task files; use claim-safe lifecycle APIs\n"
        '- If blocked, write {"state": "blocked", "reason": "..."} to your status file\n'
        "- You may spawn Codex native subagents when parallel execution improves throughput.\n"
        "- Use subagents only for independent, bounded subtasks that can run safely within this worker pane.\n"
        "</team_worker_protocol>\n"
        f"{TEAM_OVERLAY_END}"
    )


def _strip_overlay_from_content(content: str) -> str:
    """Remove an overlay block from arbitrary AGENTS.md content. Idempotent."""
    start_idx = content.find(TEAM_OVERLAY_START)
    end_idx = content.find(TEAM_OVERLAY_END)
    if start_idx == -1 or end_idx == -1 or end_idx < start_idx:
        return content
    before = content[:start_idx].rstrip()
    after = content[end_idx + len(TEAM_OVERLAY_END) :].lstrip()
    if after:
        return f"{before}\n\n{after}\n"
    return f"{before}\n"


def _drop_shadowed_skill_reference_lines(
    content: str, shadowed_skill_names: Iterable[str]
) -> str:
    """Drop lines that reference user-scope skills shadowed by project skills."""
    shadowed = set(shadowed_skill_names)
    if not shadowed:
        return content
    kept: list[str] = []
    for line in content.split("\n"):
        shadow_hit = False
        for match in _SKILL_REFERENCE_PATTERN.finditer(line):
            name = match.group(1) or ""
            if name in shadowed:
                shadow_hit = True
                break
        if not shadow_hit:
            kept.append(line)
    return "\n".join(kept)


# ---- AGENTS.md lock (per-process + on-disk directory lock). ----

# Threading lock keyed by absolute AGENTS.md path. Mirrors the TS mkdir-lock,
# extended with an in-process gate so two threads in the same interpreter
# serialize even when their on-disk lock paths collide on the same FS object.
_agents_md_thread_locks: dict[str, threading.Lock] = {}
_agents_md_thread_locks_guard = threading.Lock()


def _lock_path_for(agents_md_path: Path) -> Path:
    return agents_md_path.parent.joinpath(*_AGENTS_LOCK_PATH_PARTS)


def _is_stale_lock(lock_path: Path) -> bool:
    owner_file = lock_path / _LOCK_OWNER_FILE
    try:
        owner = json.loads(owner_file.read_text(encoding="utf-8"))
        pid = owner.get("pid")
        if not isinstance(pid, int):
            return True
        # Cross-platform "process alive" probe.
        try:
            os.kill(pid, 0)
        except OSError:
            return True
        return False
    except (OSError, json.JSONDecodeError, ValueError):
        try:
            lock_stat = lock_path.stat()
        except OSError:
            return True
        return (time.time() * 1000) - (lock_stat.st_mtime * 1000) > _LOCK_STALE_MS


def _acquire_agents_md_lock(
    agents_md_path: Path, timeout_ms: int = _LOCK_TIMEOUT_MS
) -> None:
    lock_path = _lock_path_for(agents_md_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    while (time.monotonic() - start) * 1000 < timeout_ms:
        try:
            lock_path.mkdir(parents=False, exist_ok=False)
            owner_file = lock_path / _LOCK_OWNER_FILE
            owner_file.write_text(
                json.dumps({"pid": os.getpid(), "ts": int(time.time() * 1000)}),
                encoding="utf-8",
            )
            return
        except FileExistsError:
            if _is_stale_lock(lock_path):
                _rm_rf(lock_path)
                continue
            time.sleep(_LOCK_POLL_INTERVAL_MS / 1000)
        except OSError:
            time.sleep(_LOCK_POLL_INTERVAL_MS / 1000)
    raise TimeoutError("Failed to acquire AGENTS.md lock within timeout")


def _release_agents_md_lock(agents_md_path: Path) -> None:
    _rm_rf(_lock_path_for(agents_md_path))


def _rm_rf(path: Path) -> None:
    try:
        if path.is_dir():
            for child in path.iterdir():
                _rm_rf(child)
            try:
                path.rmdir()
            except OSError:
                pass
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass
    except FileNotFoundError:
        return
    except OSError:
        return


def _thread_lock_for(key: str) -> threading.Lock:
    with _agents_md_thread_locks_guard:
        lock = _agents_md_thread_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _agents_md_thread_locks[key] = lock
        return lock


class _AgentsMdLock:
    """Context manager binding the on-disk dir lock + per-path thread lock."""

    def __init__(self, agents_md_path: Path) -> None:
        self.path = agents_md_path
        self._thread_lock = _thread_lock_for(str(agents_md_path.resolve()))

    def __enter__(self) -> "_AgentsMdLock":
        self._thread_lock.acquire()
        try:
            _acquire_agents_md_lock(self.path)
        except BaseException:
            self._thread_lock.release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            _release_agents_md_lock(self.path)
        finally:
            self._thread_lock.release()


def apply_worker_overlay(agents_md_path: str | Path, overlay: str) -> None:
    """Apply the worker overlay to ``agents_md_path``. Idempotent.

    Reads existing content, strips any previous overlay (matched by markers),
    then appends the new overlay surrounded by blank lines.
    """
    target = Path(agents_md_path)
    with _AgentsMdLock(target):
        content = ""
        try:
            content = target.read_text(encoding="utf-8")
        except OSError:
            content = ""
        content = _strip_overlay_from_content(content)
        composed = f"{content.rstrip()}\n\n{overlay}\n"
        write_atomic(target, composed)


def strip_worker_overlay(agents_md_path: str | Path) -> None:
    """Strip any worker overlay from ``agents_md_path``. Idempotent.

    Missing files are treated as a no-op.
    """
    target = Path(agents_md_path)
    with _AgentsMdLock(target):
        try:
            content = target.read_text(encoding="utf-8")
        except OSError:
            return
        stripped = _strip_overlay_from_content(content)
        if stripped != content:
            write_atomic(target, stripped)


# ---------------------------------------------------------------------------
# Composed team/role instruction files.
# ---------------------------------------------------------------------------


def _list_project_skill_names(project_root: str) -> set[str]:
    """Return the set of project-scope skill directory names.

    Mirrors the TS ``listInstalledSkillDirectories(projectRoot)`` filter for
    ``scope === "project"`` but stays stdlib-only and inlines the read so we
    do not need the broader skills catalogue.
    """
    skills_dir = project_skills_dir(Path(project_root))
    if not skills_dir.exists() or not skills_dir.is_dir():
        return set()
    out: set[str] = set()
    try:
        for entry in skills_dir.iterdir():
            if not entry.is_dir():
                continue
            if (entry / "SKILL.md").exists():
                out.add(entry.name)
    except OSError:
        return out
    return out


def write_team_worker_instructions_file(team_name: str, cwd: str, overlay: str) -> str:
    """Compose user/project AGENTS.md with ``overlay`` into a team file.

    Returns the absolute path to the composed file. Strips any pre-existing
    worker overlay from the source AGENTS.md files before composition and
    drops user-scope skill references that are shadowed by project-scope
    skills of the same directory name.
    """
    base_parts: list[str] = []
    user_agents_path = codex_home() / "AGENTS.md"
    source_paths = [user_agents_path, Path(cwd) / "AGENTS.md"]
    seen: set[Path] = set()
    project_skill_names = _list_project_skill_names(cwd)

    # Skill catalogue used here is project-scope skills under .codex/skills/.
    # The user_skills_dir() lookup is only relevant to the broader catalogue
    # logic in TS; we intentionally do not need it here because shadowing only
    # filters lines that reference the project-shadowed skill name.
    _ = user_skills_dir  # explicit reference to keep import meaningful.

    for source_path in source_paths:
        if source_path in seen:
            continue
        seen.add(source_path)
        try:
            content = source_path.read_text(encoding="utf-8")
        except OSError:
            continue
        content = _strip_overlay_from_content(content).strip()
        if source_path == user_agents_path:
            content = _drop_shadowed_skill_reference_lines(
                content, project_skill_names
            ).strip()
        if not content:
            continue
        base_parts.append(content)

    base = "\n\n".join(base_parts)
    composed = f"{base}\n\n{overlay}\n" if base.strip() else f"{overlay}\n"

    out_path = Path(cwd) / ".omx" / "state" / "team" / team_name / "worker-agents.md"
    write_atomic(out_path, composed)
    return str(out_path)


def write_worker_role_instructions_file(
    team_name: str,
    worker_name: str,
    cwd: str,
    base_instructions_path: str,
    worker_role: str,
    role_prompt_content: str,
) -> str:
    """Compose the base team worker instructions with a role overlay.

    Returns the absolute path to the per-worker AGENTS.md file.
    """
    try:
        base = Path(base_instructions_path).read_text(encoding="utf-8")
    except OSError:
        base = ""
    role_overlay = (
        "\n"
        "<!-- OMX:TEAM:ROLE:START -->\n"
        "<team_worker_role>\n"
        f"You are operating as the **{worker_role}** role for this team run. Apply the following role-local guidance in addition to the team worker protocol.\n"
        "\n"
        f"{role_prompt_content.strip()}\n"
        "</team_worker_role>\n"
        "<!-- OMX:TEAM:ROLE:END -->\n"
    )
    if base.strip():
        composed = f"{base.rstrip()}\n\n{role_overlay}"
    else:
        composed = role_overlay.lstrip()
    out_path = (
        Path(cwd)
        / ".omx"
        / "state"
        / "team"
        / team_name
        / "workers"
        / worker_name
        / "AGENTS.md"
    )
    write_atomic(out_path, composed)
    return str(out_path)


def remove_team_worker_instructions_file(team_name: str, cwd: str) -> None:
    """Remove the composed team worker instructions file. Idempotent."""
    out_path = Path(cwd) / ".omx" / "state" / "team" / team_name / "worker-agents.md"
    try:
        out_path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


# ---------------------------------------------------------------------------
# Inbox content generators.
# ---------------------------------------------------------------------------


def _build_verification_section(task_description: str) -> str:
    """Render the verification + fix-loop block used by inboxes."""
    verification = get_verification_instructions("standard", task_description).strip()
    fix_loop = get_fix_loop_instructions().strip()
    return (
        "\n"
        "## Verification Requirements\n"
        "\n"
        f"{verification}\n"
        "\n"
        f"{fix_loop}\n"
        "\n"
        "When marking completion, include structured verification evidence in your task result:\n"
        "- `Verification:`\n"
        "- One or more PASS/FAIL checks with command/output references\n"
    )


def _task_field(task: Any, *names: str, default: Any = None) -> Any:
    """Read the first present field from ``task`` by attribute or key."""
    for name in names:
        if isinstance(task, Mapping):
            if name in task:
                value = task[name]
                if value is not None:
                    return value
        else:
            if hasattr(task, name):
                value = getattr(task, name)
                if value is not None:
                    return value
    return default


def _format_task_entry(task: Any) -> str:
    """Render a single inbox task list entry from a task-like object/dict.

    Accepts either the TS-shape dict (``id``, ``subject``, ``description``,
    ``status``, ``blocked_by``, ``role``) or the Python :class:`TeamTask`
    dataclass (``task_id`` aliased to ``id``; ``description`` doubles as
    the subject when no separate subject field is provided).
    """
    task_id = _task_field(task, "id", "task_id", default="")
    subject = _task_field(task, "subject", "description", default="")
    description = _task_field(task, "description", default="")
    status_raw = _task_field(task, "status", default="")
    # TaskStatus enum -> str.
    status = getattr(status_raw, "value", status_raw)
    blocked_by = _task_field(task, "blocked_by", default=None) or []
    role = _task_field(task, "role", default=None)

    entry = (
        f"- **Task {task_id}**: {subject}\n"
        f"  Description: {description}\n"
        f"  Status: {status}"
    )
    if blocked_by:
        entry += f"\n  Blocked by: {', '.join(blocked_by)}"
    if role:
        entry += f"\n  Role: {role}"
    return entry


def generate_initial_inbox(
    worker_name: str,
    team_name: str,
    agent_type: str,
    tasks: Sequence[Any],
    *,
    team_state_root: str | None = None,
    leader_cwd: str | None = None,
    worker_role: str | None = None,
    role_prompt_content: str | None = None,
    worktree_root_agents_canonical: bool = False,
) -> str:
    """Generate the first-launch inbox content for a worker.

    Args:
        worker_name: Worker identifier (e.g., ``"worker-1"``).
        team_name: Team name.
        agent_type: Agent type (used as the displayed role when
            ``worker_role`` is not supplied).
        tasks: Sequence of TS- or Python-shape task records.
        team_state_root: Canonical team state root. Defaults to the literal
            ``"<team_state_root>"`` placeholder.
        leader_cwd: Leader cwd. Defaults to ``"<leader_cwd>"`` placeholder.
        worker_role: Display role for the worker; falls back to ``agent_type``.
        role_prompt_content: Role-specific instruction body.
        worktree_root_agents_canonical: When ``True`` the role specialization
            block is suppressed because the worktree-scoped AGENTS.md already
            owns the role wiring (avoids duplication).
    """
    task_list = "\n".join(_format_task_entry(t) for t in tasks)

    state_root = team_state_root or "<team_state_root>"
    cwd = leader_cwd or "<leader_cwd>"
    display_role = worker_role if worker_role is not None else agent_type

    if worktree_root_agents_canonical:
        specialization_section = ""
    elif role_prompt_content:
        specialization_section = (
            "\n## Your Specialization\n"
            "\n"
            f"You are operating as a **{display_role}** agent. Follow these behavioral guidelines:\n"
            "\n"
            f"{role_prompt_content}\n"
        )
    else:
        specialization_section = ""

    return (
        f"# Worker Assignment: {worker_name}\n"
        "\n"
        f"**Team:** {team_name}\n"
        f"**Role:** {display_role}\n"
        f"**Worker Name:** {worker_name}\n"
        "\n"
        "## Your Assigned Tasks\n"
        "\n"
        f"{task_list}\n"
        "\n"
        "## Instructions\n"
        "\n"
        "1. Load and follow the worker skill from the first existing path:\n"
        "   - `${CODEX_HOME:-~/.codex}/skills/worker/SKILL.md`\n"
        f"   - `{cwd}/.codex/skills/worker/SKILL.md`\n"
        f"   - `{cwd}/skills/worker/SKILL.md` (repo fallback)\n"
        "2. Send startup ACK to the lead mailbox BEFORE any task work (run this exact command):\n"
        "\n"
        f'   `omx team api send-message --input "{{\\"team_name\\":\\"{team_name}\\",\\"from_worker\\":\\"{worker_name}\\",\\"to_worker\\":\\"leader-fixed\\",\\"body\\":\\"ACK: {worker_name} initialized\\"}}" --json`\n'
        "\n"
        "3. Start with the first non-blocked task\n"
        "4. Resolve canonical team state root in this order: `OMX_TEAM_STATE_ROOT` env -> worker identity `team_state_root` -> config/manifest `team_state_root` -> local cwd fallback.\n"
        f"5. Read the task file for your selected task id at `{state_root}/team/{team_name}/tasks/task-<id>.json` (example: `task-1.json`)\n"
        "6. Task id format:\n"
        '   - State/MCP APIs use `task_id: "<id>"` (example: `"1"`), not `"task-1"`.\n'
        "7. Request a claim via CLI interop (`omx team api claim-task --json`) to claim it\n"
        "8. Complete the work described in the task\n"
        "9. After completing work, commit your changes before reporting completion:\n"
        '   `git add -A && git commit -m "task: <task-subject>"`\n'
        "   This ensures your changes are available for incremental integration into the leader branch.\n"
        '10. Complete/fail it via lifecycle transition API (`omx team api transition-task-status --json`) from `"in_progress"` to `"completed"` or `"failed"` (include `result`/`error`)\n'
        "11. Use `omx team api release-task-claim --json` only for rollback to `pending`\n"
        f'12. Write `{{"state": "idle", "updated_at": "<current ISO timestamp>"}}` to `{state_root}/team/{team_name}/workers/{worker_name}/status.json`\n'
        "13. Wait for the next instruction from the lead\n"
        f"14. For legacy team_* MCP tools (hard-deprecated), use `omx team api`; do not pass `workingDirectory` unless the lead explicitly asks (if resolution fails, use leader cwd: `{cwd}`)\n"
        "\n"
        "## Mailbox Delivery Protocol (Required)\n"
        "When you are notified about mailbox messages, always follow this exact flow:\n"
        "\n"
        "1. List mailbox:\n"
        f'   `omx team api mailbox-list --input "{{\\"team_name\\":\\"{team_name}\\",\\"worker\\":\\"{worker_name}\\"}}" --json`\n'
        "2. For each undelivered message, mark delivery:\n"
        f'   `omx team api mailbox-mark-delivered --input "{{\\"team_name\\":\\"{team_name}\\",\\"worker\\":\\"{worker_name}\\",\\"message_id\\":\\"<MESSAGE_ID>\\"}}" --json`\n'
        "\n"
        "Use terse ACK bodies (single line) for consistent parsing across Codex and Claude workers.\n"
        "After any mailbox reply, continue executing your assigned work or the next feasible task; do not stop after sending the reply.\n"
        "\n"
        "## Message Protocol\n"
        "When using `omx team api send-message`, ALWAYS include from_worker with YOUR worker name:\n"
        f'- from_worker: "{worker_name}"\n'
        '- to_worker: "leader-fixed" (for leader) or "worker-N" (for peers)\n'
        "\n"
        f'Example: omx team api send-message --input "{{\\"team_name\\":\\"{team_name}\\",\\"from_worker\\":\\"{worker_name}\\",\\"to_worker\\":\\"leader-fixed\\",\\"body\\":\\"ACK: initialized\\"}}" --json\n'
        "\n"
        f"{_build_verification_section('each assigned task')}\n"
        "\n"
        "## Scope Rules\n"
        "- Only edit files described in your task descriptions\n"
        "- Do NOT edit files that belong to other workers\n"
        '- If you need to modify a shared/common file, write `{"state": "blocked", "reason": "need to edit shared file X"}` to your status file and wait\n'
        "- You may spawn Codex native subagents when parallel execution improves throughput.\n"
        "- Use subagents only for independent, bounded subtasks that can run safely within this worker pane.\n"
        f"{specialization_section}"
    )


def generate_task_assignment_inbox(
    worker_name: str,
    team_name: str,
    task_id: str,
    task_description: str,
) -> str:
    """Generate inbox content for a follow-up task assignment."""
    return (
        "# New Task Assignment\n"
        "\n"
        f"**Worker:** {worker_name}\n"
        f"**Task ID:** {task_id}\n"
        "\n"
        "## Task Description\n"
        "\n"
        f"{task_description}\n"
        "\n"
        "## Instructions\n"
        "\n"
        f"1. Resolve canonical team state root and read the task file at `<team_state_root>/team/{team_name}/tasks/task-{task_id}.json`\n"
        "2. Task id format:\n"
        f'   - State/MCP APIs use `task_id: "{task_id}"` (not `"task-{task_id}"`).\n'
        "3. Request a claim via CLI interop (`omx team api claim-task --json`)\n"
        "4. Complete the work\n"
        "5. After completing work, commit your changes before reporting completion:\n"
        '   `git add -A && git commit -m "task: <task-subject>"`\n'
        "   This ensures your changes are available for incremental integration into the leader branch.\n"
        '6. Complete/fail via lifecycle transition API (`omx team api transition-task-status --json`) from `"in_progress"` to `"completed"` or `"failed"` (include `result`/`error`)\n'
        "7. Use `omx team api release-task-claim --json` only for rollback to `pending`\n"
        '8. Write `{"state": "idle", "updated_at": "<current ISO timestamp>"}` to your status file\n'
        "\n"
        f"{_build_verification_section(task_description)}\n"
    )


def generate_shutdown_inbox(team_name: str, worker_name: str) -> str:
    """Generate inbox content for a shutdown notification."""
    return (
        "# Shutdown Request\n"
        "\n"
        "All tasks are complete. Please wrap up any remaining work and respond with a shutdown acknowledgement.\n"
        "\n"
        "## Shutdown Ack Protocol\n"
        "1. Write your decision to:\n"
        f"   `<team_state_root>/team/{team_name}/workers/{worker_name}/shutdown-ack.json`\n"
        "2. Format:\n"
        "   - Accept:\n"
        '     `{"status":"accept","reason":"ok","updated_at":"<iso>"}`\n'
        "   - Reject:\n"
        '     `{"status":"reject","reason":"still working","updated_at":"<iso>"}`\n'
        "3. After writing the ack, exit your Codex session.\n"
        "\n"
        "Type `exit` or press Ctrl+C to end your Codex session.\n"
    )


# ---------------------------------------------------------------------------
# Trigger messages — short tmux send-keys text + structured directives.
# ---------------------------------------------------------------------------


def _build_instruction_path(*parts: str) -> str:
    """Join path parts and normalize separators to forward slashes."""
    return os.path.join(*parts).replace("\\", "/")


def generate_trigger_message(
    worker_name: str,
    team_name: str,
    team_state_root: str = ".omx/state",
) -> str:
    """Generate the short send-keys trigger message (ASCII-safe, < 200 chars)."""
    return build_trigger_directive(worker_name, team_name, team_state_root).text


def build_trigger_directive(
    worker_name: str,
    team_name: str,
    team_state_root: str = ".omx/state",
) -> TeamReminderDirective:
    """Structured ``followup-relaunch`` directive for a worker inbox."""
    inbox_path = _build_instruction_path(
        team_state_root, "team", team_name, "workers", worker_name, "inbox.md"
    )
    if team_state_root != ".omx/state":
        return TeamReminderDirective(
            intent="followup-relaunch",
            text=(
                f"Read {inbox_path}, work now, report progress, "
                "continue assigned work or next feasible task."
            ),
        )
    return TeamReminderDirective(
        intent="followup-relaunch",
        text=(
            f"Read {inbox_path}, start work now, report concrete progress, "
            "then continue assigned work or next feasible task."
        ),
    )


def generate_mailbox_trigger_message(
    worker_name: str,
    team_name: str,
    count: int,
    team_state_root: str = ".omx/state",
) -> str:
    """Short mailbox trigger message (ASCII-safe, < 200 chars)."""
    return build_mailbox_trigger_directive(
        worker_name, team_name, count, team_state_root
    ).text


def build_mailbox_trigger_directive(
    worker_name: str,
    team_name: str,
    count: int,
    team_state_root: str = ".omx/state",
) -> TeamReminderDirective:
    """Structured ``pending-mailbox-review`` directive for a worker mailbox."""
    try:
        normalized = int(count)
        if normalized < 1:
            normalized = 1
    except (TypeError, ValueError):
        normalized = 1
    mailbox_path = _build_instruction_path(
        team_state_root, "team", team_name, "mailbox", f"{worker_name}.json"
    )
    if team_state_root != ".omx/state":
        return TeamReminderDirective(
            intent="pending-mailbox-review",
            text=(
                f"{normalized} new msg(s): read {mailbox_path}, act, "
                "report progress, continue assigned work or next feasible task."
            ),
        )
    return TeamReminderDirective(
        intent="pending-mailbox-review",
        text=(
            f"You have {normalized} new message(s). Read {mailbox_path}, "
            "act now, reply with concrete progress, then continue assigned "
            "work or next feasible task."
        ),
    )


def generate_leader_mailbox_trigger_message(
    team_name: str,
    from_worker: str,
    team_state_root: str = ".omx/state",
) -> str:
    """Short leader-mailbox trigger message (ASCII-safe, < 200 chars)."""
    return build_leader_mailbox_trigger_directive(
        team_name, from_worker, team_state_root
    ).text


def build_leader_mailbox_trigger_directive(
    team_name: str,
    from_worker: str,
    team_state_root: str = ".omx/state",
) -> TeamReminderDirective:
    """Structured ``pending-mailbox-review`` directive for the leader mailbox."""
    mailbox_path = _build_instruction_path(
        team_state_root, "team", team_name, "mailbox", "leader-fixed.json"
    )
    if team_state_root != ".omx/state":
        return TeamReminderDirective(
            intent="pending-mailbox-review",
            text=(
                f"Read {mailbox_path}; new msg from {from_worker}. "
                "Review it; decide next step."
            ),
        )
    return TeamReminderDirective(
        intent="pending-mailbox-review",
        text=(
            f"Read {mailbox_path}; {from_worker} sent a new message. "
            "Review it and decide the next concrete step."
        ),
    )
