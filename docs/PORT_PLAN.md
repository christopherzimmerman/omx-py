# omx-py Port Plan

**Status:** Not feature-complete. Several runtime workflows are stubs or thin shells.
**Baseline:** 27,811 LOC across 186 files; 531 tests pass; structural skeleton in place.
**Reference:** `oh-my-codex/src` TypeScript repo (~70k LOC across ~270 files).

## Locked Decisions (2026-05-13)

1. **Concurrency model: sync + ThreadPoolExecutor.** No asyncio. Parallelism (worker probes, pane reads, monitor fan-out) goes through `concurrent.futures.ThreadPoolExecutor`. Public APIs stay synchronous.
2. **V1 team manifest: skip.** Implement V2 only. No `migrateV1ToV2`. Document in code that V1 state files are not supported.
3. **Claude worker support: in scope for Phase 2.** Port both `waitForWorkerStartupEvidence` (Codex) and `waitForClaudeStartupEvidence` (Claude). Adds ~2 days to Phase 2.
4. **Notifications: permanently out of scope.** Keep `notifications/notifier.py` as the final surface. Remove leftover notification env vars and skill references from docs.
5. **adapt / openclaw: permanently out of scope.** Both modules stay deleted. Remove any leftover references.
6. **Execution cadence: single-developer sequential.** Phases run in order. Within a phase, parallelize via Codex native subagents for independent subtasks (e.g., porting multiple small contract files concurrently). No multi-developer split.
7. **Parity validation: `docs/PARITY.md` checklist + new tests per phase.** No port of the TS test suite. No shared JSON fixtures.

---

## Audit Summary

LOC ratio (Python ÷ TypeScript) by top-level module. Anything **<0.50x** means significant functionality is missing inside files that exist.

| Module | TS LOC | Py LOC | Ratio | Status |
|---|---:|---:|---:|---|
| **team** | 18,938 | 4,180 | **0.22x** | 🔴 Critical — runtime, scaling, state half-ported |
| **cli** | 16,079 | 3,037 | **0.19x** | 🔴 Critical — many handlers thin/missing |
| **scripts** | 15,632 | 332 | **0.02x** | 🟡 Mixed — many are TS/npm build-time (N/A); a few are runtime |
| **notifications** | 5,915 | 144 | **0.02x** | ⚪ Intentional — removed in last commit (`feat: Merge notification removal`) |
| **autoresearch** | 1,757 | 404 | **0.23x** | 🔴 Critical — `runtime.py` is a 13-line stub |
| **ralph** | 459 | 97 | **0.21x** | 🔴 Critical — Visual-Ralph + ledger missing |
| **config** | 2,052 | 815 | **0.40x** | 🟠 High — config-merge & strip-logic missing (breaks `omx setup`) |
| **hooks** | 5,385 | 3,228 | **0.60x** | 🟠 High — 2 named files missing + content gaps |
| **hud** | 1,728 | 1,075 | **0.62x** | 🟠 High — watch loop, full renderHud, state readers missing |
| **mcp** | 3,238 | 2,141 | **0.66x** | 🟡 Moderate |
| **runtime** | 1,080 | 750 | 0.69x | 🟡 Moderate |
| **agents** | 818 | 573 | 0.70x | 🟡 Moderate |
| **state** | 1,317 | 1,020 | 0.77x | 🟡 Moderate |
| **modes** | 299 | 255 | 0.85x | 🟢 Acceptable |
| **catalog** | 409 | 359 | 0.88x | 🟢 Acceptable |
| **session-history** | 387 | 346 | 0.89x | 🟢 Acceptable |
| **pipeline** | 826 | 777 | 0.94x | 🟢 Acceptable |
| **question** | 1,787 | 1,970 | 1.10x | 🟢 Acceptable |
| **document-refresh** | 463 | 556 | 1.20x | 🟢 Acceptable |
| **wiki** | 1,259 | 1,638 | 1.30x | 🟢 Acceptable |
| **verification** | 125 | 193 | 1.54x | 🟢 Acceptable |
| **planning** | 183 | 299 | 1.63x | 🟢 Acceptable |
| **visual** | 90 | 171 | 1.90x | 🟢 Acceptable |
| **subagents** | 235 | 468 | 1.99x | 🟢 Acceptable |

**Intentionally dropped (do not port):** `adapt` (1,668 LOC, deleted in last commit), `openclaw` (1,157 LOC, deleted in last commit), `compat` (0 LOC), `types` (64 LOC of shared interfaces, refolded into module-local types in Python).

