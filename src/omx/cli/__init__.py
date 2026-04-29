"""OMX CLI — main entry point and command dispatcher.

Port of src/cli/index.ts.
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments and dispatch to the appropriate subcommand handler.

    Args:
        argv: Command-line arguments (defaults to sys.argv if None).
    """
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
    sp_spark.add_argument("command", nargs=argparse.REMAINDER)
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

    # --- help ---
    subparsers.add_parser("help", help="Show help")

    args = parser.parse_args(argv)

    if args.command is None or args.command == "help":
        parser.print_help()
        sys.exit(0)

    _dispatch(args)


def _dispatch(args: argparse.Namespace) -> None:
    """Route to the appropriate command handler."""
    match args.command:
        case "setup":
            from omx.cli.setup import run_setup

            run_setup(
                force=args.force,
                dry_run=args.dry_run,
                scope=args.scope,
                verbose=args.verbose,
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
    """Spawn parallel worker panes in tmux with CLI sessions.

    Creates a tmux session, launches codex/claude in each worker pane,
    waits for readiness, then dispatches tasks via inbox files and
    tmux send-keys trigger injection.
    """
    import os
    from datetime import datetime, timezone

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

    print(f"\nTeam session: {session_name}")
    print(f"Workers: {', '.join(w.worker_id for w in workers)}")
    print(f"Attach with: tmux attach -t {session_name}")


def _handle_ralph(args: argparse.Namespace) -> None:
    """Launch ralph persistence mode."""
    from omx.ralph.persistence import ensure_canonical_ralph_artifacts
    from omx.state.operations import state_write
    from omx.state.paths import resolve_working_directory

    cwd = str(resolve_working_directory())
    ensure_canonical_ralph_artifacts(cwd)

    fields = {"active": True, "current_phase": "investigate"}
    if args.prompt:
        fields["task_description"] = args.prompt

    result = state_write("ralph", cwd, fields)
    print("Ralph mode activated (phase: investigate)")
    if args.prompt:
        print(f"Task: {args.prompt}")
    print("State written to:", result.get("path", ""))


def _handle_explore(args: argparse.Namespace) -> None:
    """Run read-only exploration."""
    if not args.prompt:
        print("Error: --prompt is required for explore mode", file=sys.stderr)
        sys.exit(1)

    print(f"Explore mode: {args.prompt}")
    print("(Read-only exploration would invoke codex/claude with explore agent prompt)")
    print("Allowed commands: ls, cat, grep, rg, find, git log/diff/status, etc.")


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
    """Run codex exec non-interactively."""
    import subprocess

    from omx.utils.platform import which

    codex = which("codex") or which("claude")
    if not codex:
        print("Error: neither codex nor claude CLI found on PATH", file=sys.stderr)
        sys.exit(1)

    prompt = args.prompt
    if not prompt:
        print("Error: prompt is required for exec mode", file=sys.stderr)
        sys.exit(1)

    cmd = [str(codex), "exec", prompt]
    if args.model:
        cmd.extend(["--model", args.model])

    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode != 0 and result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    sys.exit(result.returncode)


def _handle_sparkshell(args: argparse.Namespace) -> None:
    """Run sparkshell bounded command execution."""
    from omx.sparkshell.exec import execute_command

    if not args.command:
        print("Error: command is required for sparkshell", file=sys.stderr)
        sys.exit(1)

    result = execute_command(args.command, line_limit=args.tail_lines)
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
