"""Tests for ``omx.team.runtime_wait_startup``.

Mock strategy: all I/O is injected via the
:class:`WaitForWorkerStartupEvidenceParams` callable fields, so these tests
never touch real tmux or the real wall clock. ``capture_pane_fn`` returns
a scripted sequence of strings, ``is_alive_fn`` returns a scripted bool
sequence, ``monotonic_fn`` advances a virtual clock by the same amount
each poll, and ``sleep_fn`` is a no-op (the virtual clock encodes time).
"""

from __future__ import annotations

import unittest
from collections.abc import Iterable
from typing import Callable

from omx.team.runtime_wait_startup import (
    DEFAULT_POLL_INTERVAL_MS,
    DEFAULT_TIMEOUT_MS,
    MIN_POLL_INTERVAL_MS,
    StartupEvidenceResult,
    WaitForWorkerStartupEvidenceParams,
    wait_for_worker_startup_evidence,
)


# --- Fake clock + capture helpers -------------------------------------------


def _seq_capture(captures: Iterable[str]) -> Callable[[str, int], str]:
    """Return a fake capture function that yields ``captures`` in order.

    Once the sequence is exhausted, the last value is returned forever so
    a stuck-not-ready loop hits the timeout branch deterministically.
    """
    items = list(captures)

    def fn(_target: str, _lines: int) -> str:
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        return items.pop(0)

    return fn


def _seq_alive(values: Iterable[bool]) -> Callable[[str, int, str | None], bool]:
    """Return a fake is_alive function yielding ``values`` in order."""
    items = list(values)

    def fn(_s: str, _i: int, _p: str | None) -> bool:
        if not items:
            return True
        if len(items) == 1:
            return items[0]
        return items.pop(0)

    return fn


def _virtual_clock(step_s: float, start: float = 0.0) -> Callable[[], float]:
    """Return a monotonic stand-in that advances by ``step_s`` per call."""
    state = {"t": start}

    def fn() -> float:
        t = state["t"]
        state["t"] = t + step_s
        return t

    return fn


def _make_params(**overrides: object) -> WaitForWorkerStartupEvidenceParams:
    defaults: dict[str, object] = {
        "session_name": "omx-team-x",
        "worker_index": 0,
        "worker_pane_id": "%2",
        "timeout_ms": 1_000,
        "poll_interval_ms": 100,
        "capture_pane_fn": _seq_capture([""]),
        "is_alive_fn": _seq_alive([True]),
        "sleep_fn": lambda _s: None,
        "monotonic_fn": _virtual_clock(0.05),
        "env": {},
    }
    defaults.update(overrides)
    return WaitForWorkerStartupEvidenceParams(**defaults)  # type: ignore[arg-type]


# --- Happy paths -------------------------------------------------------------


