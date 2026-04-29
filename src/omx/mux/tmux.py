"""TmuxAdapter — subprocess-based tmux operations.

Port of omx-mux/src/tmux.rs.
"""

from __future__ import annotations

import subprocess
import time

from omx.mux.types import (
    InputEnvelope,
    MuxError,
    MuxOperation,
    MuxOutcome,
    MuxTarget,
)


def _run_tmux(args: list[str]) -> str:
    """Run a tmux command, returning stdout on success."""
    try:
        result = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        raise MuxError.adapter_failed("tmux not found on PATH")
    except OSError as e:
        raise MuxError.adapter_failed(f"failed to run tmux: {e}")

    if result.returncode == 0:
        return result.stdout
    raise MuxError.adapter_failed(
        f"tmux {args[0] if args else ''} failed: {result.stderr.strip()}"
    )


def _resolve_target_handle(target: MuxTarget) -> str:
    """Extract a tmux target string from a MuxTarget."""
    if target.kind == "detached":
        raise MuxError.invalid_target("cannot operate on a detached target")
    if not target.handle:
        raise MuxError.invalid_target("empty delivery handle")
    return target.handle


def _session_from_handle(handle: str) -> str:
    """Extract session name from a tmux target (e.g. 'mysess:0.1' -> 'mysess')."""
    return handle.split(":")[0] or handle


def build_send_keys_args(target: str, text: str) -> list[str]:
    """Build tmux send-keys argument list for literal text input."""
    return ["send-keys", "-t", target, "-l", text]


def build_enter_key_args(target: str) -> list[str]:
    """Build tmux send-keys argument list for an Enter key press."""
    return ["send-keys", "-t", target, "C-m"]


def build_capture_pane_args(target: str, visible_lines: int) -> list[str]:
    """Build tmux capture-pane argument list for tail capture."""
    return ["capture-pane", "-t", target, "-p", "-S", f"-{visible_lines}"]


class TmuxAdapter:
    """Tmux-based implementation of the MuxAdapter protocol."""

    def adapter_name(self) -> str:
        return "tmux"

    def status(self) -> str:
        return "tmux adapter ready"

    def execute(self, operation: MuxOperation) -> MuxOutcome:
        """Execute a mux operation via tmux subprocess calls.

        Args:
            operation: The mux operation to perform.

        Returns:
            MuxOutcome describing the result.

        Raises:
            MuxError: If the operation fails or is unsupported.
        """
        match operation.op:
            case "resolve-target":
                return self._do_resolve_target(operation.target)
            case "send-input":
                assert operation.envelope is not None
                return self._do_send_input(operation.target, operation.envelope)
            case "capture-tail":
                return self._do_capture_tail(operation.target, operation.visible_lines)
            case "inspect-liveness":
                return self._do_inspect_liveness(operation.target)
            case "attach":
                return self._do_attach(operation.target)
            case "detach":
                return self._do_detach(operation.target)
            case _:
                raise MuxError.unsupported(f"unknown operation: {operation.op}")

    def _do_resolve_target(self, target: MuxTarget) -> MuxOutcome:
        handle = _resolve_target_handle(target)
        pane_list = _run_tmux(
            [
                "list-panes",
                "-a",
                "-F",
                "#{session_name}:#{window_index}.#{pane_index}",
            ]
        )
        found = any(line.strip() == handle for line in pane_list.splitlines())
        if found:
            return MuxOutcome(kind="target-resolved", resolved_handle=handle)
        raise MuxError.invalid_target(f"pane not found: {handle}")

    def _do_send_input(self, target: MuxTarget, envelope: InputEnvelope) -> MuxOutcome:
        handle = _resolve_target_handle(target)
        text = envelope.normalized_text()

        _run_tmux(build_send_keys_args(handle, text))

        if envelope.submit.kind == "enter":
            for i in range(envelope.submit.presses):
                if i > 0 and envelope.submit.delay_ms > 0:
                    time.sleep(envelope.submit.delay_ms / 1000.0)
                _run_tmux(build_enter_key_args(handle))

        return MuxOutcome(kind="input-accepted", bytes_written=len(text))

    def _do_capture_tail(self, target: MuxTarget, visible_lines: int) -> MuxOutcome:
        handle = _resolve_target_handle(target)
        body = _run_tmux(build_capture_pane_args(handle, visible_lines))
        return MuxOutcome(kind="tail-captured", visible_lines=visible_lines, body=body)

    def _do_inspect_liveness(self, target: MuxTarget) -> MuxOutcome:
        handle = _resolve_target_handle(target)
        session = _session_from_handle(handle)
        try:
            result = subprocess.run(
                ["tmux", "has-session", "-t", session],
                capture_output=True,
                check=False,
            )
            return MuxOutcome(kind="liveness-checked", alive=result.returncode == 0)
        except OSError:
            return MuxOutcome(kind="liveness-checked", alive=False)

    def _do_attach(self, target: MuxTarget) -> MuxOutcome:
        handle = _resolve_target_handle(target)
        _run_tmux(["attach-session", "-t", handle])
        return MuxOutcome(kind="attached", handle=handle)

    def _do_detach(self, target: MuxTarget) -> MuxOutcome:
        handle = _resolve_target_handle(target)
        _run_tmux(["detach-client", "-t", handle])
        return MuxOutcome(kind="detached", handle=handle)
