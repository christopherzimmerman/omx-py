"""Tests for omx.team.runtime_wait_claude — Claude startup evidence waiter."""

from __future__ import annotations

import unittest
from typing import Iterator

from omx.team.runtime_wait_claude import (
    DEFAULT_POLL_MS,
    DEFAULT_TIMEOUT_MS,
    MIN_POLL_MS,
    WaitForClaudeStartupParams,
    WaitForClaudeStartupResult,
    classify_claude_pane,
    wait_for_claude_startup_evidence,
)


# --- Mock helpers ----------------------------------------------------------


class _FakeClock:
    """Deterministic monotonic clock for the wait loop.

    Advances by ``step_s`` seconds on every ``read`` call. Sleep callbacks
    can manually push the clock forward to model real-time elapsing.
    """

    def __init__(self, *, start: float = 0.0, step_s: float = 0.0) -> None:
        self._now = start
        self._step_s = step_s

    def read(self) -> float:
        value = self._now
        self._now += self._step_s
        return value

    def advance(self, seconds: float) -> None:
        self._now += seconds


def _make_capture(panes: list[str]) -> tuple[list[str], "object"]:
    """Build a capture_pane fake that returns scripted snapshots.

    Returns ``(call_log, fn)``. After the scripted snapshots are exhausted
    the fake keeps returning the last value (mirrors a steady-state pane).
    """
    log: list[str] = []
    iterator: Iterator[str] = iter(panes)
    last = panes[-1] if panes else ""

    def _fake(pane_target: str, lines: int) -> str:
        log.append(pane_target)
        nonlocal last
        try:
            value = next(iterator)
            last = value
            return value
        except StopIteration:
            return last

    return log, _fake


def _always_alive(_session: str, _idx: int, _pane: str | None) -> bool:
    return True


def _never_alive(_session: str, _idx: int, _pane: str | None) -> bool:
    return False


def _make_params(
    *,
    panes: list[str],
    alive: bool = True,
    timeout_ms: int | None = 5_000,
    poll_ms: int | None = 50,
    clock: _FakeClock | None = None,
    sleep_log: list[float] | None = None,
) -> WaitForClaudeStartupParams:
    _capture_log, capture_fn = _make_capture(panes)

    def _sleep(ms: float) -> None:
        # sleep_fractional_seconds passes its callback **milliseconds**
        # (see tmux_session.sleep_fractional_seconds contract). Convert
        # to seconds for the log + clock advance so the test math stays
        # in one unit system.
        seconds = ms / 1000.0
        if sleep_log is not None:
            sleep_log.append(seconds)
        # Advance the fake clock to model real time passing.
        if clock is not None:
            clock.advance(seconds)

    return WaitForClaudeStartupParams(
        team_name="team",
        worker_name="w1",
        cwd="/tmp/work",
        session_name="omx-team-test",
        worker_index=0,
        pane_id="%5",
        timeout_ms=timeout_ms,
        poll_ms=poll_ms,
        capture_pane_impl=capture_fn,
        is_worker_alive_impl=_always_alive if alive else _never_alive,
        sleep_impl=_sleep,
        clock=clock.read if clock is not None else None,
    )


# --- classify_claude_pane --------------------------------------------------


