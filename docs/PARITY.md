# omx-py Parity Checklist

Source-of-truth for porting `oh-my-codex` (TypeScript) → `omx-py` (Python). Every TS export gets one row.

**Status codes:**
- ✅ **Ported** — full behavioral parity, tests cover it.
- 🟡 **Partial** — exists in Python but missing branches, fields, or callers.
- 🔴 **Stub** — name exists but body is a no-op or trivial placeholder.
- ❌ **Missing** — no Python equivalent.
- ⚪ **Out of scope** — intentionally not ported (see Locked Decisions in PORT_PLAN.md).

**Phase column:** matches PORT_PLAN.md phases (P0–P10).

---

## Phase 1 — Team State Core

### `team/state.ts` → `omx.team.state.*`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `TeamConfig` | ✅ | — | `team.contracts` (dict) | V1 shape only |
| `WorkerInfo` | ✅ | — | `team.contracts.TeamWorker` | |
| `WorkerHeartbeat` | ✅ | — | `team.state.io` | |
| `WorkerStatus` | ✅ | — | `team.state.io` | |
| `TeamTask` | ✅ | — | `team.contracts.TeamTask` | |
| `TeamTaskV2` | ❌ | P1 | — | |
| `TeamTaskClaim` | ✅ | — | `team.contracts` | |
| `TeamManifestV2` | 🟡 | P1 | `team.state.manifest.TeamManifestV2` | `policy`/`governance` typed as loose `dict[str, Any]`; normalized via `team.state.policy` on read/write |
| `TeamLeader` | ✅ | P1 | `team.state.manifest.TeamLeader` | |
| `TeamPolicy` | ✅ | — | `team.state.policy` | |
| `TeamGovernance` | ✅ | — | `team.state.policy` | |
| `PermissionsSnapshot` | ✅ | P1 | `team.state.manifest.PermissionsSnapshot` | |
| `TeamEvent` | ✅ | — | `team.contracts` | |
| `TeamMailboxMessage` | ✅ | — | `team.state.mailbox` | |
| `TeamMailbox` | ✅ | — | `team.state.mailbox` | |
| `TaskApprovalRecord` | ✅ | — | `team.state.approvals` | |
| `TeamDispatchRequest` | ✅ | — | `team.state.dispatch` | |
| `TeamDispatchRequestInput` | ✅ | — | `team.state.dispatch` | |
| `TeamDispatchRequestKind` | ✅ | — | `team.contracts` | |
| `TeamDispatchRequestStatus` | ✅ | — | `team.contracts` | |
| `TeamDispatchTransportPreference` | ✅ | — | `team.contracts` | |
| `TaskReadiness` | ✅ | — | `team.state.tasks.TaskReadiness` | |
| `ClaimTaskResult` | ✅ | — | `team.state.tasks` | |
| `TransitionTaskResult` | ✅ | — | `team.state.tasks` | |
| `ReleaseTaskClaimResult` | ✅ | — | `team.state.tasks` | |
| `ReclaimTaskResult` | ✅ | — | `team.state.tasks` | |
| `TeamSummary` | ✅ | — | `team.state.monitor` | |
| `ShutdownAck` | ✅ | P1 | `team.state.shutdown.ShutdownAck` | |
| `TeamMonitorSnapshotState` | ✅ | — | `team.state.monitor` | |
| `TeamWorkerIntegrationState` | ✅ | — | `team.contracts` | |
| `TeamPhaseState` | ✅ | — | `team.state.monitor` | |
| `TeamLeaderDecisionState` | ✅ | P1 | `team.state.leader.LEADER_DECISION_STATES` | Modeled as a frozenset of valid string values (no enum) |
| `TeamLeaderAttentionState` | ✅ | P1 | `team.state.leader.TeamLeaderAttentionState` | |
| `DEFAULT_MAX_WORKERS` | ✅ | — | `team.state.types` | |
| `ABSOLUTE_MAX_WORKERS` | ✅ | — | `team.state.types` | |
| `setWriteAtomicRenameForTests` | ✅ | — | `team.state.atomic.set_rename_for_tests` | Test hook |
| `resetWriteAtomicRenameForTests` | ✅ | — | `team.state.atomic.reset_rename_for_tests` | Test hook |
| `normalizeTeamPolicy` | ✅ | — | `team.state.policy.normalize_team_policy` | |
| `normalizeTeamGovernance` | ✅ | — | `team.state.policy.normalize_team_governance` | |
| `teamEventLogPath` | ✅ | — | `team.state.events` | |
| `writeAtomic` | ✅ | — | `team.state.atomic.write_atomic` | Shared utility — consolidate ad-hoc writes |
| `initTeamState` | ✅ | P1 | `team.state.manifest.init_team_state` | Creates dir tree + V1 config + V2 manifest |
| `writeTeamManifestV2` | ✅ | P1 | `team.state.manifest.write_team_manifest_v2` | Atomic via `team.state.atomic.write_atomic` |
| `readTeamManifestV2` | ✅ | P1 | `team.state.manifest.read_team_manifest_v2` | No V1 migration fallback (locked decision) |
| `migrateV1ToV2` | ⚪ | — | — | Out of scope per Locked Decision #2 |
| `readTeamConfig` | ✅ | — | `team.state.io.read_team_config` | |
| `saveTeamConfig` | ✅ | — | `team.state.io.write_team_config` | |
| `writeWorkerIdentity` | ✅ | — | `team.state.io.write_worker_identity` | |
| `readWorkerHeartbeat` | ✅ | — | `team.state.io` | |
| `updateWorkerHeartbeat` | 🟡 | P2 | `team.state.io.write_worker_heartbeat` | Verify field parity during Phase 2 wiring |
| `readWorkerStatus` | ✅ | — | `team.state.io.read_worker_status` | |
| `writeWorkerStatus` | ✅ | — | `team.state.io.write_worker_status` | |
| `withScalingLock` | ✅ | — | `team.state.locks.with_scaling_lock` | Wrapped by `team_ops.team_with_scaling_lock` |
| `writeWorkerInbox` | ✅ | — | `team.state.io.write_worker_inbox` | |
| `createTask` | ✅ | — | `team.state.tasks.create_task` | Bulk-file storage (per-task V2 file layout deferred) |
| `readTask` | ✅ | — | `team.state.tasks.read_task` | |
| `listTasks` | ✅ | — | `team.state.tasks.list_tasks` | |
| `updateTask` | ✅ | — | `team.state.tasks.update_task` | Rejects invalid status (TS parity) |
| `claimTask` | ✅ | — | `team.state.tasks.claim_task` | |
| `releaseTaskClaim` | ✅ | — | `team.state.tasks.release_task_claim` | |
| `reclaimExpiredTaskClaim` | ✅ | — | `team.state.tasks.reclaim_expired_task` | |
| `transitionTaskStatus` | ✅ | — | `team.state.tasks.transition_task_status` | |
| `computeTaskReadiness` | ✅ | — | `team.state.tasks.compute_task_readiness` | |
| `sendDirectMessage` | ✅ | — | `team.state.mailbox.send_direct_message` | |
| `broadcastMessage` | ✅ | — | `team.state.mailbox.broadcast_message` | Recipient list resolved by `team_ops.team_broadcast` |
| `listMailboxMessages` | ✅ | — | `team.state.mailbox.read_mailbox` | |
| `markMessageDelivered` | ✅ | — | `team.state.mailbox.mark_message_delivered` | |
| `markMessageNotified` | ✅ | — | `team.state.mailbox.mark_message_notified` | |
| `enqueueDispatchRequest` | ✅ | — | `team.state.dispatch.enqueue_dispatch_request` | |
| `listDispatchRequests` | ✅ | — | `team.state.dispatch.read_dispatch_requests` | |
| `readDispatchRequest` | ✅ | — | `team.state.dispatch.read_dispatch_request` | |
| `transitionDispatchRequest` | ✅ | — | `team.state.dispatch.transition_dispatch_request` | |
| `markDispatchRequestNotified` | ✅ | — | `team.state.dispatch.mark_dispatch_request_notified` | |
| `markDispatchRequestDelivered` | ✅ | — | `team.state.dispatch.mark_dispatch_request_delivered` | |
| `appendTeamEvent` | ✅ | — | `team.state.events.append_team_event` | |
| `readTaskApproval` | ✅ | — | `team.state.approvals.read_task_approval` | |
| `writeTaskApproval` | ✅ | — | `team.state.approvals.write_task_approval` | |
| `getTeamSummary` | ✅ | — | `team.state.monitor.get_team_summary` | |
| `writeShutdownRequest` | ✅ | P1 | `team.state.shutdown.write_shutdown_request` | |
| `readShutdownAck` | ✅ | P1 | `team.state.shutdown.read_shutdown_ack` | |
| `readMonitorSnapshot` | ✅ | — | `team.state.monitor.read_monitor_snapshot` | |
| `writeMonitorSnapshot` | ✅ | — | `team.state.monitor.write_monitor_snapshot` | |
| `readTeamPhase` | ✅ | — | `team.state.monitor.read_phase_state` | |
| `writeTeamPhase` | ✅ | — | `team.state.monitor.write_phase_state` | |
| `readTeamLeaderAttention` | ✅ | P1 | `team.state.leader.read_team_leader_attention` | |
| `writeTeamLeaderAttention` | ✅ | P1 | `team.state.leader.write_team_leader_attention` | Atomic via `team.state.atomic.write_atomic` |
| `markTeamLeaderSessionStopped` | ✅ | P1 | `team.state.leader.mark_team_leader_session_stopped` | Idempotent; wraps `mark_team_leader_stop_observed(source='native_session_end')` |
| `markOwnedTeamsLeaderSessionStopped` | ✅ | P1 | `team.state.leader.mark_owned_teams_leader_session_stopped` | Walks `.omx/team/*` and stops teams whose manifest `leader.session_id` matches |
| `cleanupTeamState` | ✅ | — | `team.state.io.cleanup_team_state` | Idempotent |
| `resolveDispatchLockTimeoutMs` | ✅ | — | `team.state.dispatch.resolve_dispatch_lock_timeout_ms` | Reads `OMX_TEAM_DISPATCH_LOCK_TIMEOUT_MS`, clamps to [1000, 60000] |

