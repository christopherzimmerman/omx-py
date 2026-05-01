"""OMX CLI — main entry point and command dispatcher.

Port of src/cli/index.ts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


KNOWN_SUBCOMMANDS = {
    "setup",
    "doctor",
    "exec",
    "team",
    "ralph",
    "explore",
    "autoresearch",
    "list",
    "cleanup",
    "state",
    "ask",
    "agents",
    "agents-init",
    "deepinit",
    "update",
    "uninstall",
    "session",
    "sparkshell",
    "hud",
    "wiki",
    "status",
    "cancel",
    "mcp-serve",
    "help",
    "resume",
    "notepad",
    "project-memory",
    "trace",
    "code-intel",
    "version",
}

# Flags that get normalized and passed through to codex on bare launch
CODEX_PASSTHROUGH_FLAGS = {
    "--madmax": "--dangerously-bypass-approvals-and-sandbox",
    "--high": "--high-reasoning",
    "--xhigh": "--xhigh-reasoning",
    "--spark": "--spark",
    "--madmax-spark": "--madmax-spark",
}


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and dispatch to the appropriate subcommand handler.

    Args:
        argv: Command-line arguments (defaults to sys.argv if None).
    """
    raw_args = argv if argv is not None else sys.argv[1:]

    # Intercept bare launch: no subcommand, or first arg is a flag/unknown
    if not raw_args or (
        raw_args[0].startswith("-") and raw_args[0] not in ("--version", "--help", "-h")
    ):
        _handle_launch_raw(raw_args)
        return

    # Check if first arg is a known subcommand
    if raw_args[0] not in KNOWN_SUBCOMMANDS and not raw_args[0].startswith("-"):
        # Unknown first arg — treat as bare launch with passthrough
        _handle_launch_raw(raw_args)
        return

    parser = argparse.ArgumentParser(
        prog="omx",
        description="Multi-agent orchestration layer for OpenAI Codex CLI",
    )
    parser.add_argument("--version", action="version", version="%(prog)s 0.15.0")

    subparsers = parser.add_subparsers(dest="command")

    # --- setup ---
    sp_setup = subparsers.add_parser(
        "setup", help="Install skills, prompts, MCP servers, AGENTS.md"
    )
    sp_setup.add_argument(
        "--force", action="store_true", help="Force reinstall all assets"
    )
    sp_setup.add_argument(
        "--dry-run", action="store_true", help="Show what would be done"
    )
    sp_setup.add_argument(
        "--scope",
        choices=["user", "project"],
        default="user",
        help="Installation scope (default: user)",
    )
    sp_setup.add_argument(
        "--target",
        choices=["codex", "claude"],
        default=None,
        help="Provider CLI target (default: $OMX_CLI or codex)",
    )
    sp_setup.add_argument("--verbose", action="store_true")

    # --- doctor ---
    sp_doctor = subparsers.add_parser("doctor", help="Check installation health")
    sp_doctor.add_argument(
        "--team", action="store_true", help="Run team/swarm diagnostics"
    )
    sp_doctor.add_argument("--verbose", action="store_true")
    sp_doctor.add_argument("--force", action="store_true")

    # --- exec ---
    sp_exec = subparsers.add_parser("exec", help="Run codex exec non-interactively")
    sp_exec.add_argument("prompt", nargs="?", help="Prompt to execute")
    sp_exec.add_argument("--model", help="Model override")

    # --- team ---
    sp_team = subparsers.add_parser("team", help="Spawn parallel worker panes in tmux")
    sp_team.add_argument("spec", nargs="?", help="Worker spec (e.g. 3:executor)")
    sp_team.add_argument("--prompt", help="Task prompt for workers")

    # --- ralph ---
    sp_ralph = subparsers.add_parser("ralph", help="Launch with ralph persistence mode")
    sp_ralph.add_argument("prompt", nargs="?", help="Initial task prompt")

    # --- explore ---
    sp_explore = subparsers.add_parser(
        "explore", help="Read-only repository exploration"
    )
    sp_explore.add_argument("--prompt", help="Exploration prompt")

    # --- resume ---
    subparsers.add_parser("resume", help="Resume previous interactive session")

    # --- autoresearch ---
    sp_autoresearch = subparsers.add_parser(
        "autoresearch", help="Run auto-research workflow"
    )
    sp_autoresearch.add_argument("task", nargs="?", help="Research task description")

    # --- list ---
    sp_list = subparsers.add_parser(
        "list", help="List packaged skills and agent prompts"
    )
    sp_list.add_argument(
        "--json", action="store_true", dest="json_output", help="Output as JSON"
    )

    # --- cleanup ---
    subparsers.add_parser("cleanup", help="Kill orphaned MCP processes")

    # --- state ---
    sp_state = subparsers.add_parser("state", help="Read/write/list OMX mode state")
    sp_state.add_argument(
        "action",
        choices=["read", "write", "clear", "list", "status"],
        help="State operation",
    )
    sp_state.add_argument("--mode", help="Workflow mode")
    sp_state.add_argument("--session-id", help="Session scope ID")
    sp_state.add_argument("--all-sessions", action="store_true")

    # --- ask ---
    sp_ask = subparsers.add_parser(
        "ask", help="Ask a local provider CLI (claude|gemini)"
    )
    sp_ask.add_argument("provider", nargs="?", choices=["claude", "gemini"])
    sp_ask.add_argument("prompt", nargs="?")

    # --- agents ---
    sp_agents = subparsers.add_parser("agents", help="Manage native agent TOML files")
    sp_agents.add_argument(
        "action", nargs="?", choices=["list", "show", "create"], default="list"
    )

    # --- agents-init / deepinit ---
    sp_agents_init = subparsers.add_parser(
        "agents-init", help="Bootstrap AGENTS.md files"
    )
    sp_agents_init.add_argument("path", nargs="?", default=".")

    sp_deepinit = subparsers.add_parser("deepinit", help="Alias for agents-init")
    sp_deepinit.add_argument("path", nargs="?", default=".")

    # --- update ---
    sp_update = subparsers.add_parser("update", help="Check and install updates")
    sp_update.add_argument(
        "--check", action="store_true", help="Check only, don't install"
    )

    # --- uninstall ---
    subparsers.add_parser("uninstall", help="Remove OMX configuration")

    # --- session ---
    sp_session = subparsers.add_parser(
        "session", help="Search prior session transcripts"
    )
    sp_session.add_argument("query", nargs="?")

    # --- sparkshell ---
    sp_spark = subparsers.add_parser("sparkshell", help="Native sparkshell sidecar")
    sp_spark.add_argument("argv", nargs=argparse.REMAINDER)
    sp_spark.add_argument("--tmux-pane", help="Target tmux pane")
    sp_spark.add_argument("--tail-lines", type=int, default=80)

    # --- hud ---
    sp_hud = subparsers.add_parser("hud", help="Show HUD statusline")
    sp_hud.add_argument("--watch", action="store_true")
    sp_hud.add_argument("--json", action="store_true", dest="json_output")
    sp_hud.add_argument("--preset", help="HUD preset name")

    # --- wiki ---
    sp_wiki = subparsers.add_parser("wiki", help="Wiki knowledge base")
    sp_wiki.add_argument(
        "action", nargs="?", choices=["list", "read", "write", "search"]
    )

    # --- status ---
    subparsers.add_parser("status", help="Show active modes")

    # --- cancel ---
    subparsers.add_parser("cancel", help="Cancel active execution")

    # --- mcp-serve ---
    sp_mcp = subparsers.add_parser("mcp-serve", help="Launch stdio MCP server target")
    sp_mcp.add_argument("target", nargs="?", help="Server target name")

    # --- notepad ---
    sp_notepad = subparsers.add_parser(
        "notepad", help="CLI parity for notepad MCP tools"
    )
    sp_notepad.add_argument(
        "action", nargs="?", choices=["read", "write", "append"], default="read"
    )
    sp_notepad.add_argument("text", nargs="?")

    # --- project-memory ---
    sp_pm = subparsers.add_parser(
        "project-memory", help="CLI parity for project-memory MCP tools"
    )
    sp_pm.add_argument(
        "action",
        nargs="?",
        choices=["read", "write", "add-note", "add-directive"],
        default="read",
    )
    sp_pm.add_argument("text", nargs="?")

    # --- trace ---
    sp_trace = subparsers.add_parser("trace", help="CLI parity for trace MCP tools")
    sp_trace.add_argument("action", nargs="?", choices=["list", "read"], default="list")
    sp_trace.add_argument("--last", type=int, default=20)

    # --- code-intel ---
    sp_ci = subparsers.add_parser(
        "code-intel", help="CLI parity for code-intel MCP tools"
    )
    sp_ci.add_argument(
        "action", nargs="?", choices=["symbols", "diagnostics"], default="symbols"
    )
    sp_ci.add_argument("file", nargs="?")

    # --- version ---
    subparsers.add_parser("version", help="Show version info")

    # --- help ---
    subparsers.add_parser("help", help="Show help")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "help":
        parser.print_help()
        sys.exit(0)

    _dispatch(args)


