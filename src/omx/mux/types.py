"""Mux type definitions — targets, operations, outcomes, policies.

Port of omx-mux/src/types.rs.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

MUX_OPERATION_NAMES: list[str] = [
    "resolve-target",
    "send-input",
    "capture-tail",
    "inspect-liveness",
    "attach",
    "detach",
]
MUX_TARGET_KINDS: list[str] = ["delivery-handle", "detached"]


# --- Target ---


@dataclass(frozen=True)
class MuxTarget:
    """Identifies the target pane for a mux operation.

    Attributes:
        kind: Target type ("delivery-handle" or "detached").
        handle: Tmux pane target string (e.g. "session:0.1").
    """

    kind: str  # "delivery-handle" or "detached"
    handle: str = ""

    @classmethod
    def delivery_handle(cls, handle: str) -> MuxTarget:
        return cls(kind="delivery-handle", handle=handle)

    @classmethod
    def detached(cls) -> MuxTarget:
        return cls(kind="detached")

    def __str__(self) -> str:
        if self.kind == "delivery-handle":
            return f"delivery-handle({self.handle})"
        return "detached"


# --- Submit Policy ---


@dataclass(frozen=True)
class SubmitPolicy:
    """Controls how input is submitted after typing (Enter presses).

    Attributes:
        kind: Policy type ("none" or "enter").
        presses: Number of Enter key presses.
        delay_ms: Delay in milliseconds between presses.
    """

    kind: str  # "none" or "enter"
    presses: int = 0
    delay_ms: int = 0

    @classmethod
    def none(cls) -> SubmitPolicy:
        return cls(kind="none")

    @classmethod
    def enter(cls, presses: int = 1, delay_ms: int = 100) -> SubmitPolicy:
        return cls(kind="enter", presses=max(1, presses), delay_ms=delay_ms)

    def __str__(self) -> str:
        if self.kind == "none":
            return "none"
        return f"enter(presses={self.presses}, delay_ms={self.delay_ms})"


# --- Input Envelope ---


@dataclass
class InputEnvelope:
    """Wraps literal text with submission and normalization policy.

    Attributes:
        literal_text: Raw text to type into the pane.
        submit: Policy for pressing Enter after typing.
        replace_newlines_with_spaces: Whether to flatten newlines before sending.
    """

    literal_text: str
    submit: SubmitPolicy
    replace_newlines_with_spaces: bool = True

    def normalized_text(self) -> str:
        if self.replace_newlines_with_spaces:
            return "".join(
                " " if ch in ("\r", "\n") else ch for ch in self.literal_text
            )
        return self.literal_text


# --- Injection Preflight ---


@dataclass
class InjectionPreflight:
    """Configuration for pre-injection safety checks.

    Attributes:
        skip_if_scrolling: Abort if pane is in scroll/copy mode.
        require_running_agent: Only inject if an agent process is active.
        require_ready: Only inject if the pane is ready for input.
        require_idle: Only inject if no task is actively running.
        capture_lines: Number of pane lines to capture for readiness checks.
    """

    skip_if_scrolling: bool = True
    require_running_agent: bool = True
    require_ready: bool = True
    require_idle: bool = True
    capture_lines: int = 80


# --- Pane Readiness ---


class PaneReadinessReason(StrEnum):
    """Reason codes describing why a pane is or is not ready for injection."""

    OK = "ok"
    MISSING_TARGET = "missing_target"
    SCROLL_ACTIVE = "scroll_active"
    PANE_RUNNING_SHELL = "pane_running_shell"
    PANE_HAS_ACTIVE_TASK = "pane_has_active_task"
    PANE_NOT_READY = "pane_not_ready"
    TARGET_RESOLUTION_FAILED = "target_resolution_failed"


@dataclass
class PaneReadiness:
    """Result of checking whether a pane is ready for input injection.

    Attributes:
        reason: Why the pane is or is not ready.
        pane_target: Resolved tmux pane target string.
        pane_current_command: Currently running command in the pane.
        pane_capture: Captured pane content used for checks.
    """

    reason: PaneReadinessReason
    pane_target: str | None = None
    pane_current_command: str | None = None
    pane_capture: str | None = None

    @classmethod
    def ok(cls, pane_target: str) -> PaneReadiness:
        return cls(reason=PaneReadinessReason.OK, pane_target=pane_target)


# --- Delivery ---


class DeliveryConfirmation(StrEnum):
    """Outcome of verifying whether sent input was received by the target."""

    CONFIRMED = "Confirmed"
    CONFIRMED_ACTIVE_TASK = "ConfirmedActiveTask"
    UNCONFIRMED = "Unconfirmed"


@dataclass
class ConfirmationPolicy:
    """Tuning parameters for post-delivery confirmation checks."""

    narrow_capture_lines: int = 8
    wide_capture_lines: int = 80
    verify_delay_ms: int = 250
    verify_rounds: int = 3
    allow_active_task_confirmation: bool = True
    require_ready_for_worker_targets: bool = True
    non_empty_tail_lines: int = 24
    retry_submit_without_retyping: bool = True


@dataclass
class DeliveryAttempt:
    """Record of a single delivery attempt to a pane.

    Attributes:
        pane_target: Resolved tmux pane target.
        input: The input envelope that was sent.
        typed_prompt: Whether the prompt text was typed (vs. pasted).
        confirmation: Result of confirming delivery.
    """

    pane_target: str
    input: InputEnvelope
    typed_prompt: bool
    confirmation: DeliveryConfirmation


# --- Operations & Outcomes ---


@dataclass(frozen=True)
class MuxOperation:
    """A mux operation to execute against a target pane.

    Attributes:
        op: Operation name (resolve-target, send-input, capture-tail, etc.).
        target: The target pane for the operation.
        envelope: Input envelope (for send-input operations).
        visible_lines: Number of lines to capture (for capture-tail).
    """

    op: str  # resolve-target, send-input, capture-tail, inspect-liveness, attach, detach
    target: MuxTarget
    envelope: InputEnvelope | None = None
    visible_lines: int = 0

    @classmethod
    def resolve_target(cls, target: MuxTarget) -> MuxOperation:
        return cls(op="resolve-target", target=target)

    @classmethod
    def send_input(cls, target: MuxTarget, envelope: InputEnvelope) -> MuxOperation:
        return cls(op="send-input", target=target, envelope=envelope)

    @classmethod
    def capture_tail(cls, target: MuxTarget, visible_lines: int) -> MuxOperation:
        return cls(op="capture-tail", target=target, visible_lines=visible_lines)

    @classmethod
    def inspect_liveness(cls, target: MuxTarget) -> MuxOperation:
        return cls(op="inspect-liveness", target=target)

    @classmethod
    def attach(cls, target: MuxTarget) -> MuxOperation:
        return cls(op="attach", target=target)

    @classmethod
    def detach(cls, target: MuxTarget) -> MuxOperation:
        return cls(op="detach", target=target)


@dataclass
class MuxOutcome:
    """Result of executing a MuxOperation."""

    kind: str
    resolved_handle: str | None = None
    bytes_written: int = 0
    visible_lines: int = 0
    body: str = ""
    alive: bool = False
    handle: str = ""


class MuxError(Exception):
    """Mux operation error."""

    def __init__(self, kind: str, message: str) -> None:
        self.kind = kind
        super().__init__(f"{kind}: {message}")

    @classmethod
    def unsupported(cls, message: str) -> MuxError:
        return cls("unsupported", message)

    @classmethod
    def invalid_target(cls, message: str) -> MuxError:
        return cls("invalid_target", message)

    @classmethod
    def adapter_failed(cls, message: str) -> MuxError:
        return cls("adapter_failed", message)


# --- Adapter Protocol ---


class MuxAdapter(Protocol):
    """Protocol defining the interface for terminal multiplexer adapters."""

    def adapter_name(self) -> str: ...
    def execute(self, operation: MuxOperation) -> MuxOutcome: ...