### `team/team-ops.ts` → `omx.team.team_ops`

Gateway re-export module. Imports normalize all signatures to `(team_name, ..., cwd)` matching TS. State-layer functions that take `team_dir: Path` are wrapped to resolve via `team.state_root.team_dir`.

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| All re-exports | ✅ | — | `team.team_ops` | 50 functions + 20 type re-exports; tests in `test_team_ops.py` |
| `TeamTaskV2` | 🟡 | future | — | Per-task file storage not yet adopted; bulk file kept for now |

---

## Phase 2 — Team Runtime

### `team/runtime.ts` → `omx.team.runtime`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `TeamSnapshot` | ❌ | P2 | — | |
| `TeamRuntime` | ❌ | P2 | — | |
| `TeamShutdownSummary` | ❌ | P2 | — | |
| `TeamStartOptions` | ❌ | P2 | — | |
| `StaleTeamSummary` | ❌ | P2 | — | |
| `applyCreatedInteractiveSessionToConfig` | ❌ | P2 | — | |
| `shouldPrekillInteractiveShutdownProcessTrees` | ❌ | P2 | — | |
| `cleanupTeamWorkerLaunchOrphanedMcpProcesses` | ❌ | P2 | — | |
| `waitForWorkerStartupEvidence` | ❌ | P2 | — | Codex path |
| `waitForClaudeStartupEvidence` | ❌ | P2 | — | Claude path, ~430 LOC per Locked Decision #3 |
| `resolveWorkerLaunchArgsFromEnv` | ❌ | P2 | — | |
| `startTeam` | ❌ | P2 | — | Entry — Codex first, then Claude |
| `monitorTeam` | 🔴 | P2 | `team.runtime.monitor_team` | Parallel reads via ThreadPoolExecutor per Locked Decision #1 |
| `assignTask` | 🟡 | P2 | `team.runtime.assign_pending_tasks` | Missing role-aware allocation |
| `reassignTask` | ❌ | P2 | — | |
| `shutdownTeam` | ❌ | P2 | — | |
| `resumeTeam` | ❌ | P2 | — | Largest single function (~1190 LOC) |
| `sendWorkerMessage` | ❌ | P2 | — | |
| `broadcastWorkerMessage` | ❌ | P2 | — | |