def _resolve_cli_or_exit() -> tuple[Path, str]:
    """Resolve the active provider CLI or exit with an error.

    Respects ``OMX_CLI``; otherwise prefers codex, then claude.
    """
    import os

    from omx.utils.platform import UnsupportedCliError, resolve_cli

    try:
        resolved = resolve_cli()
    except UnsupportedCliError as e:
        print(
            f"Error: OMX_CLI={str(e)!r} is not supported (use 'codex' or 'claude').",
            file=sys.stderr,
        )
        sys.exit(1)

    if resolved is None:
        forced = (os.environ.get("OMX_CLI") or "").strip().lower()
        if forced:
            print(
                f"Error: OMX_CLI={forced!r} but '{forced}' was not found on PATH.",
                file=sys.stderr,
            )
        else:
            print(
                "Error: neither codex nor claude CLI found on PATH. "
                "Install one, or set OMX_CLI to select a provider.",
                file=sys.stderr,
            )
        sys.exit(1)
    return resolved


def _handle_launch_raw(raw_args: list[str]) -> None:
    """Launch Codex with OMX overlay, handling passthrough flags.

    Normalizes OMX-specific flags (--madmax, --high, etc.) to their
    Codex equivalents and passes them through.

    Args:
        raw_args: Raw command-line arguments before argparse.
    """
    import os
    import subprocess
    import uuid
    from pathlib import Path

    from omx.hooks.session import write_session_end, write_session_start
    from omx.runtime.overlay import build_session_instructions

    codex, cli_name = _resolve_cli_or_exit()

    cwd = os.getcwd()
    session_id = uuid.uuid4().hex[:16]

    # Build model instructions overlay
    instructions_path = build_session_instructions(cwd, session_id)
    write_session_start(cwd, session_id=session_id)

    env = {**os.environ}
    env["OMX_SESSION_ID"] = session_id
    env["OMX_BYPASS_DEFAULT_SYSTEM_PROMPT"] = "1"
    env["OMX_MODEL_INSTRUCTIONS_FILE"] = instructions_path

    # Normalize passthrough flags
    codex_args: list[str] = []
    for arg in raw_args:
        normalized = CODEX_PASSTHROUGH_FLAGS.get(arg)
        if normalized:
            codex_args.append(normalized)
        else:
            codex_args.append(arg)

    if cli_name == "claude":
        try:
            instructions_text = Path(instructions_path).read_text(encoding="utf-8")
        except OSError:
            instructions_text = ""
        cmd = [str(codex)]
        if instructions_text:
            cmd.extend(["--append-system-prompt", instructions_text])
        cmd.extend(codex_args)
    else:
        escaped = instructions_path.replace("\\", "\\\\").replace('"', '\\"')
        cmd = [
            str(codex),
            "-c",
            f'model_instructions_file="{escaped}"',
            *codex_args,
        ]

    # If in tmux, create a split pane with HUD watch below
    if os.environ.get("TMUX"):
        _launch_hud_pane(cwd)

    result = subprocess.run(cmd, env=env, check=False)
    write_session_end(cwd, session_id)
    sys.exit(result.returncode)


