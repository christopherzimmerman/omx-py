"""HUD watch loop and tmux split helpers.

Port of src/hud/index.ts focused on the watch render loop, shell-escape,
and tmux split helpers. The TS code is async/await with Node-style timers;
the Python port is **sync only** and uses ``time.sleep`` for polling.

Public functions:
- ``watch_render_loop``: run a render callback at a fixed interval, with
  support for an interrupt callback (used by tests and ``run_watch_mode``).
- ``run_watch_mode``: high-level loop that reads HUD config + state, renders,
  and writes ANSI-cleared frames to stdout. Cancellable via SIGINT or an
  injected interrupt callback.
- ``hud_command``: entry point matching the TS ``hudCommand`` shape.
- ``shell_escape``: POSIX single-quote escape (matches TS behavior).
- ``build_tmux_split_args``: argument list for ``tmux split-window``.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable

from omx.hud.constants import HUD_TMUX_HEIGHT_LINES, HUD_TMUX_MAX_HEIGHT_LINES
from omx.hud.renderer import RenderHudOptions, render_hud
from omx.hud.state import read_hud_config
from omx.hud.types import HudFlags, HudRenderContext
from omx.utils.platform import is_windows

__all__ = [
    "HUD_USAGE",
    "watch_render_loop",
    "run_watch_mode",
    "hud_command",
    "shell_escape",
    "build_tmux_split_args",
]

HUD_USAGE = "\n".join(
    [
        "Usage:",
        "  omx hud              Show current HUD state",
        "  omx hud --watch      Poll every 1s with terminal clear",
        "  omx hud --json       Output raw state as JSON",
        "  omx hud --preset=X   Use preset: minimal, focused, full",
        "  omx hud --tmux       Open HUD in a tmux split pane (auto-detects orientation)",
    ]
)

_VALID_PRESETS = {"minimal", "focused", "full"}


# ---------------------------------------------------------------------------
# Sleep utilities
# ---------------------------------------------------------------------------


def _default_sleep_ms(ms: float, interrupt: Callable[[], bool] | None) -> None:
    """Sleep for *ms* milliseconds, polling the interrupt flag if provided.

    The TS code uses ``AbortSignal`` to short-circuit the sleep. The Python
    port simulates that with a callable that returns ``True`` once cancelled.
    The wait is broken into ~50ms chunks so cancellation is responsive.
    """
    remaining = max(0.0, ms)
    if remaining <= 0:
        return
    if interrupt is None:
        time.sleep(remaining / 1000.0)
        return
    end_at = time.monotonic() + remaining / 1000.0
    while True:
        if interrupt():
            return
        now = time.monotonic()
        if now >= end_at:
            return
        chunk = min(0.05, end_at - now)
        time.sleep(chunk)


# ---------------------------------------------------------------------------
# watch_render_loop
# ---------------------------------------------------------------------------


def watch_render_loop(
    render: Callable[[], None],
    *,
    interval_ms: int = 1000,
    interrupt: Callable[[], bool] | None = None,
    on_error: Callable[[BaseException], None] | None = None,
    sleep_fn: Callable[[float, Callable[[], bool] | None], None] | None = None,
    max_iterations: int | None = None,
) -> int:
    """Run a render callback at a fixed interval.

    Args:
        render: Callable invoked on each tick. Exceptions are caught and
            forwarded to ``on_error`` rather than terminating the loop.
        interval_ms: Polling interval in milliseconds (TS default is 1000).
        interrupt: Optional cancellation predicate. When it returns ``True``,
            the loop exits before the next tick and during ongoing sleeps.
        on_error: Optional error handler.
        sleep_fn: Optional sleep override (used in tests). Signature is
            ``(ms, interrupt_callback) -> None``.
        max_iterations: Optional cap on iterations (mostly for tests).

    Returns:
        Number of completed render iterations.
    """
    safe_interval = max(0, int(interval_ms))
    sleep = sleep_fn or _default_sleep_ms
    iterations = 0
    while True:
        if interrupt is not None and interrupt():
            return iterations
        started_at = time.monotonic()
        try:
            render()
        except BaseException as err:  # noqa: BLE001 — mirror TS catch-all
            if on_error is not None:
                on_error(err)
        iterations += 1
        if interrupt is not None and interrupt():
            return iterations
        if max_iterations is not None and iterations >= max_iterations:
            return iterations
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        wait_ms = max(0.0, safe_interval - elapsed_ms)
        try:
            sleep(wait_ms, interrupt)
        except BaseException:  # noqa: BLE001 — match TS .catch(() => {})
            return iterations


# ---------------------------------------------------------------------------
# run_watch_mode
# ---------------------------------------------------------------------------


@dataclass
class RunWatchModeDeps:
    """Injectable dependencies for ``run_watch_mode`` (mirrors TS deps).

    Defaults route to the real renderer/state readers and stdio writers.
    Tests can swap any of these out.
    """

    is_tty: bool | None = None
    env: dict[str, str] | None = None
    read_all_state_fn: Callable[[str], HudRenderContext] | None = None
    read_hud_config_fn: Callable[[str], object] | None = None
    render_hud_fn: Callable[[HudRenderContext, str, RenderHudOptions], str] | None = (
        None
    )
    run_authority_tick_fn: Callable[[str], None] | None = None
    write_stdout: Callable[[str], None] | None = None
    write_stderr: Callable[[str], None] | None = None
    register_sigint: Callable[[Callable[[], None]], None] | None = None
    sleep_fn: Callable[[float, Callable[[], bool] | None], None] | None = None
    max_iterations: int | None = None
    interrupt: Callable[[], bool] | None = None
    terminal_width: int | None = None


def _default_read_all_state(cwd: str) -> HudRenderContext:
    """Build a minimal render context using available state readers.

    This is a pragmatic, sync-only subset of the TS ``readAllState`` that
    pulls in the two state readers exposed in this phase (ralph, ultrawork),
    plus the HUD config-resolved git branch label if available. Higher-level
    callers can override via ``RunWatchModeDeps.read_all_state_fn``.
    """
    from omx.hud.state import read_ralph_state, read_ultrawork_state

    return HudRenderContext(
        version=None,
        git_branch=None,
        ralph=read_ralph_state(cwd),
        ultrawork=read_ultrawork_state(cwd),
    )


def _default_run_authority_tick(cwd: str) -> None:
    """Run the HUD authority tick (best-effort)."""
    try:
        from omx.hud.authority import (
            RunHudAuthorityTickOptions,
            run_hud_authority_tick,
        )

        run_hud_authority_tick(RunHudAuthorityTickOptions(cwd=cwd))
    except Exception:  # noqa: BLE001
        pass


def _default_write_stdout(text: str) -> None:
    sys.stdout.write(text)
    sys.stdout.flush()


def _default_write_stderr(text: str) -> None:
    sys.stderr.write(text)
    sys.stderr.flush()


def _default_register_sigint(handler: Callable[[], None]) -> None:
    """Install a SIGINT handler that calls *handler* on Ctrl+C."""

    def _on_signal(_signum: int, _frame: object) -> None:
        handler()

    try:
        signal.signal(signal.SIGINT, _on_signal)
    except (ValueError, OSError):
        # signal.signal only works on the main thread; ignore otherwise so
        # background callers (tests, threads) still get a working loop.
        pass


@dataclass
class _RunWatchState:
    stopped: bool = False
    first_render: bool = True
    error_message: str | None = None
    iterations: int = 0
    exit_code: int = 0


def run_watch_mode(
    cwd: str,
    flags: HudFlags,
    deps: RunWatchModeDeps | None = None,
) -> int:
    """Run the HUD watch loop (sync port of TS ``runWatchMode``).

    Args:
        cwd: Working directory.
        flags: Parsed HUD flags. Only invoked when ``flags.watch`` is True.
        deps: Optional dependency overrides.

    Returns:
        Process-style exit code (``0`` success, ``1`` on render failure or
        non-TTY refusal).
    """
    if not flags.watch:
        return 0

    d = deps or RunWatchModeDeps()
    is_tty = d.is_tty if d.is_tty is not None else bool(sys.stdout.isatty())
    env = d.env if d.env is not None else _read_env()
    read_all_state = d.read_all_state_fn or _default_read_all_state
    read_config = d.read_hud_config_fn or read_hud_config
    render_fn = d.render_hud_fn or (
        lambda ctx, preset, opts: render_hud(ctx, preset, opts)
    )
    authority_tick = d.run_authority_tick_fn or _default_run_authority_tick
    write_stdout = d.write_stdout or _default_write_stdout
    write_stderr = d.write_stderr or _default_write_stderr
    register_sigint = d.register_sigint or _default_register_sigint

    if not is_tty and not env.get("CI"):
        write_stderr("HUD watch mode requires a TTY\n")
        return 1

    state = _RunWatchState()
    write_stdout("\x1b[?25l")

    def _stop() -> None:
        if state.stopped:
            return
        state.stopped = True
        write_stdout("\x1b[?25h\x1b[2J\x1b[H")

    user_interrupt = d.interrupt

    def _interrupt() -> bool:
        if state.stopped:
            return True
        return user_interrupt() if user_interrupt is not None else False

    def _tick() -> None:
        if state.stopped:
            return
        try:
            if state.first_render:
                write_stdout("\x1b[2J\x1b[H")
                state.first_render = False
            else:
                write_stdout("\x1b[H")
            config = read_config(cwd)
            ctx = read_all_state(cwd)
            preset = flags.preset or getattr(config, "preset", "focused")
            options = RenderHudOptions(
                max_width=d.terminal_width,
                max_lines=HUD_TMUX_MAX_HEIGHT_LINES,
            )
            line = render_fn(ctx, preset, options)
            write_stdout(line + "\x1b[K\n\x1b[J")
            authority_tick(cwd)
        except Exception as err:  # noqa: BLE001
            message = str(err) if str(err) else err.__class__.__name__
            write_stderr(f"HUD watch render failed: {message}\n")
            state.exit_code = 1
            state.error_message = message
            _stop()
        finally:
            state.iterations += 1

    register_sigint(_stop)

    watch_render_loop(
        _tick,
        interval_ms=1000,
        interrupt=_interrupt,
        sleep_fn=d.sleep_fn,
        max_iterations=d.max_iterations,
    )

    return state.exit_code


def _read_env() -> dict[str, str]:
    """Read environment variables into a plain dict (copied for safety)."""
    import os

    return dict(os.environ)


# ---------------------------------------------------------------------------
# hud_command (top-level CLI entry)
# ---------------------------------------------------------------------------


@dataclass
class HudCommandDeps:
    """Injectable dependencies for ``hud_command``."""

    cwd: str | None = None
    env: dict[str, str] | None = None
    write_stdout: Callable[[str], None] | None = None
    write_stderr: Callable[[str], None] | None = None
    render_once_fn: Callable[[str, HudFlags], int] | None = None
    run_watch_mode_fn: Callable[[str, HudFlags], int] | None = None
    launch_tmux_fn: Callable[[str, HudFlags], int] | None = None


def _parse_preset(value: str | None) -> str | None:
    """Return *value* if it is a supported preset name, else None."""
    if isinstance(value, str) and value in _VALID_PRESETS:
        return value
    return None


def _parse_flags(args: list[str]) -> HudFlags:
    """Parse ``omx hud`` flag arguments."""
    flags = HudFlags(watch=False, json=False, tmux=False)
    for arg in args:
        if arg in ("--watch", "-w"):
            flags.watch = True
        elif arg == "--json":
            flags.json = True
        elif arg == "--tmux":
            flags.tmux = True
        elif arg.startswith("--preset="):
            preset = _parse_preset(arg[len("--preset=") :])
            if preset:
                flags.preset = preset
    return flags


def _default_render_once(cwd: str, flags: HudFlags) -> int:
    """Render a single HUD frame to stdout (default behavior)."""
    import json as _json

    config = read_hud_config(cwd)
    ctx = _default_read_all_state(cwd)
    preset = flags.preset or getattr(config, "preset", "focused")
    if flags.json:
        sys.stdout.write(_json.dumps(_context_to_dict(ctx), indent=2))
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 0
    sys.stdout.write(
        render_hud(ctx, preset, RenderHudOptions(max_lines=HUD_TMUX_MAX_HEIGHT_LINES))
        + "\n"
    )
    sys.stdout.flush()
    return 0


def _context_to_dict(ctx: HudRenderContext) -> dict[str, object]:
    """Convert a HudRenderContext to a JSON-friendly dict."""
    from dataclasses import asdict, is_dataclass

    if is_dataclass(ctx):
        return asdict(ctx)
    return dict(getattr(ctx, "__dict__", {}))


def _default_launch_tmux(cwd: str, flags: HudFlags) -> int:
    """Launch the HUD in a tmux split pane (best-effort, no-op on failure)."""
    import os

    if not os.environ.get("TMUX"):
        sys.stderr.write(
            "Not inside a tmux session. Start tmux first, then run: omx hud --tmux\n"
        )
        return 1

    omx_bin = sys.argv[0] if sys.argv else "omx"
    args = build_tmux_split_args(
        cwd,
        omx_bin,
        flags.preset,
        os.environ.get("OMX_SESSION_ID"),
    )
    try:
        creation_flags = 0
        if is_windows():
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(  # noqa: S603, S607
            ["tmux", *args],
            check=True,
            creationflags=creation_flags,  # type: ignore[arg-type]
        )
        sys.stdout.write(
            "HUD launched in tmux pane below. Close with: Ctrl+C in that pane, "
            "or `tmux kill-pane -t bottom`\n"
        )
        return 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        sys.stderr.write("Failed to create tmux split. Ensure tmux is available.\n")
        return 1


def hud_command(args: list[str], deps: HudCommandDeps | None = None) -> int:
    """Top-level entry point for ``omx hud`` (sync port of TS ``hudCommand``).

    Args:
        args: CLI arguments after ``omx hud``.
        deps: Optional dependency overrides.

    Returns:
        Process exit code (0 success, 1 failure).
    """
    d = deps or HudCommandDeps()
    write_stdout = d.write_stdout or _default_write_stdout
    if args and args[0] in ("--help", "-h"):
        write_stdout(HUD_USAGE + "\n")
        return 0

    flags = _parse_flags(args)
    import os

    cwd = d.cwd or os.getcwd()

    if flags.tmux:
        return (d.launch_tmux_fn or _default_launch_tmux)(cwd, flags)

    if not flags.watch:
        return (d.render_once_fn or _default_render_once)(cwd, flags)

    return (d.run_watch_mode_fn or (lambda c, f: run_watch_mode(c, f)))(cwd, flags)


# ---------------------------------------------------------------------------
# Shell escape + tmux split args
# ---------------------------------------------------------------------------


def shell_escape(value: str) -> str:
    """POSIX-safe single-quote shell escape.

    Matches the TS ``shellEscape``: wraps *value* in single quotes and escapes
    embedded single quotes via the standard ``'\\''`` sequence.

    Args:
        value: String to escape.

    Returns:
        Quoted/escaped representation safe for inclusion in a shell command.
    """
    return "'" + value.replace("'", "'\\''") + "'"


def build_tmux_split_args(
    cwd: str,
    omx_bin: str,
    preset: str | None = None,
    session_id: str | None = None,
) -> list[str]:
    """Build the argument list for ``tmux split-window``.

    Mirrors the TS implementation: returns an argv list where ``cwd`` is
    a literal argument and the inner shell command shell-escapes ``omx_bin``.

    Args:
        cwd: Working directory passed via ``-c``.
        omx_bin: Path to the OMX launcher (will be shell-escaped).
        preset: Optional preset name (validated; invalid values dropped).
        session_id: Optional ``OMX_SESSION_ID`` to forward to the child.

    Returns:
        Argv list suitable for ``subprocess.run(['tmux', *args])``.
    """
    safe_preset = _parse_preset(preset)
    preset_arg = f" --preset={safe_preset}" if safe_preset else ""
    safe_session_id = session_id.strip() if isinstance(session_id, str) else ""
    session_prefix = (
        f"OMX_SESSION_ID={shell_escape(safe_session_id)} " if safe_session_id else ""
    )
    cmd = f"{session_prefix}node {shell_escape(omx_bin)} hud --watch{preset_arg}"
    return [
        "split-window",
        "-v",
        "-l",
        str(HUD_TMUX_HEIGHT_LINES),
        "-c",
        cwd,
        cmd,
    ]
