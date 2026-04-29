"""Mux module — tmux adapter for terminal multiplexing.

Port of Rust omx-mux crate.
"""

from omx.mux.types import (
    ConfirmationPolicy,
    DeliveryAttempt,
    DeliveryConfirmation,
    InjectionPreflight,
    InputEnvelope,
    MuxError,
    MuxOperation,
    MuxOutcome,
    MuxTarget,
    PaneReadiness,
    PaneReadinessReason,
    SubmitPolicy,
)
from omx.mux.tmux import TmuxAdapter, build_capture_pane_args

__all__ = [
    "ConfirmationPolicy",
    "DeliveryAttempt",
    "DeliveryConfirmation",
    "InjectionPreflight",
    "InputEnvelope",
    "MuxError",
    "MuxOperation",
    "MuxOutcome",
    "MuxTarget",
    "PaneReadiness",
    "PaneReadinessReason",
    "SubmitPolicy",
    "TmuxAdapter",
    "build_capture_pane_args",
]
