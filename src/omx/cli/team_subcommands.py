"""Team sub-subcommand handlers.

Port of the team sub-subcommands from ``src/cli/team.ts``:

* ``omx team status [team-name] [--json]``
* ``omx team shutdown [team-name] [--force] [--confirm-issues]``
* ``omx team resume [team-name]``
* ``omx team scale-up <team-name> <count> [--agent-type <type>]``
* ``omx team scale-down <team-name> [--count N | --worker <name> ...] [--force]``
* ``omx team reassign <team-name> <task-id> <to-worker> [--from <worker>]``
* ``omx team send-message <team-name> <from-worker> <to-worker> <body>``
* ``omx team broadcast <team-name> <from-worker> <body>``

Each handler is sync, stdlib-only, and returns nothing — it prints to
stdout/stderr and exits via ``sys.exit`` on error. ``--json`` outputs a
machine-readable envelope. Failures exit code 1 with a stderr message
(TS parity).
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_json_error(command: str, message: str) -> None:
    """Emit a JSON error envelope and exit 1."""
    print(
        json.dumps(
            {
                "schema_version": "1.0",
                "timestamp": _now_iso(),
                "ok": False,
                "command": command,
                "error": {"code": "invalid_input", "message": message},
            }
        )
    )


def _resolve_default_team_name(cwd: str) -> str | None:
    """Return the most-recent team in ``.omx/team/`` or ``None`` if none."""
    team_dir = Path(cwd) / ".omx" / "team"
    if not team_dir.exists():
        return None
    teams = sorted(
        (d.name for d in team_dir.iterdir() if d.is_dir()),
        reverse=True,
    )
    return teams[0] if teams else None


# ---------------------------------------------------------------------------
# omx team status
# ---------------------------------------------------------------------------


def handle_team_status(args: list[str]) -> None:
    """Show team status snapshot. Optional ``[team-name] [--json]``."""
    cwd = str(Path.cwd())
    wants_json = "--json" in args
    positional = [a for a in args if not a.startswith("--")]
    team_name = positional[0] if positional else _resolve_default_team_name(cwd)

    if not team_name:
        if wants_json:
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "timestamp": _now_iso(),
                        "command": "omx team status",
                        "status": "missing",
                    }
                )
            )
            return
        print("No teams found.")
        return

    from omx.team.runtime_monitor import monitor_team_ts

    try:
        snapshot = monitor_team_ts(team_name, cwd, measure_performance=False)
    except Exception as exc:  # noqa: BLE001
        if wants_json:
            _print_json_error("omx team status", str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if snapshot is None:
        if wants_json:
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "timestamp": _now_iso(),
                        "command": "omx team status",
                        "team_name": team_name,
                        "status": "missing",
                    }
                )
            )
            return
        print(f"No team state found for {team_name}")
        return

    if wants_json:
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "timestamp": _now_iso(),
            "command": "omx team status",
            "team_name": snapshot.team_name,
            "status": "ok",
            "phase": snapshot.phase,
            "all_tasks_terminal": snapshot.all_tasks_terminal,
            "dead_workers": list(snapshot.dead_workers),
            "non_reporting_workers": list(snapshot.non_reporting_workers),
            "workers": {
                "total": len(snapshot.workers),
                "dead": len(snapshot.dead_workers),
                "non_reporting": len(snapshot.non_reporting_workers),
            },
            "tasks": snapshot.tasks.to_dict(),
        }
        print(json.dumps(payload))
        return

    print(f"team={snapshot.team_name} phase={snapshot.phase}")
    print(
        f"workers: total={len(snapshot.workers)} "
        f"dead={len(snapshot.dead_workers)} "
        f"non_reporting={len(snapshot.non_reporting_workers)}"
    )
    if snapshot.dead_workers:
        print(f"dead_workers: {' '.join(snapshot.dead_workers)}")
    if snapshot.non_reporting_workers:
        print(f"non_reporting_workers: {' '.join(snapshot.non_reporting_workers)}")
    tasks = snapshot.tasks
    print(
        f"tasks: total={tasks.total} pending={tasks.pending} "
        f"blocked={tasks.blocked} in_progress={tasks.in_progress} "
        f"completed={tasks.completed} failed={tasks.failed}"
    )
    if snapshot.all_tasks_terminal:
        print("All tasks complete.")


# ---------------------------------------------------------------------------
# omx team shutdown
# ---------------------------------------------------------------------------


def handle_team_shutdown(args: list[str]) -> None:
    """Shutdown a team (graceful). ``[team-name] [--force] [--confirm-issues]``."""
    cwd = str(Path.cwd())
    force = "--force" in args
    confirm_issues = "--confirm-issues" in args
    wants_json = "--json" in args
    positional = [a for a in args if not a.startswith("--")]
    team_name = positional[0] if positional else _resolve_default_team_name(cwd)

    if not team_name:
        if wants_json:
            _print_json_error("omx team shutdown", "team name required")
        else:
            print("Error: team name required", file=sys.stderr)
        sys.exit(1)

    from omx.team.runtime_shutdown import shutdown_team
    from omx.team.runtime_types import ShutdownOptions

    try:
        summary = shutdown_team(
            team_name,
            cwd,
            ShutdownOptions(force=force, confirm_issues=confirm_issues),
        )
    except Exception as exc:  # noqa: BLE001
        if wants_json:
            _print_json_error("omx team shutdown", str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if wants_json:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": _now_iso(),
                    "ok": True,
                    "command": "omx team shutdown",
                    "team_name": team_name,
                    "summary": summary.to_dict(),
                }
            )
        )
        return

    print(f"Team shutdown complete: {team_name}")
    artifacts = summary.commit_hygiene_artifacts
    if artifacts:
        if isinstance(artifacts, dict):
            json_path = artifacts.get("json_path") or artifacts.get("jsonPath")
            md_path = artifacts.get("markdown_path") or artifacts.get("markdownPath")
            if json_path:
                print(f"commit_hygiene_context_json: {json_path}")
            if md_path:
                print(f"commit_hygiene_context_md: {md_path}")


# ---------------------------------------------------------------------------
# omx team resume
# ---------------------------------------------------------------------------


def handle_team_resume(args: list[str]) -> None:
    """Resume a team. ``[team-name]``."""
    cwd = str(Path.cwd())
    wants_json = "--json" in args
    positional = [a for a in args if not a.startswith("--")]
    team_name = positional[0] if positional else _resolve_default_team_name(cwd)

    if not team_name:
        if wants_json:
            _print_json_error("omx team resume", "team name required")
        else:
            print("Error: team name required", file=sys.stderr)
        sys.exit(1)

    from omx.team.runtime_resume import resume_team

    try:
        runtime = resume_team(team_name, cwd)
    except Exception as exc:  # noqa: BLE001
        if wants_json:
            _print_json_error("omx team resume", str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if runtime is None:
        if wants_json:
            print(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "timestamp": _now_iso(),
                        "command": "omx team resume",
                        "team_name": team_name,
                        "status": "missing",
                    }
                )
            )
            return
        print(f"No resumable team found for {team_name}")
        return

    if wants_json:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": _now_iso(),
                    "ok": True,
                    "command": "omx team resume",
                    "team_name": runtime.team_name,
                    "session_name": runtime.session_name,
                }
            )
        )
        return

    print(f"Resumed team: {runtime.team_name}")
    if runtime.session_name:
        print(f"Session: {runtime.session_name}")


# ---------------------------------------------------------------------------
# omx team scale-up
# ---------------------------------------------------------------------------


def _parse_named_arg(args: list[str], name: str) -> str | None:
    """Return the value following ``--<name>`` or ``None``."""
    for i, token in enumerate(args):
        if token == f"--{name}":
            if i + 1 < len(args):
                return args[i + 1]
            return None
        prefix = f"--{name}="
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def handle_team_scale_up(args: list[str]) -> None:
    """Scale up a team. ``<team-name> <count> [--agent-type X]``."""
    cwd = str(Path.cwd())
    wants_json = "--json" in args
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) < 2:
        msg = "Usage: omx team scale-up <team-name> <count> [--agent-type <type>]"
        if wants_json:
            _print_json_error("omx team scale-up", msg)
        else:
            print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    team_name = positional[0]
    try:
        count = int(positional[1])
    except ValueError:
        msg = f"Invalid count: {positional[1]}"
        if wants_json:
            _print_json_error("omx team scale-up", msg)
        else:
            print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    agent_type = _parse_named_arg(args, "agent-type") or "executor"

    from omx.team.scaling import ScaleError, scale_up

    try:
        result = scale_up(team_name, count, agent_type, [], cwd)
    except Exception as exc:  # noqa: BLE001
        if wants_json:
            _print_json_error("omx team scale-up", str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if isinstance(result, ScaleError):
        if wants_json:
            _print_json_error("omx team scale-up", result.error)
        else:
            print(f"Error: {result.error}", file=sys.stderr)
        sys.exit(1)

    if wants_json:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": _now_iso(),
                    "ok": True,
                    "command": "omx team scale-up",
                    "team_name": team_name,
                    "added_workers": [
                        w.get("name") if isinstance(w, dict) else str(w)
                        for w in result.added_workers
                    ],
                    "new_worker_count": result.new_worker_count,
                }
            )
        )
        return

    names = [
        w.get("name") if isinstance(w, dict) else str(w) for w in result.added_workers
    ]
    print(f"Scaled up team {team_name}: +{len(result.added_workers)} workers")
    if names:
        print(f"Added: {' '.join(n for n in names if n)}")
    print(f"New worker count: {result.new_worker_count}")


# ---------------------------------------------------------------------------
# omx team scale-down
# ---------------------------------------------------------------------------


def handle_team_scale_down(args: list[str]) -> None:
    """Scale down a team. ``<team-name> [--count N] [--worker name ...] [--force]``."""
    cwd = str(Path.cwd())
    wants_json = "--json" in args
    force = "--force" in args
    positional = [a for a in args if not a.startswith("--")]

    if not positional:
        msg = "Usage: omx team scale-down <team-name> [--count N | --worker NAME] [--force]"
        if wants_json:
            _print_json_error("omx team scale-down", msg)
        else:
            print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    team_name = positional[0]
    worker_names: list[str] | None = None
    count: int | None = None

    # collect --worker repeats (positional after first) or --worker=NAME
    workers_collected: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token == "--worker" and i + 1 < len(args):
            workers_collected.append(args[i + 1])
            i += 2
            continue
        if token.startswith("--worker="):
            workers_collected.append(token[len("--worker=") :])
            i += 1
            continue
        i += 1
    if workers_collected:
        worker_names = workers_collected

    count_raw = _parse_named_arg(args, "count")
    if count_raw is not None:
        try:
            count = int(count_raw)
        except ValueError:
            msg = f"Invalid --count: {count_raw}"
            if wants_json:
                _print_json_error("omx team scale-down", msg)
            else:
                print(f"Error: {msg}", file=sys.stderr)
            sys.exit(1)

    from omx.team.scaling import ScaleError
    from omx.team.scaling_down import ScaleDownOptions, scale_down

    options = ScaleDownOptions(worker_names=worker_names, count=count, force=force)

    try:
        result = scale_down(team_name, cwd, options)
    except Exception as exc:  # noqa: BLE001
        if wants_json:
            _print_json_error("omx team scale-down", str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if isinstance(result, ScaleError):
        if wants_json:
            _print_json_error("omx team scale-down", result.error)
        else:
            print(f"Error: {result.error}", file=sys.stderr)
        sys.exit(1)

    if wants_json:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": _now_iso(),
                    "ok": True,
                    "command": "omx team scale-down",
                    "team_name": team_name,
                    "removed_workers": list(result.removed_workers),
                    "new_worker_count": result.new_worker_count,
                }
            )
        )
        return

    print(f"Scaled down team {team_name}: -{len(result.removed_workers)} workers")
    if result.removed_workers:
        print(f"Removed: {' '.join(result.removed_workers)}")
    print(f"New worker count: {result.new_worker_count}")


# ---------------------------------------------------------------------------
# omx team reassign
# ---------------------------------------------------------------------------


def handle_team_reassign(args: list[str]) -> None:
    """Reassign a task. ``<team-name> <task-id> <to-worker> [--from <worker>]``."""
    wants_json = "--json" in args
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) < 3:
        msg = (
            "Usage: omx team reassign <team-name> <task-id> <to-worker> "
            "[--from <worker>]"
        )
        if wants_json:
            _print_json_error("omx team reassign", msg)
        else:
            print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    team_name = positional[0]
    task_id = positional[1]
    to_worker = positional[2]
    from_worker = _parse_named_arg(args, "from") or ""

    cwd = str(Path.cwd())
    from omx.team.runtime_assign import reassign_task

    try:
        reassign_task(team_name, task_id, from_worker, to_worker, cwd)
    except Exception as exc:  # noqa: BLE001
        if wants_json:
            _print_json_error("omx team reassign", str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if wants_json:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": _now_iso(),
                    "ok": True,
                    "command": "omx team reassign",
                    "team_name": team_name,
                    "task_id": task_id,
                    "to_worker": to_worker,
                }
            )
        )
        return

    print(f"Reassigned task {task_id} to {to_worker} on {team_name}")


# ---------------------------------------------------------------------------
# omx team send-message
# ---------------------------------------------------------------------------


def handle_team_send_message(args: list[str]) -> None:
    """Send a mailbox message. ``<team-name> <from> <to> <body>``."""
    wants_json = "--json" in args
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) < 4:
        msg = (
            "Usage: omx team send-message <team-name> <from-worker> <to-worker> <body>"
        )
        if wants_json:
            _print_json_error("omx team send-message", msg)
        else:
            print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    team_name = positional[0]
    from_worker = positional[1]
    to_worker = positional[2]
    body = " ".join(positional[3:])

    cwd = str(Path.cwd())
    from omx.team.runtime_messaging import send_worker_message

    try:
        outcome = send_worker_message(team_name, from_worker, to_worker, body, cwd)
    except Exception as exc:  # noqa: BLE001
        if wants_json:
            _print_json_error("omx team send-message", str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if wants_json:
        outcome_payload: dict[str, Any] = {
            "ok": bool(getattr(outcome, "ok", True)),
            "transport": getattr(outcome, "transport", None),
            "reason": getattr(outcome, "reason", None),
        }
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": _now_iso(),
                    "ok": True,
                    "command": "omx team send-message",
                    "team_name": team_name,
                    "from_worker": from_worker,
                    "to_worker": to_worker,
                    "outcome": outcome_payload,
                }
            )
        )
        return

    transport = getattr(outcome, "transport", "?")
    print(
        f"Sent message {from_worker} -> {to_worker} on {team_name} (transport={transport})"
    )


# ---------------------------------------------------------------------------
# omx team broadcast
# ---------------------------------------------------------------------------


def handle_team_broadcast(args: list[str]) -> None:
    """Broadcast a mailbox message. ``<team-name> <from-worker> <body>``."""
    wants_json = "--json" in args
    positional = [a for a in args if not a.startswith("--")]

    if len(positional) < 3:
        msg = "Usage: omx team broadcast <team-name> <from-worker> <body>"
        if wants_json:
            _print_json_error("omx team broadcast", msg)
        else:
            print(f"Error: {msg}", file=sys.stderr)
        sys.exit(1)

    team_name = positional[0]
    from_worker = positional[1]
    body = " ".join(positional[2:])

    cwd = str(Path.cwd())
    from omx.team.runtime_messaging import broadcast_worker_message

    try:
        broadcast_worker_message(team_name, from_worker, body, cwd)
    except Exception as exc:  # noqa: BLE001
        if wants_json:
            _print_json_error("omx team broadcast", str(exc))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if wants_json:
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "timestamp": _now_iso(),
                    "ok": True,
                    "command": "omx team broadcast",
                    "team_name": team_name,
                    "from_worker": from_worker,
                }
            )
        )
        return

    print(f"Broadcast from {from_worker} on {team_name}")


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


TEAM_SUBCOMMANDS = {
    "status": handle_team_status,
    "shutdown": handle_team_shutdown,
    "resume": handle_team_resume,
    "scale-up": handle_team_scale_up,
    "scale-down": handle_team_scale_down,
    "reassign": handle_team_reassign,
    "send-message": handle_team_send_message,
    "broadcast": handle_team_broadcast,
}


def dispatch_team_subcommand(subcommand: str, args: list[str]) -> bool:
    """Dispatch a team sub-subcommand by name.

    Args:
        subcommand: e.g. ``"status"``, ``"shutdown"``, etc.
        args: Arguments after the subcommand token.

    Returns:
        ``True`` if a sub-subcommand was matched and dispatched,
        ``False`` if ``subcommand`` is not a known sub-subcommand.
    """
    handler = TEAM_SUBCOMMANDS.get(subcommand)
    if handler is None:
        return False
    handler(args)
    return True
