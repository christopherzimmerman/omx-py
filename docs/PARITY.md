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

### `team/state-root.ts` → `omx.team.state_root`

Single source of truth for the team state-root layout. `team.state.io`, `team.state.manifest`, `team.state.leader`, and `team.team_ops` all route their path computations through this module instead of hand-rolling `Path(cwd) / ".omx" / "team" / team_name`.

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `resolveCanonicalTeamStateRoot` | ✅ | P2.8 | `team.state_root.resolve_team_state_root` | Honors `OMX_TEAM_STATE_ROOT`; defaults to `.omx/team` (Python convention) instead of TS `.omx/state/team` to avoid breaking existing on-disk state. Absolute overrides win; relative overrides are anchored at `cwd`. |
| `team_dir` (helper) | ✅ | P2.8 | `team.state_root.team_dir` | Wrapper that appends `{team_name}` to `resolve_team_state_root(cwd)`. All `team.state.*` and `team.team_ops` callers go through this. |
| Composed-instructions path | 🟡 | future | `team.worker_bootstrap` | `worker_bootstrap` still writes `worker-agents.md` under the TS canonical `.omx/state/team/{name}/` directly — divergent from `state_root` until a coordinated breaking change moves the artifact. Flagged in `state_root.py` docstring. |

### `team/team-ops.ts` → `omx.team.team_ops`

Gateway re-export module. Imports normalize all signatures to `(team_name, ..., cwd)` matching TS. State-layer functions that take `team_dir: Path` are wrapped to resolve via `team.state_root.team_dir`.

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| All re-exports | ✅ | — | `team.team_ops` | 50 functions + 20 type re-exports; tests in `test_team_ops.py` |
| `TeamTaskV2` | 🟡 | future | — | Per-task file storage not yet adopted; bulk file kept for now |

### `team/api-interop.ts` → `omx.team.api_interop`

Canonical MCP-side envelope for team operations. Validates input, normalizes legacy tool names to canonical operation slugs, dispatches against `team.team_ops`, and returns the typed `TeamApiEnvelope` shape. Sync per Locked Decision #1; stdlib-only per Locked Decision #2.

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `LEGACY_TEAM_MCP_TOOLS` | ✅ | P2.7 | `team.api_interop.LEGACY_TEAM_MCP_TOOLS` | Tuple matches TS verbatim |
| `TEAM_API_OPERATIONS` | ✅ | P2.7 | `team.api_interop.TEAM_API_OPERATIONS` | 33-entry tuple; matches TS order |
| `TeamApiOperation` | ✅ | P2.7 | `team.api_interop.TeamApiOperation` | String alias; TS union collapsed to `str` |
| `TeamApiEnvelope` | ✅ | P2.7 | `team.api_interop.TeamApiEnvelope` | `dict[str, Any]`; `{ok, operation, data}` or `{ok, operation, error: {code, message}}` |
| `resolveTeamApiOperation` | ✅ | P2.7 | `team.api_interop.resolve_team_api_operation` | Accepts both `team_send_message` and `send-message` |
| `buildLegacyTeamDeprecationHint` | ✅ | P2.7 | `team.api_interop.build_legacy_team_deprecation_hint` | Emits `omx team api <operation> --input '<json>' --json` |
| `executeTeamApiOperation` | 🟡 | P2.7 | `team.api_interop.execute_team_api_operation` | All 33 ops dispatched; `send-message`, `cleanup`, `read-stall-state` return `not_implemented_yet` until team runtime ships (Phase 2.9) |
| `validateCommonFields` | ✅ | P2.7 | `team.api_interop._validate_common_fields` | Internal; mirrors TS regex gates on `team_name` / worker fields / `task_id` |
| `parseOptionalNonNegativeInteger` | ✅ | P2.7 | `team.api_interop._parse_optional_non_negative_integer` | Booleans rejected (TS parity) |
| `parseOptionalBoolean` | ✅ | P2.7 | `team.api_interop._parse_optional_boolean` | |
| `parseOptionalEventType` | ✅ | P2.7 | `team.api_interop._parse_optional_event_type` | |
| `parseOptionalMetadata` | ✅ | P2.7 | `team.api_interop._parse_optional_metadata` | |
| `parseValidatedTaskIdArray` | ✅ | P2.7 | `team.api_interop._parse_validated_task_id_array` | |
| `resolveTeamWorkingDirectory` | ✅ | P2.7 | `team.api_interop._resolve_team_working_directory` | Honors `OMX_TEAM_STATE_ROOT`; walks parents looking for team state |
| `buildIdleState` | ✅ | P2.7 | `team.api_interop._build_idle_state` | |
| `buildStallState` | ❌ | P2.9 | — | Depends on `readLatestTeamProgressEvidenceMs` + leader-attention scoring |
| `markLatestMailboxDispatchDelivered` | ❌ | P2.9 | — | Depends on dispatch-request notification flow |
| `readLegacyMailboxMessages` | ❌ | P2.7 | — | TS-only legacy fallback for the message-persistence race |

Notes:
- TS-only branches collapsed: the `send-message` interactive-vs-queued split (TS lines 615-660) requires `sendWorkerMessage`/`queueDirectMailboxMessage`; these arrive with the runtime port. The current envelope short-circuits with `code="not_implemented_yet"`.
- The `update-task` `subject` field is preserved in the input contract but is merged into `description` on persistence because Python `TeamTask` has no separate `subject` slot.
- `cleanup` (TS calls `shutdownTeam`) is gated; `orphan-cleanup` is fully functional via `team_cleanup`.
- Pre-existing arity drift in `team_ops.team_get_summary` is caught defensively in `_maybe_summary`; the envelope returns a typed stub summary rather than crashing while the upstream signature is corrected in a follow-up.

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
| `waitForWorkerStartupEvidence` | ✅ | P2.9c | `team.runtime_wait_startup.wait_for_worker_startup_evidence` | Codex path. Phase 2.9c port uses tmux-pane evidence (`capture_pane` + `_pane_looks_ready`) until `readWorkerStatus`/`listMailboxMessages` land; injection seams (`looks_ready_fn`, `capture_pane_fn`, `is_alive_fn`) let callers layer status-file evidence on later. Returns `StartupEvidenceResult(ok, reason)` where `reason ∈ {"ready","timeout","pane_missing"}`. Honors `OMX_TEAM_READY_TIMEOUT_MS` (>= 5000) with 45_000 ms default. |
| `waitForClaudeStartupEvidence` | ✅ | P2.9d | `team.runtime_wait_claude.wait_for_claude_startup_evidence` | Claude path; pane-content classifier + sync poll loop. Recognises welcome/auth_pending/trust_prompt/auth_error/network_error/model_loading/ready phases. |
| `resolveWorkerLaunchArgsFromEnv` | ❌ | P2 | — | |
| `startTeam` | ✅ | P2.9a | `team.runtime_start.start_team` | Sync port — preflight (`_assert_team_startup_is_non_destructive`, `_assert_nested_team_allowed`, stale-team detection) → optional worktree provisioning → V2 manifest + config init → per-worker bootstrap plans (role prompt + inbox + AGENTS.md overlay) → interactive `create_team_session` **or** prompt-mode child spawn → per-worker readiness wait → 3-attempt critical inbox dispatch w/ trust-prompt dismissal → recoverable-issue recording / full rollback (panes + state + worktrees + instructions file). Both **Codex** and **Claude** worker paths driven via `resolve_team_worker_cli_plan`. |
| `monitorTeam` | ✅ | P2.8c | `team.runtime_monitor.monitor_team_ts` | TS-parity TeamSnapshot; parallel worker scan via ThreadPoolExecutor (max 8). Phase 1 `team.runtime.monitor_team` (dict) kept for back-compat. |
| `assignTask` | ✅ | P2.8a | `team.runtime_assign.assign_task` | Sync port — sanitize → governance gates → claim → 2-attempt dispatch w/ trust-prompt dismissal → cancellation-inbox + claim rollback on failure. Phase 1 `team.runtime.assign_pending_tasks` kept for back-compat |
| `reassignTask` | ✅ | P2.8a | `team.runtime_assign.reassign_task` | Thin re-target wrapper over `assign_task` |
| `shutdownTeam` | ✅ | P2.9b | `team.runtime_shutdown.shutdown_team` | Sync port. Phases: send shutdown inboxes → poll for drain (15s default) → force-kill panes → tmux session destroy (non-shared) → worktree rollback → AGENTS.md / instructions cleanup → write terminal phase (`complete` / `cancelled`) → mark leader stopped → cleanup team state. Simplifications: `classifyShutdown` gate collapsed to `force` + per-worker ack; `prepareWorkerWorktreeShutdownReports` / commit-hygiene skipped (summary returns `commit_hygiene_artifacts=None`); `syncTeamModeStateOnShutdown` is a stub (`omx.modes.base.update_mode_state` not yet ported); shared-session topology folded into per-worker `kill_worker` loop. |
| `resumeTeam` | ✅ | P2.10 | `team.runtime_resume.resume_team` | Phase 2.10 — sync port. Public `resume_team` mirrors the TS 45-LOC public surface (manifest/config read, prompt-mode liveness check, tmux session probe, returns `TeamRuntime` or `None`). Richer `resume_team_with_signals` adds parallel worker scan via `ThreadPoolExecutor`, pending dispatch redispatch (stale `created_at`), leader-attention rotation detection (`RotatedSessionError`), manifest-vs-config consistency check, and a `resumed` transition on the phase state. Raises `TeamNotRunningError` when an interactive team's tmux session is missing. Simplifications: prompt-worker handle registry is approximated via persisted `pid` (no in-process child registry yet); pane-id mismatch reported as advisory metadata instead of fatal. |
| `sendWorkerMessage` | ✅ | P2 | `team.runtime_messaging.send_worker_message` | Phase 2.8b — `finalizeQueuedMailboxDispatch` partially folded into `queue_direct_mailbox_message`; leader-pane-missing soft-persist preserved |
| `broadcastWorkerMessage` | ✅ | P2 | `team.runtime_messaging.broadcast_worker_message` | Phase 2.8b — broadcast finalize collapses recipient-missing failures only |