def _launch_hud_pane(cwd: str) -> None:
    """Create a tmux split pane running the HUD watch.

    Args:
        cwd: Working directory for the HUD pane.
    """
    import subprocess

    try:
        subprocess.run(
            [
                "tmux",
                "split-window",
                "-v",
                "-l",
                "3",
                "-d",
                f"cd {cwd} && python -m omx hud --watch",
            ],
            check=False,
            capture_output=True,
        )
    except FileNotFoundError:
        pass  # tmux not available despite $TMUX being set


def _dispatch(args: argparse.Namespace) -> None:
    """Route to the appropriate command handler."""
    match args.command:
        case "setup":
            from omx.cli.setup import SetupTarget, run_setup

            target = SetupTarget(args.target) if args.target else None
            run_setup(
                force=args.force,
                dry_run=args.dry_run,
                scope=args.scope,
                verbose=args.verbose,
                target=target,
            )
        case "doctor":
            from omx.cli.doctor import run_doctor

            run_doctor(team=args.team, verbose=args.verbose, force=args.force)
        case "state":
            _handle_state(args)
        case "status":
            _handle_status()
        case "list":
            _handle_list(args)
        case "cleanup":
            from omx.cli.cleanup import run_cleanup

            run_cleanup()
        case "mcp-serve":
            _handle_mcp_serve(args)
        case "team":
            _handle_team(args)
        case "ralph":
            _handle_ralph(args)
        case "explore":
            _handle_explore(args)
        case "resume":
            _handle_resume()
        case "autoresearch":
            print("omx autoresearch is deprecated.")
            print("Use the $autoresearch skill keyword instead:")
            print("  In a Codex session, type: $autoresearch <task>")
            sys.exit(0)
        case "agents-init" | "deepinit":
            _handle_agents_init(args)
        case "agents":
            _handle_agents(args)
        case "exec":
            _handle_exec(args)
        case "sparkshell":
            _handle_sparkshell(args)
        case "hud":
            _handle_hud(args)
        case "cancel":
            _handle_cancel()
        case "update":
            _handle_update(args)
        case "uninstall":
            _handle_uninstall()
        case "session":
            _handle_session(args)
        case "ask":
            _handle_ask(args)
        case "wiki":
            _handle_wiki(args)
        case "notepad":
            _handle_notepad(args)
        case "project-memory":
            _handle_project_memory(args)
        case "trace":
            _handle_trace(args)
        case "code-intel":
            _handle_code_intel(args)
        case "version":
            from omx import __version__

            print(f"omx {__version__}")
        case _:
            print(f"Unknown command: '{args.command}'", file=sys.stderr)
            sys.exit(1)


