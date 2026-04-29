"""Worker process bootstrap and worktree management.

Port of src/team/worker-bootstrap.ts.
Handles worker initialization, identity file creation, and model
instruction generation.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from omx.team.state.types import WorkerInfo
from omx.team.state_root import team_dir


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

    # Write identity file
    identity_path = worker_dir / "identity.json"
    identity = {
        **info.to_dict(),
        "team_name": team_name,
        "bootstrapped_at": datetime.now(timezone.utc).isoformat(),
    }
    identity_path.write_text(json.dumps(identity, indent=2), encoding="utf-8")

    # Write initial status
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

    # Generate model instructions (AGENTS.md for the role)
    _write_model_instructions(td, worker_name, role, cwd)

    return info


def _write_model_instructions(
    td: Path,
    worker_name: str,
    role: str,
    cwd: str,
) -> None:
    """Generate model instructions file for a worker.

    Args:
        td: Team directory path.
        worker_name: Worker name.
        role: Agent role.
        cwd: Working directory.
    """
    instructions_path = td / "workers" / worker_name / "model-instructions.md"

    # Try to find role-specific prompt
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
    """Clean up a worker's resources (worktree, state files).

    Args:
        team_name: Team name.
        worker_name: Worker name.
        cwd: Working directory.
    """
    td = team_dir(team_name, cwd)
    worker_dir = td / "workers" / worker_name

    # Read identity to check for worktree
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
