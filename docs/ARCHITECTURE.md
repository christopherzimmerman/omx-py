# Architecture

## Overview

omx-py is a 1:1 port of [oh-my-codex](https://github.com/Yeachan-Heo/oh-my-codex) from TypeScript + Rust to pure Python 3.12+ with zero non-stdlib dependencies.

The system is a multi-agent orchestration harness that wraps Codex CLI (or Claude CLI) with:
- Workflow state management
- Multi-agent team coordination via tmux
- Plugin/hook extensibility
- MCP (Model Context Protocol) servers for tool integration
- Local desktop notifications (basic surface; see docs/PORT_PLAN.md for scope)

**Port status:** in progress. See `docs/PORT_PLAN.md` and `docs/PARITY.md` for the canonical gap list and phased roadmap.

## Module Dependency Graph

```
omx.cli (entry point)
 ├── omx.cli.setup       → omx.config, omx.utils
 ├── omx.cli.doctor      → omx.utils, omx.config
 ├── omx.cli.cleanup     → subprocess (direct)
 │
 ├── omx.state           → omx.utils.paths
 │   ├── paths.py        (foundational — no omx deps)
 │   ├── operations.py   → state.paths, state.mode_state_context, state.skill_active
 │   ├── workflow_transition.py (pure logic, no I/O)
 │   └── skill_active.py → state.paths
 │
 ├── omx.mcp
 │   ├── protocol.py     (foundational — json + sys only)
 │   ├── bootstrap.py    → os, signal, threading
 │   └── state_server.py → mcp.protocol, state.operations
 │
 ├── omx.runtime
 │   ├── run_outcome.py  (pure logic)
 │   ├── run_loop.py     → runtime.run_outcome
 │   ├── run_state.py    → runtime.run_outcome, state.paths
 │   ├── bridge.py       → core.engine, core.types
 │   └── terminal_lifecycle.py → runtime.run_outcome
 │
 ├── omx.core            (port of Rust omx-runtime-core)
 │   ├── types.py        (foundational — dataclasses + enums)
 │   ├── authority.py    → core.types
 │   ├── dispatch.py     → core.types
 │   ├── mailbox.py      (standalone)
 │   ├── replay.py       → core.types
 │   └── engine.py       → core.authority, core.dispatch, core.mailbox, core.replay
 │
 ├── omx.mux             (port of Rust omx-mux)
 │   ├── types.py        (standalone dataclasses)
 │   └── tmux.py         → mux.types, subprocess
 │
 ├── omx.hooks
 │   ├── types.py        (standalone)
 │   ├── loader.py       → pathlib only
 │   ├── dispatcher.py   → hooks.types, hooks.loader, utils.paths
 │   ├── keyword_detector.py (pure logic)
 │   ├── triage.py       (pure logic)
 │   └── session.py      → utils.paths
 │
 ├── omx.team
 │   ├── contracts.py    (standalone dataclasses)
 │   ├── state/io.py     → team.contracts
 │   ├── allocation_policy.py → team.contracts
 │   ├── runtime.py      → team.contracts, team.state.io, team.allocation_policy
 │   ├── tmux_session.py → utils.platform
 │   ├── mcp_comm.py     → core.types, runtime.bridge
 │   └── ...
 │
 ├── omx.agents
 │   ├── roles.py        (standalone definitions)
 │   └── policy.py       (standalone logic)
 │
 ├── omx.sparkshell      (port of Rust omx-sparkshell)
 │   ├── exec.py         → subprocess
 │   └── registry/       (pure classification logic)
 │
 ├── omx.explore         (port of Rust omx-explore)
 │   ├── allowlist.py    (standalone sets)
 │   └── harness.py      → explore.allowlist, sparkshell.exec
 │
 ├── omx.notifications   → subprocess (basic desktop dispatch; advanced subsystem intentionally out of scope)
 ├── omx.hud             → state.operations, hud.state
 ├── omx.ralph           → state.paths
 ├── omx.autoresearch    (standalone)
 ├── omx.catalog         → utils.paths
 ├── omx.config          → utils.paths, tomllib
 └── omx.utils           (foundational — pathlib, os, subprocess)
```

## Data Flow

### State Management

```
User/CLI → state operations → .omx/state/{mode}-state.json
                            → .omx/state/sessions/{id}/{mode}-state.json
```

State is scoped by session ID (auto-detected from environment or explicit). Session-scoped state takes precedence over root state.

### Team Orchestration

```
omx team → create tmux session
         → write tasks to .omx/team/tasks.json
         → allocation policy assigns tasks to workers
         → dispatch via tmux send-keys
         → workers report completion
         → events logged to .omx/team/events.jsonl
```

### MCP Protocol

```
Client ←→ [Content-Length framing] ←→ McpServer (JSON-RPC 2.0)
                                         │
                                         ├── tools/list → tool definitions
                                         └── tools/call → state operations
```

### Runtime Loop

```
run_until_terminal(step_fn)
  └── step_fn(state) → RunLoopIteration(outcome, state)
       └── classify_run_outcome(outcome)
            ├── terminal → stop loop, return result
            └── non-terminal → continue iteration
```

## File Format Conventions

| File | Format | Location |
|------|--------|----------|
| Mode state | JSON | `.omx/state/{mode}-state.json` |
| Team tasks | JSON | `.omx/team/tasks.json` |
| Team workers | JSON | `.omx/team/workers.json` |
| Team events | JSONL | `.omx/team/events.jsonl` |
| Session | JSON | `.omx/session.json` |
| Session history | JSONL | `.omx/logs/session-history.jsonl` |
| Daily log | JSONL | `.omx/logs/omx-{date}.jsonl` |
| Delivery log | JSONL | `.omx/logs/team-delivery-{date}.jsonl` |
| Hook log | JSONL | `.omx/logs/hooks-{date}.jsonl` |
| Runtime snapshot | JSON | `.omx/state/snapshot.json` |
| Runtime events | JSON | `.omx/state/events.json` |
| Config | TOML | `~/.codex/config.toml` |
| Ralph plans | Markdown | `.omx/ralph/plans/current.md` |

## Platform Support

- **Linux/macOS**: Full support including tmux team mode
- **Windows**: Full support except:
  - PID checking uses `ctypes.windll.kernel32.OpenProcess` instead of `os.kill(pid, 0)`
  - `echo` requires `cmd /c echo` (shell builtin, not a binary)
  - tmux requires WSL or a Windows tmux port

## Testing Strategy

All tests use stdlib `unittest`. No test dependencies.

- **Unit tests**: Test individual modules in isolation
- **State tests**: Use `tempfile.TemporaryDirectory()` for ephemeral state
- **Subprocess tests**: Test CLI via `subprocess.run([sys.executable, "-m", "omx", ...])`
- **No mocking framework**: Uses `unittest.mock.patch` from stdlib where needed