### `team/tmux-session.ts` → `omx.team.tmux_session`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `TeamSession` | ✅ | P2.1 | `team.tmux_session.TeamSession` | Dataclass; adds `resize_hook_name` / `resize_hook_target` |
| `TeamWorkerCli` | ✅ | P2.1 | `team.tmux_session.TeamWorkerCli` | `Literal["codex","claude","gemini"]` |
| `TeamWorkerLaunchMode` | ✅ | P2.1 | `team.tmux_session.TeamWorkerLaunchMode` | `Literal["interactive","prompt"]` |
| `WorkerSubmitPlan` | ✅ | P2.1 | `team.tmux_session.WorkerSubmitPlan` | Frozen dataclass |
| `WorkerProcessLaunchSpec` | ✅ | P2.1 | `team.tmux_session.WorkerProcessLaunchSpec` | Frozen dataclass |
| `mitigateCopyModeUnderlineArtifacts` | ✅ | P2.1 | `team.tmux_session.mitigate_copy_mode_underline_artifacts` | |
| `hasCurrentTmuxClientContext` | ✅ | P2.1 | `team.tmux_session.has_current_tmux_client_context` | |
| `isMsysOrGitBash` | ✅ | P2.1 | `team.tmux_session.is_msys_or_git_bash` | |
| `translatePathForMsys` | ✅ | P2.1 | `team.tmux_session.translate_path_for_msys` | Accepts injectable `spawn_impl` |
| `listPaneIds` | ✅ | P2.1 | `team.tmux_session.list_pane_ids` | |
| `chooseTeamLeaderPaneId` | ✅ | P2.1 | `team.tmux_session.choose_team_leader_pane_id` | |
| `sleepFractionalSeconds` | ✅ | P2.1 | `team.tmux_session.sleep_fractional_seconds` | Caps at 60s |
| `buildResizeHookTarget` | ✅ | P2.1 | `team.tmux_session.build_resize_hook_target` | |
| `buildResizeHookName` | ✅ | P2.1 | `team.tmux_session.build_resize_hook_name` | |
| `buildHudPaneTarget` | ✅ | P2.1 | `team.tmux_session.build_hud_pane_target` | |
| `buildRegisterResizeHookArgs` | ✅ | P2.1 | `team.tmux_session.build_register_resize_hook_args` | |
| `buildUnregisterResizeHookArgs` | ✅ | P2.1 | `team.tmux_session.build_unregister_resize_hook_args` | |
| `unregisterResizeHook` | ✅ | P2.1 | `team.tmux_session.unregister_resize_hook` | |
| `buildClientAttachedReconcileHookName` | ✅ | P2.1 | `team.tmux_session.build_client_attached_reconcile_hook_name` | |
| `buildRegisterClientAttachedReconcileArgs` | ✅ | P2.1 | `team.tmux_session.build_register_client_attached_reconcile_args` | |
| `buildUnregisterClientAttachedReconcileArgs` | ✅ | P2.1 | `team.tmux_session.build_unregister_client_attached_reconcile_args` | |
| `buildScheduleDelayedHudResizeArgs` | ✅ | P2.1 | `team.tmux_session.build_schedule_delayed_hud_resize_args` | |
| `buildReconcileHudResizeArgs` | ✅ | P2.1 | `team.tmux_session.build_reconcile_hud_resize_args` | |
| `resolveTeamWorkerLaunchMode` | ✅ | P2.1 | `team.tmux_session.resolve_team_worker_launch_mode` | |
| `resolveTeamWorkerCli` | ✅ | P2.1 | `team.tmux_session.resolve_team_worker_cli` | |
| `resolveTeamWorkerCliPlan` | ✅ | P2.1 | `team.tmux_session.resolve_team_worker_cli_plan` | |
| `translateWorkerLaunchArgsForCli` | ✅ | P2.1 | `team.tmux_session.translate_worker_launch_args_for_cli` | Role-aware bypass via `agents.roles.get_agent` |
| `assertTeamWorkerCliBinaryAvailable` | ✅ | P2.1 | `team.tmux_session.assert_team_worker_cli_binary_available` | |
| `resolveWorkerCliForSend` | ✅ | P2.1 | `team.tmux_session.resolve_worker_cli_for_send` | |
| `buildWorkerStartupCommand` | 🟡 | P2.1 | `team.tmux_session.build_worker_startup_command` | PowerShell branch simplified — `platform-command` resolver and psmux quoting tightening deferred until `utils/platform-command` ports |
| `buildWorkerProcessLaunchSpec` | 🟡 | P2.1 | `team.tmux_session.build_worker_process_launch_spec` | `config.models.read_active_provider_env_overrides` is now ported; wiring it into the spec builder is the remaining step |
| `sanitizeTeamName` | ✅ | P2.1 | `team.tmux_session.sanitize_team_name` | |
| `isWsl2` | ✅ | P2.1 | `team.tmux_session.is_wsl2` | |
| `isNativeWindows` | ✅ | P2.1 | `team.tmux_session.is_native_windows` | |
| `isTmuxAvailable` | ✅ | P2.1 | `team.tmux_session.is_tmux_available` | |
| `createTeamSession` | 🟡 | P2.1 | `team.tmux_session.create_team_session` | HUD pane creation gated on `OMX_HUD_COMMAND` env (TS uses `resolveOmxCliEntryPath`); rollback/hooks identical |
| `restoreStandaloneHudPane` | 🟡 | P2.1 | `team.tmux_session.restore_standalone_hud_pane` | Same HUD-command env gate as `create_team_session` |
| `enableMouseScrolling` | ✅ | P2.1 | `team.tmux_session.enable_mouse_scrolling` | |
| `paneIsBootstrapping` | 🟡 | P2.1 | `team.tmux_session._pane_shows_codex_viewport` (heuristic) | TS re-exports hook-engine helpers; we provide local viewport heuristic until `scripts/tmux-hook-engine` ports |
| `paneLooksReady` | ✅ | P2.1 | `team.tmux_session._pane_looks_ready` | Pre-existing heuristic preserved (passes regression tests) |
| `paneHasActiveTask` | ✅ | P2.1 | `team.tmux_session._pane_has_active_task` | |
| `normalizeTmuxCapture` | ✅ | P2.1 | `team.tmux_session.normalize_tmux_capture` | Strips CSI + OSC escapes |
| `waitForWorkerReady` | ✅ | P2.1 | `team.tmux_session.wait_for_worker_ready` | Sync; supports both legacy `(pane_id, timeout)` and full TS `(session, index, …)` calling conventions |
| `dismissTrustPromptIfPresent` | ✅ | P2.1 | `team.tmux_session.dismiss_trust_prompt_if_present` | |
| `buildWorkerSubmitPlan` | ✅ | P2.1 | `team.tmux_session.build_worker_submit_plan` | |
| `shouldAttemptAdaptiveRetry` | ✅ | P2.1 | `team.tmux_session.should_attempt_adaptive_retry` | |
| `sendToWorker` | ✅ | P2.1 | `team.tmux_session.send_to_worker` | Sync; supports legacy `(pane, text, cli)` and full TS `(session, index, text, …)` calling conventions |
| `sendToWorkerStdin` | ✅ | P2.1 | `team.tmux_session.send_to_worker_stdin` | |
| `notifyLeaderStatus` | ✅ | P2.1 | `team.tmux_session.notify_leader_status` | |
| `getWorkerPanePid` | ✅ | P2.1 | `team.tmux_session.get_worker_pane_pid` | |
| `isWorkerAlive` | ✅ | P2.1 | `team.tmux_session.is_worker_alive` | Uses `os.kill(pid, 0)` |
| `isWorkerPaneOpen` | ✅ | P2.1 | `team.tmux_session.is_worker_pane_open` | |
| `killWorker` | ✅ | P2.1 | `team.tmux_session.kill_worker` | Sync C-c → C-d → kill-pane escalation |
| `killWorkerByPaneId` | ✅ | P2.1 | `team.tmux_session.kill_worker_by_pane_id` | |
| `destroyTeamSession` | ✅ | P2.1 | `team.tmux_session.destroy_team_session` | |
| `listTeamSessions` | ✅ | P2.1 | `team.tmux_session.list_team_sessions` | |
| `killWorkerByPaneIdAsync` | 🟡 | P2.1 | `team.tmux_session.kill_worker_by_pane_id` | Async-only TS variant collapses into the sync version (locked decision: sync only) |
| `teardownWorkerPanes` / `killWorkerPanes` / `resolveSharedSessionShutdownTopology` / `PaneTeardownSummary` / `PaneTeardownOptions` / `SharedSessionShutdownTopology` | ❌ | P2 | — | Async teardown primitives belong with `team.runtime` shutdown; defer until `shutdownTeam` ports |
| `notifyLeaderMailboxAsync` | ❌ | P2 | — | Mailbox-based notify lives with `team.state` / runtime; not in scope for this module |