---

## Critical Gaps (block "feature complete")

### G1. team/runtime.ts (4,593 LOC) → team/runtime.py (304 LOC) — **7% ported**

This is the single biggest gap. The Python file has 9 functions covering basic task assignment and completion marking. The TypeScript runtime is the team orchestrator and is missing:

| Function | TS LOC | Purpose |
|---|---:|---|
| `startTeam` | ~600 | Team initialization, tmux session creation, worker bootstrap, role assignment, manifest persistence |
| `monitorTeam` | ~250 | Full team snapshot with per-worker state, task state, phase state |
| `assignTask` | ~110 | Role-aware task assignment, allocation policy, claim logic, dispatch envelope |
| `reassignTask` | ~13 | Move a task between workers |
| `shutdownTeam` | ~354 | Graceful shutdown: drain workers, kill panes, persist final state, cleanup MCP orphans |
| `resumeTeam` | ~1,190 | Reconstruct runtime from persisted state, replay pending dispatch, rewire MCP, restore pane attachment |
| `sendWorkerMessage` | ~40 | Direct message to a worker |
| `broadcastWorkerMessage` | ~50 | Broadcast to all workers in a team |
| `waitForWorkerStartupEvidence` | ~20 | Probe for Codex worker readiness |
| `waitForClaudeStartupEvidence` | ~430 | Probe for Claude worker readiness (different telemetry path) |
| `applyCreatedInteractiveSessionToConfig` | ~55 | Persist session-id captured at startup |
| `resolveWorkerLaunchArgsFromEnv` | ~75 | Resolve CLI launch args (model, reasoning effort) from env contract |
| `cleanupTeamWorkerLaunchOrphanedMcpProcesses` | — | Kill orphaned MCP children from a failed launch |
| `shouldPrekillInteractiveShutdownProcessTrees` | — | Decide whether to kill child trees on shutdown |
| `TeamSnapshot`, `TeamRuntime`, `TeamShutdownSummary`, `TeamStartOptions`, `StaleTeamSummary` | — | Public types |

### G2. team/scaling.ts (825 LOC) → team/scaling.py (94 LOC) — **11% ported**

Already detailed in the prior gap review. **Dynamic mid-session scaling is entirely absent.** Python has only a heuristic decision function (`evaluate_scaling`) that is never called. Missing:

- `isScalingEnabled` / `assertScalingEnabled` (env gate `OMX_TEAM_SCALING_ENABLED`)
- `scaleUp` — ~430 LOC executor (validate capacity, create panes, bootstrap workers, full rollback path)
- `scaleDown` — ~160 LOC drainer (write `draining` status, poll-or-timeout, teardown panes, worktree cleanup)
- `ScaleDownOptions` (workerNames / count / force / drainTimeoutMs)
- All result types (`ScaleUpResult`, `ScaleDownResult`, `ScaleError`)
- `notifyWorkerPaneOutcome` with `DispatchOutcome` reporting
- `next_worker_index` monotonic counter in team config
- The `'draining'` state machine (`DRAINING` enum is defined in `state/types.py:30` but nothing reads/writes it)
- `withScalingLock` usage (the lock primitive exists in `state/locks.py:114` but is never acquired)

### G3. team/state.ts (2,100 LOC) → split across 9 Python files (1,020 LOC) — **~49% ported**

The canonical team state surface is half-ported. Underlying impls in `state/io.py`, `state/tasks.py`, `state/mailbox.py`, `state/dispatch.py`, `state/monitor.py`, `state/approvals.py`, `state/events.py`, `state/locks.py`. Missing:

- **V2 manifest** (`TeamManifestV2`) — `readTeamManifestV2`, `writeTeamManifestV2` — Python has no V2 manifest at all
- **V1→V2 migration** — `migrateV1ToV2`
- **Policy/governance normalization** — `normalizeTeamPolicy`, `normalizeTeamGovernance`
- **Task readiness** — `computeTaskReadiness` (was the dependency-aware ready-to-claim check; `depends_on` field also missing from Python tasks until removed in last cleanup)
- **Leader attention** — `readTeamLeaderAttention`, `writeTeamLeaderAttention`, `markTeamLeaderSessionStopped`, `markOwnedTeamsLeaderSessionStopped`
- **Shutdown handshake** — `writeShutdownRequest`, `readShutdownAck` (status enum mentioned in `contracts.py:61-62` only, no impl)
- **Cleanup** — `cleanupTeamState`
- **Atomic write helper** — `writeAtomic` (no shared utility; Python re-implements per file)

