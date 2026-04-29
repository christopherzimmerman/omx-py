"""Team orchestrator runtime — full orchestration loop.

Port of src/team/runtime.ts.
The leader spawns workers, writes inbox files, injects trigger messages
via tmux send-keys, and polls worker status to dispatch follow-up tasks.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.team.allocation_policy import choose_task_owner
from omx.team.contracts import (
    TaskStatus,
    TeamEvent,
    TeamTask,
    TeamWorker,
)
from omx.team.followup_planner import all_tasks_terminal, get_pending_tasks
from omx.team.state.io import (
    append_team_event,
    read_tasks,
    read_team_config,
    read_worker_heartbeat,
    read_worker_status,
    read_workers,
    write_tasks,
    write_worker_inbox,
    write_worker_status,
    write_workers,
)
from omx.team.tmux_session import (
    capture_pane,
    send_to_worker,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_inbox_prompt(task: TeamTask, worker_name: str, team_name: str) -> str:
    """Build the markdown inbox content for a worker's task assignment.

    Args:
        task: The task being assigned.
        worker_name: Name of the worker.
        team_name: Name of the team.

    Returns:
        Markdown string for the inbox file.
    """
    lines = [
        f"# Task: {task.task_id}",
        "",
        f"**Role:** {task.role or 'executor'}",
        f"**Assigned to:** {worker_name}",
        "",
        "## Description",
        "",
        task.description,
        "",
    ]
    if task.file_paths:
        lines.extend(["## Scope", ""])
        for fp in task.file_paths:
            lines.append(f"- `{fp}`")
        lines.append("")
    lines.extend(
        [
            "## Instructions",
            "",
            "1. Read this file to understand your task",
            "2. Execute the task in your working directory",
            "3. Report progress via commits or status updates",
            "4. When done, the leader will detect completion",
            "",
        ]
    )
    return "\n".join(lines)


def build_trigger_message(team_name: str, worker_name: str, cwd: str) -> str:
    """Build the short trigger text sent via tmux send-keys.

    Args:
        team_name: Team name for path resolution.
        worker_name: Worker name.
        cwd: Working directory.

    Returns:
        Short trigger string that tells the CLI to read the inbox.
    """
    inbox_path = (
        Path(cwd) / ".omx" / "team" / team_name / "workers" / worker_name / "inbox.md"
    )
    return f"Read {inbox_path}, start work now, report concrete progress, then continue assigned work or next feasible task."


def dispatch_task_to_worker(
    cwd: str,
    team_name: str,
    task: TeamTask,
    worker: TeamWorker,
    worker_cli: str = "codex",
) -> bool:
    """Write inbox file and send trigger to a worker pane.

    Args:
        cwd: Working directory.
        team_name: Team name.
        task: Task to dispatch.
        worker: Worker to dispatch to.
        worker_cli: CLI type for send-keys behavior.

    Returns:
        True if the trigger was likely consumed.
    """
    inbox_content = build_inbox_prompt(task, worker.worker_id, team_name)
    write_worker_inbox(cwd, team_name, worker.worker_id, inbox_content)
    write_worker_status(cwd, team_name, worker.worker_id, "working", task.task_id)

    trigger = build_trigger_message(team_name, worker.worker_id, cwd)
    return send_to_worker(worker.pane_id, trigger, worker_cli)


def assign_pending_tasks(cwd: str, team_name: str = "default") -> list[str]:
    """Assign pending tasks to available workers and dispatch via tmux.

    Args:
        cwd: Working directory.
        team_name: Team name.

    Returns:
        List of assigned task IDs.
    """
    tasks = read_tasks(cwd, team_name)
    workers = read_workers(cwd, team_name)
    config = read_team_config(cwd, team_name)
    worker_cli = config.get("worker_cli", "codex")
    pending = get_pending_tasks(tasks)

    if not pending or not workers:
        return []

    assigned_ids: list[str] = []
    for task in pending:
        decision = choose_task_owner(task, workers, tasks)
        if decision is None:
            continue

        task.status = TaskStatus.IN_PROGRESS
        task.owner = decision.owner
        task.started_at = _now_iso()

        for w in workers:
            if w.worker_id == decision.owner:
                w.current_task_id = task.task_id
                w.assigned_tasks.append(task.task_id)
                w.status = "busy"
                dispatch_task_to_worker(cwd, team_name, task, w, worker_cli)
                break

        assigned_ids.append(task.task_id)
        append_team_event(
            cwd,
            TeamEvent(
                event_type="task_assigned",
                timestamp=_now_iso(),
                worker_id=decision.owner,
                task_id=task.task_id,
                detail={"reason": decision.reason},
            ),
            team_name,
        )

    write_tasks(cwd, tasks, team_name)
    write_workers(cwd, workers, team_name)
    return assigned_ids


def mark_task_completed(
    cwd: str, task_id: str, worker_id: str | None = None, team_name: str = "default"
) -> None:
    """Mark a task as completed."""
    tasks = read_tasks(cwd, team_name)
    for task in tasks:
        if task.task_id == task_id:
            task.status = TaskStatus.COMPLETED
            task.completed_at = _now_iso()
            break
    write_tasks(cwd, tasks, team_name)
    append_team_event(
        cwd,
        TeamEvent(
            event_type="task_completed",
            timestamp=_now_iso(),
            worker_id=worker_id,
            task_id=task_id,
        ),
        team_name,
    )


def mark_task_failed(
    cwd: str,
    task_id: str,
    error: str,
    worker_id: str | None = None,
    team_name: str = "default",
) -> None:
    """Mark a task as failed."""
    tasks = read_tasks(cwd, team_name)
    for task in tasks:
        if task.task_id == task_id:
            task.status = TaskStatus.FAILED
            task.completed_at = _now_iso()
            task.error = error
            break
    write_tasks(cwd, tasks, team_name)
    append_team_event(
        cwd,
        TeamEvent(
            event_type="task_failed",
            timestamp=_now_iso(),
            worker_id=worker_id,
            task_id=task_id,
            detail={"error": error},
        ),
        team_name,
    )


def check_team_completion(cwd: str, team_name: str = "default") -> bool:
    """Check if all tasks are in terminal state."""
    return all_tasks_terminal(read_tasks(cwd, team_name))


def monitor_team(cwd: str, team_name: str = "default") -> dict[str, Any]:
    """Poll worker status and return a team snapshot.

    Args:
        cwd: Working directory.
        team_name: Team name.

    Returns:
        Dict with workers, tasks, and recommendations.
    """
    workers = read_workers(cwd, team_name)
    tasks = read_tasks(cwd, team_name)

    worker_snapshots: list[dict[str, Any]] = []
    dead_workers: list[str] = []

    for w in workers:
        status = read_worker_status(cwd, team_name, w.worker_id)
        heartbeat = read_worker_heartbeat(cwd, team_name, w.worker_id)

        alive = True
        if w.pane_id:
            captured = capture_pane(w.pane_id, lines=5)
            alive = bool(captured.strip())

        if not alive:
            dead_workers.append(w.worker_id)

        worker_snapshots.append(
            {
                "name": w.worker_id,
                "pane_id": w.pane_id,
                "role": w.role,
                "alive": alive,
                "status": status.get("state", "unknown") if status else "unknown",
                "current_task": status.get("current_task_id") if status else None,
                "heartbeat": heartbeat,
            }
        )

    task_counts = {
        "total": len(tasks),
        "pending": sum(1 for t in tasks if t.status == TaskStatus.PENDING),
        "in_progress": sum(1 for t in tasks if t.status == TaskStatus.IN_PROGRESS),
        "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
        "failed": sum(1 for t in tasks if t.status == TaskStatus.FAILED),
    }

    recommendations: list[str] = []
    if dead_workers:
        recommendations.append(f"Dead workers: {', '.join(dead_workers)}")
    if task_counts["pending"] > 0 and not dead_workers:
        recommendations.append(
            f"{task_counts['pending']} pending tasks ready for assignment"
        )

    return {
        "team_name": team_name,
        "workers": worker_snapshots,
        "tasks": task_counts,
        "all_tasks_terminal": all_tasks_terminal(tasks),
        "dead_workers": dead_workers,
        "recommendations": recommendations,
    }