def _handle_state(args: argparse.Namespace) -> None:
    """Handle the state subcommand."""
    import json
    from omx.state.operations import (
        state_clear,
        state_get_status,
        state_list_active,
        state_read,
        state_write,
    )
    from omx.state.paths import resolve_working_directory

    cwd = str(resolve_working_directory())

    match args.action:
        case "read":
            if not args.mode:
                print("Error: --mode is required for state read", file=sys.stderr)
                sys.exit(1)
            result = state_read(args.mode, cwd, args.session_id)
            print(json.dumps(result, indent=2))
        case "write":
            if not args.mode:
                print("Error: --mode is required for state write", file=sys.stderr)
                sys.exit(1)
            result = state_write(args.mode, cwd, {"active": True}, args.session_id)
            print(json.dumps(result, indent=2))
        case "clear":
            if not args.mode:
                print("Error: --mode is required for state clear", file=sys.stderr)
                sys.exit(1)
            result = state_clear(args.mode, cwd, args.session_id, args.all_sessions)
            print(json.dumps(result, indent=2))
        case "list":
            result = state_list_active(cwd, args.session_id)
            print(json.dumps(result, indent=2))
        case "status":
            result = state_get_status(cwd, args.session_id, args.mode)
            print(json.dumps(result, indent=2))


def _handle_status() -> None:
    """Show active workflow modes."""
    from omx.state.operations import state_list_active
    from omx.state.paths import resolve_working_directory

    cwd = str(resolve_working_directory())
    result = state_list_active(cwd)
    modes = result.get("active_modes", [])
    if modes:
        print(f"Active modes: {', '.join(modes)}")
    else:
        print("No active workflow modes.")


def _handle_list(args: argparse.Namespace) -> None:
    """List packaged skills and prompts."""
    import json
    from omx.utils.paths import package_root

    root = package_root()
    skills_dir = root / "assets" / "skills"
    prompts_dir = root / "assets" / "prompts"

    skills = (
        sorted(d.name for d in skills_dir.iterdir() if d.is_dir())
        if skills_dir.exists()
        else []
    )
    prompts = (
        sorted(f.stem for f in prompts_dir.glob("*.md")) if prompts_dir.exists() else []
    )

    if args.json_output:
        print(json.dumps({"skills": skills, "prompts": prompts}, indent=2))
    else:
        if skills:
            print("Skills:")
            for s in skills:
                print(f"  {s}")
        if prompts:
            print("Prompts:")
            for p in prompts:
                print(f"  {p}")
        if not skills and not prompts:
            print("No skills or prompts found. Run 'omx setup' first.")


def _handle_mcp_serve(args: argparse.Namespace) -> None:
    """Launch an MCP server by target name."""
    target = args.target
    match target:
        case "state":
            from omx.mcp.state_server import main as state_main

            state_main()
        case "memory":
            from omx.mcp.memory_server import main as memory_main

            memory_main()
        case "code_intel":
            from omx.mcp.code_intel_server import main as code_intel_main

            code_intel_main()
        case "trace":
            from omx.mcp.trace_server import main as trace_main

            trace_main()
        case "wiki":
            from omx.mcp.wiki_server import main as wiki_main

            wiki_main()
        case _:
            print(f"Unknown MCP server target: {target}", file=sys.stderr)
            sys.exit(1)