### G4. team/team-ops.ts (129 LOC) → team/team_ops.py (72 LOC) — **gateway pattern abandoned**

The TS `team-ops.ts` is the canonical MCP-aligned gateway — both the MCP server and the runtime import ~50 `team*`-prefixed functions from it. The Python port has 2 functions (`create_team_task`, `list_team_tasks`). Callers reach into `state/` directly instead, scattering the import surface and breaking the "single canonical surface" contract.

### G5. autoresearch/runtime.ts (1,294 LOC) → runtime.py (13 LOC) — **1% ported**

`runtime.py` is a stub. The entire autoresearch lifecycle is missing:

- `prepareAutoresearchRuntime` (main entry — sets up worktree, mission contract, run manifest, ledger)
- `resumeAutoresearchRuntime` (resume from persisted run)
- `runAutoresearchEvaluator` (invoke evaluator child agent, parse result)
- `processAutoresearchCandidate` (single candidate iteration: build instructions, invoke worker, evaluate, record)
- `materializeAutoresearchMissionToWorktree`
- `loadAutoresearchRunManifest` / `assertResetSafeWorktree`
- `buildAutoresearchInstructions` / `decideAutoresearchOutcome`
- `parseAutoresearchCandidateArtifact`
- `finalizeAutoresearchRunState` / `stopAutoresearchRuntime`
- `countTrailingAutoresearchNoops` / `buildAutoresearchRunTag`

The contracts file (`contracts.py`) has the data types but no parser (`parseSandboxContract`, `parseEvaluatorResult`, `loadAutoresearchMissionContract`).

### G6. ralph/ — **21% ported**

Python has `validate_and_normalize_ralph_state`, `ensure_canonical_ralph_artifacts`, `read_ralph_plan`, `write_ralph_plan`. Missing:

- `recordRalphVisualFeedback` + `RalphVisualFeedback` type — **Visual-Ralph workflow is broken** (the `$visual-ralph` skill writes verdict JSON to `.omx/state/{scope}/ralph-progress.json`; no Python writer)
- `RalphProgressLedger` type and write path
- `RalphCanonicalArtifacts` type (full artifact bundle)
- ~~`normalizeRalphPhase` (Python has different signature)~~ — ported as `normalize_ralph_phase`; phase set + alias map now aligned with TS

---

## High Gaps

### G7. cli/ — **19% ratio, but structurally consolidated**

Python collapses 29 TS subcommand files into a single 1,410-line `__init__.py` with 27 `_handle_*` functions. Some are real, many are thin. CLI handlers known to be thin or missing:

| Subcommand | TS file | Python state |
|---|---|---|
| `omx adapt` | `cli/adapt.ts` (existed) | Intentionally removed (adapt module deleted) |
| `omx autoresearch` | `cli/autoresearch.ts` + `-guided` + `-intake` | Deprecated stub — prints "use skill keyword instead" |
| `omx team` | `cli/team.ts` | `_handle_team` + `_handle_team_status` + `_handle_team_shutdown` — covers spawn/status/shutdown only; missing reassign, scale_up, scale_down, send-message, broadcast |
| `omx ralph` | `cli/ralph.ts` | `_handle_ralph` only |
| `omx star-prompt` | `cli/star-prompt.ts` | Not in `KNOWN_SUBCOMMANDS` list |
| `omx mcp-parity` | `cli/mcp-parity.ts` | Missing |
| `omx tmux-hook` | `cli/tmux-hook.ts` | In subcommand list but not in `KNOWN_SUBCOMMANDS` |
| `omx catalog-contract` | `cli/catalog-contract.ts` | Missing |
| `omx native-assets` | `cli/native-assets.ts` | Missing |
| `omx omx` | `cli/omx.ts` | Missing |
| `omx question` | `cli/question.ts` | Missing as top-level subcommand |
| `omx codex-home` | `cli/codex-home.ts` | Missing |

Real depth audit needed per handler — many existing handlers (e.g., `_handle_exec`, `_handle_session`) may also be thin.

### G8. config/generator.ts (1,200+ LOC of strip/merge/repair) → generator.py (37 LOC of 3 functions)

This is critical for `omx setup`. Python has trivial read/write/merge. Missing:

