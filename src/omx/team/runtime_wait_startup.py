"""Wait for tmux-pane evidence that a Codex worker has finished startup.

Port of ``waitForWorkerStartupEvidence`` from ``src/team/runtime.ts``
(lines 1462-1480) — adapted for Phase 2.9c.

The TS implementation reads structured worker status / mailbox state via
``readWorkerStatus`` / ``listMailboxMessages``. Those state-store readers
are not yet ported into the Python tree, so this Phase 2.9c port uses the
tmux-pane evidence that is already available: ``capture_pane`` +
``_pane_looks_ready`` from :mod:`omx.team.tmux_session`. Once the
worker-status readers land, callers can layer them on top of this helper
via the ``looks_ready_fn`` injection seam.

Sync conversion: TS ``await new Promise(...setTimeout...)`` becomes
``time.sleep`` via the injected ``sleep_fn`` (default
``sleep_fractional_seconds``).

Stdlib only.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

from omx.team.tmux_session import (
    _pane_looks_ready,
    capture_pane,
    is_worker_alive,
    sleep_fractional_seconds,
)


# --- Constants ---------------------------------------------------------------

# Mirrors TS ``resolveWorkerReadyTimeoutMs`` default (runtime.ts:1358-1363).
DEFAULT_TIMEOUT_MS = 45_000

# Minimum env-supplied ``OMX_TEAM_READY_TIMEOUT_MS`` value accepted; matches
# TS ``parsed >= 5_000`` floor (runtime.ts:1361).
MIN_ENV_TIMEOUT_MS = 5_000

# Default poll cadence. Larger than the 100ms TS poll because this Python
# port polls tmux (which is slower than reading an in-memory status file).
DEFAULT_POLL_INTERVAL_MS = 500

# Lower bound on poll interval; matches TS ``Math.max(25, ...)``
# (runtime.ts:1471).
MIN_POLL_INTERVAL_MS = 25


# --- Result + params dataclasses --------------------------------------------


@dataclass(frozen=True)
class StartupEvidenceResult:
    """Outcome of :func:`wait_for_worker_startup_evidence`.

    Attributes:
        ok: ``True`` iff ready evidence was observed before the deadline.
        reason: One of:
            * ``"ready"`` — pane shows a CLI prompt / banner ready for input.
            * ``"timeout"`` — deadline elapsed without ready evidence.
            * ``"pane_missing"`` — pane process is no longer alive.
    """

    ok: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"ok": self.ok, "reason": self.reason}


@dataclass
class WaitForWorkerStartupEvidenceParams:
    """Parameters for :func:`wait_for_worker_startup_evidence`.

    Attributes:
        session_name: Tmux session name backing the team. Used together with
            ``worker_index`` for the liveness check.
        worker_index: Zero-based worker slot index.
        worker_pane_id: Optional explicit pane id (e.g. ``"%5"``). When
            provided, capture/liveness target the pane id directly. When
            absent, target is ``session_name:worker_index``.
        timeout_ms: Total timeout in milliseconds. ``None`` falls back to
            ``OMX_TEAM_READY_TIMEOUT_MS`` when set and >= 5000, else
            :data:`DEFAULT_TIMEOUT_MS` (45_000).
        poll_interval_ms: Poll cadence in milliseconds. ``None`` defaults to
            :data:`DEFAULT_POLL_INTERVAL_MS` (500). Floored at
            :data:`MIN_POLL_INTERVAL_MS` (25).
        capture_lines: How many lines of pane content to inspect each tick.
            Defaults to 80 (matches :func:`capture_pane` default).
        capture_pane_fn: Override for the pane-capture call. Signature
            ``(pane_target: str, lines: int) -> str``. Tests inject a fake
            sequence here.
        is_alive_fn: Override for the liveness check. Signature
            ``(session_name: str, worker_index: int,
            worker_pane_id: str | None) -> bool``.
        looks_ready_fn: Override for the ready heuristic. Signature
            ``(captured: str) -> bool``. Defaults to
            ``_pane_looks_ready``.
        sleep_fn: Override for the inter-poll sleep. Signature
            ``(seconds: float) -> None``.
        monotonic_fn: Override for the monotonic clock. Signature
            ``() -> float`` returning seconds. Tests inject a fake clock here.
        env: Environment mapping consulted for
            ``OMX_TEAM_READY_TIMEOUT_MS`` when ``timeout_ms`` is ``None``.
            Defaults to ``os.environ``.
    """

    session_name: str
    worker_index: int
    worker_pane_id: str | None = None
    timeout_ms: int | None = None
    poll_interval_ms: int | None = None
    capture_lines: int = 80
    capture_pane_fn: Callable[[str, int], str] | None = None
    is_alive_fn: Callable[[str, int, str | None], bool] | None = None
    looks_ready_fn: Callable[[str], bool] | None = None
    sleep_fn: Callable[[float], None] | None = None
    monotonic_fn: Callable[[], float] | None = None
    env: dict[str, str] | None = None


# --- Internal helpers -------------------------------------------------------


def _resolve_timeout_ms(explicit: int | None, env: dict[str, str]) -> int:
    """Pick the effective timeout in ms.

    Mirrors TS ``resolveWorkerReadyTimeoutMs`` (runtime.ts:1358-1363) when
    no explicit value is provided. Negative explicit values are clamped to
    zero, matching TS ``Math.max(0, ...)`` (runtime.ts:1470).
    """
    if explicit is not None:
        return max(0, int(explicit))
    raw = env.get("OMX_TEAM_READY_TIMEOUT_MS")
    if isinstance(raw, str):
        try:
            parsed = int(raw.strip())
        except (TypeError, ValueError):
            parsed = None
        if parsed is not None and parsed >= MIN_ENV_TIMEOUT_MS:
            return parsed
    return DEFAULT_TIMEOUT_MS


def _resolve_poll_interval_ms(explicit: int | None) -> int:
    """Pick the effective poll interval in ms.

    Mirrors TS ``Math.max(25, Math.floor(params.pollMs ?? STARTUP_POLL_MS))``
    (runtime.ts:1471).
    """
    if explicit is None:
        return DEFAULT_POLL_INTERVAL_MS
    return max(MIN_POLL_INTERVAL_MS, int(explicit))


def _pane_target(
    session_name: str, worker_index: int, worker_pane_id: str | None
) -> str:
    """Build the tmux pane target used for capture calls.

    Prefer the explicit pane id when provided (it survives layout changes);
    fall back to ``session:worker_index`` otherwise.
    """
    if worker_pane_id and worker_pane_id.startswith("%"):
        return worker_pane_id
    return f"{session_name}:{worker_index}"


# --- Public entry point -----------------------------------------------------


def wait_for_worker_startup_evidence(
    params: WaitForWorkerStartupEvidenceParams,
) -> StartupEvidenceResult:
    """Poll a worker pane until it shows startup evidence or times out.

    Port of ``waitForWorkerStartupEvidence`` (runtime.ts:1462-1480), adapted
    to tmux-pane evidence for Phase 2.9c.

    Algorithm:

    1. Capture pane contents.
    2. If ``looks_ready_fn`` returns ``True`` -> return ``ok=True,
       reason="ready"``.
    3. If the worker pane is no longer alive -> return ``ok=False,
       reason="pane_missing"``.
    4. If the deadline has elapsed -> return ``ok=False, reason="timeout"``.
    5. Otherwise sleep ``poll_interval_ms`` and repeat.

    Args:
        params: See :class:`WaitForWorkerStartupEvidenceParams`.

    Returns:
        :class:`StartupEvidenceResult` describing the outcome.
    """
    env = params.env if params.env is not None else dict(os.environ)
    timeout_ms = _resolve_timeout_ms(params.timeout_ms, env)
    poll_interval_ms = _resolve_poll_interval_ms(params.poll_interval_ms)

    capture_fn = params.capture_pane_fn or capture_pane
    alive_fn = params.is_alive_fn or is_worker_alive
    looks_ready = params.looks_ready_fn or _pane_looks_ready
    sleep_fn = params.sleep_fn or sleep_fractional_seconds
    monotonic = params.monotonic_fn or time.monotonic

    target = _pane_target(
        params.session_name, params.worker_index, params.worker_pane_id
    )
    deadline_s = monotonic() + (timeout_ms / 1000.0)

    while True:
        captured = capture_fn(target, params.capture_lines)
        if looks_ready(captured):
            return StartupEvidenceResult(ok=True, reason="ready")

        if not alive_fn(
            params.session_name, params.worker_index, params.worker_pane_id
        ):
            return StartupEvidenceResult(ok=False, reason="pane_missing")

        if monotonic() >= deadline_s:
            return StartupEvidenceResult(ok=False, reason="timeout")

        sleep_fn(poll_interval_ms / 1000.0)


__all__ = [
    "DEFAULT_POLL_INTERVAL_MS",
    "DEFAULT_TIMEOUT_MS",
    "MIN_ENV_TIMEOUT_MS",
    "MIN_POLL_INTERVAL_MS",
    "StartupEvidenceResult",
    "WaitForWorkerStartupEvidenceParams",
    "wait_for_worker_startup_evidence",
]
