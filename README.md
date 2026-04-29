# omx-py

Pure Python 3.12+ port of [oh-my-codex](https://github.com/Yeachan-Heo/oh-my-codex) — a multi-agent orchestration layer for OpenAI Codex CLI.

**Zero non-stdlib dependencies.** No pip install required for development or testing.

## Requirements

- Python 3.12 or later
- tmux (for team/multi-agent mode)
- Codex CLI or Claude CLI (for runtime invocation)

## Install

```bash
# Clone the repository
git clone <repo-url> omx-py
cd omx-py

# That's it. No pip install, no venv, no dependencies.
# Run directly:
PYTHONPATH=src python -m omx --version

# On Windows PowerShell:
$env:PYTHONPATH="src"; python -m omx --version
```

### System-wide install (optional)

If you want the `omx` command available globally:

```bash
pip install .
omx --version
```

This installs zero external packages — just registers the entry point.

## Usage

```bash
# Show help
omx help

# Health check
omx doctor

# Install skills, prompts, and config
omx setup
omx setup --scope project   # project-local install

# List available skills and prompts
omx list
omx list --json

# State management
omx state list              # show active workflow modes
omx state read --mode ralph
omx state clear --mode autopilot
omx status                  # shorthand for active modes

# Team mode (requires tmux)
omx team 3:executor          # spawn 3 executor workers
omx doctor --team            # team diagnostics

# Ralph persistence mode
omx ralph

# Read-only exploration
omx explore --prompt "how does auth work?"

# Cleanup orphaned processes
omx cleanup

# Launch MCP server
omx mcp-serve state
```

## Project Structure

```
omx-py/
├── pyproject.toml              # PEP 621 metadata, entry point
├── src/
│   └── omx/
│       ├── __init__.py         # Package root, version
│       ├── __main__.py         # python -m omx entry point
│       ├── cli/                # CLI commands (argparse dispatcher)
│       │   ├── __init__.py     # Main dispatcher with 20+ subcommands
│       │   ├── doctor.py       # Health diagnostics
│       │   ├── setup.py        # Asset installation
│       │   └── cleanup.py      # Orphaned process cleanup
│       ├── core/               # Runtime engine (port of Rust omx-runtime-core)
│       │   ├── types.py        # Commands, events, snapshots, enums
│       │   ├── authority.py    # Mutual-exclusion lease management
│       │   ├── dispatch.py     # Task dispatch queue and transitions
│       │   ├── mailbox.py      # Inter-worker message passing
│       │   ├── replay.py       # Event replay and deduplication
│       │   └── engine.py       # Central engine: process commands, persist state
│       ├── mux/                # Terminal multiplexer adapter (port of Rust omx-mux)
│       │   ├── types.py        # Targets, operations, policies, outcomes
│       │   └── tmux.py         # Tmux subprocess wrapper
│       ├── mcp/                # MCP servers (pure stdlib JSON-RPC over stdio)
│       │   ├── protocol.py     # JSON-RPC 2.0 transport
│       │   ├── bootstrap.py    # Server lifecycle, parent watchdog
│       │   └── state_server.py # State management MCP server
│       ├── state/              # Workflow state management
│       │   ├── paths.py        # State directory/file resolution
│       │   ├── operations.py   # Read/write/clear/list/status
│       │   ├── workflow_transition.py  # Mode overlap and transition rules
│       │   ├── skill_active.py # Canonical skill state sync
│       │   └── mode_state_context.py   # Tmux pane context capture
│       ├── runtime/            # Codex/Claude invocation
│       │   ├── run_loop.py     # Iterate until terminal outcome
│       │   ├── run_outcome.py  # Outcome classification and normalization
│       │   ├── run_state.py    # Persistent run state
│       │   ├── bridge.py       # Engine bridge (replaces Rust binary)
│       │   └── terminal_lifecycle.py   # Lifecycle outcome mapping
│       ├── hooks/              # Plugin extensibility
│       │   ├── types.py        # Event envelopes
│       │   ├── loader.py       # Plugin discovery
│       │   ├── dispatcher.py   # Event dispatch to plugins
│       │   ├── keyword_detector.py     # Skill keyword detection
│       │   ├── triage.py       # Prompt routing heuristic
│       │   └── session.py      # Session lifecycle (start/end/staleness)
│       ├── team/               # Multi-agent orchestration
│       │   ├── contracts.py    # Task/worker/event types
│       │   ├── runtime.py      # Orchestrator: assign, complete, check
│       │   ├── allocation_policy.py    # Task-to-worker scoring
│       │   ├── tmux_session.py # Tmux session/pane management
│       │   ├── state/io.py     # File-based team state persistence
│       │   ├── delivery_log.py # JSONL delivery event logging
│       │   ├── idle_nudge.py   # Idle worker detection
│       │   ├── commit_hygiene.py       # Git branch/merge operations
│       │   ├── model_contract.py       # Worker CLI/model resolution
│       │   ├── mcp_comm.py     # Inter-worker messaging via bridge
│       │   └── followup_planner.py     # Task dependency queries
│       ├── agents/             # Agent role system
│       │   ├── roles.py        # 17 agent definitions
│       │   └── policy.py       # Catalog policy and validation
│       ├── sparkshell/         # Bounded shell execution (port of Rust omx-sparkshell)
│       │   ├── exec.py         # Command execution with limits
│       │   └── registry/generic.py     # Read-only vs mutating classification
│       ├── explore/            # Read-only exploration (port of Rust omx-explore)
│       │   ├── allowlist.py    # Permitted commands
│       │   └── harness.py      # Explore mode executor
│       ├── notifications/      # External notification adapters
│       │   ├── types.py        # Payload and result types
│       │   ├── dispatcher.py   # Multi-provider dispatch
│       │   ├── discord.py      # Discord webhook (urllib)
│       │   ├── slack.py        # Slack webhook (urllib)
│       │   └── telegram.py     # Telegram bot API (urllib)
│       ├── hud/                # Tmux statusline
│       │   ├── state.py        # HUD state persistence
│       │   └── renderer.py     # Statusline string rendering
│       ├── ralph/              # Persistent completion loop
│       │   ├── contract.py     # Phase validation (investigate/plan/execute/verify)
│       │   └── persistence.py  # Plan and artifact management
│       ├── autoresearch/       # Research iteration workflow
│       │   ├── contracts.py    # Mission and candidate types
│       │   └── runtime.py      # Research loop executor
│       ├── catalog/            # Skill/agent discovery
│       │   ├── discovery.py    # Filesystem skill/prompt scanning
│       │   └── metadata.py     # Catalog entry schema
│       ├── config/             # Configuration management
│       │   ├── generator.py    # Config read/write/merge
│       │   ├── toml_writer.py  # Minimal TOML serializer
│       │   └── models.py       # Model selection defaults
│       └── utils/              # Shared utilities
│           ├── paths.py        # Path resolution (pathlib-based)
│           ├── platform.py     # Cross-platform subprocess helpers
│           └── toml_read.py    # tomllib wrapper
├── assets/                     # Static content (skills, prompts, templates)
└── tests/
    └── unit/                   # 212 tests, all stdlib unittest
```

## Architecture

### What was ported

| Original | Language | Python module | Lines |
|----------|----------|---------------|-------|
| `src/` (267 TS files) | TypeScript | `src/omx/` | ~6,000 |
| `crates/omx-runtime-core/` | Rust | `omx.core` | — |
| `crates/omx-mux/` | Rust | `omx.mux` | — |
| `crates/omx-sparkshell/` | Rust | `omx.sparkshell` | — |
| `crates/omx-explore/` | Rust | `omx.explore` | — |
| `@modelcontextprotocol/sdk` | npm package | `omx.mcp.protocol` | ~100 |
| `@iarna/toml` | npm package | `tomllib` + `omx.config.toml_writer` | ~50 |
| `zod` | npm package | `dataclasses` + `__post_init__` | — |

### Key design decisions

- **`enum.StrEnum`** for string-valued enums (free JSON serialization)
- **`dataclasses`** with `to_dict()`/`from_dict()` classmethods for serialization (replaces Zod)
- **`pathlib.Path`** throughout (no string path manipulation)
- **`asyncio` not used** — the codebase is synchronous; concurrency is via tmux subprocesses
- **JSON-RPC over stdio** for MCP (~100 lines, replaces the entire MCP SDK)
- **`tomllib`** for TOML reading (stdlib since 3.11), custom ~50-line writer
- **`urllib.request`** for HTTP (notifications only)
- **`subprocess`** for tmux and CLI invocation
- **`ctypes.windll.kernel32`** for Windows PID checking (since `os.kill(pid, 0)` terminates on Windows)

## Running Tests

```bash
# Linux/macOS
PYTHONPATH=src python -m unittest discover -s tests/unit -v

# Windows PowerShell
$env:PYTHONPATH="src"; python -m unittest discover -s tests/unit -v

# Run a single test file
PYTHONPATH=src python -m unittest tests.unit.test_core_engine -v
```

## Dependency Audit

To verify zero non-stdlib imports:

```bash
grep -rn "^import\|^from" src/omx/ \
  | grep -v "__future__\|omx\.\|json\|os\|sys\|re\|signal\|time\|uuid\|shutil" \
  | grep -v "subprocess\|threading\|tempfile\|random\|argparse\|pathlib\|typing" \
  | grep -v "dataclasses\|enum\|datetime\|tomllib\|fcntl\|msvcrt\|ctypes\|urllib"
```

Should return empty (or only `__pycache__` binary matches).

## MCP Server

The state management MCP server can be launched standalone:

```bash
PYTHONPATH=src python -m omx.mcp.state_server
```

Or via the CLI:

```bash
omx mcp-serve state
```

It speaks JSON-RPC 2.0 over stdio with Content-Length framing, compatible with any MCP client.

## Environment Variables

| Variable | Purpose |
|----------|---------|
| `CODEX_HOME` | Override Codex home directory (default: `~/.codex`) |
| `OMX_SESSION_ID` | Explicit session scope ID |
| `OMX_TEAM_WORKER_CLI` | Worker CLI tool (`codex`, `claude`, `gemini`) |
| `OMX_TEAM_WORKER_MODEL` | Worker model override |
| `OMX_TEAM_STATE_ROOT` | Override team state directory |
| `OMX_MCP_WORKDIR_ROOTS` | Allowed working directory roots (path-separated) |
| `OMX_HOOK_PLUGINS` | Enable/disable hook plugins (`0` to disable) |
| `OMX_HOOK_PLUGIN_TIMEOUT_MS` | Plugin execution timeout (default: 1500ms) |
| `OMX_DISCORD_WEBHOOK` | Discord notification webhook URL |
| `OMX_SLACK_WEBHOOK` | Slack notification webhook URL |
| `OMX_TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `OMX_TELEGRAM_CHAT_ID` | Telegram chat ID |
| `OMX_MCP_SERVER_DISABLE_AUTO_START` | Disable all MCP auto-start (`1`) |
| `OMX_STATE_SERVER_DISABLE_AUTO_START` | Disable state server auto-start (`1`) |

## License

MIT