def _handle_team(args: argparse.Namespace) -> None:
    """Spawn parallel worker panes in tmux, or manage existing teams.

    Subcommands via spec position:
    - omx team 3:executor --prompt "task"  → spawn workers
    - omx team status [team-name]          → show team status
    - omx team shutdown [team-name]        → kill team session
    """
    import os
    from datetime import datetime, timezone

    spec = args.spec or ""

    # Handle team subcommands
    if spec == "status":
        _handle_team_status()
        return
    if spec == "shutdown":
        _handle_team_shutdown()
        return

    from omx.team.contracts import TeamTask, TeamWorker
    from omx.team.runtime import assign_pending_tasks
    from omx.team.state.io import (
        write_tasks,
        write_team_config,
        write_workers,
    )
    from omx.team.tmux_session import (
        create_team_session,
        wait_for_worker_ready,
    )
    from omx.utils.platform import which

    if not which("tmux"):
        print(
            "Error: tmux is required for team mode. Install tmux and try again.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Parse spec like "3:executor" or "2"
    spec = args.spec or "2:executor"
    parts = spec.split(":", 1)
    try:
        count = int(parts[0])
    except ValueError:
        print(f"Error: invalid worker count in spec '{spec}'", file=sys.stderr)
        sys.exit(1)
    role = parts[1] if len(parts) > 1 else "executor"

    # Determine worker CLI
    worker_cli = os.environ.get("OMX_TEAM_WORKER_CLI", "codex").strip().lower()
    if not which(worker_cli):
        print(f"Error: {worker_cli} CLI not found on PATH", file=sys.stderr)
        sys.exit(1)

    cwd = str(__import__("pathlib").Path.cwd())
    team_name = f"team-{os.getpid()}"
    session_name = f"omx-{team_name}"

    # Write team config
    write_team_config(
        cwd,
        {
            "team_name": team_name,
            "session_name": session_name,
            "worker_cli": worker_cli,
            "worker_count": count,
            "role": role,
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        team_name,
    )

    # Create tmux session with CLI workers in each pane
    print(f"Creating team session with {count} {worker_cli} {role} workers...")
    session = create_team_session(
        session_name,
        count,
        cwd,
        worker_cli=worker_cli,
        team_name=team_name,
    )

    # Register workers in state
    workers = [
        TeamWorker(
            worker_id=f"worker-{i + 1}",
            pane_id=pane_id,
            role=role,
            cli=worker_cli,
        )
        for i, pane_id in enumerate(session.worker_pane_ids)
    ]
    write_workers(cwd, workers, team_name)

    # Wait for each worker to become ready
    print("Waiting for workers to initialize...")
    for w in workers:
        ready = wait_for_worker_ready(w.pane_id, timeout_ms=60_000)
        status = "ready" if ready else "timeout"
        print(f"  {w.worker_id}: {status}")

    # If prompt provided, create tasks and dispatch
    if args.prompt:
        tasks = [
            TeamTask(
                task_id=f"task-{i + 1}",
                description=args.prompt,
                role=role,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            for i in range(count)
        ]
        write_tasks(cwd, tasks, team_name)
        assigned = assign_pending_tasks(cwd, team_name)
        print(f"Dispatched {len(assigned)} tasks to workers.")
    else:
        print("Workers ready. Send tasks with: omx team dispatch --prompt '...'")

    print(f"\nTeam session: {session_name}")
    print(f"Workers: {', '.join(w.worker_id for w in workers)}")
    print(f"Attach with: tmux attach -t {session_name}")


def _handle_team_status() -> None:
    """Show status of the most recent team."""
    from pathlib import Path

    from omx.team.runtime import monitor_team

    cwd = str(Path.cwd())
    team_dir = Path(cwd) / ".omx" / "team"
    if not team_dir.exists():
        print("No teams found.")
        return

    # Find most recent team
    teams = sorted(
        (d.name for d in team_dir.iterdir() if d.is_dir()),
        reverse=True,
    )
    if not teams:
        print("No teams found.")
        return

    team_name = teams[0]
    snapshot = monitor_team(cwd, team_name)
    tasks = snapshot.get("tasks", {})
    workers = snapshot.get("workers", [])

    print(f"Team: {team_name}")
    print(
        f"Tasks: {tasks.get('total', 0)} total, "
        f"{tasks.get('pending', 0)} pending, "
        f"{tasks.get('in_progress', 0)} in progress, "
        f"{tasks.get('completed', 0)} completed, "
        f"{tasks.get('failed', 0)} failed"
    )
    for w in workers:
        status = w.get("status", "unknown")
        alive = "alive" if w.get("alive") else "DEAD"
        task = w.get("current_task", "-")
        print(f"  {w['name']:15s} {status:10s} {alive:5s} task={task}")

    if snapshot.get("all_tasks_terminal"):
        print("\nAll tasks complete.")


def _handle_team_shutdown() -> None:
    """Shutdown the most recent team session."""
    from pathlib import Path

    from omx.team.tmux_session import kill_team_session

    cwd = str(Path.cwd())
    team_dir = Path(cwd) / ".omx" / "team"
    if not team_dir.exists():
        print("No teams found.")
        return

    teams = sorted(
        (d.name for d in team_dir.iterdir() if d.is_dir()),
        reverse=True,
    )
    if not teams:
        print("No teams found.")
        return

    team_name = teams[0]
    session_name = f"omx-{team_name}"
    kill_team_session(session_name)
    print(f"Killed team session: {session_name}")


def _handle_ralph(args: argparse.Namespace) -> None:
    """Launch ralph persistence mode.

    Writes ralph state, ensures artifacts, builds session instructions,
    and launches an interactive codex/claude session.
    """
    import subprocess
    from pathlib import Path

    from omx.ralph.persistence import ensure_canonical_ralph_artifacts
    from omx.state.operations import state_write
    from omx.state.paths import resolve_working_directory

    cwd = str(resolve_working_directory())
    ensure_canonical_ralph_artifacts(cwd)

    task = args.prompt or "No specific task provided — explore and investigate."

    fields: dict[str, object] = {
        "active": True,
        "current_phase": "investigate",
        "task_description": task,
    }

    state_write("ralph", cwd, fields)

    # Build ralph session instructions
    instructions = (
        "You are in OMX Ralph persistence mode.\n"
        f"Primary task: {task}\n"
        "\n"
        "Follow the Ralph workflow phases:\n"
        "1. Investigate — understand the problem, explore the codebase\n"
        "2. Plan — create a detailed implementation plan\n"
        "3. Execute — implement the plan with tests\n"
        "4. Verify — run tests, check quality, ensure completion\n"
        "\n"
        "Report your current phase progress. "
        "When one phase is complete, transition to the next.\n"
    )

    # Write session model instructions
    omx_dir = Path(cwd) / ".omx"
    omx_dir.mkdir(parents=True, exist_ok=True)
    instructions_path = omx_dir / "ralph-instructions.md"
    instructions_path.write_text(instructions, encoding="utf-8")

    print("Ralph mode activated (phase: investigate)")
    print(f"Task: {task}")

    codex, cli_name = _resolve_cli_or_exit()

    if cli_name == "claude":
        cmd = [str(codex), "--append-system-prompt", instructions]
    else:
        escaped = str(instructions_path).replace("\\", "\\\\").replace('"', '\\"')
        cmd = [
            str(codex),
            "-c",
            f'model_instructions_file="{escaped}"',
        ]
    subprocess.run(cmd, check=False)


def _handle_explore(args: argparse.Namespace) -> None:
    """Run read-only exploration via codex/claude.

    One-shot read-only exploration: instructions are passed as a system
    prompt (codex via `-c model_instructions_file=...`, claude via
    `--append-system-prompt`) and the user's prompt is run non-interactively.
    """
    import subprocess
    from pathlib import Path

    from omx.utils.paths import package_root

    if not args.prompt:
        print("Error: --prompt is required for explore mode", file=sys.stderr)
        sys.exit(1)

    codex, cli_name = _resolve_cli_or_exit()

    explore_asset = package_root() / "assets" / "prompts" / "explore.md"
    base_instructions = (
        explore_asset.read_text(encoding="utf-8") if explore_asset.exists() else ""
    )

    read_only_preamble = (
        "You are in read-only exploration mode. "
        "Do not modify any files. "
        "Only use read-only commands "
        "(ls, cat, grep, find, git log/status/diff)."
    )
    system_instructions = (
        f"{base_instructions}\n\n{read_only_preamble}"
        if base_instructions
        else read_only_preamble
    )

    cwd = Path.cwd()
    omx_dir = cwd / ".omx"
    omx_dir.mkdir(parents=True, exist_ok=True)
    instructions_path = omx_dir / "explore-instructions.md"
    instructions_path.write_text(
        f"{system_instructions}\n\nTask: {args.prompt}\n",
        encoding="utf-8",
    )

    if cli_name == "claude":
        cmd = [
            str(codex),
            "--append-system-prompt",
            system_instructions,
            "--print",
            args.prompt,
        ]
    else:
        escaped = str(instructions_path).replace("\\", "\\\\").replace('"', '\\"')
        cmd = [
            str(codex),
            "-c",
            f'model_instructions_file="{escaped}"',
            "exec",
            args.prompt,
        ]

    subprocess.run(cmd, check=False)


def _handle_resume() -> None:
    """Resume a previous interactive session.

    Reads session state from .omx/session.json, checks whether the
    previous process is still alive, and relaunches codex with the
    last session instructions if the process has exited.
    """
    import json
    import os
    import subprocess
    from pathlib import Path

    cwd = Path.cwd()
    session_file = cwd / ".omx" / "session.json"

    if not session_file.exists():
        print("Error: no previous session found (.omx/session.json missing)")
        sys.exit(1)

    try:
        session = json.loads(session_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error: could not read session state: {exc}")
        sys.exit(1)

    pid = session.get("pid")
    if pid is not None:
        try:
            os.kill(pid, 0)
            print(f"Session still running (PID {pid})")
            return
        except (OSError, ProcessLookupError):
            pass  # Process is dead, we can resume

    # Rebuild and relaunch
    codex, cli_name = _resolve_cli_or_exit()

    instructions_path = session.get("instructions_path", "")
    cmd = [str(codex)]
    if instructions_path and Path(instructions_path).exists():
        if cli_name == "claude":
            cmd.extend(
                [
                    "--append-system-prompt",
                    Path(instructions_path).read_text(encoding="utf-8"),
                ]
            )
        else:
            escaped = instructions_path.replace("\\", "\\\\").replace('"', '\\"')
            cmd.extend(["-c", f'model_instructions_file="{escaped}"'])

    print("Resuming previous session...")
    result = subprocess.run(cmd, check=False)
    sys.exit(result.returncode)


def _handle_agents_init(args: argparse.Namespace) -> None:
    """Bootstrap AGENTS.md files."""
    from pathlib import Path

    from omx.utils.paths import package_root

    target = Path(args.path).resolve()
    target.mkdir(parents=True, exist_ok=True)
    agents_md = target / "AGENTS.md"

    template = package_root() / "assets" / "templates" / "AGENTS.md"
    if template.exists():
        import shutil

        if agents_md.exists():
            print(f"AGENTS.md already exists at {agents_md}")
            print("Use --force to overwrite (not yet implemented).")
            return
        shutil.copy2(template, agents_md)
        print(f"Created {agents_md}")
    else:
        # Generate a minimal AGENTS.md
        agents_md.write_text(
            "# Agents\n\n"
            "This file describes the agent roles available in this project.\n\n"
            "## Roles\n\n"
            "- **executor** — Code implementation\n"
            "- **architect** — System design\n"
            "- **debugger** — Bug investigation\n"
            "- **explorer** — Codebase exploration (read-only)\n",
            encoding="utf-8",
        )
        print(f"Created {agents_md}")


def _handle_agents(args: argparse.Namespace) -> None:
    """Manage native agent TOML files."""
    import json

    from omx.agents.roles import AGENT_DEFINITIONS

    match args.action:
        case "list":
            for agent in AGENT_DEFINITIONS:
                print(f"  {agent.name:20s} {agent.routing_role:10s} {agent.category}")
        case "show":
            print(json.dumps([a.__dict__ for a in AGENT_DEFINITIONS], indent=2))
        case _:
            for agent in AGENT_DEFINITIONS:
                print(f"  {agent.name:20s} {agent.routing_role:10s} {agent.category}")


def _handle_exec(args: argparse.Namespace) -> None:
    """Run codex exec non-interactively with OMX overlay.

    Builds session instructions, sets OMX environment variables,
    and passes model instructions to the codex/claude exec command.

    Args:
        args: Parsed CLI arguments with prompt and optional model.
    """
    import os
    import subprocess
    import uuid
    from pathlib import Path

    from omx.runtime.overlay import build_session_instructions

    codex, cli_name = _resolve_cli_or_exit()

    prompt = args.prompt
    if not prompt:
        print("Error: prompt is required for exec mode", file=sys.stderr)
        sys.exit(1)

    cwd = os.getcwd()
    session_id = uuid.uuid4().hex[:16]

    # Build session instructions overlay
    instructions_path = build_session_instructions(cwd, session_id)

    # Build environment with OMX session context
    env = {**os.environ}
    env["OMX_SESSION_ID"] = session_id
    env["OMX_MODEL_INSTRUCTIONS_FILE"] = instructions_path

    if cli_name == "claude":
        try:
            instructions_text = Path(instructions_path).read_text(encoding="utf-8")
        except OSError:
            instructions_text = ""
        cmd = [str(codex), "--print"]
        if instructions_text:
            cmd.extend(["--append-system-prompt", instructions_text])
        if args.model:
            cmd.extend(["--model", args.model])
        cmd.append(prompt)
    else:
        escaped = instructions_path.replace("\\", "\\\\").replace('"', '\\"')
        cmd = [
            str(codex),
            "-c",
            f'model_instructions_file="{escaped}"',
            "exec",
        ]
        if args.model:
            cmd.extend(["--model", args.model])
        cmd.append(prompt)

    result = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0 and result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    sys.exit(result.returncode)


def _handle_sparkshell(args: argparse.Namespace) -> None:
    """Run sparkshell bounded command execution."""
    from omx.sparkshell.exec import execute_command

    if not args.argv:
        print("Error: command is required for sparkshell", file=sys.stderr)
        sys.exit(1)

    result = execute_command(args.argv, line_limit=args.tail_lines)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if result.truncated:
        print(f"(output truncated to {args.tail_lines} lines)", file=sys.stderr)
    sys.exit(result.exit_code if result.exit_code >= 0 else 1)


def _handle_hud(args: argparse.Namespace) -> None:
    """Show HUD statusline."""
    import json
    import time

    from omx.hud.renderer import render_statusline

    if args.json_output:
        from omx.hud.state import read_hud_state

        print(json.dumps(read_hud_state(), indent=2))
        return

    if args.watch:
        try:
            while True:
                line = render_statusline(preset=args.preset)
                print(f"\r{line}", end="", flush=True)
                time.sleep(1)
        except KeyboardInterrupt:
            print()
    else:
        print(render_statusline(preset=args.preset))


def _handle_cancel() -> None:
    """Cancel active execution modes."""
    from omx.state.operations import state_clear, state_list_active
    from omx.state.paths import resolve_working_directory

    cwd = str(resolve_working_directory())
    result = state_list_active(cwd)
    active = result.get("active_modes", [])

    if not active:
        print("No active modes to cancel.")
        return

    for mode in active:
        state_clear(mode, cwd)
        print(f"Cancelled: {mode}")


def _handle_update(args: argparse.Namespace) -> None:
    """Check for and install updates."""
    import subprocess

    if args.check:
        print("Checking for updates...")
        result = subprocess.run(
            ["pip", "index", "versions", "omx"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            print(result.stdout.strip())
        else:
            print("Could not check for updates (package may not be on PyPI).")
        return

    print("Updating omx...")
    result = subprocess.run(
        ["pip", "install", "--upgrade", "omx"],
        check=False,
    )
    if result.returncode == 0:
        print("Update complete.")
    else:
        print("Update failed. You may need to reinstall from source.")


def _handle_uninstall() -> None:
    """Remove OMX configuration."""
    from omx.utils.paths import codex_prompts_dir, user_skills_dir

    print("This will remove OMX-installed skills, prompts, and config.")
    print("Directories that would be affected:")
    for d in [codex_prompts_dir(), user_skills_dir()]:
        print(f"  {d}")
    print("\nRun 'pip uninstall omx' to remove the package itself.")


def _handle_session(args: argparse.Namespace) -> None:
    """Search prior session transcripts."""
    from omx.utils.paths import omx_logs_dir

    logs_dir = omx_logs_dir()
    if not logs_dir.exists():
        print("No session logs found.")
        return

    history = logs_dir / "session-history.jsonl"
    if not history.exists():
        print("No session history found.")
        return

    import json

    lines = history.read_text(encoding="utf-8").strip().splitlines()
    query = (args.query or "").lower()

    for line in reversed(lines[-20:]):  # Show last 20 entries
        try:
            entry = json.loads(line)
            summary = f"{entry.get('timestamp', '?'):25s} {entry.get('event', '?'):15s} {entry.get('session_id', '?')}"
            if not query or query in summary.lower():
                print(summary)
        except json.JSONDecodeError:
            pass


def _handle_ask(args: argparse.Namespace) -> None:
    """Ask a local provider CLI."""
    import subprocess

    from omx.utils.platform import which

    provider = args.provider or "codex"
    cli = which(provider)
    if not cli:
        print(f"Error: {provider} CLI not found on PATH", file=sys.stderr)
        sys.exit(1)

    prompt = args.prompt
    if not prompt:
        print("Error: prompt is required for 'omx ask'", file=sys.stderr)
        sys.exit(1)

    result = subprocess.run([str(cli), prompt], check=False)
    sys.exit(result.returncode)


def _handle_wiki(args: argparse.Namespace) -> None:
    """Wiki knowledge base operations."""
    import os

    from omx.wiki.storage import get_wiki_dir, list_pages, read_page

    root = os.getcwd()
    wiki_dir = get_wiki_dir(root)

    match args.action:
        case "list":
            pages = list_pages(root)
            if not pages:
                print("No wiki entries.")
                return
            for p in pages:
                page = read_page(root, p)
                title = page.frontmatter.title if page else p
                print(f"  {p:40s} {title}")
        case "read":
            print("Usage: omx wiki read <topic>")
        case "write":
            print("Usage: omx wiki write <topic> (reads from stdin)")
        case "search":
            print("Usage: omx wiki search <query>")
        case _:
            pages = list_pages(root)
            if not pages:
                print("No wiki entries. Use 'omx wiki list' to check.")
            else:
                print(f"Wiki: {len(pages)} entries in {wiki_dir}")
                print("Commands: omx wiki [list|read|write|search]")


def _handle_notepad(args: argparse.Namespace) -> None:
    """CLI parity for notepad MCP tools."""
    import json
    import os

    from omx.mcp.memory_server import handle_tool_call

    cwd = os.getcwd()
    match args.action:
        case "read":
            result = handle_tool_call("notepad_read", {"workingDirectory": cwd})
            print(json.loads(result["content"][0]["text"]).get("content", "(empty)"))
        case "write":
            if not args.text:
                print("Error: text required for notepad write", file=sys.stderr)
                sys.exit(1)
            handle_tool_call(
                "notepad_write_working", {"workingDirectory": cwd, "content": args.text}
            )
            print("Written.")
        case "append":
            if not args.text:
                print("Error: text required for notepad append", file=sys.stderr)
                sys.exit(1)
            handle_tool_call(
                "notepad_write_working", {"workingDirectory": cwd, "content": args.text}
            )
            print("Appended.")


def _handle_project_memory(args: argparse.Namespace) -> None:
    """CLI parity for project-memory MCP tools."""
    import json
    import os

    from omx.mcp.memory_server import handle_tool_call

    cwd = os.getcwd()
    match args.action:
        case "read":
            result = handle_tool_call("project_memory_read", {"workingDirectory": cwd})
            text = json.loads(result["content"][0]["text"])
            print(json.dumps(text, indent=2))
        case "write":
            if not args.text:
                print("Error: text required", file=sys.stderr)
                sys.exit(1)
            handle_tool_call(
                "project_memory_write", {"workingDirectory": cwd, "content": args.text}
            )
            print("Written.")
        case "add-note":
            if not args.text:
                print("Error: note text required", file=sys.stderr)
                sys.exit(1)
            handle_tool_call(
                "project_memory_add_note", {"workingDirectory": cwd, "note": args.text}
            )
            print("Note added.")
        case "add-directive":
            if not args.text:
                print("Error: directive text required", file=sys.stderr)
                sys.exit(1)
            handle_tool_call(
                "project_memory_add_directive",
                {"workingDirectory": cwd, "directive": args.text},
            )
            print("Directive added.")


def _handle_trace(args: argparse.Namespace) -> None:
    """CLI parity for trace MCP tools."""
    import json
    import os

    from omx.mcp.trace_server import handle_tool_call

    cwd = os.getcwd()
    result = handle_tool_call(
        "trace_timeline", {"workingDirectory": cwd, "last": args.last}
    )
    text = json.loads(result["content"][0]["text"])
    if isinstance(text, list):
        for entry in text:
            ts = entry.get("timestamp", "?")
            event = entry.get("event", "?")
            print(f"  {ts:25s} {event}")
    else:
        print(json.dumps(text, indent=2))


def _handle_code_intel(args: argparse.Namespace) -> None:
    """CLI parity for code-intel MCP tools."""
    import json
    import os

    from omx.mcp.code_intel_server import handle_tool_call

    cwd = os.getcwd()
    match args.action:
        case "symbols":
            tool_args = {"workingDirectory": cwd}
            if args.file:
                tool_args["file"] = args.file
            result = handle_tool_call("lsp_document_symbols", tool_args)
            text = json.loads(result["content"][0]["text"])
            if isinstance(text, list):
                for sym in text:
                    print(f"  {sym.get('kind', '?'):12s} {sym.get('name', '?')}")
            else:
                print(json.dumps(text, indent=2))
        case "diagnostics":
            result = handle_tool_call("lsp_diagnostics", {"workingDirectory": cwd})
            print(json.loads(result["content"][0]["text"]))