Notes:
- The TS module re-exports several helpers from `scripts/tmux-hook-engine` (`paneHasActiveTask`, `paneIsBootstrapping`, `paneShowsCodexViewport`, `paneLooksReady`, `normalizeTmuxCapture`, `buildCapturePaneArgv`, `buildVisibleCapturePaneArgv`). The hook-engine module is not yet ported; equivalent local heuristics live in `team.tmux_session` and are exercised by `tests/unit/test_readiness_regression.py`.
- The Windows/native-Windows launch path produces a working `powershell.exe -EncodedCommand` invocation but does not yet route through a full `utils/platform-command` resolver. # TODO: port `utils/platform-command` and call it from `build_worker_startup_command` / `build_worker_process_launch_spec`.
- HUD pane spawning inside `create_team_session` is gated on the `OMX_HUD_COMMAND` env var instead of TS's `resolveOmxCliEntryPath()`. Once `utils/paths.resolveOmxCliEntryPath` ports, this gate should switch to that resolver.
- `_pane_has_bypass_prompt` keeps a lenient (banner + accept/confirm) heuristic to match existing regression tests; `_pane_has_strict_bypass_prompt` mirrors the TS four-marker check and is used by the auto-accept dismissal path.

### `team/worker-bootstrap.ts` → `omx.team.worker_bootstrap`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `generateWorkerRootAgentsContent` | ✅ | P2.2 | `team.worker_bootstrap.generate_worker_root_agents_content` | Takes `WorkerRootAgentsOptions` dataclass; output is snapshot-identical to TS template |
| `writeWorkerWorktreeRootAgentsFile` | ✅ | P2.2 | `team.worker_bootstrap.write_worker_worktree_root_agents_file` | Sync; uses `subprocess.run` for git probes; `write_atomic` for both backup + AGENTS.md |
| `removeWorkerWorktreeRootAgentsFile` | ✅ | P2.2 | `team.worker_bootstrap.remove_worker_worktree_root_agents_file` | Restores or removes; calls `git update-index --no-skip-worktree` when previously applied |
| `generateWorkerOverlay` | ✅ | P2.2 | `team.worker_bootstrap.generate_worker_overlay` | Bounded by `TEAM_OVERLAY_START` / `TEAM_OVERLAY_END` |
| `applyWorkerOverlay` | ✅ | P2.2 | `team.worker_bootstrap.apply_worker_overlay` | Idempotent; per-path `threading.Lock` + on-disk mkdir lock for cross-process safety |
| `stripWorkerOverlay` | ✅ | P2.2 | `team.worker_bootstrap.strip_worker_overlay` | Idempotent; missing-file is a no-op |
| `writeTeamWorkerInstructionsFile` | ✅ | P2.2 | `team.worker_bootstrap.write_team_worker_instructions_file` | Composes user/project AGENTS.md + overlay; drops user-scope skill references shadowed by project-scope skills |
| `writeWorkerRoleInstructionsFile` | ✅ | P2.2 | `team.worker_bootstrap.write_worker_role_instructions_file` | Layers a `<team_worker_role>` overlay onto the team-worker base |
| `removeTeamWorkerInstructionsFile` | ✅ | P2.2 | `team.worker_bootstrap.remove_team_worker_instructions_file` | Idempotent |
| `generateInitialInbox` | ✅ | P2.2 | `team.worker_bootstrap.generate_initial_inbox` | Accepts TS-shape dicts (`id`, `subject`, …) or the Python `TeamTask` dataclass (`task_id` mapped via duck-typed reader) |
| `generateTaskAssignmentInbox` | ✅ | P2.2 | `team.worker_bootstrap.generate_task_assignment_inbox` | |
| `generateShutdownInbox` | ✅ | P2.2 | `team.worker_bootstrap.generate_shutdown_inbox` | |
| `generateTriggerMessage` | ✅ | P2.2 | `team.worker_bootstrap.generate_trigger_message` | Delegates to `build_trigger_directive` |
| `buildTriggerDirective` | ✅ | P2.2 | `team.worker_bootstrap.build_trigger_directive` | Returns local `TeamReminderDirective` (text + intent) |
| `generateMailboxTriggerMessage` | ✅ | P2.2 | `team.worker_bootstrap.generate_mailbox_trigger_message` | |
| `buildMailboxTriggerDirective` | ✅ | P2.2 | `team.worker_bootstrap.build_mailbox_trigger_directive` | Clamps non-finite/negative `count` to `1` |
| `generateLeaderMailboxTriggerMessage` | ✅ | P2.2 | `team.worker_bootstrap.generate_leader_mailbox_trigger_message` | |
| `buildLeaderMailboxTriggerDirective` | ✅ | P2.2 | `team.worker_bootstrap.build_leader_mailbox_trigger_directive` | |

