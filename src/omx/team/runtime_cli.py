"""CLI entry point for team runtime operations.

Port of src/team/runtime-cli.ts.
"""

from __future__ import annotations

import sys

from omx.team.runtime import (
    monitor_team,
)


def run_team_status(team_name: str, cwd: str, watch: bool = False) -> None:
    """Display team status.

    Args:
        team_name: Team name.
        cwd: Working directory.
        watch: If True, poll continuously.
    """
    import time

    while True:
        snapshot = monitor_team(cwd, team_name)
        if not snapshot:
            print("No team found.", file=sys.stderr)
            return

        tasks = snapshot.get("tasks", {})
        workers = snapshot.get("workers", [])

        print(f"\n=== Team: {team_name} ===")
        print(
            f"Tasks: {tasks.get('total', 0)} total, "
            f"{tasks.get('pending', 0)} pending, "
            f"{tasks.get('in_progress', 0)} in progress, "
            f"{tasks.get('completed', 0)} completed, "
            f"{tasks.get('failed', 0)} failed"
        )
        print(f"Workers: {len(workers)}")
        for w in workers:
            status = w.get("status", "unknown")
            alive = "alive" if w.get("alive") else "DEAD"
            task = w.get("current_task", "-")
            print(f"  {w['name']:15s} {status:10s} {alive:5s} task={task}")

        if snapshot.get("recommendations"):
            print("Recommendations:")
            for r in snapshot["recommendations"]:
                print(f"  - {r}")

        if snapshot.get("all_tasks_terminal"):
            print("\nAll tasks complete.")

        if not watch:
            break

        time.sleep(5)