class TestClassifyClaudePane(unittest.TestCase):
    def test_ready_box_prompt(self) -> None:
        text = "Welcome\n╭────────────────╮\n│ >              │\n╰────────────────╯"
        phase, reason = classify_claude_pane(text)
        self.assertEqual(phase, "ready")
        self.assertIn("ready", reason)

    def test_ready_try_hint(self) -> None:
        phase, _ = classify_claude_pane('Try "explain this codebase":')
        self.assertEqual(phase, "ready")

    def test_ready_bare_caret_line(self) -> None:
        phase, _ = classify_claude_pane("some banner\n>\nready for input")
        self.assertEqual(phase, "ready")

    def test_welcome_only(self) -> None:
        phase, reason = classify_claude_pane("Welcome to Claude Code")
        self.assertEqual(phase, "welcome")
        self.assertIn("welcome", reason)

    def test_auth_pending_press_enter(self) -> None:
        phase, _ = classify_claude_pane("Press Enter to log in with Anthropic")
        # The line also contains "Anthropic" which is a welcome cue, but
        # auth_pending takes precedence.
        self.assertEqual(phase, "auth_pending")

    def test_auth_pending_browser(self) -> None:
        phase, _ = classify_claude_pane("Opening browser to auth.anthropic.com")
        self.assertEqual(phase, "auth_pending")

    def test_auth_error_invalid_key(self) -> None:
        phase, reason = classify_claude_pane("Error: invalid API key")
        self.assertEqual(phase, "auth_error")
        self.assertIn("auth_error", reason)

    def test_auth_error_401(self) -> None:
        phase, _ = classify_claude_pane("HTTP 401 Unauthorized")
        self.assertEqual(phase, "auth_error")

    def test_network_error_enotfound(self) -> None:
        phase, reason = classify_claude_pane("getaddrinfo ENOTFOUND api.anthropic.com")
        self.assertEqual(phase, "network_error")
        self.assertIn("network_error", reason)

    def test_network_error_timeout(self) -> None:
        phase, _ = classify_claude_pane("connect ETIMEDOUT 1.2.3.4:443")
        self.assertEqual(phase, "network_error")

    def test_trust_prompt(self) -> None:
        phase, reason = classify_claude_pane(
            "Do you trust the files in this folder?\n[y/N]"
        )
        self.assertEqual(phase, "trust_prompt")
        self.assertIn("trust", reason)

    def test_model_loading(self) -> None:
        phase, _ = classify_claude_pane("Loading...")
        self.assertEqual(phase, "model_loading")

    def test_model_loading_initializing(self) -> None:
        phase, _ = classify_claude_pane("Initializing Claude session")
        self.assertEqual(phase, "model_loading")

    def test_empty_pane(self) -> None:
        phase, reason = classify_claude_pane("")
        self.assertEqual(phase, "unknown")
        self.assertEqual(reason, "empty_pane")

    def test_non_string_input(self) -> None:
        phase, reason = classify_claude_pane(None)  # type: ignore[arg-type]
        self.assertEqual(phase, "unknown")
        self.assertEqual(reason, "empty_pane")

    def test_priority_auth_error_over_ready(self) -> None:
        # If the pane still shows the box-prompt but also an auth error,
        # the error wins so the leader does not falsely declare ready.
        text = "Error: authentication failed\n╭──────╮\n│ >    │\n╰──────╯"
        phase, _ = classify_claude_pane(text)
        self.assertEqual(phase, "auth_error")

    def test_priority_trust_over_ready(self) -> None:
        text = "Do you trust the files in this folder?\n╭──────╮\n│ >    │\n╰──────╯"
        phase, _ = classify_claude_pane(text)
        self.assertEqual(phase, "trust_prompt")

    def test_unknown_when_nothing_matches(self) -> None:
        phase, reason = classify_claude_pane("random noise")
        self.assertEqual(phase, "unknown")
        self.assertEqual(reason, "no_claude_evidence")


# --- wait_for_claude_startup_evidence --------------------------------------