Notes:
- `TeamReminderDirective` (the `{ text, intent }` envelope) is declared locally inside `worker_bootstrap.py` as a frozen dataclass to keep this surface narrow; the existing `omx.team.reminder_intents` module models the (different) TS `TeamReminderIntent` metadata for dispatch tracking and is left untouched.
- `listInstalledSkillDirectories(project)` is inlined as `_list_project_skill_names(cwd)` because the TS helper only contributes the project-scope skill-name set to `writeTeamWorkerInstructionsFile`; no broader skills catalogue port is required.
- The TS module's per-process AGENTS.md mkdir lock is augmented with an in-process `threading.Lock` keyed by the resolved AGENTS.md path. The on-disk directory lock (`.omx/state/agents-md.lock/`) still gates cross-process callers and reuses the TS stale-owner heuristic (`os.kill(pid, 0)` probe + 30s mtime fallback).
- Composed-instructions output paths use the canonical TS `.omx/state/team/<name>/…` layout to match the TS file write contract (separate from the Python state convention `.omx/team/<name>/` used by manifest/io/leader).

### `team/mcp-comm.ts` → `omx.team.mcp_comm`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `TeamNotifierTarget` | ✅ | P2.6 | `team.mcp_comm.TeamNotifierTarget` | Frozen dataclass; snake-case (`worker_name`, `worker_index`, `pane_id`) |
| `DispatchTransport` | ✅ | P2.6 | `team.mcp_comm.DispatchTransport` | `StrEnum`, values match TS string union exactly |
| `DispatchOutcome` | ✅ | P2.6 | `team.mcp_comm.DispatchOutcome` | `to_dict()` omits unset optional fields for byte-identical JSON parity |
| `TeamNotifier` | ✅ | P2.6 | `team.mcp_comm.TeamNotifier` | `Protocol`; sync-only per Locked Decision #1 (no `Promise` return) |
| `queueInboxInstruction` | ✅ | P2.6 | `team.mcp_comm.queue_inbox_instruction` | Sync; takes `QueueInboxParams` dataclass |
| `queueDirectMailboxMessage` | ✅ | P2.6 | `team.mcp_comm.queue_direct_mailbox_message` | Honors existing-notified short-circuit + leader-pane-missing deferral |
| `queueBroadcastMailboxMessage` | ✅ | P2.6 | `team.mcp_comm.queue_broadcast_mailbox_message` | Sequential fan-out (no asyncio); returns `list[DispatchOutcome]` |
| `waitForDispatchReceipt` | ✅ | P2.6 | `team.mcp_comm.wait_for_dispatch_receipt` | Backoff loop (50→500ms, 1.5×); `_sleep`/`_now_ms` test seams |

Notes:
- Internal helpers (`_is_confirmed_notification`, `_is_leader_pane_missing_persisted`, `_fallback_transport_for_preference`, `_notify_exception_reason`, `_log_dispatch_outcome`, `_mark_immediate_dispatch_failure`, `_mark_leader_pane_missing_deferred`) mirror the TS module-private functions name-for-name.
- The TS `enqueueDispatchRequest` returns `{ request, deduped }`; the Python state-layer equivalent transparently returns the existing pending request, so `_enqueue_with_dedup` reconstructs the boolean by snapshotting the pending request-id set before the call.
- `_mark_leader_pane_missing_deferred` attempts a `pending → pending` transition which the state layer rejects (forward-only); the swallowed failure matches the TS `.catch(() => {})` semantics. The `last_reason` will instead be stamped on the next legitimate transition.
- `create_mailbox_message` and `queue_dispatch` are retained from the pre-port skeleton as legacy bridge helpers (no TS counterpart); they remain available for any in-tree caller of the runtime bridge.

### `team/delivery-log.ts` → `omx.team.delivery_log`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `TeamDeliveryEventName` | ⚪ | P2.6 | — | String-literal union; modeled as plain `str` on the `event` parameter |
| `TeamDeliveryResult` | ⚪ | P2.6 | — | String-literal union; modeled as plain `str \| None` on the `result` parameter |
| `TeamDeliveryLogEvent` | ⚪ | P2.6 | — | TS open-shape interface; modeled as explicit kwargs on `append_delivery_event` |
| `teamDeliveryLogPath` | ✅ | P2.6 | `team.delivery_log` (inline) | Resolved inline via `omx_logs_dir(cwd) / f"team-delivery-{date}.jsonl"` |
| `appendTeamDeliveryLog` | ⚪ | P2.6 | — | Python only exposes the `ForCwd` variant; logs-dir is always resolved from `cwd` |
| `appendTeamDeliveryLogForCwd` | ✅ | P2.6 | `team.delivery_log.append_delivery_event` | Top-level keys match TS (`request_id`, `message_id`, `dispatch_kind`, `intent`, `transport_preference`, `reason`); `transport` normalized to TS shorthand (`send-keys` / `prompt-stdin`); `detail` retained for arbitrary extras |

Notes:
- TS allows arbitrary extra keys via the `[key: string]: unknown` index signature; the Python port enumerates the keys that actual callers (`mcp_comm._log_dispatch_outcome`) use, with `detail` as the catch-all for anything else.
- `_normalize_transport` mirrors TS `normalizeTransport` for `tmux_send_keys` and `prompt_stdin`; all other values pass through unchanged.

### `team/worktree.ts` → `omx.team.worktree`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `WorktreeMode` | ✅ | P2.5 | `team.worktree.WorktreeMode` | Frozen dataclass; collapses TS discriminated union into `(enabled, detached, name)` |
| `ParsedWorktreeMode` | ✅ | P2.5 | `team.worktree.ParsedWorktreeMode` | |
| `WorktreePlanInput` | ✅ | P2.5 | `team.worktree.WorktreePlanInput` | Snake-case fields: `team_name`, `worker_name`, `worktree_tag` |
| `PlannedWorktreeTarget` | ✅ | P2.5 | `team.worktree.PlannedWorktreeTarget` | Always `enabled=True`; disabled case → `WorktreeDisabled` sentinel |
| `EnsureWorktreeResult` | ✅ | P2.5 | `team.worktree.EnsureWorktreeResult` | `dirty: Optional[bool]` (None when clean, True when reused dirty) |
| `EnsureWorktreeOptions` | ✅ | P2.5 | `team.worktree.EnsureWorktreeOptions` | `allow_dirty_reuse: bool` |
| `RollbackWorktreeOptions` | ✅ | P2.5 | `team.worktree.RollbackWorktreeOptions` | `skip_branch_deletion: bool` |
| `isGitRepository` | ✅ | P2.5 | `team.worktree.is_git_repository` | |
| `isWorktreeDirty` | ✅ | P2.5 | `team.worktree.is_worktree_dirty` | Raises `RuntimeError` on git failure (TS parity) |
| `readWorkspaceStatusLines` | ✅ | P2.5 | `team.worktree.read_workspace_status_lines` | |
| `assertCleanLeaderWorkspaceForWorkerWorktrees` | ✅ | P2.5 | `team.worktree.assert_clean_leader_workspace_for_worker_worktrees` | |
| `parseWorktreeMode` | ✅ | P2.5 | `team.worktree.parse_worktree_mode` | Preserves TS branch-name heuristic (no `-` prefix, no `:`) |
| `planWorktreeTarget` | ✅ | P2.5 | `team.worktree.plan_worktree_target` | Returns `PlannedWorktreeTarget` or `WorktreeDisabled` |
| `ensureWorktree` | ✅ | P2.5 | `team.worktree.ensure_worktree` | Sync per Locked Decision; `upsert_current_task_baseline` integration is best-effort (no-op until baseline module ports `upsert_current_task_baseline` / `assert_current_task_branch_available`) |
| `rollbackProvisionedWorktrees` | ✅ | P2.5 | `team.worktree.rollback_provisioned_worktrees` | Sync (was TS `async`); aggregates per-step errors into single `worktree_rollback_failed:` message |
| `removeWorktreeForce` | ✅ | P2.5 | `team.worktree.remove_worktree_force` | Sync; raises `RuntimeError` on failure |