---

## Phase 3 — Team Scaling

### `team/scaling.ts` → `omx.team.scaling`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `isScalingEnabled` | ❌ | P3 | — | Env gate `OMX_TEAM_SCALING_ENABLED` |
| `ScaleUpResult` | ❌ | P3 | — | |
| `ScaleDownResult` | ❌ | P3 | — | |
| `ScaleError` | ❌ | P3 | — | |
| `ScaleDownOptions` | ❌ | P3 | — | |
| `scaleUp` | ❌ | P3 | — | ~430 LOC executor |
| `scaleDown` | ❌ | P3 | — | ~160 LOC drainer |
| `evaluate_scaling` (Python-side heuristic) | ✅ | — | `team.scaling.evaluate_scaling` | Keep; wire to scaleUp/scaleDown in future auto-scaler |
| `resolve_max_workers` (Python-side helper) | ✅ | — | `team.scaling.resolve_max_workers` | Reads `OMX_TEAM_MAX_WORKERS` |

---

## Phase 4 — Autoresearch Runtime

### `autoresearch/contracts.ts` → `omx.autoresearch.contracts`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `AutoresearchEvaluatorContract` | ❌ | P4 | — | |
| `ParsedSandboxContract` | ❌ | P4 | — | |
| `AutoresearchEvaluatorResult` | ❌ | P4 | — | |
| `AutoresearchMissionContract` | ❌ | P4 | — | |
| `ResearchMission` (Python) | ✅ | — | `autoresearch.contracts.ResearchMission` | Equivalent shape exists |
| `ResearchCandidate` (Python) | ✅ | — | `autoresearch.contracts.ResearchCandidate` | |
| `slugifyMissionName` | ❌ | P4 | — | |
| `parseSandboxContract` | ❌ | P4 | — | |
| `parseEvaluatorResult` | ❌ | P4 | — | |
| `loadAutoresearchMissionContract` | ❌ | P4 | — | |