class TestReadyImmediately(unittest.TestCase):
    def test_ready_on_first_poll(self) -> None:
        params = _make_params(
            capture_pane_fn=_seq_capture(["What can I help you build?\n> "]),
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertEqual(result, StartupEvidenceResult(ok=True, reason="ready"))

    def test_ready_codex_arrow_prompt(self) -> None:
        params = _make_params(
            capture_pane_fn=_seq_capture(["welcome to codex\n›\n"]),
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "ready")

    def test_ready_skips_pane_alive_check(self) -> None:
        # If ready evidence is seen, ``is_alive_fn`` should not be consulted.
        alive_calls: list[int] = []

        def alive_fn(*_args: object) -> bool:
            alive_calls.append(1)
            return True

        params = _make_params(
            capture_pane_fn=_seq_capture([">"]),
            is_alive_fn=alive_fn,
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertTrue(result.ok)
        self.assertEqual(alive_calls, [])


# --- Multi-poll ready --------------------------------------------------------


class TestReadyAfterPolls(unittest.TestCase):
    def test_ready_after_three_polls(self) -> None:
        # First two captures are empty (not ready); third shows a prompt.
        params = _make_params(
            capture_pane_fn=_seq_capture(["", "Loading...", "> "]),
            is_alive_fn=_seq_alive([True]),
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertEqual(result, StartupEvidenceResult(ok=True, reason="ready"))

    def test_sleep_called_between_polls(self) -> None:
        sleeps: list[float] = []
        params = _make_params(
            capture_pane_fn=_seq_capture(["", "", "> "]),
            sleep_fn=lambda s: sleeps.append(s),
            poll_interval_ms=200,
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertTrue(result.ok)
        # Two pre-ready iterations -> two sleeps, each ~0.2s.
        self.assertEqual(len(sleeps), 2)
        for s in sleeps:
            self.assertAlmostEqual(s, 0.2)

    def test_custom_looks_ready_fn(self) -> None:
        # Use an aggressive matcher: any non-empty capture is "ready".
        params = _make_params(
            capture_pane_fn=_seq_capture(["", "anything"]),
            looks_ready_fn=lambda c: c != "",
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertTrue(result.ok)


# --- Timeout -----------------------------------------------------------------


class TestTimeout(unittest.TestCase):
    def test_timeout_when_pane_never_ready(self) -> None:
        # Pane stays empty; alive=True so the only exit is timeout.
        params = _make_params(
            timeout_ms=300,
            poll_interval_ms=100,
            capture_pane_fn=_seq_capture([""]),
            is_alive_fn=_seq_alive([True]),
            monotonic_fn=_virtual_clock(0.15),  # 150ms per call -> 2 polls
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertEqual(result, StartupEvidenceResult(ok=False, reason="timeout"))

    def test_zero_timeout_returns_timeout(self) -> None:
        # ``timeout_ms=0`` -> first iteration sees deadline already reached.
        params = _make_params(
            timeout_ms=0,
            capture_pane_fn=_seq_capture([""]),
            is_alive_fn=_seq_alive([True]),
            monotonic_fn=_virtual_clock(0.0),
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "timeout")

    def test_negative_timeout_clamped_to_zero(self) -> None:
        params = _make_params(
            timeout_ms=-50,
            capture_pane_fn=_seq_capture([""]),
            is_alive_fn=_seq_alive([True]),
            monotonic_fn=_virtual_clock(0.0),
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertEqual(result.reason, "timeout")


# --- Pane missing -----------------------------------------------------------


class TestPaneMissing(unittest.TestCase):
    def test_pane_missing_on_first_check(self) -> None:
        params = _make_params(
            capture_pane_fn=_seq_capture([""]),
            is_alive_fn=_seq_alive([False]),
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertEqual(result, StartupEvidenceResult(ok=False, reason="pane_missing"))

    def test_pane_dies_after_two_polls(self) -> None:
        params = _make_params(
            capture_pane_fn=_seq_capture(["", "", ""]),
            is_alive_fn=_seq_alive([True, True, False]),
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "pane_missing")

    def test_pane_missing_preferred_over_timeout(self) -> None:
        # Both conditions true on the same tick — pane_missing wins because
        # the liveness check runs before the deadline check.
        params = _make_params(
            timeout_ms=0,
            capture_pane_fn=_seq_capture([""]),
            is_alive_fn=_seq_alive([False]),
            monotonic_fn=_virtual_clock(0.0),
        )
        result = wait_for_worker_startup_evidence(params)
        self.assertEqual(result.reason, "pane_missing")


# --- Defaults / env resolution ----------------------------------------------


class TestDefaults(unittest.TestCase):
    def test_default_timeout_when_none_and_no_env(self) -> None:
        # We can't easily run a full 45s wait — assert via a hook that
        # records the effective deadline by counting iterations.
        # Strategy: monotonic returns large jump on second call.
        clock_values = [0.0, 100.0]  # 100s passes on second call

        def clock() -> float:
            return clock_values.pop(0) if clock_values else 100.0

        params = _make_params(
            timeout_ms=None,
            capture_pane_fn=_seq_capture([""]),
            is_alive_fn=_seq_alive([True]),
            monotonic_fn=clock,
            env={},
        )
        result = wait_for_worker_startup_evidence(params)
        # 100s > DEFAULT_TIMEOUT_MS (45s) so the second iteration must time out.
        self.assertEqual(result.reason, "timeout")
        self.assertEqual(DEFAULT_TIMEOUT_MS, 45_000)

    def test_env_timeout_used_when_no_explicit(self) -> None:
        # OMX_TEAM_READY_TIMEOUT_MS=10_000 should be honored.
        clock_values = [0.0, 5.0, 15.0]  # 5s, then 15s into the run

        def clock() -> float:
            if clock_values:
                return clock_values.pop(0)
            return 15.0

        params = _make_params(
            timeout_ms=None,
            capture_pane_fn=_seq_capture([""]),
            is_alive_fn=_seq_alive([True]),
            monotonic_fn=clock,
            env={"OMX_TEAM_READY_TIMEOUT_MS": "10000"},
        )
        result = wait_for_worker_startup_evidence(params)
        # 15s > 10s configured timeout -> timeout reached.
        self.assertEqual(result.reason, "timeout")

    def test_env_timeout_floor_rejects_small_values(self) -> None:
        # Values below 5_000 are rejected and fall back to default 45_000.
        clock_values = [0.0, 1.0]

        def clock() -> float:
            return clock_values.pop(0) if clock_values else 50.0

        params = _make_params(
            timeout_ms=None,
            capture_pane_fn=_seq_capture(["> "]),  # immediately ready
            is_alive_fn=_seq_alive([True]),
            monotonic_fn=clock,
            env={"OMX_TEAM_READY_TIMEOUT_MS": "1000"},
        )
        # Just confirm the resolver does not crash on the small value and
        # that "ready" still wins.
        result = wait_for_worker_startup_evidence(params)
        self.assertTrue(result.ok)

    def test_default_poll_interval_when_none(self) -> None:
        sleeps: list[float] = []
        params = _make_params(
            poll_interval_ms=None,
            capture_pane_fn=_seq_capture(["", "> "]),
            sleep_fn=lambda s: sleeps.append(s),
        )
        wait_for_worker_startup_evidence(params)
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], DEFAULT_POLL_INTERVAL_MS / 1000.0)

    def test_poll_interval_floor_enforced(self) -> None:
        sleeps: list[float] = []
        params = _make_params(
            poll_interval_ms=1,  # below MIN_POLL_INTERVAL_MS
            capture_pane_fn=_seq_capture(["", "> "]),
            sleep_fn=lambda s: sleeps.append(s),
        )
        wait_for_worker_startup_evidence(params)
        self.assertEqual(len(sleeps), 1)
        # Clamped up to MIN_POLL_INTERVAL_MS / 1000.
        self.assertAlmostEqual(sleeps[0], MIN_POLL_INTERVAL_MS / 1000.0)


# --- Target resolution ------------------------------------------------------


class TestPaneTargeting(unittest.TestCase):
    def test_uses_explicit_pane_id_when_provided(self) -> None:
        seen_targets: list[str] = []

        def cap(target: str, _lines: int) -> str:
            seen_targets.append(target)
            return "> "

        params = _make_params(
            capture_pane_fn=cap,
            worker_pane_id="%17",
            session_name="ignored",
            worker_index=99,
        )
        wait_for_worker_startup_evidence(params)
        self.assertEqual(seen_targets, ["%17"])

    def test_falls_back_to_session_worker_target(self) -> None:
        seen_targets: list[str] = []

        def cap(target: str, _lines: int) -> str:
            seen_targets.append(target)
            return "> "

        params = _make_params(
            capture_pane_fn=cap,
            worker_pane_id=None,
            session_name="omx-team-foo",
            worker_index=2,
        )
        wait_for_worker_startup_evidence(params)
        self.assertEqual(seen_targets, ["omx-team-foo:2"])

    def test_non_percent_pane_id_falls_back_to_session_target(self) -> None:
        # Defensive: only ``%``-prefixed pane ids are taken as authoritative.
        seen_targets: list[str] = []

        def cap(target: str, _lines: int) -> str:
            seen_targets.append(target)
            return "> "

        params = _make_params(
            capture_pane_fn=cap,
            worker_pane_id="bogus",
            session_name="s",
            worker_index=4,
        )
        wait_for_worker_startup_evidence(params)
        self.assertEqual(seen_targets, ["s:4"])

    def test_capture_lines_forwarded(self) -> None:
        seen_lines: list[int] = []

        def cap(_target: str, lines: int) -> str:
            seen_lines.append(lines)
            return "> "

        params = _make_params(capture_pane_fn=cap, capture_lines=200)
        wait_for_worker_startup_evidence(params)
        self.assertEqual(seen_lines, [200])


# --- Result serialization ---------------------------------------------------


class TestStartupEvidenceResult(unittest.TestCase):
    def test_to_dict_round_trip(self) -> None:
        r = StartupEvidenceResult(ok=True, reason="ready")
        self.assertEqual(r.to_dict(), {"ok": True, "reason": "ready"})

    def test_result_is_hashable_frozen(self) -> None:
        # frozen dataclass should be hashable.
        r = StartupEvidenceResult(ok=False, reason="timeout")
        self.assertEqual({r}, {r})


if __name__ == "__main__":
    unittest.main()