Notes:
- All git calls go through stdlib `subprocess.run` (no `gitpython`).
- Legacy helpers `create_worktree`, `remove_worktree`, `list_worktrees`, `prune_worktrees` are retained in the same module for `team.worker_bootstrap` callers; new code should use the TS-parity API above.
- The TS `upsertCurrentTaskBaseline` / `assertCurrentTaskBranchAvailable` integration points are wired via dynamic lookup so they activate automatically once `team.current_task_baseline` grows those helpers; until then they degrade to no-ops.

### `team/model-contract.ts` → `omx.team.model_contract`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `TEAM_LOW_COMPLEXITY_DEFAULT_MODEL` | ✅ | P2 | `team.model_contract.TEAM_LOW_COMPLEXITY_DEFAULT_MODEL` | Aliases `DEFAULT_SPARK_MODEL` per TS |
| `TeamReasoningEffort` | ✅ | P2 | `team.model_contract.TeamReasoningEffort` | `Literal["low","medium","high","xhigh"]` |
| `ParsedTeamWorkerLaunchArgs` | ✅ | P2 | `team.model_contract.ParsedTeamWorkerLaunchArgs` | Frozen dataclass |
| `ResolveTeamWorkerLaunchArgsOptions` | ✅ | P2 | `team.model_contract.ResolveTeamWorkerLaunchArgsOptions` | Frozen dataclass |
| `splitWorkerLaunchArgs` | ✅ | P2 | `team.model_contract.split_worker_launch_args` | Naive whitespace split (TS parity, no shlex) |
| `parseTeamWorkerLaunchArgs` | ✅ | P2 | `team.model_contract.parse_team_worker_launch_args` | Orphan `--model` silently dropped |
| `collectInheritableTeamWorkerArgs` | ✅ | P2 | `team.model_contract.collect_inheritable_team_worker_args` | Order: bypass, reasoning, model |
| `normalizeTeamWorkerLaunchArgs` | ✅ | P2 | `team.model_contract.normalize_team_worker_launch_args` | Single `--model` after dedup |
| `resolveTeamWorkerLaunchArgs` | ✅ | P2 | `team.model_contract.resolve_team_worker_launch_args` | env → inherited → fallback precedence |
| `resolveAgentReasoningEffort` | ✅ | P2 | `team.model_contract.resolve_agent_reasoning_effort` | Reads `agents.roles.get_agent` |
| `resolveAgentDefaultModel` | ✅ | P2 | `team.model_contract.resolve_agent_default_model` | `-low` suffix + executor frontier override |
| `isLowComplexityAgentType` | ✅ | P2 | `team.model_contract.is_low_complexity_agent_type` | `explore`/`explorer`/`style-reviewer` + any `*-low` |
| `resolveTeamLowComplexityDefaultModel` | ✅ | P2 | `team.model_contract.resolve_team_low_complexity_default_model` | Delegates to spark default chain |

Notes:
- Env-resolution chain (`OMX_DEFAULT_FRONTIER_MODEL` / `OMX_DEFAULT_STANDARD_MODEL` / `OMX_DEFAULT_SPARK_MODEL` + legacy `OMX_SPARK_MODEL` alias) and the `.omx-config.json` / `config.toml` reader are currently inlined inside `team/model_contract.py`. The canonical port now lives in `config.models` (full parity, see section below); a follow-up should replace the inlined private helpers (`_get_main_default_model`, `_get_standard_default_model`, `_get_spark_default_model`, etc.) with imports from `omx.config.models`.
- TOML parsing uses stdlib `tomllib` (Python 3.11+) per the "stdlib only" Locked Decision.
- Legacy back-compat helpers (`resolve_worker_cli`, `resolve_worker_model`, `DEFAULT_WORKER_CLI`, `DEFAULT_WORKER_MODEL`) are preserved because `tests/unit/test_team.py` and historic callers import them; they are not TS exports.

### `team/role-router.ts` → `omx.team.role_router`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `loadRolePrompt` | ✅ | P2.4 | `team.role_router.load_role_prompt` | Sync stdlib `Path.read_text`; rejects unsafe role names via `SAFE_ROLE_PATTERN`; blank content → `None` |
| `isKnownRole` | ✅ | P2.4 | `team.role_router.is_known_role` | Mirrors TS `existsSync` semantics |
| `listAvailableRoles` | ✅ | P2.4 | `team.role_router.list_available_roles` | Sorted, files only (subdirs ignored); missing dir → `[]` |
| `RoleRouterResult` | ✅ | P2.4 | `team.role_router.RoleRouterResult` | Frozen dataclass; `confidence` typed as `Literal["high","medium","low"]` |
| `routeTaskToRole` | ✅ | P2.4 | `team.role_router.route_task_to_role` | Full TS-parity intent regexes + role-keyword score; phase param accepted as `str \| None` (no `TeamPhase` enum yet) |

Notes:
- Two Python-only legacy helpers (`infer_role_from_task_legacy`, `route_task_to_role_legacy`) preserve the previous dict-based dispatch API; they have no current callers but are retained for back-compat.
- `PHASE_CONTEXT_LABELS` is a plain `dict[str, str]` mirroring the TS partial record; only `team-verify`, `team-fix`, `team-plan`, and `team-prd` produce diagnostic phase hints.

---

## Phase 3 — Team Scaling

### `team/scaling.ts` → `omx.team.scaling`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `isScalingEnabled` | ✅ | P3a | `team.scaling.is_scaling_enabled` | Env gate `OMX_TEAM_SCALING_ENABLED`; also `team.scaling_down.is_scaling_enabled` (alias from P3b). |
| `assertScalingEnabled` | ✅ | P3a | `team.scaling.assert_scaling_enabled` | Raises `RuntimeError` when disabled. |
| `ScaleUpResult` | ✅ | P3a | `team.scaling.ScaleUpResult` | Dataclass with `ok=True`, `added_workers`, `new_worker_count`, `next_worker_index` |
| `ScaleDownResult` | ✅ | P3b | `team.scaling_down.ScaleDownResult` | Frozen dataclass with `ok=True`, `removed_workers`, `new_worker_count` |
| `ScaleError` | ✅ | P3a | `team.scaling.ScaleError` | Dataclass with `ok=False`, `error` (also re-exported from `team.scaling_down`) |
| `ScaleDownOptions` | ✅ | P3b | `team.scaling_down.ScaleDownOptions` | Frozen dataclass; defaults match TS (count=None, force=False, drain_timeout_ms=30000) |
| `scaleUp` | ✅ | P3a | `team.scaling.scale_up` | Sync; uses `subprocess.run(['tmux', 'split-window', ...])` (no `create_team_session` since pane is added to an existing session). Helpers: `_resolve_legacy_scaled_team_worktree_mode`, `_resolve_scale_up_worktree_mode`, `_notify_worker_pane_outcome`. |
| `scaleDown` | ✅ | P3b | `team.scaling_down.scale_down` | Sync; per-worker `kill_worker` instead of batched `teardownWorkerPanes`; emits `team_leader_nudge` event |
| `evaluate_scaling` (Python-side heuristic) | ✅ | — | `team.scaling.evaluate_scaling` | Keep; wire to scaleUp/scaleDown in future auto-scaler |
| `resolve_max_workers` (Python-side helper) | ✅ | — | `team.scaling.resolve_max_workers` | Reads `OMX_TEAM_MAX_WORKERS` |

---

## Phase 4 — Autoresearch Runtime

