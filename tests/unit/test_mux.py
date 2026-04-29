"""Tests for omx.mux — port of Rust mux tests."""

import unittest

from omx.mux.tmux import (
    TmuxAdapter,
    _resolve_target_handle,
    _session_from_handle,
    build_capture_pane_args,
    build_enter_key_args,
    build_send_keys_args,
)
from omx.mux.types import (
    ConfirmationPolicy,
    InputEnvelope,
    MuxError,
    MuxOperation,
    MuxTarget,
    SubmitPolicy,
)


class TestMux(unittest.TestCase):
    def test_resolve_target_handle_rejects_empty(self):
        with self.assertRaises(MuxError):
            _resolve_target_handle(MuxTarget.delivery_handle(""))

    def test_resolve_target_handle_rejects_detached(self):
        with self.assertRaises(MuxError):
            _resolve_target_handle(MuxTarget.detached())

    def test_resolve_target_handle_accepts_valid(self):
        handle = _resolve_target_handle(MuxTarget.delivery_handle("sess:0.1"))
        self.assertEqual(handle, "sess:0.1")

    def test_session_from_handle(self):
        self.assertEqual(_session_from_handle("mysess:0.1"), "mysess")
        self.assertEqual(_session_from_handle("plain"), "plain")
        self.assertEqual(_session_from_handle("a:b:c"), "a")

    def test_build_send_keys_args(self):
        args = build_send_keys_args("sess:0.1", "hello world")
        self.assertEqual(args, ["send-keys", "-t", "sess:0.1", "-l", "hello world"])

    def test_build_enter_key_args(self):
        args = build_enter_key_args("sess:0.1")
        self.assertEqual(args, ["send-keys", "-t", "sess:0.1", "C-m"])

    def test_build_capture_pane_args(self):
        args = build_capture_pane_args("sess:0.1", 80)
        self.assertEqual(args, ["capture-pane", "-t", "sess:0.1", "-p", "-S", "-80"])

    def test_input_envelope_normalizes_text(self):
        envelope = InputEnvelope("hello\nbridge", SubmitPolicy.enter(2, 100))
        self.assertEqual(envelope.normalized_text(), "hello bridge")
        self.assertEqual(envelope.submit.presses, 2)
        self.assertEqual(str(envelope.submit), "enter(presses=2, delay_ms=100)")

    def test_confirmation_policy_defaults(self):
        policy = ConfirmationPolicy()
        self.assertEqual(policy.narrow_capture_lines, 8)
        self.assertEqual(policy.wide_capture_lines, 80)
        self.assertEqual(policy.verify_delay_ms, 250)
        self.assertEqual(policy.verify_rounds, 3)
        self.assertTrue(policy.allow_active_task_confirmation)
        self.assertTrue(policy.require_ready_for_worker_targets)
        self.assertEqual(policy.non_empty_tail_lines, 24)
        self.assertTrue(policy.retry_submit_without_retyping)

    def test_adapter_name(self):
        adapter = TmuxAdapter()
        self.assertEqual(adapter.adapter_name(), "tmux")

    def test_adapter_status(self):
        adapter = TmuxAdapter()
        self.assertEqual(adapter.status(), "tmux adapter ready")

    def test_execute_rejects_detached_target(self):
        adapter = TmuxAdapter()
        op = MuxOperation.resolve_target(MuxTarget.detached())
        with self.assertRaises(MuxError):
            adapter.execute(op)


if __name__ == "__main__":
    unittest.main()