- `hasLegacyOmxTeamRunTable`, `getRootModelName`
- `stripOmxSeededBehavioralDefaults`, `stripOmxTopLevelKeys`, `stripOmxFeatureFlags`, `stripOmxEnvSettings`
- `stripExistingOmxBlocks`, `stripExistingSharedMcpRegistryBlock`
- `upsertCodexHooksFeatureFlag`
- `buildMergedConfig`, `repairConfigIfNeeded`, `mergeConfig` (the *real* merger, not the 5-line one in Python)

Without these, `omx setup` cannot cleanly re-install over an existing config: it can't strip prior OMX blocks before merging new ones.

### G9. hooks/ — 2 named files missing + content gaps

Missing TS files:
- `prompt-guidance-contract.ts` — the canonical prompt-guidance contract (referenced by AGENTS.md schema)
- `triage-heuristic.ts` — the triage routing heuristic used by `UserPromptSubmit`

### G10. hud/ — watch loop, full renderer, state readers missing

Python `renderer.py` has one function (`render_statusline`); TS has `renderHud` + `countRenderedHudLines` + `RenderHudOptions`. Also missing: `watchRenderLoop`, `runWatchMode`, `hudCommand`, `buildTmuxSplitArgs`, `readRalphState`, `readUltraworkState`. The `omx hud watch` command surface is non-functional.

---

## Phased Plan

Phases are ordered by dependency and risk. Each phase ends at a tested, mergeable state.

### Phase 0 — Pre-port hygiene (1 day)
- Update README ("~6,000 lines" → actual 27,811; "520 tests" → 532).
- Delete two confirmed-orphan files now (no callers anywhere): `team/team_ops.py`'s misleading shape — replace with the proper gateway re-export (Phase 4 work), OR keep current state with a TODO header. Don't delete `scaling.py` yet — `evaluate_scaling` is salvageable.
- Add a `docs/PARITY.md` checklist tracking each TS export's port status.
- Add `pyflakes` + `mypy` to CI; tests already pass.