### `autoresearch/contracts.ts` → `omx.autoresearch.contracts`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `AutoresearchEvaluatorContract` | ✅ | — | `autoresearch.contracts.AutoresearchEvaluatorContract` | |
| `ParsedSandboxContract` | ✅ | — | `autoresearch.contracts.ParsedSandboxContract` | |
| `AutoresearchEvaluatorResult` | ✅ | — | `autoresearch.contracts.AutoresearchEvaluatorResult` | `pass` exposed as `pass_` (reserved keyword); wire shape preserved |
| `AutoresearchMissionContract` | ✅ | — | `autoresearch.contracts.AutoresearchMissionContract` | TS camelCase field names retained for parity |
| `ResearchMission` (Python) | ✅ | — | `autoresearch.contracts.ResearchMission` | Legacy lightweight shape retained |
| `ResearchCandidate` (Python) | ✅ | — | `autoresearch.contracts.ResearchCandidate` | Legacy lightweight shape retained |
| `slugifyMissionName` | ✅ | — | `autoresearch.contracts.slugify_mission_name` | |
| `parseSandboxContract` | ✅ | — | `autoresearch.contracts.parse_sandbox_contract` | |
| `parseEvaluatorResult` | ✅ | — | `autoresearch.contracts.parse_evaluator_result` | |
| `loadAutoresearchMissionContract` | ✅ | — | `autoresearch.contracts.load_autoresearch_mission_contract` | Sync (was TS `async`); uses `subprocess.run` for `git rev-parse --show-toplevel` |

### `autoresearch/runtime.ts` → `omx.autoresearch.runtime`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `PreparedAutoresearchRuntime` | ✅ | — | `autoresearch.runtime.PreparedAutoresearchRuntime` | |
| `AutoresearchEvaluationRecord` | ✅ | — | `autoresearch.runtime.AutoresearchEvaluationRecord` | `pass` exposed as `pass_`; on-disk JSON key remains `pass` |
| `AutoresearchCandidateArtifact` | ✅ | — | `autoresearch.runtime.AutoresearchCandidateArtifact` | |
| `AutoresearchLedgerEntry` | ✅ | — | `autoresearch.runtime.AutoresearchLedgerEntry` | |
| `AutoresearchRunManifest` | ✅ | — | `autoresearch.runtime.AutoresearchRunManifest` | |
| `buildAutoresearchRunTag` | ✅ | — | `autoresearch.runtime.build_autoresearch_run_tag` | |
| `assertResetSafeWorktree` | ✅ | — | `autoresearch.runtime.assert_reset_safe_worktree` | |
| `countTrailingAutoresearchNoops` | ✅ | — | `autoresearch.runtime.count_trailing_autoresearch_noops` | |
| `runAutoresearchEvaluator` | ✅ | — | `autoresearch.runtime.run_autoresearch_evaluator` | Sync `subprocess.run(shell=True)` |
| `decideAutoresearchOutcome` | ✅ | — | `autoresearch.runtime.decide_autoresearch_outcome` | Pure function; accepts manifest or `{keep_policy, last_kept_score}` dict |
| `buildAutoresearchInstructions` | ✅ | — | `autoresearch.runtime.build_autoresearch_instructions` | |
| `materializeAutoresearchMissionToWorktree` | ✅ | — | `autoresearch.runtime.materialize_autoresearch_mission_to_worktree` | |
| `loadAutoresearchRunManifest` | ✅ | — | `autoresearch.runtime.load_autoresearch_run_manifest` | |
| `prepareAutoresearchRuntime` | 🟡 | P4 | `autoresearch.runtime.prepare_autoresearch_runtime` | Mode-state writers are local shims; canonical `modes.base.start_mode` / `update_mode_state` now available — caller migration follow-up |
| `resumeAutoresearchRuntime` | 🟡 | P4 | `autoresearch.runtime.resume_autoresearch_runtime` | Same migration follow-up as prepare; canonical workflow-transition reconciliation is now in place |
| `parseAutoresearchCandidateArtifact` | ✅ | — | `autoresearch.runtime.parse_autoresearch_candidate_artifact` | |
| `processAutoresearchCandidate` | ✅ | — | `autoresearch.runtime.process_autoresearch_candidate` | |
| `finalizeAutoresearchRunState` | ✅ | — | `autoresearch.runtime.finalize_autoresearch_run_state` | |
| `stopAutoresearchRuntime` | ✅ | — | `autoresearch.runtime.stop_autoresearch_runtime` | |
| `run_research_loop` (Python stub) | ✅ | — | `autoresearch.runtime.run_research_loop` | Retained as legacy lightweight generate/evaluate loop |

### `modes/base.ts` → `omx.modes.base`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `ModeName` | ✅ | P4 | `modes.base.ModeName` | StrEnum |
| `DeprecatedModeName` | ✅ | P4 | `modes.base.DeprecatedModeName` | StrEnum |
| `getDeprecationWarning` | ✅ | P4 | `modes.base.get_deprecation_warning` | |
| `assertModeStartAllowed` | ✅ | P4 | `modes.base.assert_mode_start_allowed` | Sync; reads active workflow modes and asserts transition allowed |
| `startMode` | ✅ | P4 | `modes.base.start_mode` | Sync; reconciles workflow-transition mutex, applies ralph contract + run-outcome contract, syncs `skill-active`. Initial phase = `"starting"` for all modes (ralph contract aligned with TS — see `RALPH_PHASES` row). |
| `readModeState` | ✅ | P4 | `modes.base.read_mode_state` (dataclass) + `modes.base.read_mode_state_dict` (raw dict) | The dataclass reader pre-dated the port; the dict reader preserves the TS `[key: string]: unknown` shape used by autoresearch/team |
| `updateModeState` | ✅ | P4 | `modes.base.update_mode_state` | Sync; merges updates, strips `run_outcome` unless explicit, applies ralph + run-outcome contracts, runs `with_mode_runtime_context`, syncs `skill-active` |
| `cancelMode` | ✅ | — | `modes.base.cancel_mode` | Pre-existing |
| `cancelAllModes` | ✅ | — | `modes.base.cancel_all_modes` | Pre-existing |
| `listActiveModes` | ✅ | — | `modes.base.list_active_modes` | Pre-existing |

### `state/workflow-transition.ts` → `omx.state.workflow_transition`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `TRACKED_WORKFLOW_MODES` | ✅ | P4 | `state.workflow_transition.TRACKED_WORKFLOW_MODES` | |
| `TrackedWorkflowMode` | ✅ | P4 | (string literals; `is_tracked_workflow_mode` predicate) | |
| `WorkflowTransitionAction` | ✅ | P4 | (string; `"activate" \| "start" \| "write"`) | |
| `WorkflowTransitionKind` | ✅ | P4 | (string in `WorkflowTransitionDecision.kind`) | |
| `WorkflowTransitionDecision` | ✅ | P4 | `state.workflow_transition.WorkflowTransitionDecision` | Dataclass |
| `isTrackedWorkflowMode` | ✅ | P4 | `state.workflow_transition.is_tracked_workflow_mode` | |
| `evaluateWorkflowTransition` | ✅ | P4 | `state.workflow_transition.evaluate_workflow_transition` | |
| `buildWorkflowTransitionError` | ✅ | P4 | `state.workflow_transition.build_workflow_transition_error` | |
| `buildWorkflowTransitionMessage` | ✅ | P4 | `state.workflow_transition.build_workflow_transition_message` | |
| `assertWorkflowTransitionAllowed` | ✅ | P4 | `state.workflow_transition.assert_workflow_transition_allowed` | Raises `RuntimeError` (Python uses `RuntimeError` consistently across `state/*`) |
| `readActiveWorkflowModes` | ✅ | P4 | `state.workflow_transition.read_active_workflow_modes` | Sync; reads first existing path per `get_read_scoped_state_paths` and raises on parse error (TS parity) |
| `pickPrimaryWorkflowMode` | ✅ | P4 | `state.workflow_transition.pick_primary_workflow_mode` | |