### `autoresearch/runtime.ts` → `omx.autoresearch.runtime`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `PreparedAutoresearchRuntime` | ❌ | P4 | — | |
| `AutoresearchEvaluationRecord` | ❌ | P4 | — | |
| `AutoresearchCandidateArtifact` | ❌ | P4 | — | |
| `AutoresearchLedgerEntry` | ❌ | P4 | — | |
| `AutoresearchRunManifest` | ❌ | P4 | — | |
| `buildAutoresearchRunTag` | ❌ | P4 | — | |
| `assertResetSafeWorktree` | ❌ | P4 | — | |
| `countTrailingAutoresearchNoops` | ❌ | P4 | — | |
| `runAutoresearchEvaluator` | ❌ | P4 | — | |
| `decideAutoresearchOutcome` | ❌ | P4 | — | |
| `buildAutoresearchInstructions` | ❌ | P4 | — | |
| `materializeAutoresearchMissionToWorktree` | ❌ | P4 | — | |
| `loadAutoresearchRunManifest` | ❌ | P4 | — | |
| `prepareAutoresearchRuntime` | ❌ | P4 | — | Main entry |
| `resumeAutoresearchRuntime` | ❌ | P4 | — | |
| `parseAutoresearchCandidateArtifact` | ❌ | P4 | — | |
| `processAutoresearchCandidate` | ❌ | P4 | — | |
| `finalizeAutoresearchRunState` | ❌ | P4 | — | |
| `stopAutoresearchRuntime` | ❌ | P4 | — | |
| `run_research_loop` (Python stub) | 🔴 | P4 | `autoresearch.runtime` | 13-LOC placeholder; replace |

---

## Phase 5 — Ralph Completion

### `ralph/persistence.ts` → `omx.ralph.persistence`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `RalphVisualFeedback` | ❌ | P5 | — | Unblocks `$visual-ralph` |
| `RalphProgressLedger` | ❌ | P5 | — | |
| `RalphCanonicalArtifacts` | ❌ | P5 | — | |
| `recordRalphVisualFeedback` | ❌ | P5 | — | |
| `ensureCanonicalRalphArtifacts` | ✅ | — | `ralph.persistence.ensure_canonical_ralph_artifacts` | |

### `ralph/contract.ts` → `omx.ralph.contract`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `RALPH_PHASES` | ✅ | — | `ralph.contract` | Verify constant matches |
| `RalphStateValidationResult` | ✅ | — | `ralph.contract` | |
| `normalizeRalphPhase` | 🟡 | P5 | `ralph.contract` | Confirm signature parity |
| `validateAndNormalizeRalphState` | ✅ | — | `ralph.contract.validate_and_normalize_ralph_state` | |

---

## Phase 6 — Config Strip/Merge

