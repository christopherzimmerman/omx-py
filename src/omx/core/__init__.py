"""Core runtime engine — port of Rust omx-runtime-core."""

from omx.core.types import (
    AuthoritySnapshot,
    BacklogSnapshot,
    DispatchOutcomeReason,
    DispatchTransportKind,
    QueueTransition,
    ReadinessSnapshot,
    ReplaySnapshot,
    RuntimeCommand,
    RuntimeEvent,
    RuntimeSnapshot,
    WorkerCli,
    classify_dispatch_outcome,
    submit_presses_for_worker_cli,
)
from omx.core.authority import AuthorityError, AuthorityLease
from omx.core.dispatch import DispatchError, DispatchLog, DispatchRecord, DispatchStatus
from omx.core.engine import EngineError, RuntimeEngine, derive_readiness
from omx.core.mailbox import MailboxError, MailboxLog, MailboxRecord
from omx.core.replay import ReplayState

__all__ = [
    "AuthorityError",
    "AuthorityLease",
    "AuthoritySnapshot",
    "BacklogSnapshot",
    "DispatchError",
    "DispatchLog",
    "DispatchOutcomeReason",
    "DispatchRecord",
    "DispatchStatus",
    "DispatchTransportKind",
    "EngineError",
    "MailboxError",
    "MailboxLog",
    "MailboxRecord",
    "QueueTransition",
    "ReadinessSnapshot",
    "ReplaySnapshot",
    "ReplayState",
    "RuntimeCommand",
    "RuntimeEngine",
    "RuntimeEvent",
    "RuntimeSnapshot",
    "WorkerCli",
    "classify_dispatch_outcome",
    "derive_readiness",
    "submit_presses_for_worker_cli",
]