### `state/workflow-transition-reconcile.ts` → `omx.state.workflow_transition_reconcile`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `ReconciledWorkflowTransition` | ✅ | P4 | `state.workflow_transition_reconcile.ReconciledWorkflowTransition` | Dataclass |
| `reconcileWorkflowTransition` | ✅ | P4 | `state.workflow_transition_reconcile.reconcile_workflow_transition` | Sync; auto-completes source modes and syncs canonical skill state. `applyRunOutcomeContract` integration for completed source state currently omitted (Python writes the completed mode JSON directly); revisit if hooks observe differences |

### `state/skill-active.ts` → `omx.state.skill_active`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `SKILL_ACTIVE_STATE_MODE` / `SKILL_ACTIVE_STATE_FILE` | ✅ | P4 | `state.skill_active.SKILL_ACTIVE_STATE_MODE` / `SKILL_ACTIVE_STATE_FILE` | |
| `CANONICAL_WORKFLOW_SKILLS` | ✅ | P4 | `state.skill_active.CANONICAL_WORKFLOW_SKILLS` | |
| `listActiveSkills` | ✅ | P4 | `state.skill_active.list_active_skills` | Reads `active_skills` array |
| `readSkillActiveState` | ✅ | P4 | `state.skill_active.read_skill_active_state` | |
| `readVisibleSkillActiveState` | ✅ | P4 | `state.skill_active.read_visible_skill_active_state` | |
| `syncCanonicalSkillStateForMode` | 🟡 | P4 | `state.skill_active.sync_canonical_skill_state_for_mode` | Simplified single-file sync (no session-merging or root/session fan-out). Sufficient for `start_mode` / `update_mode_state` and the reconciler; richer multi-session sync is a follow-up |
| `normalizeSkillActiveState` | ❌ | P4 | — | Not used by current Python callers; revisit if `omx_state.*` MCP tools land |
| `writeSkillActiveStateCopies` | ❌ | P4 | — | Same |
| `tracksCanonicalWorkflowSkill` | ✅ | P4 | (membership check via `CANONICAL_WORKFLOW_SKILLS`) | |

### `state/mode-state-context.ts` → `omx.state.mode_state_context`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `ModeStateContextLike` | ✅ | P4 | (`dict[str, Any]`) | |
| `captureTmuxPaneFromEnv` | ✅ | P4 | `state.mode_state_context.capture_tmux_pane_from_env` | |
| `withModeRuntimeContext` | ✅ | P4 | `state.mode_state_context.with_mode_runtime_context` | Mutates `next_state` in place, returns it; TS parity |

---

## Phase 5 — Ralph Completion

### `ralph/persistence.ts` → `omx.ralph.persistence`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `RalphVisualFeedback` | ✅ | P5 | `ralph.persistence.RalphVisualFeedback` | Dataclass + `to_dict`/`from_dict` |
| `RalphProgressLedger` | ✅ | P5 | `ralph.persistence.RalphProgressLedger` | Dataclass + `to_dict`/`from_dict`; schema v2 |
| `RalphCanonicalArtifacts` | ✅ | P5 | `ralph.persistence.RalphCanonicalArtifacts` | Dataclass + `to_dict`/`from_dict` |
| `recordRalphVisualFeedback` | ✅ | P5 | `ralph.persistence.record_ralph_visual_feedback` | Writes `<state_dir>/ralph-progress.json`; retains last 30 entries; session-scoped |
| `ensureCanonicalRalphArtifacts` | 🟡 | P5 | `ralph.persistence.ensure_canonical_ralph_artifacts` | Python variant creates `state/../ralph/{plans,evidence,checkpoints}/`; TS additionally migrates legacy PRD/progress and returns `RalphCanonicalArtifacts`. Migration path not yet ported. |

### `ralph/contract.ts` → `omx.ralph.contract`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `RALPH_PHASES` | ✅ | P5 | `ralph.contract.RALPH_PHASES` | Aligned with TS 8-phase set (`starting`, `executing`, `verifying`, `fixing`, `blocked_on_user`, `complete`, `failed`, `cancelled`). CLI `_handle_ralph` and `start_mode("ralph", ...)` emit `"starting"`. |
| `RALPH_TERMINAL_PHASE_SET` | ✅ | P5 | `ralph.contract.RALPH_TERMINAL_PHASE_SET` | `{blocked_on_user, complete, failed, cancelled}`. |
| `LEGACY_PHASE_ALIASES` | ✅ | P5 | `ralph.contract.LEGACY_PHASE_ALIASES` | Superset of TS map plus pre-port Python 4-phase aliases (`investigate`/`investigating`/`plan`/`planning` -> `starting`; `execute`/`execution`/`implementation` -> `executing`; `verify`/`verification` -> `verifying`). |
| `RalphStateValidationResult` | ✅ | — | `ralph.contract` (dict shape) | |
| `normalizeRalphPhase` | ✅ | P5 | `ralph.contract.normalize_ralph_phase` | Standalone helper returns `{phase, warning?}` or `{error}`. |
| `validateAndNormalizeRalphState` | ✅ | P5 | `ralph.contract.validate_and_normalize_ralph_state` | Full TS port: phase normalization, lifecycle defaults (`iteration`, `max_iterations`, `current_phase`, `started_at`) for active state, integer validation, terminal-phase / `completed_at` handling, ISO timestamp validation. Optional `now_iso` keyword for deterministic stamping. |

---

## Phase 6 — Config Strip/Merge

### `config/generator.ts` → `omx.config.generator`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `hasLegacyOmxTeamRunTable` | ✅ | P6 | `config.generator.has_legacy_omx_team_run_table` | |
| `getRootModelName` | ✅ | P6 | `config.generator.get_root_model_name` | |
| `stripOmxSeededBehavioralDefaults` | ✅ | P6 | `config.generator.strip_omx_seeded_behavioral_defaults` | |
| `stripOmxTopLevelKeys` | ✅ | P6 | `config.generator.strip_omx_top_level_keys` | |
| `upsertCodexHooksFeatureFlag` | ✅ | P6 | `config.generator.upsert_codex_hooks_feature_flag` | |
| `stripOmxFeatureFlags` | ✅ | P6 | `config.generator.strip_omx_feature_flags` | |
| `stripOmxEnvSettings` | ✅ | P6 | `config.generator.strip_omx_env_settings` | |
| `stripExistingOmxBlocks` | ✅ | P6 | `config.generator.strip_existing_omx_blocks` | Returns `StripResult(cleaned, removed)` |
| `stripExistingSharedMcpRegistryBlock` | ✅ | P6 | `config.generator.strip_existing_shared_mcp_registry_block` | Returns `StripResult(cleaned, removed)` |
| `buildMergedConfig` | ✅ | P6 | `config.generator.build_merged_config` | Sync; takes `MergeOptions` dataclass. `DEFAULT_SETUP_MODEL` literal `gpt-5.5` mirrored locally (a public `DEFAULT_SETUP_MODEL` alias now also lives in `config.models`; generator still uses its private copy until callers are hoisted) |
| `repairConfigIfNeeded` | ✅ | P6 | `config.generator.repair_config_if_needed` | Sync (no asyncio per locked decision) |
| `mergeConfig` | ✅ | P6 | `config.generator.merge_config` | Sync file-level merger. The previous trivial dict-deep-merge was renamed to `deep_merge_dicts` (used by `cli.setup`) |