### `config/generator.ts` → `omx.config.generator`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `hasLegacyOmxTeamRunTable` | ❌ | P6 | — | |
| `getRootModelName` | ❌ | P6 | — | |
| `stripOmxSeededBehavioralDefaults` | ❌ | P6 | — | |
| `stripOmxTopLevelKeys` | ❌ | P6 | — | |
| `upsertCodexHooksFeatureFlag` | ❌ | P6 | — | |
| `stripOmxFeatureFlags` | ❌ | P6 | — | |
| `stripOmxEnvSettings` | ❌ | P6 | — | |
| `stripExistingOmxBlocks` | ❌ | P6 | — | |
| `stripExistingSharedMcpRegistryBlock` | ❌ | P6 | — | |
| `buildMergedConfig` | ❌ | P6 | — | Replaces trivial Python `merge_config` |
| `repairConfigIfNeeded` | ❌ | P6 | — | |
| `mergeConfig` | 🔴 | P6 | `config.generator.merge_config` | 5-line stub vs real merger |

---

## Phase 7 — Hooks Completion

### Missing TS files

| TS File | Status | Phase | Notes |
|---|---|---|---|
| `hooks/prompt-guidance-contract.ts` | ❌ | P7 | Canonical prompt-guidance contract |
| `hooks/triage-heuristic.ts` | ❌ | P7 | Triage routing heuristic for UserPromptSubmit |

Content-level gaps in existing files to be audited during P7.

---

## Phase 8 — HUD Watch Loop

### `hud/index.ts` → `omx.hud`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `watchRenderLoop` | ❌ | P8 | — | |
| `runWatchMode` | ❌ | P8 | — | |
| `hudCommand` | ❌ | P8 | — | |
| `shellEscape` | ❌ | P8 | — | |
| `buildTmuxSplitArgs` | ❌ | P8 | — | |

### `hud/render.ts` → `omx.hud.renderer`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `RenderHudOptions` | ❌ | P8 | — | |
| `renderHud` | 🔴 | P8 | `hud.renderer.render_statusline` | Python has minimal statusline only |
| `countRenderedHudLines` | ❌ | P8 | — | |

### `hud/state.ts` → `omx.hud.state`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `readRalphState` | ❌ | P8 | — | |
| `readUltraworkState` | ❌ | P8 | — | |
| `normalizeHudConfig` | ❌ | P8 | — | |

---

## Phase 9 — CLI Surface

Per-subcommand audit. Each existing `_handle_*` needs depth verification against the matching `cli/*.ts`. Subcommands fully missing from Python:

| TS Subcommand | Status | Phase | Notes |
|---|---|---|---|
| `omx star-prompt` | ❌ | P9 | Not in `KNOWN_SUBCOMMANDS` |
| `omx mcp-parity` | ❌ | P9 | |
| `omx tmux-hook` | ❌ | P9 | Referenced but not registered |
| `omx catalog-contract` | ❌ | P9 | |
| `omx native-assets` | ❌ | P9 | |
| `omx omx` | ❌ | P9 | |
| `omx question` | ❌ | P9 | Top-level subcommand missing |
| `omx codex-home` | ❌ | P9 | |
| `omx team scale-up` | ❌ | P9 | Subcommand of team |
| `omx team scale-down` | ❌ | P9 | |
| `omx team reassign` | ❌ | P9 | |
| `omx team send-message` | ❌ | P9 | |
| `omx team broadcast` | ❌ | P9 | |
| `omx adapt` | ⚪ | — | Out of scope per Locked Decision #5 |
| `omx autoresearch-guided` | 🟡 | P4/P9 | Deprecated stub today; wire to P4 runtime |
| `omx autoresearch-intake` | 🟡 | P4/P9 | Same |

---

## Out of Scope (per Locked Decisions)

| TS Module | LOC | Status | Locked Decision |
|---|---:|---|---|
| `adapt/` | 1,668 | ⚪ Out of scope | #5 |
| `openclaw/` | 1,157 | ⚪ Out of scope | #5 |
| `notifications/` (advanced) | 5,800 | ⚪ Out of scope | #4 — basic `notifier.py` retained |
| `compat/` | 0 | ⚪ Out of scope | Backward-compat shims unused in Python |
| `types/` | 64 | ⚪ Refolded | Per-module local types in Python |

---

## How to Use This File

1. **Before starting a port:** find the row, claim it in the working PR description.
2. **When porting:** update Status and Python Location columns in the same commit.
3. **When you find a new gap:** add a row immediately with status ❌.
4. **At phase end:** every ❌ in that phase must be either ✅ or moved to a later phase with reason.

This file is the parallelization contract — Codex native subagents working concurrent file ports use it to claim non-overlapping rows.
