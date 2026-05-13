"""Wait for Claude-CLI startup evidence in a tmux pane.

Port of ``waitForClaudeStartupEvidence`` (``src/team/runtime.ts:1482-1490``)
plus the Claude-specific pane recognition that the TS leader performs ad hoc
across the surrounding bring-up code.

The TS source funnels Claude detection through the shared
``waitForWorkerStartupEvidence`` helper (which only inspects worker status
files + leader mailbox messages). The Python port keeps that contract as a
fallback but enriches it with a pane-content scanner because Claude has a
fundamentally different startup surface than Codex:

- Distinct welcome banner ("Welcome to Claude Code").
- Authentication / login flows ("Sign in", "Press Enter to log in").
- Trust-the-folder prompt that blocks the CLI until acknowledged.
- Model-loading / "Initializing" intermediate states.
- Slash-command idle prompt (``╭─...─╮`` input box with a leading ``>``).

Recognising those phases lets the leader give detailed reasons in its
status updates instead of the generic ``startup_no_evidence`` that Codex
ports emit.

Sync conversion: TS ``await new Promise(setTimeout(...))`` becomes
``sleep_fractional_seconds`` from ``team.tmux_session``. The function does
not import the broader runtime modules — it talks to tmux through the
public helpers and accepts injected fakes for tests.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Literal

from omx.team.tmux_session import (
    capture_pane,
    is_worker_alive,
    sleep_fractional_seconds,
)


# --- Constants -------------------------------------------------------------

# Mirror of TS ``STARTUP_EVIDENCE_TIMEOUT_MS`` / ``STARTUP_EVIDENCE_POLL_MS``
# (runtime.ts:1322-1323). The Claude path uses a longer default deadline
# because Claude's first-run auth + trust-prompt handshake can outlast the
# 2-second Codex timeout. Callers can still override via ``timeout_ms``.
DEFAULT_TIMEOUT_MS = 30_000
DEFAULT_POLL_MS = 250
MIN_POLL_MS = 25

# Phase labels — small enum-ish set so callers can switch on the result
# without parsing the free-form ``reason`` string.
ClaudePhase = Literal[
    "ready",
    "auth_pending",
    "trust_prompt",
    "model_loading",
    "welcome",
    "auth_error",
    "network_error",
    "pane_missing",
    "timeout",
    "unknown",
]


# --- Pattern recognition ---------------------------------------------------


# Banner / "Claude is loaded" cues. Hitting only these without further
# evidence is **not** ready — Claude prints the banner before auth.
_WELCOME_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Welcome to Claude Code", re.IGNORECASE),
    re.compile(r"\bClaude Code\b", re.IGNORECASE),
    re.compile(r"Anthropic", re.IGNORECASE),
)

# Trust-folder prompt — Claude refuses to dispatch tools until the user
# confirms. This is a recoverable "stalled" state for an interactive
# leader but a hard stop for autonomous teams.
_TRUST_PROMPT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Do you trust the files in this folder\??", re.IGNORECASE),
    re.compile(r"Trust this (?:directory|folder)", re.IGNORECASE),
    re.compile(r"\btrust prompt\b", re.IGNORECASE),
)

# Authentication flows. Both unauth + mid-auth banners route here so the
# leader can decide whether to nudge or escalate.
_AUTH_PENDING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Press Enter to (?:log in|sign in|authenticate)", re.IGNORECASE),
    re.compile(r"\bSign in (?:with|to)\b", re.IGNORECASE),
    re.compile(r"\bLog in (?:with|to)\b", re.IGNORECASE),
    re.compile(r"Awaiting authentication", re.IGNORECASE),
    re.compile(r"auth\.anthropic\.com", re.IGNORECASE),
    re.compile(r"Opening browser", re.IGNORECASE),
)

# Auth failures (token rejected, scopes missing, etc.). Distinct from
# generic "Error:" because we want to surface a specific phase to the
# orchestrator.
_AUTH_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"authentication failed", re.IGNORECASE),
    re.compile(r"invalid (?:api )?key", re.IGNORECASE),
    re.compile(r"unauthori[sz]ed", re.IGNORECASE),
    re.compile(r"401 (?:unauthorized|error)", re.IGNORECASE),
    re.compile(r"403 forbidden", re.IGNORECASE),
    re.compile(r"please (?:re)?authenticate", re.IGNORECASE),
)

# Network / DNS / connectivity problems. Same surfacing rationale as the
# auth-error bucket.
_NETWORK_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"network error", re.IGNORECASE),
    re.compile(r"ENOTFOUND", re.IGNORECASE),
    re.compile(r"ECONNREFUSED", re.IGNORECASE),
    re.compile(r"ETIMEDOUT", re.IGNORECASE),
    re.compile(r"could not (?:connect|reach)", re.IGNORECASE),
    re.compile(r"dns lookup failed", re.IGNORECASE),
    re.compile(r"503 service unavailable", re.IGNORECASE),
)

# Model-loading / initialising / spinner cues. Claude prints these between
# auth-complete and the slash-command prompt being interactive.
_MODEL_LOADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Loading(?:\.\.\.|…)", re.IGNORECASE),
    re.compile(r"Initiali[sz]ing", re.IGNORECASE),
    re.compile(r"Loading model", re.IGNORECASE),
    re.compile(r"Connecting to", re.IGNORECASE),
    re.compile(r"Starting Claude", re.IGNORECASE),
)

# Slash-command idle / ready cues. Order matters: the Unicode box-drawing
# prompt is the strongest signal. ``Try "...":`` is the onboarding hint
# Claude prints once the input box is interactive.
_READY_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Box-drawing prompt: ``╭───…───╮`` with a ``>`` cursor row below.
    # Matches either a continuous run of box-drawing dashes (``╭───╮``)
    # or a banner with content between two dash runs.
    re.compile(r"╭[─━]{3,}╮", re.UNICODE),
    re.compile(r"╭[─━]{3,}.+[─━]{3,}╮", re.UNICODE),
    re.compile(r"^\s*>\s*$", re.MULTILINE),
    re.compile(r'Try ["“]', re.IGNORECASE),
    re.compile(r"^/\w", re.MULTILINE),
    re.compile(r"\bready for input\b", re.IGNORECASE),
    re.compile(r"\bidle\b.*\bcursor\b", re.IGNORECASE),
)


def _matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    return any(p.search(text) for p in patterns)


def classify_claude_pane(pane_text: str) -> tuple[ClaudePhase, str]:
    """Classify a Claude pane snapshot into a startup phase.

    Returns ``(phase, reason)``. ``reason`` is a short human-readable
    explanation suitable for embedding in worker status reports.

    Priority order is deliberately:
        1. Hard errors (auth/network) — they will not self-resolve.
        2. Trust prompt — blocks all downstream evidence.
        3. Auth pending — distinguish from generic "loading".
        4. Ready — the idle input box.
        5. Model loading — transient.
        6. Welcome banner — earliest cue, weakest signal.
        7. Unknown — nothing matched.
    """
    if not isinstance(pane_text, str) or pane_text == "":
        return "unknown", "empty_pane"

    if _matches_any(pane_text, _AUTH_ERROR_PATTERNS):
        return "auth_error", "claude_auth_error_detected"
    if _matches_any(pane_text, _NETWORK_ERROR_PATTERNS):
        return "network_error", "claude_network_error_detected"
    if _matches_any(pane_text, _TRUST_PROMPT_PATTERNS):
        return "trust_prompt", "claude_trust_prompt_blocking"
    if _matches_any(pane_text, _AUTH_PENDING_PATTERNS):
        return "auth_pending", "claude_auth_pending"
    if _matches_any(pane_text, _READY_PATTERNS):
        return "ready", "claude_ready_prompt_detected"
    if _matches_any(pane_text, _MODEL_LOADING_PATTERNS):
        return "model_loading", "claude_model_loading"
    if _matches_any(pane_text, _WELCOME_PATTERNS):
        return "welcome", "claude_welcome_banner"
    return "unknown", "no_claude_evidence"


# --- Public API ------------------------------------------------------------


@dataclass
class WaitForClaudeStartupParams:
    """Parameters for :func:`wait_for_claude_startup_evidence`.

    Mirrors the TS ``waitForClaudeStartupEvidence`` argument bag plus the
    pane-targeting fields the Python port needs to call ``capture_pane`` /
    ``is_worker_alive`` directly.

    Args:
        team_name: Team identifier (parity with TS).
        worker_name: Worker identifier (parity with TS).
        cwd: Working directory (parity with TS).
        session_name: Tmux session name. Required for liveness checks.
        worker_index: Tmux pane index for the worker.
        pane_id: Optional explicit pane id. Falls back to session+index.
        timeout_ms: Maximum total wait in milliseconds.
            Defaults to :data:`DEFAULT_TIMEOUT_MS`.
        poll_ms: Poll interval in milliseconds. Clamped to
            :data:`MIN_POLL_MS`.
        pane_capture_lines: Lines of scrollback to capture each tick.
        capture_pane_impl: Optional injectable capture function for tests.
        is_worker_alive_impl: Optional injectable liveness function.
        sleep_impl: Optional injectable sleep function (receives **ms**,
            matching the TS contract).
        clock: Optional injectable monotonic clock (returns seconds).
    """

    team_name: str
    worker_name: str
    cwd: str
    session_name: str
    worker_index: int
    pane_id: str | None = None
    timeout_ms: int | None = None
    poll_ms: int | None = None
    pane_capture_lines: int = 200
    capture_pane_impl: Callable[[str, int], str] | None = None
    is_worker_alive_impl: Callable[[str, int, str | None], bool] | None = None
    sleep_impl: Callable[[float], None] | None = None
    clock: Callable[[], float] | None = None
    # Internal: terminal phases short-circuit the loop. Exposed for tests
    # that want to wait through e.g. model_loading -> ready.
    terminal_phases: tuple[ClaudePhase, ...] = field(
        default=(
            "ready",
            "trust_prompt",
            "auth_error",
            "network_error",
            "pane_missing",
        )
    )


@dataclass
class WaitForClaudeStartupResult:
    """Result of :func:`wait_for_claude_startup_evidence`."""

    ok: bool
    phase: ClaudePhase
    reason: str

    def as_dict(self) -> dict[str, object]:
        """Return the JSON-shape used in status reports."""
        return {"ok": self.ok, "phase": self.phase, "reason": self.reason}


def _resolve_pane_target(params: WaitForClaudeStartupParams) -> str:
    """Best-effort pane target. Falls back to ``session:index``."""
    if params.pane_id:
        return params.pane_id
    return f"{params.session_name}.{params.worker_index}"


def _resolve_timeout_ms(timeout_ms: int | None) -> int:
    if timeout_ms is None:
        return DEFAULT_TIMEOUT_MS
    return max(0, int(timeout_ms))


def _resolve_poll_ms(poll_ms: int | None) -> int:
    if poll_ms is None:
        return DEFAULT_POLL_MS
    return max(MIN_POLL_MS, int(poll_ms))


def wait_for_claude_startup_evidence(
    params: WaitForClaudeStartupParams,
) -> WaitForClaudeStartupResult:
    """Poll a tmux pane for Claude-specific startup evidence.

    Port of ``waitForClaudeStartupEvidence`` (runtime.ts:1482-1490). The TS
    version delegates straight to ``waitForWorkerStartupEvidence``; the
    Python port keeps the same outer contract but actually classifies the
    pane content so the leader can report *why* startup stalled.

    Loop shape (sync, stdlib only):

        deadline = now + timeout_ms
        while now < deadline:
            if not is_worker_alive(pane): return pane_missing
            pane = capture_pane(pane)
            phase, reason = classify_claude_pane(pane)
            if phase in terminal_phases: return {ok: phase=='ready', ...}
            sleep(poll_ms)
        return timeout

    Args:
        params: :class:`WaitForClaudeStartupParams` bag.

    Returns:
        :class:`WaitForClaudeStartupResult` describing the final phase. The
        ``ok`` field is ``True`` only when phase is ``"ready"``.
    """
    timeout_ms = _resolve_timeout_ms(params.timeout_ms)
    poll_ms = _resolve_poll_ms(params.poll_ms)
    pane_target = _resolve_pane_target(params)

    capture_impl: Callable[[str, int], str] = (
        params.capture_pane_impl
        if params.capture_pane_impl is not None
        else capture_pane
    )
    liveness_impl: Callable[[str, int, str | None], bool] = (
        params.is_worker_alive_impl
        if params.is_worker_alive_impl is not None
        else is_worker_alive
    )
    clock: Callable[[], float] = (
        params.clock if params.clock is not None else time.monotonic
    )

    deadline = clock() + (timeout_ms / 1000.0)

    # Track the best phase we have observed so we can return a useful
    # reason on timeout (e.g. "stuck in model_loading"). ``unknown`` is the
    # weakest baseline.
    best_phase: ClaudePhase = "unknown"
    best_reason = "startup_no_evidence"
    _PROGRESSION: dict[ClaudePhase, int] = {
        "unknown": 0,
        "welcome": 1,
        "auth_pending": 2,
        "model_loading": 3,
        "trust_prompt": 4,
        "auth_error": 5,
        "network_error": 5,
        "pane_missing": 5,
        "ready": 6,
        "timeout": 0,
    }

    # We always perform at least one capture, even if timeout_ms <= 0, so
    # that a freshly-ready pane is recognised without an extra poll.
    first_iteration = True

    while True:
        # Liveness check first — capturing a dead pane returns empty
        # output and is indistinguishable from a slow-starting worker.
        try:
            alive = liveness_impl(
                params.session_name, params.worker_index, params.pane_id
            )
        except Exception:  # noqa: BLE001 - liveness is best-effort
            alive = True
        if not alive:
            return WaitForClaudeStartupResult(
                ok=False, phase="pane_missing", reason="worker_pane_dead"
            )

        try:
            pane_text = capture_impl(pane_target, params.pane_capture_lines)
        except Exception:  # noqa: BLE001 - capture is best-effort
            pane_text = ""

        phase, reason = classify_claude_pane(pane_text)

        if _PROGRESSION.get(phase, 0) > _PROGRESSION.get(best_phase, 0):
            best_phase = phase
            best_reason = reason

        if phase in params.terminal_phases:
            return WaitForClaudeStartupResult(
                ok=phase == "ready", phase=phase, reason=reason
            )

        now = clock()
        if not first_iteration and now >= deadline:
            break
        if first_iteration and timeout_ms <= 0:
            break
        first_iteration = False

        # Sleep for poll_ms, but never overshoot the deadline.
        remaining_ms = max(0.0, (deadline - clock()) * 1000.0)
        effective_sleep_ms = min(poll_ms, int(remaining_ms))
        if effective_sleep_ms <= 0:
            break
        sleep_fractional_seconds(
            effective_sleep_ms / 1000.0, sleep_impl=params.sleep_impl
        )

    # Timeout fall-through. Surface the strongest phase we saw so the
    # leader can show "stalled at model_loading" instead of plain
    # "timeout".
    if best_phase in ("unknown", "timeout"):
        return WaitForClaudeStartupResult(
            ok=False, phase="timeout", reason="startup_no_evidence"
        )
    return WaitForClaudeStartupResult(
        ok=False,
        phase="timeout",
        reason=f"timeout_after_phase:{best_phase}:{best_reason}",
    )


__all__ = [
    "ClaudePhase",
    "DEFAULT_POLL_MS",
    "DEFAULT_TIMEOUT_MS",
    "MIN_POLL_MS",
    "WaitForClaudeStartupParams",
    "WaitForClaudeStartupResult",
    "classify_claude_pane",
    "wait_for_claude_startup_evidence",
]