### `config/models.ts` → `omx.config.models`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `ModelsConfig` | ✅ | P6 | `config.models.ModelsConfig` | Type alias `dict[str, str \| None]` |
| `OmxConfigEnv` | ✅ | P6 | `config.models.OmxConfigEnv` | Type alias `dict[str, str \| None]` |
| `OMX_DEFAULT_FRONTIER_MODEL_ENV` | ✅ | P6 | `config.models.OMX_DEFAULT_FRONTIER_MODEL_ENV` | |
| `OMX_DEFAULT_STANDARD_MODEL_ENV` | ✅ | P6 | `config.models.OMX_DEFAULT_STANDARD_MODEL_ENV` | |
| `OMX_DEFAULT_SPARK_MODEL_ENV` | ✅ | P6 | `config.models.OMX_DEFAULT_SPARK_MODEL_ENV` | |
| `OMX_SPARK_MODEL_ENV` | ✅ | P6 | `config.models.OMX_SPARK_MODEL_ENV` | Legacy alias preserved |
| `DEFAULT_FRONTIER_MODEL` | ✅ | P6 | `config.models.DEFAULT_FRONTIER_MODEL` | `"gpt-5.5"` |
| `DEFAULT_STANDARD_MODEL` | ✅ | P6 | `config.models.DEFAULT_STANDARD_MODEL` | `"gpt-5.4-mini"` |
| `DEFAULT_SPARK_MODEL` | ✅ | P6 | `config.models.DEFAULT_SPARK_MODEL` | `"gpt-5.3-codex-spark"` |
| `readConfiguredEnvOverrides` | ✅ | P6 | `config.models.read_configured_env_overrides` | Trim + drop empty/non-string values |
| `readActiveProviderEnvOverrides` | ✅ | P6 | `config.models.read_active_provider_env_overrides` | Reads `model_provider` + `model_providers.<name>.env_key` from `config.toml`; defaults env source to `os.environ` |
| `getEnvConfiguredMainDefaultModel` | ✅ | P6 | `config.models.get_env_configured_main_default_model` | env → `.omx-config.json` env block |
| `getEnvConfiguredStandardDefaultModel` | ✅ | P6 | `config.models.get_env_configured_standard_default_model` | env → `.omx-config.json` env block |
| `getEnvConfiguredSparkDefaultModel` | ✅ | P6 | `config.models.get_env_configured_spark_default_model` | Honours legacy `OMX_SPARK_MODEL` alias |
| `getMainDefaultModel` | ✅ | P6 | `config.models.get_main_default_model` | env → toml root `model` → canonical |
| `getStandardDefaultModel` | ✅ | P6 | `config.models.get_standard_default_model` | Standard env → frontier chain |
| `getModelForMode` | ✅ | P6 | `config.models.get_model_for_mode` | `models.<mode>` → `models.default` → main default |
| `getSparkDefaultModel` | ✅ | P6 | `config.models.get_spark_default_model` | env → models-block low-complexity override → canonical |
| `getTeamLowComplexityModel` | ✅ | P6 | `config.models.get_team_low_complexity_model` | Explicit low-complexity key beats env |

Notes:
- `read_omx_config_file` / `read_codex_config_file` are also exported (TS module-local helpers) so `team.model_contract` can be migrated to import from here in a follow-up.
- Legacy Python-only API (`DEFAULT_MODEL`, `FAST_MODEL`, `REASONING_MODEL`, `MODEL_ALIASES`, `resolve_model`) is retained from the original stub for back-compat; not a TS export.
- `team.model_contract` currently keeps inlined copies of the env/config readers (`_get_main_default_model`, `_get_standard_default_model`, `_get_spark_default_model`); these should be hoisted to import from `config.models` in a follow-up refactor.

---

## Phase 7 — Hooks Completion

### Missing TS files

| TS File | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `hooks/prompt-guidance-contract.ts` | ✅ | P7 | `hooks.prompt_guidance_contract` | Canonical prompt-guidance contract; legacy `hooks.prompt_guidance` is now a re-export shim |
| `hooks/triage-heuristic.ts` | ✅ | P7 | `hooks.triage_heuristic` | Triage routing heuristic for UserPromptSubmit; coarser legacy `hooks.triage` retired — all callers migrated to canonical 11-rule classifier |

Content-level gaps in existing files to be audited during P7. Legacy
`hooks.triage` has been removed; all callers now use
`hooks.triage_heuristic.triage_prompt` (canonical multi-rule classifier).

---

## Phase 8 — HUD Watch Loop

### `hud/index.ts` → `omx.hud`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `watchRenderLoop` | ✅ | P8 | `hud.watch.watch_render_loop` | Sync port using `time.sleep`; accepts interrupt callback in lieu of `AbortSignal` |
| `runWatchMode` | ✅ | P8 | `hud.watch.run_watch_mode` | Sync port; injectable deps via `RunWatchModeDeps`; returns process exit code |
| `hudCommand` | ✅ | P8 | `hud.watch.hud_command` | Top-level CLI entry; injectable deps via `HudCommandDeps` |
| `shellEscape` | ✅ | P8 | `hud.watch.shell_escape` | POSIX single-quote escape — matches TS for all representative inputs |
| `buildTmuxSplitArgs` | ✅ | P8 | `hud.watch.build_tmux_split_args` | Argv list for `tmux split-window` |

### `hud/render.ts` → `omx.hud.renderer`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `RenderHudOptions` | ✅ | P8 | `hud.renderer.RenderHudOptions` | Dataclass with `max_width`/`max_lines` |
| `renderHud` | ✅ | P8 | `hud.renderer.render_hud` | Full preset/element renderer; legacy `render_statusline` retained as compact helper |
| `countRenderedHudLines` | ✅ | P8 | `hud.renderer.count_rendered_hud_lines` | |

### `hud/state.ts` → `omx.hud.state`

| TS Export | Status | Phase | Python Location | Notes |
|---|---|---|---|---|
| `readRalphState` | ✅ | P8 | `hud.state.read_ralph_state` | Session-aware; accepts optional `session_id` |
| `readUltraworkState` | ✅ | P8 | `hud.state.read_ultrawork_state` | Session-aware; accepts optional `session_id` |
| `normalizeHudConfig` | ✅ | P8 | `hud.state.normalize_hud_config` | Accepts dict (TS camelCase or snake_case keys), `HudConfig`, or `None` |

---

## Phase 9 — CLI Surface

Per-subcommand audit. Each existing `_handle_*` needs depth verification against the matching `cli/*.ts`. Subcommands fully missing from Python:

| TS Subcommand | Status | Phase | Notes |
|---|---|---|---|
| `omx star-prompt` | ✅ | P9 | `cli/star_prompt.py`; `--status` / `--force` |
| `omx mcp-parity` | ✅ | P9 | `cli/mcp_parity.py`; routes to state/notepad/project-memory/trace/code-intel/wiki MCP servers |
| `omx tmux-hook` | ✅ | P9 | `cli/tmux_hook.py`; init/status/validate/test (synthetic) |
| `omx catalog-contract` | ✅ | P9 | `cli/catalog_contract.py`; expectations + headlines |
| `omx native-assets` | ✅ | P9 | `cli/native_assets.py`; status + cache-root (Python-native sparkshell — no binary downloads) |
| `omx omx` | ❌ | P9 | Self-launch alias (not yet ported) |
| `omx question` | ✅ | P9 | `cli/question.py`; inline-UI mode + `--ui --state-path` (no out-of-process renderer) |
| `omx codex-home` | ✅ | P9 | `cli/codex_home.py`; show + scope |
| `omx team status` | ✅ | P9 | `cli/team_subcommands.py:handle_team_status` |
| `omx team shutdown` | ✅ | P9 | `cli/team_subcommands.py:handle_team_shutdown` |
| `omx team resume` | ✅ | P9 | `cli/team_subcommands.py:handle_team_resume` |
| `omx team scale-up` | ✅ | P9 | `cli/team_subcommands.py:handle_team_scale_up` |
| `omx team scale-down` | ✅ | P9 | `cli/team_subcommands.py:handle_team_scale_down` |
| `omx team reassign` | ✅ | P9 | `cli/team_subcommands.py:handle_team_reassign` |
| `omx team send-message` | ✅ | P9 | `cli/team_subcommands.py:handle_team_send_message` |
| `omx team broadcast` | ✅ | P9 | `cli/team_subcommands.py:handle_team_broadcast` |
| `omx autoresearch` | ✅ | P9 | Hard-deprecation shim (TS parity: exit 1 with `$autoresearch` redirect) |
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