### Phase 1 — Team state core (3–4 days, blocks everything else)
Port **team/state.ts** to a single canonical surface (or keep the split). Add the missing primitives:
- `TeamManifestV2`, `readTeamManifestV2`, `writeTeamManifestV2` *(no V1 migration — V2 only per Locked Decision #2)*
- `normalizeTeamPolicy`, `normalizeTeamGovernance`
- `computeTaskReadiness` (re-add `depends_on` task field; it was removed in cleanup but the upstream design uses it)
- `readTeamLeaderAttention` / `writeTeamLeaderAttention` / `markTeamLeaderSessionStopped` / `markOwnedTeamsLeaderSessionStopped`
- `writeShutdownRequest` / `readShutdownAck`
- `cleanupTeamState`
- Shared `write_atomic` utility (consolidate the ~6 ad-hoc atomic writes in current Python state files)

Port **team/team-ops.ts** as the canonical gateway re-exporting Phase 1 primitives under MCP-aligned names. Update MCP servers and runtime to import from `team_ops` only.

**Acceptance:** all current tests still pass; new tests cover V2 manifest, leader-attention, shutdown handshake, `computeTaskReadiness`.

### Phase 2 — Team runtime (15–22 days; rescoped after Phase 1 audit)
Port **team/runtime.ts** (4,593 LOC) plus the support modules it depends on. Original 7–9d estimate undercounted by missing the ~4,700 LOC of support-module gaps. Concurrency uses `ThreadPoolExecutor` per Locked Decision #1.

**Phase 2.0 — Independent slice (~1 day, parallel-safe):**
- `TeamSnapshot` / `TeamRuntime` / `TeamShutdownSummary` / `TeamStartOptions` / `StaleTeamSummary` dataclasses
- `applyCreatedInteractiveSessionToConfig`
- `resolveWorkerLaunchArgsFromEnv`
- `shouldPrekillInteractiveShutdownProcessTrees`
- `cleanupTeamWorkerLaunchOrphanedMcpProcesses`

**Phase 2.1–2.7 — Deepen support modules (~9–12 days, several can parallelize):**
Each support module currently runs <30% of TS LOC; runtime functions can't be built without them.

| Sub | Module | TS LOC | Py LOC | Notes |
|---|---|---:|---:|---|
| 2.1 | `tmux_session.py` | 2000 | 396 | 48 missing exports (createTeamSession, buildWorkerStartupCommand, resolveTeamWorkerCli*, sanitizeTeamName, waitForWorkerReady, dismissTrustPromptIfPresent, sendToWorker, isWorkerAlive, killWorker, resize hooks, mouse scrolling, leader-pane selection, …) |
| 2.2 | `worker_bootstrap.py` | 911 | 171 | 15 missing exports (generateWorkerRootAgentsContent, writeWorker*OverlayFile, generateInitialInbox, generateTaskAssignmentInbox, generateShutdownInbox, buildTriggerDirective, buildMailboxTriggerDirective, …) |
| 2.3 | `model_contract.py` | 204 | 26 | 11 missing exports (splitWorkerLaunchArgs, parseTeamWorkerLaunchArgs, normalizeTeamWorkerLaunchArgs, resolveTeamWorkerLaunchArgs, resolveAgentDefaultModel, …) |
| 2.4 | `role_router.py` | 350 | 100 | 3 missing exports (loadRolePrompt, isKnownRole, listAvailableRoles, RoleRouterResult, routeTaskToRole) |
| 2.5 | `worktree.py` | 552 | 132 | 12 missing exports (isGitRepository, isWorktreeDirty, parseWorktreeMode, planWorktreeTarget, ensureWorktree, rollbackProvisionedWorktrees, removeWorktreeForce, …) |
| 2.6 | `mcp_comm.py` | 492 | 60 | 6 missing exports (queueInboxInstruction, queueDirectMailboxMessage, queueBroadcastMailboxMessage, waitForDispatchReceipt, DispatchOutcome) |
| 2.7 | `api_interop.py` | 1189 | 89 | 5 missing exports (resolveTeamApiOperation, executeTeamApiOperation, TeamApiEnvelope, TeamApiOperation, …) |

**Phase 2.8 — Mid-level runtime (~3 days, can parallelize after 2.1–2.7):**
- `assignTask` + `reassignTask`
- `sendWorkerMessage` + `broadcastWorkerMessage`
- `monitorTeam` (TS-style TeamSnapshot, parallel reads via ThreadPoolExecutor)

**Phase 2.9 — startTeam + shutdown (~4 days, sequential):**
- `startTeam` (Codex path first, then Claude path)
- `waitForWorkerStartupEvidence` (Codex)
- `waitForClaudeStartupEvidence` (Claude — ~430 LOC)
- `shutdownTeam` (depends on shutdown handshake from Phase 1 ✅)

**Phase 2.10 — resumeTeam (~2 days, last):**
- `resumeTeam` (~1190 LOC — largest single function in the port)

**Acceptance:** spin up real Codex and Claude 3-worker teams, assign + complete a task, scale up/down, shut down cleanly, resume after kill; integration test with `tmux` shim.

### Phase 3 — Team scaling (2–3 days, depends on Phase 2)
Port **team/scaling.ts**. Replace the heuristic-only Python file with full executors:
1. `isScalingEnabled` + env gate.
2. `scaleUp` — use Phase 1 manifest, Phase 2 `startTeam` helpers for pane creation and bootstrap.
3. `scaleDown` — drain via `'draining'` worker state, then `teardownWorkerPanes`.
4. Wire `with_scaling_lock` (already in `state/locks.py`) into both.
5. Keep `evaluate_scaling` as a separate heuristic surface — make it actually feed `scaleUp`/`scaleDown` from a future auto-scaler tick.

**Acceptance:** can scale a running team from 3→5 workers and back without data loss.

### Phase 4 — Autoresearch runtime (4–5 days)
Port **autoresearch/runtime.ts** and finish `autoresearch/contracts.py`:
1. Contracts: `parseSandboxContract`, `parseEvaluatorResult`, `loadAutoresearchMissionContract`, `slugifyMissionName`, `assertResetSafeWorktree`.
2. Manifest: `loadAutoresearchRunManifest`, `buildAutoresearchRunTag`, `materializeAutoresearchMissionToWorktree`.
3. Loop: `buildAutoresearchInstructions`, `runAutoresearchEvaluator`, `decideAutoresearchOutcome`, `parseAutoresearchCandidateArtifact`, `processAutoresearchCandidate`.
4. Lifecycle: `prepareAutoresearchRuntime`, `resumeAutoresearchRuntime`, `finalizeAutoresearchRunState`, `stopAutoresearchRuntime`, `countTrailingAutoresearchNoops`.

**Acceptance:** `omx autoresearch` (or the skill-driven path) can drive a 3-iteration mission to completion.

### Phase 5 — Ralph completion (1–2 days)
Port the missing **ralph/persistence.ts** pieces:
- `RalphVisualFeedback`, `recordRalphVisualFeedback` — unblocks `$visual-ralph`.
- `RalphProgressLedger` + writer.
- `RalphCanonicalArtifacts` bundle type.

**Acceptance:** `$visual-ralph` skill can write and re-read verdict JSON.

### Phase 6 — Config strip/merge for `omx setup` idempotency (2–3 days)
Port **config/generator.ts** strip-and-merge functions. Order:
1. `stripOmxSeededBehavioralDefaults`, `stripOmxTopLevelKeys`, `stripOmxFeatureFlags`, `stripOmxEnvSettings`.
2. `stripExistingOmxBlocks`, `stripExistingSharedMcpRegistryBlock`.
3. `upsertCodexHooksFeatureFlag`, `getRootModelName`, `hasLegacyOmxTeamRunTable`.
4. `buildMergedConfig`, `repairConfigIfNeeded`.
5. Replace the trivial `merge_config` in Python with `buildMergedConfig`-equivalent.

**Acceptance:** running `omx setup` twice produces an idempotent config; running over a manually-edited config preserves user blocks.

### Phase 7 — Hooks completion (2 days)
- Port `hooks/prompt-guidance-contract.ts`.
- Port `hooks/triage-heuristic.ts`.
- Audit existing hook files for content-level parity (the 60% LOC ratio suggests several have skipped branches).

### Phase 8 — HUD watch loop (2 days)
- Port `hud/index.ts` — `watchRenderLoop`, `runWatchMode`, `hudCommand`, `buildTmuxSplitArgs`, `shellEscape`.
- Port full `renderHud` from `hud/render.ts` (currently only `render_statusline` exists).
- Port `readRalphState` / `readUltraworkState` from `hud/state.ts`.

**Acceptance:** `omx hud watch` runs and updates the statusline at the right cadence.

### Phase 9 — CLI surface completion (3–4 days)
Per-subcommand depth audit and gap-fill. Likely focus areas:
- `omx team` — add reassign/scale-up/scale-down/send-message/broadcast subcommands.
- `omx star-prompt`, `omx mcp-parity`, `omx catalog-contract`, `omx native-assets`, `omx omx`, `omx question`, `omx codex-home`, `omx tmux-hook` — add missing top-level subcommands.
- Audit thin handlers (`_handle_exec`, `_handle_session`, `_handle_ask`, `_handle_resume`) for content depth.

### Phase 10 — Polish (1 day)
- Remove dead references to deleted modules (`adapt`, `openclaw`, full notifications stack). Audit `KNOWN_SUBCOMMANDS`, README env-var table, and asset manifests for stale entries.
- Reconcile Python-larger modules (`subagents`, `visual`, `planning`, `verification`) — confirm extra code is intentional helpers and not zombie code.
- Final PARITY.md sweep: every TS export accounted for as ported, deferred, or out-of-scope.

---

## Effort Estimate

| Phase | Description | Days |
|---|---|---:|
| 0 | Pre-port hygiene | 1 |
| 1 | Team state core + team-ops gateway | 3–4 |
| 2.0 | Runtime types + small helpers | 1 |
| 2.1–2.7 | Support modules (tmux_session, worker_bootstrap, model_contract, role_router, worktree, mcp_comm, api_interop) | 9–12 |
| 2.8 | Mid-level runtime (assignTask, monitorTeam, sendWorkerMessage, broadcastWorkerMessage) | 3 |
| 2.9 | startTeam + shutdownTeam + waitFor*StartupEvidence (Codex + Claude) | 4 |
| 2.10 | resumeTeam | 2 |
| 3 | Team scaling | 2–3 |
| 4 | Autoresearch runtime | 4–5 |
| 5 | Ralph completion | 1–2 |
| 6 | Config strip/merge | 2–3 |
| 7 | Hooks completion | 2 |
| 8 | HUD watch loop | 2 |
| 9 | CLI surface completion | 3–4 |
| 10 | Polish | 1 |
| **Total** | — | **36–46 days** |

Single-developer sequential execution per Locked Decision #6. Within a phase, spawn Codex native subagents for independent file-level ports (e.g., in Phase 1, port `normalizeTeamPolicy`, `normalizeTeamGovernance`, and `computeTaskReadiness` concurrently as bounded subtasks). No multi-developer split or phase-level parallelism.

---

## Next Step

Phase 0 + Phase 1 start now. Phase 0 is the half-day hygiene pass (docs, PARITY.md scaffold). Phase 1 is the team state foundation that unblocks Phases 2 and 3. All open questions are resolved (see Locked Decisions at top); proceed without further blocking discussions.