class TestWaitForClaudeStartupEvidence(unittest.TestCase):
    def test_returns_ready_on_first_capture(self) -> None:
        ready = '╭───────────╮\n│ Try "x":   │\n╰───────────╯'
        params = _make_params(panes=[ready])
        result = wait_for_claude_startup_evidence(params)
        self.assertIsInstance(result, WaitForClaudeStartupResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.phase, "ready")
        self.assertEqual(
            result.as_dict(),
            {"ok": True, "phase": "ready", "reason": result.reason},
        )

    def test_returns_trust_prompt_terminal(self) -> None:
        params = _make_params(panes=["Do you trust the files in this folder?"])
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "trust_prompt")

    def test_returns_auth_error_terminal(self) -> None:
        params = _make_params(panes=["authentication failed"])
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "auth_error")

    def test_returns_network_error_terminal(self) -> None:
        params = _make_params(panes=["ENOTFOUND api.anthropic.com"])
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "network_error")

    def test_pane_missing_when_worker_dead(self) -> None:
        params = _make_params(panes=[""], alive=False)
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "pane_missing")
        self.assertEqual(result.reason, "worker_pane_dead")

    def test_progression_welcome_to_model_loading_to_ready(self) -> None:
        # Worker walks through three transient states before settling.
        clock = _FakeClock(start=0.0)
        sleep_log: list[float] = []
        params = _make_params(
            panes=[
                "Welcome to Claude Code",
                "Loading...",
                '╭─────╮\n│ Try "x": │\n╰─────╯',
            ],
            timeout_ms=10_000,
            poll_ms=100,
            clock=clock,
            sleep_log=sleep_log,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertTrue(result.ok)
        self.assertEqual(result.phase, "ready")
        # Two sleeps between the three captures.
        self.assertEqual(len(sleep_log), 2)

    def test_auth_pending_then_ready(self) -> None:
        clock = _FakeClock(start=0.0)
        params = _make_params(
            panes=[
                "Press Enter to log in",
                "Welcome\n╭─────╮\n│ >   │\n╰─────╯",
            ],
            timeout_ms=10_000,
            poll_ms=100,
            clock=clock,
        )
        result = wait_for_claude_startup_evidence(params)
        # auth_pending is NOT terminal — we should keep polling until ready.
        self.assertTrue(result.ok)
        self.assertEqual(result.phase, "ready")

    def test_timeout_stalled_in_model_loading(self) -> None:
        clock = _FakeClock(start=0.0)
        sleep_log: list[float] = []
        params = _make_params(
            # Pane reports Loading indefinitely; capture fake returns the
            # last entry forever after exhaustion.
            panes=["Loading..."],
            timeout_ms=300,
            poll_ms=100,
            clock=clock,
            sleep_log=sleep_log,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "timeout")
        # Reason should preserve the strongest observed phase.
        self.assertIn("model_loading", result.reason)
        # At least one sleep should have been issued.
        self.assertGreaterEqual(len(sleep_log), 1)

    def test_timeout_with_no_evidence(self) -> None:
        clock = _FakeClock(start=0.0)
        params = _make_params(
            panes=["random noise"],
            timeout_ms=200,
            poll_ms=100,
            clock=clock,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "timeout")
        self.assertEqual(result.reason, "startup_no_evidence")

    def test_zero_timeout_does_one_capture(self) -> None:
        # timeout_ms=0 should still attempt a single classification — a
        # freshly-ready pane must be recognised without polling.
        ready = '╭───────────╮\n│ Try "x":   │\n╰───────────╯'
        clock = _FakeClock(start=0.0)
        sleep_log: list[float] = []
        params = _make_params(
            panes=[ready],
            timeout_ms=0,
            poll_ms=100,
            clock=clock,
            sleep_log=sleep_log,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertTrue(result.ok)
        # No sleep because we matched on the first capture.
        self.assertEqual(sleep_log, [])

    def test_zero_timeout_no_evidence_returns_timeout(self) -> None:
        clock = _FakeClock(start=0.0)
        sleep_log: list[float] = []
        params = _make_params(
            panes=["nothing here"],
            timeout_ms=0,
            poll_ms=100,
            clock=clock,
            sleep_log=sleep_log,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "timeout")
        # No sleeps issued — we exited immediately after the single capture.
        self.assertEqual(sleep_log, [])

    def test_pane_capture_exception_treated_as_empty(self) -> None:
        # Capture raising should not crash the loop; the snapshot is
        # treated as empty and the loop times out cleanly.
        clock = _FakeClock(start=0.0)

        def _boom(_target: str, _lines: int) -> str:
            raise RuntimeError("tmux capture failed")

        params = WaitForClaudeStartupParams(
            team_name="t",
            worker_name="w",
            cwd="/tmp",
            session_name="s",
            worker_index=0,
            timeout_ms=100,
            poll_ms=50,
            capture_pane_impl=_boom,
            is_worker_alive_impl=_always_alive,
            sleep_impl=lambda ms: clock.advance(ms / 1000.0),
            clock=clock.read,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "timeout")

    def test_liveness_exception_treated_as_alive(self) -> None:
        # Worker liveness should be best-effort. A throwing liveness fn
        # must not flip us to pane_missing — we keep polling.
        clock = _FakeClock(start=0.0)
        ready = '╭─────╮\n│ Try "x": │\n╰─────╯'

        def _alive_boom(_s: str, _i: int, _p: str | None) -> bool:
            raise RuntimeError("tmux liveness failed")

        params = WaitForClaudeStartupParams(
            team_name="t",
            worker_name="w",
            cwd="/tmp",
            session_name="s",
            worker_index=0,
            timeout_ms=1_000,
            poll_ms=50,
            capture_pane_impl=lambda _t, _l: ready,
            is_worker_alive_impl=_alive_boom,
            sleep_impl=lambda ms: clock.advance(ms / 1000.0),
            clock=clock.read,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertTrue(result.ok)
        self.assertEqual(result.phase, "ready")

    def test_partial_output_recovery(self) -> None:
        # Pane emits a truncated/garbled snapshot first, then the proper
        # ready banner. The waiter should ride through the partial output.
        clock = _FakeClock(start=0.0)
        params = _make_params(
            panes=[
                "elcome to Cla",  # truncated — no known pattern matches
                "Welcome to Claude Code",  # welcome but not terminal
                "╭─────╮\n│ >   │\n╰─────╯",
            ],
            timeout_ms=5_000,
            poll_ms=50,
            clock=clock,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertTrue(result.ok)
        self.assertEqual(result.phase, "ready")

    def test_poll_clamped_to_min(self) -> None:
        # Sub-min poll_ms should be raised to MIN_POLL_MS but otherwise
        # behave normally.
        ready = '╭─────╮\n│ Try "x": │\n╰─────╯'
        params = _make_params(panes=[ready], poll_ms=1)
        result = wait_for_claude_startup_evidence(params)
        self.assertTrue(result.ok)

    def test_negative_timeout_treated_as_zero(self) -> None:
        params = _make_params(panes=["nothing"], timeout_ms=-1, poll_ms=50)
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "timeout")

    def test_pane_target_falls_back_to_session_index(self) -> None:
        # When pane_id is None, target should be session.index.
        seen: list[str] = []

        def _capture(target: str, _lines: int) -> str:
            seen.append(target)
            return "Loading..."

        clock = _FakeClock(start=0.0)
        params = WaitForClaudeStartupParams(
            team_name="t",
            worker_name="w",
            cwd="/tmp",
            session_name="sess-x",
            worker_index=2,
            pane_id=None,
            timeout_ms=50,
            poll_ms=50,
            capture_pane_impl=_capture,
            is_worker_alive_impl=_always_alive,
            sleep_impl=lambda ms: clock.advance(ms / 1000.0),
            clock=clock.read,
        )
        wait_for_claude_startup_evidence(params)
        self.assertTrue(seen)
        self.assertEqual(seen[0], "sess-x.2")

    def test_pane_id_used_when_provided(self) -> None:
        seen: list[str] = []

        def _capture(target: str, _lines: int) -> str:
            seen.append(target)
            return "Loading..."

        clock = _FakeClock(start=0.0)
        params = WaitForClaudeStartupParams(
            team_name="t",
            worker_name="w",
            cwd="/tmp",
            session_name="sess-x",
            worker_index=2,
            pane_id="%42",
            timeout_ms=50,
            poll_ms=50,
            capture_pane_impl=_capture,
            is_worker_alive_impl=_always_alive,
            sleep_impl=lambda ms: clock.advance(ms / 1000.0),
            clock=clock.read,
        )
        wait_for_claude_startup_evidence(params)
        self.assertEqual(seen[0], "%42")

    def test_custom_terminal_phases(self) -> None:
        # Override terminal phases so welcome counts as terminal.
        params = _make_params(panes=["Welcome to Claude Code"])
        # Mutate params to include welcome in terminal_phases.
        params.terminal_phases = ("welcome", "ready")
        result = wait_for_claude_startup_evidence(params)
        # Welcome is not "ready", so ok=False but phase=welcome.
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "welcome")

    def test_sleep_capped_to_remaining_deadline(self) -> None:
        # Verify the loop never sleeps past the deadline: a 300ms timeout
        # with 1000ms poll should still terminate within ~300ms simulated.
        clock = _FakeClock(start=0.0)
        sleep_log: list[float] = []
        params = _make_params(
            panes=["random"],
            timeout_ms=300,
            poll_ms=1_000,
            clock=clock,
            sleep_log=sleep_log,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertEqual(result.phase, "timeout")
        # No single sleep should exceed remaining time.
        for s in sleep_log:
            self.assertLessEqual(s, 1.0)

    def test_default_constants_exposed(self) -> None:
        # Smoke test for the public constants — locks down the TS parity
        # contract against accidental tweaks.
        self.assertGreaterEqual(DEFAULT_TIMEOUT_MS, 1_000)
        self.assertGreaterEqual(DEFAULT_POLL_MS, MIN_POLL_MS)
        self.assertEqual(MIN_POLL_MS, 25)

    def test_result_dataclass_shape(self) -> None:
        result = WaitForClaudeStartupResult(
            ok=True, phase="ready", reason="claude_ready_prompt_detected"
        )
        self.assertEqual(
            result.as_dict(),
            {
                "ok": True,
                "phase": "ready",
                "reason": "claude_ready_prompt_detected",
            },
        )

    def test_progression_tracks_highest_phase(self) -> None:
        # If the pane briefly shows model_loading and then drops back to
        # welcome (unlikely but possible during reconnects), the timeout
        # reason should still cite model_loading as the strongest signal.
        clock = _FakeClock(start=0.0)
        params = _make_params(
            panes=[
                "Welcome to Claude Code",
                "Loading...",
                "Welcome to Claude Code",  # regress
            ],
            timeout_ms=400,
            poll_ms=100,
            clock=clock,
        )
        result = wait_for_claude_startup_evidence(params)
        self.assertFalse(result.ok)
        self.assertEqual(result.phase, "timeout")
        self.assertIn("model_loading", result.reason)


if __name__ == "__main__":
    unittest.main()
