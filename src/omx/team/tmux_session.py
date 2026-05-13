"""Tmux session management for team workers.

Port of ``src/team/tmux-session.ts`` (2000 LOC). Handles session creation,
worker process launch, send-keys injection, trust prompt dismissal, worker
readiness polling, pane management, resize hooks, and platform detection.

Locked decisions:

* Sync only. ``time.sleep`` for waits (TS ``setTimeout``).
* Stdlib only. ``subprocess.run`` for tmux calls (no asyncio).
* Cross-platform. tmux requires WSL on Windows; functions handle "tmux not
  available" gracefully via :func:`is_tmux_available`.

The TS ``Promise``-returning helpers (``runTmuxAsync``, ``sendKeyAsync``,
``capturePaneAsync``, ``killWorker``, ``killWorkerPanes``, …) collapse to
synchronous variants here. The TS module additionally exports several
async-only helpers (``teardownWorkerPanes``, ``killWorkerByPaneIdAsync``,
``notifyLeaderMailboxAsync``) that are intentionally **not** ported in this
file — those are owned by ``team.runtime`` and its mailbox layer.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from omx.team.model_contract import (
    CODEX_BYPASS_FLAG,
    CONFIG_FLAG,
    MADMAX_FLAG,
    MODEL_FLAG,
)

# --- TS constants mirrored ---------------------------------------------------

CLAUDE_SKIP_PERMISSIONS_FLAG = "--dangerously-skip-permissions"
GEMINI_PROMPT_INTERACTIVE_FLAG = "-i"
GEMINI_APPROVAL_MODE_FLAG = "--approval-mode"
GEMINI_APPROVAL_MODE_YOLO = "yolo"

HUD_TMUX_TEAM_HEIGHT_LINES = 3
HUD_RESIZE_RECONCILE_DELAY_SECONDS = 2

INJECTION_MARKER = "[OMX_TMUX_INJECT]"
MODEL_INSTRUCTIONS_FILE_KEY = "model_instructions_file"
OMX_BYPASS_DEFAULT_SYSTEM_PROMPT_ENV = "OMX_BYPASS_DEFAULT_SYSTEM_PROMPT"
OMX_MODEL_INSTRUCTIONS_FILE_ENV = "OMX_MODEL_INSTRUCTIONS_FILE"
OMX_TEAM_WORKER_CLI_ENV = "OMX_TEAM_WORKER_CLI"
OMX_TEAM_WORKER_CLI_MAP_ENV = "OMX_TEAM_WORKER_CLI_MAP"
OMX_TEAM_WORKER_LAUNCH_MODE_ENV = "OMX_TEAM_WORKER_LAUNCH_MODE"
OMX_TEAM_AUTO_INTERRUPT_RETRY_ENV = "OMX_TEAM_AUTO_INTERRUPT_RETRY"
OMX_LEADER_NODE_PATH_ENV = "OMX_LEADER_NODE_PATH"
OMX_LEADER_CLI_PATH_ENV = "OMX_LEADER_CLI_PATH"

TMUX_WORKER_AMBIENT_ENV_ALLOWLIST = (
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
    "https_proxy",
    "http_proxy",
    "no_proxy",
)
TMUX_NO_UNDERLINE_STYLE_FLAGS = (
    "nounderscore",
    "nodouble-underscore",
    "nocurly-underscore",
    "nodotted-underscore",
    "nodashed-underscore",
)
TMUX_COPY_MODE_STYLE_OPTIONS = ("mode-style", "copy-mode-selection-style")
TMUX_PANE_STABILITY_POLL_MS = 60
TMUX_PANE_STABILITY_POLLS_REQUIRED = 2
TMUX_PANE_STABILITY_TIMEOUT_MS = 750

# Signed-32-bit max — used to bound tmux hook index slot.
TMUX_HOOK_INDEX_MAX = 2147483647

# Maximum sleep (ms) per :func:`sleep_fractional_seconds` invocation.
MAX_FRACTIONAL_SLEEP_MS = 60_000


# --- Type aliases ------------------------------------------------------------

TeamWorkerCli = Literal["codex", "claude", "gemini"]
TeamWorkerCliMode = Literal["auto", "codex", "claude", "gemini"]
TeamWorkerLaunchMode = Literal["interactive", "prompt"]


# --- Dataclasses (TS interfaces) --------------------------------------------


@dataclass
class TeamSession:
    """Describes an active tmux team session.

    Mirrors TS ``TeamSession`` (tmux-session.ts:36-49).

    Attributes:
        name: Tmux target in ``"session:window"`` form.
        worker_count: Number of worker panes created.
        cwd: Working directory for the session.
        worker_pane_ids: List of worker pane target strings (``%N`` pane IDs).
        leader_pane_id: Leader's own pane ID — must never be targeted by
            worker cleanup routines.
        hud_pane_id: HUD pane spawned below the leader column, or ``None`` if
            creation failed.
        resize_hook_name: Registered tmux resize hook name for the HUD pane,
            or ``None`` if unavailable.
        resize_hook_target: Registered tmux resize hook target in
            ``"<session>:<window>"`` form, or ``None``.
    """

    name: str
    worker_count: int
    cwd: str
    worker_pane_ids: list[str] = field(default_factory=list)
    leader_pane_id: str = ""
    hud_pane_id: str | None = None
    resize_hook_name: str | None = None
    resize_hook_target: str | None = None


@dataclass(frozen=True)
class WorkerSubmitPlan:
    """Submit plan for :func:`send_to_worker`.

    Mirrors TS ``WorkerSubmitPlan`` (tmux-session.ts:91-97).
    """

    should_interrupt: bool
    queue_first_round: bool
    rounds: int
    submit_key_presses_per_round: int
    allow_adaptive_retry: bool


@dataclass(frozen=True)
class WorkerProcessLaunchSpec:
    """Process-level launch description for a worker.

    Mirrors TS ``WorkerProcessLaunchSpec`` (tmux-session.ts:104-109).
    """

    worker_cli: TeamWorkerCli
    command: str
    args: list[str]
    env: dict[str, str]


@dataclass(frozen=True)
class TmuxPaneInfo:
    """One entry returned by ``tmux list-panes`` with ``pane_id`` /
    ``pane_current_command`` / ``pane_start_command``.

    Mirrors TS ``TmuxPaneInfo`` (tmux-session.ts:111-115).
    """

    pane_id: str
    current_command: str
    start_command: str


# --- Low-level tmux helpers --------------------------------------------------


@dataclass(frozen=True)
class _TmuxResult:
    """Result of :func:`_run_tmux`. ``ok`` indicates success."""

    ok: bool
    stdout: str = ""
    stderr: str = ""


def _run_tmux(args: list[str]) -> _TmuxResult:
    """Run a tmux command and return a normalized result.

    Mirrors TS ``runTmux`` (tmux-session.ts:119-128).

    Returns ``ok=False`` when the binary is missing, exits non-zero, or any
    OS error fires. Never raises.
    """
    try:
        result = subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except (FileNotFoundError, OSError) as err:
        return _TmuxResult(ok=False, stderr=str(err))
    if result.returncode != 0:
        stderr = (result.stderr or "").strip() or f"tmux exited {result.returncode}"
        return _TmuxResult(ok=False, stderr=stderr)
    return _TmuxResult(ok=True, stdout=(result.stdout or "").strip())


def _base_session_name(target: str) -> str:
    return target.split(":")[0] or target


def _list_panes(target: str) -> list[TmuxPaneInfo]:
    """List panes for a tmux target with pane_id/current/start commands.

    Mirrors TS ``listPanes`` (tmux-session.ts:221-233).
    """
    result = _run_tmux(
        [
            "list-panes",
            "-t",
            target,
            "-F",
            "#{pane_id}\t#{pane_current_command}\t#{pane_start_command}",
        ]
    )
    if not result.ok:
        return []
    panes: list[TmuxPaneInfo] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split("\t")
        pane_id = parts[0] if len(parts) > 0 else ""
        current_command = parts[1] if len(parts) > 1 else ""
        start_command = parts[2] if len(parts) > 2 else ""
        if pane_id.startswith("%"):
            panes.append(
                TmuxPaneInfo(
                    pane_id=pane_id,
                    current_command=current_command,
                    start_command=start_command,
                )
            )
    return panes


def list_pane_ids(target: str) -> list[str]:
    """Return pane IDs for a tmux target.

    Mirrors TS ``listPaneIds`` (tmux-session.ts:235-237).
    """
    return [p.pane_id for p in _list_panes(target)]


def _pane_exists_in_target(target: str, pane_id: str) -> bool:
    if not pane_id.startswith("%"):
        return False
    return pane_id in list_pane_ids(target)


def _wait_for_pane_to_remain_present(
    target: str,
    pane_id: str,
    timeout_ms: int = TMUX_PANE_STABILITY_TIMEOUT_MS,
) -> bool:
    """Mirror TS ``waitForPaneToRemainPresent`` (tmux-session.ts:244-269)."""
    if not pane_id.startswith("%"):
        return False
    stable_required = max(1, TMUX_PANE_STABILITY_POLLS_REQUIRED)
    started_at = time.monotonic() * 1000.0
    stable_polls = 0

    while (time.monotonic() * 1000.0) - started_at <= timeout_ms:
        if _pane_exists_in_target(target, pane_id):
            stable_polls += 1
            if stable_polls >= stable_required:
                return True
        else:
            stable_polls = 0
        remaining = timeout_ms - ((time.monotonic() * 1000.0) - started_at)
        if remaining <= 0:
            break
        poll_ms = max(0.0, min(TMUX_PANE_STABILITY_POLL_MS, remaining))
        sleep_fractional_seconds(poll_ms / 1000.0)
    return False


_HUD_WATCH_RE = re.compile(r"\bomx\b.*\bhud\b.*--watch", re.IGNORECASE)


def _is_hud_watch_pane(pane: TmuxPaneInfo) -> bool:
    return bool(_HUD_WATCH_RE.search(pane.start_command or ""))


def choose_team_leader_pane_id(
    panes: list[TmuxPaneInfo],
    preferred_pane_id: str,
) -> str:
    """Pick the team leader's pane from a list, skipping HUD watch panes.

    Mirrors TS ``chooseTeamLeaderPaneId`` (tmux-session.ts:276-284).
    """
    for pane in panes:
        if pane.pane_id == preferred_pane_id and not _is_hud_watch_pane(pane):
            return pane.pane_id
    for pane in panes:
        if not _is_hud_watch_pane(pane):
            return pane.pane_id
    return preferred_pane_id


def _find_hud_pane_ids(target: str, leader_pane_id: str) -> list[str]:
    return [
        p.pane_id
        for p in _list_panes(target)
        if p.pane_id != leader_pane_id and _is_hud_watch_pane(p)
    ]


# --- Sleep helpers -----------------------------------------------------------


def _to_fractional_sleep_ms(seconds: float) -> int:
    """Mirror TS ``toFractionalSleepMs`` (tmux-session.ts:296-301)."""
    try:
        if not (seconds == seconds) or seconds <= 0 or seconds == float("inf"):
            return 0
    except TypeError:
        return 0
    import math

    ms = math.ceil(seconds * 1000)
    if ms <= 0:
        return 0
    return min(MAX_FRACTIONAL_SLEEP_MS, ms)


def sleep_fractional_seconds(
    seconds: float,
    sleep_impl: Callable[[float], None] | None = None,
) -> None:
    """Sleep for the given fractional seconds, capped at 60s.

    Mirrors TS ``sleepFractionalSeconds`` (tmux-session.ts:307-314). The
    ``sleep_impl`` callback receives **milliseconds** (matching the TS
    contract) so callers can swap in a fake clock during tests.
    """
    ms = _to_fractional_sleep_ms(seconds)
    if ms <= 0:
        return
    if sleep_impl is None:
        time.sleep(ms / 1000.0)
    else:
        sleep_impl(ms)


def _sleep_seconds(seconds: float) -> None:
    """Mirror TS ``sleepSeconds`` (tmux-session.ts:303-305)."""
    sleep_fractional_seconds(seconds)


# --- Shell quoting + path translation ---------------------------------------


def _shell_quote_single(value: str) -> str:
    """POSIX single-quote a string. Mirrors TS ``shellQuoteSingle``."""
    return "'" + value.replace("'", "'\\''") + "'"


def _quote_powershell_arg(value: str) -> str:
    """Single-quote for PowerShell (escape ``'`` as ``''``)."""
    return "'" + value.replace("'", "''") + "'"


def _encode_powershell_command(command_text: str) -> str:
    """Encode UTF-16 LE + base64 (matches PowerShell ``-EncodedCommand``)."""
    return base64.b64encode(command_text.encode("utf-16-le")).decode("ascii")


def _normalize_tmux_hook_token(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized if normalized else "unknown"


def _normalize_hud_pane_token(hud_pane_id: str) -> str:
    trimmed = hud_pane_id.strip()
    if trimmed.startswith("%"):
        trimmed = trimmed[1:]
    return _normalize_tmux_hook_token(trimmed)


# --- Platform / env detection ------------------------------------------------


def is_msys_or_git_bash(
    env: dict[str, str] | None = None,
    platform: str | None = None,
) -> bool:
    """Detect MSYS/MinGW/Cygwin (Git Bash) shells on Windows.

    Mirrors TS ``isMsysOrGitBash`` (tmux-session.ts:181-188).
    """
    eff_env = env if env is not None else os.environ
    eff_platform = platform if platform is not None else sys.platform
    if eff_platform != "win32":
        return False
    msystem = str(eff_env.get("MSYSTEM") or "").strip()
    if msystem:
        return True
    ostype = str(eff_env.get("OSTYPE") or "").strip()
    if ostype and re.search(r"(msys|mingw|cygwin)", ostype, re.IGNORECASE):
        return True
    return False


def _fallback_msys_path_translation(value: str) -> str:
    """Convert ``C:\\foo\\bar`` → ``/c/foo/bar`` as a cygpath fallback."""
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", value)
    if not match:
        return value
    drive = match.group(1)
    tail = match.group(2)
    if not drive or not tail:
        return value
    return f"/{drive.lower()}/{tail.replace(chr(92), '/')}"


def translate_path_for_msys(
    value: str,
    env: dict[str, str] | None = None,
    platform: str | None = None,
    spawn_impl: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> str:
    """Translate a Windows path to MSYS form when running in Git Bash/MSYS.

    Mirrors TS ``translatePathForMsys`` (tmux-session.ts:199-215).
    """
    if not isinstance(value, str) or value.strip() == "":
        return value
    if not is_msys_or_git_bash(env, platform):
        return value

    def _default_spawn(argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(argv, capture_output=True, text=True, check=False)
        except (FileNotFoundError, OSError) as err:
            cp: subprocess.CompletedProcess[str] = subprocess.CompletedProcess(
                argv, 1, "", str(err)
            )
            return cp

    spawn = spawn_impl if spawn_impl is not None else _default_spawn
    try:
        result = spawn(["cygpath", "-u", value])
    except Exception:  # noqa: BLE001 - best-effort fallback
        result = subprocess.CompletedProcess(["cygpath", "-u", value], 1, "", "")
    if (
        getattr(result, "returncode", 1) == 0
        and isinstance(result.stdout, str)
        and result.stdout.strip()
    ):
        return result.stdout.strip()
    return _fallback_msys_path_translation(value)


def is_wsl2() -> bool:
    """Detect WSL2 environment.

    Mirrors TS ``isWsl2`` (tmux-session.ts:973-983).
    """
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        with open("/proc/version", encoding="utf-8") as fh:
            return bool(re.search(r"microsoft", fh.read(), re.IGNORECASE))
    except OSError:
        return False


def is_native_windows() -> bool:
    """Detect native Windows (not WSL2, not Git Bash/MSYS).

    Mirrors TS ``isNativeWindows`` (tmux-session.ts:989-991).
    """
    return sys.platform == "win32" and not is_wsl2() and not is_msys_or_git_bash()


def is_tmux_available() -> bool:
    """Probe whether ``tmux -V`` succeeds.

    Mirrors TS ``isTmuxAvailable`` (tmux-session.ts:994-998).
    """
    try:
        result = subprocess.run(
            ["tmux", "-V"], capture_output=True, text=True, check=False
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def has_current_tmux_client_context() -> bool:
    """Detect whether the caller is running inside a tmux client.

    Mirrors TS ``hasCurrentTmuxClientContext`` (tmux-session.ts:169-179).
    """
    tmux_pane = (os.environ.get("TMUX_PANE") or "").strip()
    display_args = (
        ["display-message", "-p", "-t", tmux_pane, "#S:#I #{pane_id}"]
        if tmux_pane
        else ["display-message", "-p", "#S:#I #{pane_id}"]
    )
    context = _run_tmux(display_args)
    if not context.ok:
        return False
    parts = context.stdout.split(" ")
    session_and_window = parts[0] if len(parts) > 0 else ""
    detected_leader_pane_id = parts[1] if len(parts) > 1 else ""
    sw_parts = session_and_window.split(":")
    session_name = sw_parts[0] if len(sw_parts) > 0 else ""
    window_index = sw_parts[1] if len(sw_parts) > 1 else ""
    return bool(
        session_name and window_index and detected_leader_pane_id.startswith("%")
    )


# --- Copy-mode underline mitigation -----------------------------------------


def _append_no_underline_style_flags(style: str) -> str:
    """Mirror TS ``appendNoUnderlineStyleFlags``."""
    tokens = [t.strip() for t in re.split(r"[,\s]+", style) if t.strip()]
    combined = list(tokens)
    for flag in TMUX_NO_UNDERLINE_STYLE_FLAGS:
        if flag not in combined:
            combined.append(flag)
    return ",".join(combined)


def _sanitize_tmux_style_option(session_target: str, option_name: str) -> bool:
    shown = _run_tmux(["show-options", "-gv", "-t", session_target, option_name])
    if not shown.ok:
        return False
    current = shown.stdout.strip()
    if current == "":
        return False
    sanitized = _append_no_underline_style_flags(current)
    if sanitized == current:
        return True
    return _run_tmux(["set-option", "-t", session_target, option_name, sanitized]).ok


def mitigate_copy_mode_underline_artifacts(session_target: str) -> bool:
    """Append ``no-*-underscore`` flags to tmux copy-mode style options.

    Mirrors TS ``mitigateCopyModeUnderlineArtifacts`` (tmux-session.ts:156-167).
    """
    normalized = session_target.strip()
    if normalized == "":
        return False
    applied = False
    for option_name in TMUX_COPY_MODE_STYLE_OPTIONS:
        if _sanitize_tmux_style_option(normalized, option_name):
            applied = True
    return applied


# --- HUD resize hook builders -----------------------------------------------


def build_resize_hook_target(session_name: str, window_index: str) -> str:
    """Build ``<session>:<window>`` target for hook commands.

    Mirrors TS ``buildResizeHookTarget`` (tmux-session.ts:423-425).
    """
    return f"{session_name}:{window_index}"


def build_resize_hook_name(
    team_name: str,
    session_name: str,
    window_index: str,
    hud_pane_id: str,
) -> str:
    """Build a stable hook-name slug.

    Mirrors TS ``buildResizeHookName`` (tmux-session.ts:427-440).
    """
    return "_".join(
        [
            "omx_resize",
            _normalize_tmux_hook_token(team_name),
            _normalize_tmux_hook_token(session_name),
            _normalize_tmux_hook_token(window_index),
            _normalize_hud_pane_token(hud_pane_id),
        ]
    )


def build_hud_pane_target(hud_pane_id: str) -> str:
    """Ensure ``%``-prefix on a HUD pane id.

    Mirrors TS ``buildHudPaneTarget`` (tmux-session.ts:442-445).
    """
    trimmed = hud_pane_id.strip()
    return trimmed if trimmed.startswith("%") else f"%{trimmed}"


def _resolve_hud_height_lines(height_lines: int) -> int:
    try:
        normalized = int(height_lines)
    except (TypeError, ValueError):
        return HUD_TMUX_TEAM_HEIGHT_LINES
    return normalized if normalized > 0 else HUD_TMUX_TEAM_HEIGHT_LINES


def _build_hud_resize_command(
    hud_pane_id: str, height_lines: int = HUD_TMUX_TEAM_HEIGHT_LINES
) -> str:
    return (
        f"resize-pane -t {build_hud_pane_target(hud_pane_id)} "
        f"-y {_resolve_hud_height_lines(height_lines)}"
    )


def _build_hud_resize_args(
    hud_pane_id: str, height_lines: int = HUD_TMUX_TEAM_HEIGHT_LINES
) -> list[str]:
    return [
        "resize-pane",
        "-t",
        build_hud_pane_target(hud_pane_id),
        "-y",
        str(_resolve_hud_height_lines(height_lines)),
    ]


def _resolve_absolute_binary_path(binary: str) -> str:
    """Resolve a binary's absolute path via :func:`shutil.which`."""
    found = shutil.which(binary)
    return found if found else binary


def _build_nested_tmux_shell_command(command: str) -> str:
    """Build a nested ``tmux <command>`` invocation usable inside ``run-shell``.

    Mirrors TS ``buildNestedTmuxShellCommand`` (tmux-session.ts:464-475).
    """
    if sys.platform != "win32":
        return f"tmux {command}"
    resolved = _resolve_absolute_binary_path("tmux")
    if resolved == "tmux":
        return f"tmux {command}"
    forwarded = resolved.replace("\\", "/")
    return f"{_shell_quote_single(forwarded)} {command}"


def _build_best_effort_shell_command(command: str) -> str:
    return f"{command} >/dev/null 2>&1 || true"


def _build_resize_hook_slot(hook_name: str) -> str:
    """Mirror TS ``buildResizeHookSlot``."""
    h = 0
    for ch in hook_name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        if h & 0x80000000:
            h -= 0x100000000
    return f"client-resized[{abs(h) % TMUX_HOOK_INDEX_MAX}]"


def _build_client_attached_hook_slot(hook_name: str) -> str:
    """Mirror TS ``buildClientAttachedHookSlot``."""
    h = 0
    for ch in hook_name:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
        if h & 0x80000000:
            h -= 0x100000000
    return f"client-attached[{abs(h) % TMUX_HOOK_INDEX_MAX}]"


def build_register_resize_hook_args(
    hook_target: str,
    hook_name: str,
    hud_pane_id: str,
    height_lines: int = HUD_TMUX_TEAM_HEIGHT_LINES,
) -> list[str]:
    """Build ``set-hook`` args registering a client-resized HUD reconcile.

    Mirrors TS ``buildRegisterResizeHookArgs`` (tmux-session.ts:500-510).
    """
    resize_command = _shell_quote_single(
        _build_best_effort_shell_command(
            _build_nested_tmux_shell_command(
                _build_hud_resize_command(hud_pane_id, height_lines)
            )
        )
    )
    return [
        "set-hook",
        "-t",
        hook_target,
        _build_resize_hook_slot(hook_name),
        f"run-shell -b {resize_command}",
    ]


def build_unregister_resize_hook_args(hook_target: str, hook_name: str) -> list[str]:
    """Mirror TS ``buildUnregisterResizeHookArgs`` (tmux-session.ts:512-514)."""
    return ["set-hook", "-u", "-t", hook_target, _build_resize_hook_slot(hook_name)]


def build_client_attached_reconcile_hook_name(
    team_name: str,
    session_name: str,
    window_index: str,
    hud_pane_id: str,
) -> str:
    """Mirror TS ``buildClientAttachedReconcileHookName`` (516-529)."""
    return "_".join(
        [
            "omx_attached",
            _normalize_tmux_hook_token(team_name),
            _normalize_tmux_hook_token(session_name),
            _normalize_tmux_hook_token(window_index),
            _normalize_hud_pane_token(hud_pane_id),
        ]
    )


def build_register_client_attached_reconcile_args(
    hook_target: str,
    hook_name: str,
    hud_pane_id: str,
    height_lines: int = HUD_TMUX_TEAM_HEIGHT_LINES,
) -> list[str]:
    """Mirror TS ``buildRegisterClientAttachedReconcileArgs`` (531-542)."""
    hook_slot = _build_client_attached_hook_slot(hook_name)
    resize_part = _build_best_effort_shell_command(
        _build_nested_tmux_shell_command(
            _build_hud_resize_command(hud_pane_id, height_lines)
        )
    )
    unregister_part = _build_nested_tmux_shell_command(
        f"set-hook -u -t {hook_target} {hook_slot}"
    )
    one_shot_command = _shell_quote_single(f"{resize_part}; {unregister_part}")
    return [
        "set-hook",
        "-t",
        hook_target,
        hook_slot,
        f"run-shell -b {one_shot_command}",
    ]


def build_unregister_client_attached_reconcile_args(
    hook_target: str, hook_name: str
) -> list[str]:
    """Mirror TS ``buildUnregisterClientAttachedReconcileArgs`` (544-546)."""
    return [
        "set-hook",
        "-u",
        "-t",
        hook_target,
        _build_client_attached_hook_slot(hook_name),
    ]


def unregister_resize_hook(hook_target: str, hook_name: str) -> bool:
    """Send the resize-hook unregister to tmux.

    Mirrors TS ``unregisterResizeHook`` (tmux-session.ts:548-551).
    """
    return _run_tmux(build_unregister_resize_hook_args(hook_target, hook_name)).ok


def build_schedule_delayed_hud_resize_args(
    hud_pane_id: str,
    delay_seconds: float = HUD_RESIZE_RECONCILE_DELAY_SECONDS,
    height_lines: int = HUD_TMUX_TEAM_HEIGHT_LINES,
) -> list[str]:
    """Mirror TS ``buildScheduleDelayedHudResizeArgs`` (553-560)."""
    try:
        delay = (
            float(delay_seconds)
            if float(delay_seconds) > 0
            else HUD_RESIZE_RECONCILE_DELAY_SECONDS
        )
    except (TypeError, ValueError):
        delay = HUD_RESIZE_RECONCILE_DELAY_SECONDS
    nested = _build_best_effort_shell_command(
        _build_nested_tmux_shell_command(
            _build_hud_resize_command(hud_pane_id, height_lines)
        )
    )
    return ["run-shell", "-b", f"sleep {delay}; {nested}"]


def build_reconcile_hud_resize_args(
    hud_pane_id: str, height_lines: int = HUD_TMUX_TEAM_HEIGHT_LINES
) -> list[str]:
    """Mirror TS ``buildReconcileHudResizeArgs`` (562-567)."""
    return [
        "run-shell",
        _build_best_effort_shell_command(
            _build_nested_tmux_shell_command(
                _build_hud_resize_command(hud_pane_id, height_lines)
            )
        ),
    ]


# --- Worker CLI resolution ---------------------------------------------------


def _normalize_team_worker_cli_mode(
    raw: str | None, source_env: str = OMX_TEAM_WORKER_CLI_ENV
) -> TeamWorkerCliMode:
    """Mirror TS ``normalizeTeamWorkerCliMode`` (tmux-session.ts:633-638)."""
    normalized = str(raw if raw is not None else "auto").strip().lower()
    if normalized == "" or normalized == "auto":
        return "auto"
    if normalized in ("codex", "claude", "gemini"):
        return normalized  # type: ignore[return-value]
    raise ValueError(
        f'Invalid {source_env} value "{raw}". Expected: auto, codex, claude, gemini'
    )


def resolve_team_worker_launch_mode(
    env: dict[str, str] | None = None,
) -> TeamWorkerLaunchMode:
    """Resolve ``OMX_TEAM_WORKER_LAUNCH_MODE`` → ``interactive``/``prompt``.

    Mirrors TS ``resolveTeamWorkerLaunchMode`` (tmux-session.ts:640-647).
    """
    eff_env = env if env is not None else os.environ
    raw_value = eff_env.get(OMX_TEAM_WORKER_LAUNCH_MODE_ENV, "interactive")
    raw = str(raw_value).strip().lower()
    if raw == "" or raw == "interactive":
        return "interactive"
    if raw == "prompt":
        return "prompt"
    raise ValueError(
        f'Invalid {OMX_TEAM_WORKER_LAUNCH_MODE_ENV} value "{raw_value}". '
        "Expected: interactive, prompt"
    )


def _extract_model_override(args: list[str]) -> str | None:
    """Mirror TS ``extractModelOverride`` (tmux-session.ts:649-667)."""
    model: str | None = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == MODEL_FLAG:
            if i + 1 < len(args):
                maybe = args[i + 1]
                if (
                    isinstance(maybe, str)
                    and maybe.strip() != ""
                    and not maybe.startswith("-")
                ):
                    model = maybe.strip()
                    i += 1
            i += 1
            continue
        if arg.startswith(f"{MODEL_FLAG}="):
            inline = arg[len(MODEL_FLAG) + 1 :].strip()
            if inline != "":
                model = inline
        i += 1
    return model


def _resolve_team_worker_cli_from_launch_args(
    launch_args: list[str],
) -> TeamWorkerCli:
    """Mirror TS ``resolveTeamWorkerCliFromLaunchArgs`` (tmux-session.ts:675-680)."""
    model = _extract_model_override(launch_args)
    if model and re.search(r"claude", model, re.IGNORECASE):
        return "claude"
    if model and re.search(r"gemini", model, re.IGNORECASE):
        return "gemini"
    return "codex"


def resolve_team_worker_cli(
    launch_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> TeamWorkerCli:
    """Resolve the team-worker CLI (env-forced ``OMX_TEAM_WORKER_CLI`` wins).

    Mirrors TS ``resolveTeamWorkerCli`` (tmux-session.ts:669-673).
    """
    eff_env = env if env is not None else os.environ
    launch = launch_args or []
    mode = _normalize_team_worker_cli_mode(eff_env.get(OMX_TEAM_WORKER_CLI_ENV))
    if mode != "auto":
        return mode  # type: ignore[return-value]
    return _resolve_team_worker_cli_from_launch_args(launch)


def resolve_team_worker_cli_plan(
    worker_count: int,
    launch_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> list[TeamWorkerCli]:
    """Resolve per-worker CLI assignment from ``OMX_TEAM_WORKER_CLI_MAP``.

    Mirrors TS ``resolveTeamWorkerCliPlan`` (tmux-session.ts:682-728).
    """
    if not isinstance(worker_count, int) or worker_count < 1:
        raise ValueError(f"workerCount must be >= 1 (got {worker_count})")
    eff_env = env if env is not None else os.environ
    launch = launch_args or []
    raw_value = eff_env.get(OMX_TEAM_WORKER_CLI_MAP_ENV)
    raw_map = str(raw_value or "").strip()

    if raw_map == "":
        cli = resolve_team_worker_cli(launch, eff_env)
        return [cli for _ in range(worker_count)]

    entries = [part.strip() for part in raw_map.split(",")]
    if not entries or all(part == "" for part in entries):
        raise ValueError(
            f'Invalid {OMX_TEAM_WORKER_CLI_MAP_ENV} value "{raw_value}". '
            "Expected comma-separated values: auto|codex|claude|gemini."
        )
    if any(part == "" for part in entries):
        raise ValueError(
            f'Invalid {OMX_TEAM_WORKER_CLI_MAP_ENV} value "{raw_value}". '
            "Empty entries are not allowed."
        )
    if len(entries) not in (1, worker_count):
        raise ValueError(
            f"Invalid {OMX_TEAM_WORKER_CLI_MAP_ENV} length {len(entries)}; "
            f"expected 1 or {worker_count} comma-separated values."
        )

    if len(entries) == 1:
        expanded = [entries[0] for _ in range(worker_count)]
    else:
        expanded = entries

    plan: list[TeamWorkerCli] = []
    for entry in expanded:
        mode = _normalize_team_worker_cli_mode(entry, OMX_TEAM_WORKER_CLI_MAP_ENV)
        if mode == "auto":
            plan.append(_resolve_team_worker_cli_from_launch_args(launch))
        else:
            plan.append(mode)  # type: ignore[arg-type]
    return plan


def _should_grant_execution_bypass_for_role(worker_role: str | None) -> bool:
    """Mirror TS ``shouldGrantExecutionBypassForRole`` (tmux-session.ts:730-736).

    Looks up ``worker_role`` in the agent registry; missing roles default to
    True (bypass allowed). Roles with ``tools != "execution"`` return False.
    """
    if not worker_role:
        return True
    normalized = worker_role.strip().lower()
    if not normalized:
        return True
    try:
        from omx.agents.roles import get_agent
    except Exception:  # noqa: BLE001 - missing agents module is non-fatal
        return True
    agent = get_agent(normalized)
    if agent is None:
        return True
    return getattr(agent, "tools", None) == "execution"


def translate_worker_launch_args_for_cli(
    worker_cli: TeamWorkerCli,
    args: list[str],
    initial_prompt: str | None = None,
    worker_role: str | None = None,
) -> list[str]:
    """Translate Codex-style launch args for the chosen worker CLI.

    Mirrors TS ``translateWorkerLaunchArgsForCli`` (tmux-session.ts:738-761).
    """
    if worker_cli == "codex":
        return list(args)
    if worker_cli == "gemini":
        model = _extract_model_override(args)
        gemini_model = (
            model if model and re.search(r"gemini", model, re.IGNORECASE) else None
        )
        translated: list[str] = (
            [GEMINI_APPROVAL_MODE_FLAG, GEMINI_APPROVAL_MODE_YOLO]
            if _should_grant_execution_bypass_for_role(worker_role)
            else []
        )
        trimmed = initial_prompt.strip() if isinstance(initial_prompt, str) else ""
        if trimmed:
            translated.extend([GEMINI_PROMPT_INTERACTIVE_FLAG, trimmed])
        if gemini_model:
            translated.extend([MODEL_FLAG, gemini_model])
        return translated

    # Claude — drop everything except the permissions bypass flag.
    if _should_grant_execution_bypass_for_role(worker_role):
        return [CLAUDE_SKIP_PERMISSIONS_FLAG]
    return []


def _command_exists(binary: str) -> bool:
    """Mirror TS ``commandExists`` — best-effort version probe."""
    try:
        result = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return result.returncode == 0 or bool(result.stdout) or bool(result.stderr)


def assert_team_worker_cli_binary_available(
    worker_cli: TeamWorkerCli,
    exists_impl: Callable[[str], bool] | None = None,
) -> None:
    """Raise if the chosen worker CLI is not on PATH.

    Mirrors TS ``assertTeamWorkerCliBinaryAvailable`` (tmux-session.ts:795-804).
    """
    checker = exists_impl if exists_impl is not None else _command_exists
    if checker(worker_cli):
        return
    raise RuntimeError(
        f'Selected team worker CLI "{worker_cli}" is not available on PATH. '
        f'Install "{worker_cli}" or set {OMX_TEAM_WORKER_CLI_ENV}=codex|claude|gemini.'
    )


def _resolve_worker_cli_from_map_for_send(
    worker_index: int,
    launch_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> TeamWorkerCli | None:
    """Mirror TS ``resolveWorkerCliFromMapForSend`` (tmux-session.ts:1361-1378)."""
    eff_env = env if env is not None else os.environ
    launch = launch_args or []
    raw_map = str(eff_env.get(OMX_TEAM_WORKER_CLI_MAP_ENV) or "").strip()
    if raw_map == "":
        return None
    entries = [entry.strip() for entry in raw_map.split(",")]
    if not entries or any(entry == "" for entry in entries):
        return None
    selected_raw = (
        entries[0]
        if len(entries) == 1
        else (entries[worker_index - 1] if 0 < worker_index <= len(entries) else None)
    )
    if not selected_raw:
        return None
    try:
        mode = _normalize_team_worker_cli_mode(
            selected_raw, OMX_TEAM_WORKER_CLI_MAP_ENV
        )
    except ValueError:
        return None
    if mode == "auto":
        return _resolve_team_worker_cli_from_launch_args(launch)
    return mode  # type: ignore[return-value]


def resolve_worker_cli_for_send(
    worker_index: int,
    worker_cli: TeamWorkerCli | None = None,
    launch_args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> TeamWorkerCli:
    """Resolve the worker CLI to use when sending a message.

    Mirrors TS ``resolveWorkerCliForSend`` (tmux-session.ts:1386-1396).
    """
    if worker_cli:
        return worker_cli
    mapped = _resolve_worker_cli_from_map_for_send(worker_index, launch_args, env)
    if mapped:
        return mapped
    return resolve_team_worker_cli(launch_args, env)


# --- Worker process launch spec ---------------------------------------------


def _shell_affinity_spec(
    shell_path: str | None,
) -> tuple[str, str | None] | None:
    """Mirror TS ``resolveSupportedShellAffinity``."""
    if not shell_path or shell_path.strip() == "" or not Path(shell_path).exists():
        return None
    if re.search(r"/zsh$", shell_path, re.IGNORECASE):
        return (shell_path, "~/.zshrc")
    if re.search(r"/bash$", shell_path, re.IGNORECASE):
        return (shell_path, "~/.bashrc")
    return None


_ZSH_CANDIDATE_PATHS = (
    "/bin/zsh",
    "/usr/bin/zsh",
    "/usr/local/bin/zsh",
    "/opt/homebrew/bin/zsh",
)
_BASH_CANDIDATE_PATHS = ("/bin/bash", "/usr/bin/bash")


def _resolve_shell_from_candidates(
    paths: tuple[str, ...], rc_file: str
) -> tuple[str, str | None] | None:
    for shell_path in paths:
        if Path(shell_path).exists():
            return (shell_path, rc_file)
    return None


def _build_worker_launch_spec(shell_path: str | None) -> tuple[str, str | None]:
    """Mirror TS ``buildWorkerLaunchSpec`` (tmux-session.ts:590-605)."""
    if is_msys_or_git_bash():
        return ("/bin/sh", None)
    affinity = _shell_affinity_spec(shell_path)
    if affinity:
        return affinity
    zsh = _resolve_shell_from_candidates(_ZSH_CANDIDATE_PATHS, "~/.zshrc")
    if zsh:
        return zsh
    bash = _resolve_shell_from_candidates(_BASH_CANDIDATE_PATHS, "~/.bashrc")
    if bash:
        return bash
    return ("/bin/sh", None)


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _is_model_instructions_override(value: str) -> bool:
    pattern = re.compile(rf"^{re.escape(MODEL_INSTRUCTIONS_FILE_KEY)}\s*=")
    return bool(pattern.match(value.strip()))


_LONG_CONFIG_FLAG = "--config"


def _has_model_instructions_override(args: list[str]) -> bool:
    """Mirror TS ``hasModelInstructionsOverride``."""
    for i, arg in enumerate(args):
        if arg in (CONFIG_FLAG, _LONG_CONFIG_FLAG):
            if i + 1 < len(args):
                maybe = args[i + 1]
                if isinstance(maybe, str) and _is_model_instructions_override(maybe):
                    return True
            continue
        if arg.startswith(f"{_LONG_CONFIG_FLAG}="):
            inline = arg[len(_LONG_CONFIG_FLAG) + 1 :]
            if _is_model_instructions_override(inline):
                return True
    return False


def _should_bypass_default_system_prompt(env: dict[str, str]) -> bool:
    return env.get(OMX_BYPASS_DEFAULT_SYSTEM_PROMPT_ENV) != "0"


def _build_model_instructions_override(cwd: str, env: dict[str, str]) -> str:
    file_path = translate_path_for_msys(
        env.get(OMX_MODEL_INSTRUCTIONS_FILE_ENV) or str(Path(cwd) / "AGENTS.md"),
        env=env,
    )
    return f'{MODEL_INSTRUCTIONS_FILE_KEY}="{_escape_toml_string(file_path)}"'


def _read_tmux_worker_ambient_env(env: dict[str, str]) -> dict[str, str]:
    inherited: dict[str, str] = {}
    for key in TMUX_WORKER_AMBIENT_ENV_ALLOWLIST:
        value = env.get(key)
        if isinstance(value, str) and value.strip():
            inherited[key] = value
    return inherited


def _resolve_worker_launch_args(
    extra_args: list[str] | None,
    cwd: str,
    env: dict[str, str],
    argv: list[str] | None = None,
) -> list[str]:
    """Mirror TS ``resolveWorkerLaunchArgs`` (tmux-session.ts:825-835)."""
    merged = list(extra_args or [])
    effective_argv = argv if argv is not None else sys.argv
    wants_bypass = CODEX_BYPASS_FLAG in effective_argv or MADMAX_FLAG in effective_argv
    if wants_bypass and CODEX_BYPASS_FLAG not in merged:
        merged.append(CODEX_BYPASS_FLAG)
    if _should_bypass_default_system_prompt(
        env
    ) and not _has_model_instructions_override(merged):
        merged.extend([CONFIG_FLAG, _build_model_instructions_override(cwd, env)])
    return merged


_LEADER_NODE_PATH_CACHE: dict[str, str] = {}


def _resolve_leader_node_path() -> str:
    """Resolve a node binary path with env override + cache.

    Mirrors TS ``resolveLeaderNodePath`` (tmux-session.ts:784-793).
    """
    override = (os.environ.get(OMX_LEADER_NODE_PATH_ENV) or "").strip()
    if override:
        return override
    if "node" not in _LEADER_NODE_PATH_CACHE:
        _LEADER_NODE_PATH_CACHE["node"] = _resolve_absolute_binary_path("node")
    return _LEADER_NODE_PATH_CACHE["node"]


def build_worker_process_launch_spec(
    team_name: str,
    worker_index: int,
    launch_args: list[str] | None = None,
    cwd: str | None = None,
    extra_env: dict[str, str] | None = None,
    worker_cli_override: TeamWorkerCli | None = None,
    initial_prompt: str | None = None,
    worker_role: str | None = None,
) -> WorkerProcessLaunchSpec:
    """Build the process-level launch spec for a worker.

    Mirrors TS ``buildWorkerProcessLaunchSpec`` (tmux-session.ts:897-950).

    The TS variant additionally calls ``readActiveProviderEnvOverrides`` for
    Codex workers — that helper depends on ``config/models.ts`` which is not
    yet ported. Here we mirror the rest of the contract and leave provider-env
    injection as a forward-compatible no-op until ``config.models`` lands.
    """
    cwd_eff = cwd if cwd is not None else os.getcwd()
    extra_env_eff = dict(extra_env or {})
    effective_env: dict[str, str] = {**os.environ, **extra_env_eff}
    full_launch_args = _resolve_worker_launch_args(launch_args, cwd_eff, effective_env)
    worker_cli: TeamWorkerCli = (
        worker_cli_override
        if worker_cli_override is not None
        else resolve_team_worker_cli(full_launch_args, effective_env)
    )
    cli_launch_args = translate_worker_launch_args_for_cli(
        worker_cli, full_launch_args, initial_prompt, worker_role
    )
    effective_cli_launch_args = list(cli_launch_args)
    if (
        worker_cli == "codex"
        and _should_grant_execution_bypass_for_role(worker_role)
        and CODEX_BYPASS_FLAG not in effective_cli_launch_args
    ):
        effective_cli_launch_args.append(CODEX_BYPASS_FLAG)

    resolved_cli_path = _resolve_absolute_binary_path(worker_cli)
    # Native Windows would route through a platform-specific spec builder in TS.
    # We simplify here: use the resolved cli path directly. (See PARITY notes.)
    command = resolved_cli_path
    args = effective_cli_launch_args
    resolved_launcher_path = command

    worker_env: dict[str, str] = {
        "OMX_TEAM_WORKER": f"{team_name}/worker-{worker_index}",
        OMX_LEADER_NODE_PATH_ENV: _resolve_leader_node_path(),
        OMX_LEADER_CLI_PATH_ENV: resolved_launcher_path,
    }
    # NOTE: Codex provider-env overrides (readActiveProviderEnvOverrides) are
    # not yet ported; once config.models lands we should inject those for the
    # codex CLI. # TODO: wire provider env overrides for codex workers.
    for key, value in extra_env_eff.items():
        if isinstance(value, str) and value.strip():
            worker_env[key] = value

    return WorkerProcessLaunchSpec(
        worker_cli=worker_cli, command=command, args=list(args), env=worker_env
    )


def build_worker_startup_command(
    team_name: str,
    worker_index: int,
    launch_args: list[str] | None = None,
    cwd: str | None = None,
    extra_env: dict[str, str] | None = None,
    worker_cli_override: TeamWorkerCli | None = None,
    initial_prompt: str | None = None,
    worker_role: str | None = None,
) -> str:
    """Build the full shell command string to launch a worker in a tmux pane.

    Mirrors TS ``buildWorkerStartupCommand`` (tmux-session.ts:837-895).
    """
    cwd_eff = cwd if cwd is not None else os.getcwd()
    process_spec = build_worker_process_launch_spec(
        team_name,
        worker_index,
        launch_args,
        cwd_eff,
        extra_env,
        worker_cli_override,
        initial_prompt,
        worker_role,
    )
    startup_env: dict[str, str] = {
        **_read_tmux_worker_ambient_env(dict(os.environ)),
        **process_spec.env,
    }
    resolved_leader_node_path = (
        process_spec.env.get(OMX_LEADER_NODE_PATH_ENV, "").strip()
        or _resolve_leader_node_path()
    )
    leader_node_dir = ""
    if re.search(r"[\\/]", resolved_leader_node_path):
        leader_node_dir = re.sub(r"[\\/][^\\/]+$", "", resolved_leader_node_path)

    if is_native_windows():
        # # TODO: full native-Windows PowerShell launcher fidelity. This path
        # produces a working `-EncodedCommand` invocation that mirrors the TS
        # shape; psmux-specific path quoting can be tightened once a Python
        # platform-command helper lands.
        powershell_path = (
            _resolve_absolute_binary_path("powershell.exe") or "powershell.exe"
        )
        pieces: list[str] = ["$ErrorActionPreference = 'Stop'"]
        if leader_node_dir:
            pieces.append(
                f"$env:PATH = {_quote_powershell_arg(leader_node_dir + ';')} + $env:PATH"
            )
        env_assignments = "; ".join(
            f"$env:{k} = {_quote_powershell_arg(v)}" for k, v in startup_env.items()
        )
        if env_assignments:
            pieces.append(env_assignments)
        invocation = " ".join(
            ["&", _quote_powershell_arg(process_spec.command)]
            + [_quote_powershell_arg(a) for a in process_spec.args]
        )
        pieces.append(invocation)
        encoded = _encode_powershell_command("; ".join(pieces))
        return (
            f"{powershell_path} -NoLogo -NoProfile -ExecutionPolicy Bypass "
            f"-EncodedCommand {encoded}"
        )

    shell_path, rc_file = _build_worker_launch_spec(os.environ.get("SHELL"))
    path_prefix = (
        f"export PATH={_shell_quote_single(leader_node_dir)}:$PATH; "
        if leader_node_dir
        else ""
    )
    quoted_args = " ".join(_shell_quote_single(a) for a in process_spec.args)
    quoted_command = _shell_quote_single(process_spec.command)
    cli_invocation = (
        f"exec {quoted_command} {quoted_args}"
        if quoted_args
        else f"exec {quoted_command}"
    )
    rc_prefix = f"if [ -f {rc_file} ]; then source {rc_file}; fi; " if rc_file else ""
    inner = f"{rc_prefix}{path_prefix}{cli_invocation}"
    env_parts = [f"{k}={v}" for k, v in startup_env.items()]
    env_quoted = " ".join(_shell_quote_single(p) for p in env_parts)
    return (
        f"env {env_quoted} {_shell_quote_single(shell_path)} "
        f"-c {_shell_quote_single(inner)}"
    )


# --- sanitize team name ------------------------------------------------------


def sanitize_team_name(name: str) -> str:
    """Lowercase, alphanumeric + hyphens, max 30 chars; reject empty result.

    Mirrors TS ``sanitizeTeamName`` (tmux-session.ts:953-966).
    """
    lowered = name.lower()
    replaced = re.sub(r"[^a-z0-9]+", "-", lowered)
    replaced = re.sub(r"-+", "-", replaced)
    replaced = re.sub(r"^-", "", replaced)
    replaced = re.sub(r"-$", "", replaced)
    truncated = re.sub(r"-$", "", replaced[:30])
    if truncated.strip() == "":
        raise ValueError("sanitizeTeamName: empty after sanitization")
    return truncated


# --- Trust / bypass / update prompt detection -------------------------------


def _pane_has_trust_prompt(captured: str) -> bool:
    """Detect Codex 'Do you trust this directory?' prompt.

    Mirrors TS ``paneHasTrustPrompt`` (tmux-session.ts:1312-1321).
    """
    lines = [ln.replace("\r", "").strip() for ln in captured.splitlines() if ln.strip()]
    tail = lines[-12:] if len(lines) > 12 else lines
    text = "\n".join(tail)
    has_question = bool(
        re.search(r"Do you trust the contents of this directory\?", text, re.IGNORECASE)
    )
    has_choices = bool(
        re.search(
            r"Yes,\s*continue|No,\s*quit|Press enter to continue",
            text,
            re.IGNORECASE,
        )
    )
    return has_question and has_choices


def _pane_has_bypass_prompt(captured: str) -> bool:
    """Detect Claude 'Bypass Permissions mode' prompt (lenient legacy heuristic).

    Returns True when the capture contains the bypass-mode banner alongside
    either ``Yes, I accept`` or ``Enter to confirm``. The stricter four-marker
    TS check is provided by :func:`_pane_has_strict_bypass_prompt` and used
    inside :func:`_dismiss_claude_bypass_permissions_prompt_if_present`.
    """
    return "Bypass Permissions mode" in captured and (
        "Yes, I accept" in captured or "Enter to confirm" in captured
    )


def _pane_has_strict_bypass_prompt(captured: str) -> bool:
    """Strict TS-equivalent bypass prompt detection.

    Mirrors TS ``paneHasClaudeBypassPermissionsPrompt`` (tmux-session.ts:1323-1334).
    Requires all four markers (banner, ``No, exit``, ``Yes, I accept``,
    ``Enter to confirm``) within the last 20 trimmed lines.
    """
    lines = [ln.replace("\r", "").strip() for ln in captured.splitlines() if ln.strip()]
    tail = lines[-20:] if len(lines) > 20 else lines
    has_warning = any(
        re.search(r"Bypass Permissions mode", line, re.IGNORECASE) for line in tail
    )
    has_choices = (
        any(re.search(r"No,\s*exit", line, re.IGNORECASE) for line in tail)
        and any(re.search(r"Yes,\s*I\s*accept", line, re.IGNORECASE) for line in tail)
        and any(
            re.search(r"Enter\s*to\s*confirm", line, re.IGNORECASE) for line in tail
        )
    )
    return has_warning and has_choices


def _pane_has_update_prompt(captured: str) -> bool:
    """Detect Codex update available prompt."""
    return bool(
        re.search(
            r"update available|new version|upgrade.*available|npm install -g",
            captured,
            re.IGNORECASE,
        )
    )


def _dismiss_trust_prompt(pane_id: str) -> None:
    """Auto-dismiss trust prompt with Enter presses."""
    _run_tmux(["send-keys", "-t", pane_id, "C-m"])
    sleep_fractional_seconds(0.12)
    _run_tmux(["send-keys", "-t", pane_id, "C-m"])


def _accept_bypass_prompt(pane_id: str) -> None:
    """Auto-accept Claude bypass permissions prompt."""
    _run_tmux(["send-keys", "-t", pane_id, "-l", "--", "2"])
    sleep_fractional_seconds(0.12)
    _run_tmux(["send-keys", "-t", pane_id, "C-m"])


def _dismiss_update_prompt(pane_id: str) -> None:
    """Auto-dismiss Codex update prompt by selecting '2' (skip update)."""
    _run_tmux(["send-keys", "-t", pane_id, "-l", "--", "2"])
    sleep_fractional_seconds(0.12)
    _run_tmux(["send-keys", "-t", pane_id, "C-m"])


def _dismiss_claude_bypass_permissions_prompt_if_present(
    target: str, captured: str
) -> bool:
    """Mirror TS ``dismissClaudeBypassPermissionsPromptIfPresent`` (1342-1347)."""
    if os.environ.get("OMX_TEAM_AUTO_ACCEPT_BYPASS") == "0":
        return False
    if not _pane_has_strict_bypass_prompt(captured):
        return False
    _accept_bypass_prompt(target)
    return True


# --- Pane content classification (delegates to tmux-hook-engine in TS) ------
#
# The TS file re-exports several helpers from ``scripts/tmux-hook-engine``
# (``normalizeTmuxCapture``, ``paneHasActiveTask``, ``paneIsBootstrapping``,
# ``paneShowsCodexViewport``, ``paneLooksReady``). That hook-engine module is
# not yet ported. We provide local, behavior-equivalent implementations here
# so this module is self-contained.


def normalize_tmux_capture(captured: str) -> str:
    """Strip ANSI escapes and trailing whitespace from a pane capture.

    Best-effort port of ``normalizeTmuxCapture`` — used by send-flow plumbing
    to compare current pane text against the trigger we just sent.
    """
    if not isinstance(captured, str):
        return ""
    # Strip CSI escapes and OSC sequences.
    no_ansi = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", captured)
    no_ansi = re.sub(r"\x1b\][^\x07]*\x07", "", no_ansi)
    # Normalize line endings + trim each line.
    lines = [line.rstrip() for line in no_ansi.replace("\r", "").splitlines()]
    # Drop trailing empty lines.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _pane_looks_ready(captured: str) -> bool:
    """Heuristic: pane shows a CLI prompt ready for input."""
    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    if not lines:
        return False
    tail = "\n".join(lines[-5:])
    if any(ln.startswith(("›", ">", "❯")) for ln in lines[-3:]):
        return True
    return bool(
        re.search(
            r"[>$#%›❯]\s*$|What can I help|How can I help|Enter a prompt",
            tail,
        )
    )


def _pane_has_active_task(captured: str) -> bool:
    """Heuristic: the pane shows a worker actively running a task."""
    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    if not lines:
        return False
    tail = "\n".join(lines[-8:])
    return bool(
        re.search(
            r"Running|Thinking|Generating|Working|Reading|Writing|Searching",
            tail,
            re.IGNORECASE,
        )
    )


def _pane_shows_codex_viewport(captured: str) -> bool:
    """Best-effort: the visible pane already shows a Codex viewport frame."""
    return bool(
        re.search(r"codex|welcome to codex|enter a prompt", captured, re.IGNORECASE)
    )


def _pane_has_queued_codex_submission(captured: str | None) -> bool:
    """Mirror TS ``paneHasQueuedCodexSubmission`` (tmux-session.ts:1443-1448)."""
    normalized = normalize_tmux_capture(captured or "")
    if normalized == "":
        return False
    if re.search(
        r"messages to be submitted after next tool call", normalized, re.IGNORECASE
    ):
        return True
    if re.search(
        r"press esc to interrupt and send immediately", normalized, re.IGNORECASE
    ):
        return True
    return False


# --- Pane target builder -----------------------------------------------------


def _pane_target(
    session_name: str, worker_index: int, worker_pane_id: str | None = None
) -> str:
    """Mirror TS ``paneTarget`` (tmux-session.ts:1301-1307)."""
    if worker_pane_id and worker_pane_id.startswith("%"):
        return worker_pane_id
    if ":" in session_name:
        return f"{session_name}.{worker_index}"
    return f"{session_name}:{worker_index}"


# --- Capture pane (preserve existing helper) --------------------------------


def capture_pane(pane_id: str, lines: int = 80) -> str:
    """Capture the visible content of a tmux pane.

    Args:
        pane_id: Tmux pane target string.
        lines: Number of lines of scrollback to include.

    Returns:
        Captured pane text (empty on failure).
    """
    result = _run_tmux(["capture-pane", "-t", pane_id, "-p", "-S", f"-{lines}"])
    return result.stdout if result.ok else ""


def capture_visible_pane(pane_id: str) -> str:
    """Capture only the visible region of a pane (no scrollback)."""
    result = _run_tmux(["capture-pane", "-t", pane_id, "-p"])
    return result.stdout if result.ok else ""


# --- Worker liveness ---------------------------------------------------------


def get_worker_pane_pid(
    session_name: str, worker_index: int, worker_pane_id: str | None = None
) -> int | None:
    """Return the PID of the worker pane's leader process.

    Mirrors TS ``getWorkerPanePid`` (tmux-session.ts:1727-1737).
    """
    result = _run_tmux(
        [
            "list-panes",
            "-t",
            _pane_target(session_name, worker_index, worker_pane_id),
            "-F",
            "#{pane_pid}",
        ]
    )
    if not result.ok:
        return None
    first = result.stdout.splitlines()[0].strip() if result.stdout else ""
    if not first:
        return None
    try:
        return int(first)
    except ValueError:
        return None


def is_worker_alive(
    session_name: str, worker_index: int, worker_pane_id: str | None = None
) -> bool:
    """Check whether the pane process is still running.

    Mirrors TS ``isWorkerAlive`` (tmux-session.ts:1740-1767).
    """
    result = _run_tmux(
        [
            "list-panes",
            "-t",
            _pane_target(session_name, worker_index, worker_pane_id),
            "-F",
            "#{pane_dead} #{pane_pid}",
        ]
    )
    if not result.ok:
        return False
    line = (result.stdout.splitlines()[0] if result.stdout else "").strip()
    if not line:
        return False
    parts = re.split(r"\s+", line)
    if len(parts) < 2:
        return False
    pane_dead = parts[0]
    try:
        pid = int(parts[1])
    except ValueError:
        return False
    if pane_dead == "1":
        return False
    return _pid_is_alive(pid)


def _pid_is_alive(pid: int) -> bool:
    """Cross-platform best-effort liveness check (``kill -0`` equivalent)."""
    try:
        if sys.platform == "win32":
            # ``os.kill(pid, 0)`` raises OSError on missing process; signal 0
            # is invalid on Windows but kill(pid, 0) still surfaces the error
            # path correctly for our needs.
            os.kill(pid, 0)
            return True
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def is_worker_pane_open(
    session_name: str, worker_index: int, worker_pane_id: str | None = None
) -> bool:
    """Return True iff the worker pane still exists.

    Mirrors TS ``isWorkerPaneOpen`` (tmux-session.ts:1769-1780).
    """
    result = _run_tmux(
        [
            "list-panes",
            "-t",
            _pane_target(session_name, worker_index, worker_pane_id),
            "-F",
            "#{pane_dead}",
        ]
    )
    if not result.ok:
        return False
    line = (result.stdout.splitlines()[0] if result.stdout else "").strip()
    if not line:
        return False
    return line != "1"


def kill_worker(
    session_name: str,
    worker_index: int,
    worker_pane_id: str | None = None,
    leader_pane_id: str | None = None,
) -> None:
    """Kill a specific worker via C-c → C-d → kill-pane escalation.

    Mirrors TS ``killWorker`` (tmux-session.ts:1784-1799). Sync version.
    """
    if leader_pane_id and worker_pane_id == leader_pane_id:
        return
    target = _pane_target(session_name, worker_index, worker_pane_id)
    _run_tmux(["send-keys", "-t", target, "C-c"])
    time.sleep(1.0)
    if is_worker_alive(session_name, worker_index, worker_pane_id):
        _run_tmux(["send-keys", "-t", target, "C-d"])
        time.sleep(1.0)
    if is_worker_alive(session_name, worker_index, worker_pane_id):
        _run_tmux(["kill-pane", "-t", target])


def kill_worker_by_pane_id(
    worker_pane_id: str, leader_pane_id: str | None = None
) -> None:
    """Kill a worker pane by pane id, guarding against the leader pane.

    Mirrors TS ``killWorkerByPaneId`` (tmux-session.ts:1802-1807).
    """
    if not worker_pane_id.startswith("%"):
        return
    if leader_pane_id and worker_pane_id == leader_pane_id:
        return
    _run_tmux(["kill-pane", "-t", worker_pane_id])


# --- Worker readiness polling ------------------------------------------------


def dismiss_trust_prompt_if_present(
    session_name: str,
    worker_index: int,
    worker_pane_id: str | None = None,
) -> bool:
    """Detect and dismiss a Codex trust prompt in a worker pane.

    Mirrors TS ``dismissTrustPromptIfPresent`` (tmux-session.ts:1570-1586).
    """
    if os.environ.get("OMX_TEAM_AUTO_TRUST") == "0":
        return False
    if not is_tmux_available():
        return False
    target = _pane_target(session_name, worker_index, worker_pane_id)
    result = _run_tmux(["capture-pane", "-t", target, "-p"])
    if not result.ok:
        return False
    if not _pane_has_trust_prompt(result.stdout):
        return False
    _run_tmux(["send-keys", "-t", target, "C-m"])
    sleep_fractional_seconds(0.12)
    _run_tmux(["send-keys", "-t", target, "C-m"])
    return True


def wait_for_worker_ready(
    session_name_or_pane: str,
    worker_index_or_timeout: int | None = None,
    timeout_ms: int = 30_000,
    worker_pane_id: str | None = None,
    *,
    auto_trust: bool = True,
) -> bool:
    """Poll a worker pane until it shows a ready prompt.

    Two calling conventions are supported (preserving the previous public API
    and matching TS):

    * Legacy single-arg form (used by the pre-port stub):
      ``wait_for_worker_ready(pane_id, timeout_ms=..., auto_trust=...)``.
      Detected when ``worker_index_or_timeout`` is large (>200) or ``None``,
      and the first positional looks like a tmux pane id.

    * Full TS-shaped form:
      ``wait_for_worker_ready(session_name, worker_index,
      timeout_ms=30_000, worker_pane_id=None)``.

    Returns True if the worker becomes ready within ``timeout_ms``.
    """
    # Legacy form: first arg is a pane id, second is timeout (or None).
    if worker_index_or_timeout is None or (
        isinstance(worker_index_or_timeout, int)
        and worker_index_or_timeout >= 200
        and (
            session_name_or_pane.startswith("%")
            or "." in session_name_or_pane
            or ":" in session_name_or_pane
        )
    ):
        effective_timeout_ms = (
            worker_index_or_timeout
            if isinstance(worker_index_or_timeout, int) and worker_index_or_timeout > 0
            else timeout_ms
        )
        return _wait_for_worker_ready_by_pane(
            session_name_or_pane,
            timeout_ms=effective_timeout_ms,
            auto_trust=auto_trust,
        )

    # Full TS form.
    assert isinstance(worker_index_or_timeout, int)
    return _wait_for_worker_ready_full(
        session_name_or_pane,
        worker_index_or_timeout,
        timeout_ms=timeout_ms,
        worker_pane_id=worker_pane_id,
    )


def _wait_for_worker_ready_by_pane(
    pane_id: str, timeout_ms: int = 30_000, auto_trust: bool = True
) -> bool:
    """Legacy single-pane readiness polling (preserves the pre-port API)."""
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    delay = 0.15

    while time.monotonic() < deadline:
        captured = capture_pane(pane_id, lines=20)
        if auto_trust and _pane_has_trust_prompt(captured):
            _dismiss_trust_prompt(pane_id)
            delay = 0.15
            time.sleep(delay)
            continue
        if auto_trust and _pane_has_bypass_prompt(captured):
            _accept_bypass_prompt(pane_id)
            delay = 0.15
            time.sleep(delay)
            continue
        if _pane_has_update_prompt(captured):
            _dismiss_update_prompt(pane_id)
            delay = 0.15
            time.sleep(delay)
            continue
        if _pane_looks_ready(captured):
            return True
        time.sleep(delay)
        delay = min(delay * 2, 8.0)
    return False


def _wait_for_worker_ready_full(
    session_name: str,
    worker_index: int,
    timeout_ms: int = 30_000,
    worker_pane_id: str | None = None,
) -> bool:
    """Full TS-shape readiness polling.

    Mirrors TS ``waitForWorkerReady`` (tmux-session.ts:1492-1563).
    """
    initial_backoff_ms = 150
    max_backoff_ms = 8000
    started_at = time.monotonic() * 1000.0
    blocked_by_trust_prompt = False
    prompt_dismissed = False

    def send_robust_enter() -> None:
        target = _pane_target(session_name, worker_index, worker_pane_id)
        _run_tmux(["send-keys", "-t", target, "C-m"])
        sleep_fractional_seconds(0.12)
        _run_tmux(["send-keys", "-t", target, "C-m"])

    def check() -> bool:
        nonlocal blocked_by_trust_prompt, prompt_dismissed
        target = _pane_target(session_name, worker_index, worker_pane_id)
        result = _run_tmux(["capture-pane", "-t", target, "-p"])
        if not result.ok:
            return False
        if _dismiss_claude_bypass_permissions_prompt_if_present(target, result.stdout):
            prompt_dismissed = True
            return False
        if _pane_has_bypass_prompt(result.stdout):
            return False
        if _pane_has_trust_prompt(result.stdout):
            if os.environ.get("OMX_TEAM_AUTO_TRUST") != "0":
                send_robust_enter()
                prompt_dismissed = True
                return False
            blocked_by_trust_prompt = True
            return False
        if _pane_looks_ready(result.stdout):
            return True
        if not _pane_shows_codex_viewport(result.stdout):
            return False
        scrollback = _run_tmux(["capture-pane", "-t", target, "-p", "-S", "-80"])
        if not scrollback.ok:
            return False
        return _pane_looks_ready(scrollback.stdout)

    delay_ms = initial_backoff_ms
    while (time.monotonic() * 1000.0) - started_at < timeout_ms:
        if check():
            return True
        if blocked_by_trust_prompt:
            return False
        if prompt_dismissed:
            delay_ms = initial_backoff_ms
            prompt_dismissed = False
        remaining = timeout_ms - ((time.monotonic() * 1000.0) - started_at)
        if remaining <= 0:
            break
        sleep_ms = max(0.0, min(delay_ms, remaining))
        sleep_fractional_seconds(sleep_ms / 1000.0)
        delay_ms = min(max_backoff_ms, delay_ms * 2)
    return False


# --- Submit plan + send-to-worker -------------------------------------------


def build_worker_submit_plan(
    strategy: Literal["auto", "queue", "interrupt"],
    worker_cli: TeamWorkerCli,
    pane_busy_at_start: bool,
    allow_adaptive_retry: bool,
) -> WorkerSubmitPlan:
    """Build a per-message :class:`WorkerSubmitPlan`.

    Mirrors TS ``buildWorkerSubmitPlan`` (tmux-session.ts:1398-1412).
    """
    queue_requested = strategy == "queue" or (strategy == "auto" and pane_busy_at_start)
    return WorkerSubmitPlan(
        should_interrupt=strategy == "interrupt",
        queue_first_round=worker_cli == "codex" and queue_requested,
        rounds=6,
        submit_key_presses_per_round=1 if worker_cli == "claude" else 2,
        allow_adaptive_retry=worker_cli == "codex" and allow_adaptive_retry,
    )


def should_attempt_adaptive_retry(
    strategy: Literal["auto", "queue", "interrupt"],
    pane_busy_at_start: bool,
    allow_adaptive_retry: bool,
    latest_capture: str | None,
    text: str,
) -> bool:
    """Decide whether to attempt the adaptive resend branch.

    Mirrors TS ``shouldAttemptAdaptiveRetry`` (tmux-session.ts:1414-1434).
    """
    if not allow_adaptive_retry:
        return False
    if strategy != "auto":
        return False
    if not pane_busy_at_start:
        return False
    if not isinstance(latest_capture, str):
        return False
    normalized_text = normalize_tmux_capture(text)
    if normalized_text == "":
        return False
    normalized_capture = normalize_tmux_capture(latest_capture)
    if normalized_text not in normalized_capture:
        return False
    if _pane_has_active_task(latest_capture):
        return False
    if not _pane_looks_ready(latest_capture):
        return False
    return True


def _resolve_send_strategy_from_env() -> Literal["auto", "queue", "interrupt"]:
    raw = str(os.environ.get("OMX_TEAM_SEND_STRATEGY") or "").strip().lower()
    if raw in ("interrupt", "queue", "auto"):
        return raw  # type: ignore[return-value]
    return "auto"


def _assert_worker_trigger_text(text: str) -> None:
    """Mirror TS ``assertWorkerTriggerText``."""
    if len(text) >= 200:
        raise ValueError("send_to_worker: text must be < 200 characters")
    if text.strip() == "":
        raise ValueError("send_to_worker: text must be non-empty")
    if INJECTION_MARKER in text:
        raise ValueError("send_to_worker: injection marker is not allowed")


def send_to_worker_stdin(stdin, text: str) -> None:
    """Write ``text + '\\n'`` to a worker's stdin.

    Mirrors TS ``sendToWorkerStdin`` (tmux-session.ts:1602-1611).
    """
    _assert_worker_trigger_text(text)
    if stdin is None or getattr(stdin, "closed", False):
        raise RuntimeError("send_to_worker_stdin: stdin is not writable")
    write = getattr(stdin, "write", None)
    if not callable(write):
        raise RuntimeError("send_to_worker_stdin: stdin has no write method")
    write(f"{text}\n")


def _attempt_submit_rounds(
    target: str,
    text: str,
    rounds: int,
    queue_first_round: bool,
    submit_key_presses_per_round: int,
) -> bool:
    """Sync port of TS ``attemptSubmitRounds`` (tmux-session.ts:1450-1487)."""
    presses = max(1, int(submit_key_presses_per_round))
    for rnd in range(rounds):
        time.sleep(0.1)
        if rnd == 0 and queue_first_round:
            _run_tmux(["send-keys", "-t", target, "Tab"])
            time.sleep(0.08)
            _run_tmux(["send-keys", "-t", target, "C-m"])
        else:
            for press in range(presses):
                _run_tmux(["send-keys", "-t", target, "C-m"])
                if press < presses - 1:
                    time.sleep(0.2)
        time.sleep(0.14)
        captured = capture_pane(target, lines=80)
        visible = capture_visible_pane(target)
        normalized_capture = normalize_tmux_capture(captured)
        if normalize_tmux_capture(
            text
        ) not in normalized_capture and not _pane_has_queued_codex_submission(visible):
            return True
        time.sleep(0.14)
    return False


def send_to_worker(
    session_name_or_pane: str,
    worker_index_or_text: int | str,
    text_or_worker_cli: str | None = None,
    worker_pane_id: str | None = None,
    worker_cli: TeamWorkerCli | None = None,
) -> bool | None:
    """Send a short trigger string to a worker.

    Two calling conventions:

    * **Legacy** (preserved from the pre-port stub):
      ``send_to_worker(pane_id, text, worker_cli="codex") -> bool``.
    * **Full TS** (mirrors ``sendToWorker``):
      ``send_to_worker(session_name, worker_index, text,
      worker_pane_id=None, worker_cli=None) -> None``.

    Returns ``True``/``False`` for the legacy form and ``None`` for the
    full-shape form (matching the TS Promise<void> contract). Both forms
    validate ``text`` length and reject the injection marker.
    """
    # Legacy positional form: (pane_id, text, [cli_str])
    if isinstance(worker_index_or_text, str):
        text = worker_index_or_text
        worker_cli_str = text_or_worker_cli or "codex"
        return _send_to_worker_legacy(session_name_or_pane, text, worker_cli_str)

    assert isinstance(worker_index_or_text, int)
    assert isinstance(text_or_worker_cli, str)
    _send_to_worker_full(
        session_name_or_pane,
        worker_index_or_text,
        text_or_worker_cli,
        worker_pane_id=worker_pane_id,
        worker_cli=worker_cli,
    )
    return None


def _send_to_worker_legacy(pane_id: str, text: str, worker_cli: str) -> bool:
    """Legacy single-pane send (matches the pre-port public API)."""
    _assert_worker_trigger_text(text)
    captured = capture_pane(pane_id, lines=12)
    if _pane_has_trust_prompt(captured):
        _dismiss_trust_prompt(pane_id)
        time.sleep(0.3)
    if _pane_has_bypass_prompt(captured):
        _accept_bypass_prompt(pane_id)
        time.sleep(0.3)

    _run_tmux(["send-keys", "-t", pane_id, "-l", "--", text])
    time.sleep(0.1)

    presses = 1 if worker_cli == "claude" else 2
    rounds = 6
    for _ in range(rounds):
        for press in range(presses):
            _run_tmux(["send-keys", "-t", pane_id, "C-m"])
            if press < presses - 1:
                time.sleep(0.2)
        time.sleep(0.14)
        post = capture_pane(pane_id, lines=12)
        if _pane_has_active_task(post) or text not in post:
            return True
    return False


def _send_to_worker_full(
    session_name: str,
    worker_index: int,
    text: str,
    worker_pane_id: str | None = None,
    worker_cli: TeamWorkerCli | None = None,
) -> None:
    """Full TS-shape send-to-worker.

    Mirrors TS ``sendToWorker`` (tmux-session.ts:1616-1715).
    """
    _assert_worker_trigger_text(text)
    target = _pane_target(session_name, worker_index, worker_pane_id)
    strategy = _resolve_send_strategy_from_env()
    resolved_worker_cli = resolve_worker_cli_for_send(worker_index, worker_cli)

    captured_str = capture_pane(target, lines=80)
    pane_busy = _pane_has_active_task(captured_str)
    if _dismiss_claude_bypass_permissions_prompt_if_present(target, captured_str):
        time.sleep(0.2)
    if _pane_has_trust_prompt(captured_str):
        _run_tmux(["send-keys", "-t", target, "C-m"])
        time.sleep(0.12)
        _run_tmux(["send-keys", "-t", target, "C-m"])
        time.sleep(0.2)

    send = _run_tmux(["send-keys", "-t", target, "-l", "--", text])
    if not send.ok:
        raise RuntimeError(f"send_to_worker: failed to send text: {send.stderr}")
    time.sleep(0.15)

    allow_auto_interrupt_retry = (
        os.environ.get(OMX_TEAM_AUTO_INTERRUPT_RETRY_ENV) != "0"
    )
    plan = build_worker_submit_plan(
        strategy, resolved_worker_cli, pane_busy, allow_auto_interrupt_retry
    )
    if plan.should_interrupt:
        _run_tmux(["send-keys", "-t", target, "C-c"])
        time.sleep(0.1)

    if _attempt_submit_rounds(
        target,
        text,
        plan.rounds,
        plan.queue_first_round,
        plan.submit_key_presses_per_round,
    ):
        return

    latest_capture = capture_pane(target, lines=80)
    if should_attempt_adaptive_retry(
        strategy, pane_busy, plan.allow_adaptive_retry, latest_capture, text
    ):
        _run_tmux(["send-keys", "-t", target, "C-u"])
        time.sleep(0.08)
        resend = _run_tmux(["send-keys", "-t", target, "-l", "--", text])
        if not resend.ok:
            raise RuntimeError(
                f"send_to_worker: failed to resend text: {resend.stderr}"
            )
        time.sleep(0.12)
        if _attempt_submit_rounds(
            target, text, 4, False, plan.submit_key_presses_per_round
        ):
            return

    strict = os.environ.get("OMX_TEAM_STRICT_SUBMIT") == "1"
    if strict:
        raise RuntimeError(
            "send_to_worker: submit_failed (trigger text still visible after retries)"
        )

    _run_tmux(["send-keys", "-t", target, "C-m"])
    time.sleep(0.12)
    _run_tmux(["send-keys", "-t", target, "C-m"])
    time.sleep(0.3)
    verify_capture = capture_pane(target, lines=80)
    verify_visible = capture_visible_pane(target)
    if verify_capture:
        if _pane_has_active_task(verify_capture):
            return
        if normalize_tmux_capture(text) not in normalize_tmux_capture(
            verify_capture
        ) and not _pane_has_queued_codex_submission(verify_visible):
            return
        _run_tmux(["send-keys", "-t", target, "C-m"])
        time.sleep(0.15)
        _run_tmux(["send-keys", "-t", target, "C-m"])
        final_visible = capture_visible_pane(target)
        if _pane_has_queued_codex_submission(final_visible):
            raise RuntimeError("send_to_worker: submit_queued_after_tool_call")


# --- Notify leader (status line bell) ---------------------------------------


def notify_leader_status(session_name: str, message: str) -> bool:
    """Emit a tmux ``display-message`` to the session.

    Mirrors TS ``notifyLeaderStatus`` (tmux-session.ts:1717-1724).
    """
    if not is_tmux_available():
        return False
    trimmed = message.strip()
    if not trimmed:
        return False
    capped = trimmed[:177] + "..." if len(trimmed) > 180 else trimmed
    return _run_tmux(["display-message", "-t", session_name, "--", capped]).ok


# --- Mouse scrolling ---------------------------------------------------------


def enable_mouse_scrolling(session_target: str) -> bool:
    """Enable tmux mouse mode on a session, with copy-mode underline mitigation.

    Mirrors TS ``enableMouseScrolling`` (tmux-session.ts:1286-1299).
    """
    set_result = _run_tmux(["set-option", "-t", session_target, "mouse", "on"])
    if not set_result.ok:
        return False
    _run_tmux(["set-option", "-t", session_target, "set-clipboard", "on"])
    mitigate_copy_mode_underline_artifacts(session_target)
    return True


# --- Session lifecycle -------------------------------------------------------


def kill_team_session(session_name: str) -> None:
    """Kill a tmux team session. Tolerates already-dead sessions."""
    _run_tmux(["kill-session", "-t", session_name])


def is_session_alive(session_name: str) -> bool:
    """Check if a tmux session exists."""
    return _run_tmux(["has-session", "-t", session_name]).ok


def destroy_team_session(session_name: str) -> None:
    """Mirror TS ``destroyTeamSession`` (tmux-session.ts:1958-1964).

    Alias of :func:`kill_team_session`.
    """
    try:
        _run_tmux(["kill-session", "-t", session_name])
    except Exception:  # noqa: BLE001 - tolerate already-dead sessions
        pass


def list_team_sessions() -> list[str]:
    """List tmux session names, stripped to base form.

    Mirrors TS ``listTeamSessions`` (tmux-session.ts:1967-1976).
    """
    result = _run_tmux(["list-sessions", "-F", "#{session_name}"])
    if not result.ok:
        return []
    return [
        _base_session_name(line.strip())
        for line in result.stdout.splitlines()
        if line.strip()
    ]


@dataclass
class _WorkerStartup:
    cwd: str | None = None
    env: dict[str, str] | None = None
    initial_prompt: str | None = None
    launch_args: list[str] | None = None
    worker_cli: TeamWorkerCli | None = None
    worker_role: str | None = None


def _coerce_worker_startup(raw: dict | _WorkerStartup | None) -> _WorkerStartup:
    if isinstance(raw, _WorkerStartup):
        return raw
    if raw is None:
        return _WorkerStartup()
    return _WorkerStartup(
        cwd=raw.get("cwd"),
        env=raw.get("env"),
        initial_prompt=raw.get("initial_prompt") or raw.get("initialPrompt"),
        launch_args=raw.get("launch_args") or raw.get("launchArgs"),
        worker_cli=raw.get("worker_cli") or raw.get("workerCli"),
        worker_role=raw.get("worker_role") or raw.get("workerRole"),
    )


def create_team_session(
    team_name: str,
    worker_count: int,
    cwd: str,
    worker_launch_args: list[str] | None = None,
    worker_startups: list[dict | _WorkerStartup] | None = None,
) -> TeamSession:
    """Create a tmux team session by splitting the caller's leader window.

    Mirrors TS ``createTeamSession`` (tmux-session.ts:1003-1233).

    Requirements / behavior:

    * Requires :func:`is_tmux_available` and :func:`has_current_tmux_client_context`.
    * Splits leader → workers → HUD; rolls back panes + hooks on failure.
    * Returns a :class:`TeamSession` with ``name`` in ``"session:window"``
      form and per-worker pane ids.

    The Python port simplifies a few branches relative to TS:

    * No nested HUD process spawn for now — the HUD pane is attempted but
      the inner command is left as a no-op when ``OMX_HUD_COMMAND`` is
      unset. Resize hooks are still registered/rolled back identically.
    """
    if not is_tmux_available():
        raise RuntimeError("tmux is not available")
    if not isinstance(worker_count, int) or worker_count < 1:
        raise ValueError(f"workerCount must be >= 1 (got {worker_count})")
    if not has_current_tmux_client_context():
        raise RuntimeError("team mode requires running inside tmux leader pane")

    eff_worker_launch_args = list(worker_launch_args or [])
    normalized_worker_launch_args = _resolve_worker_launch_args(
        eff_worker_launch_args, cwd, dict(os.environ)
    )
    default_plan = resolve_team_worker_cli_plan(
        worker_count, normalized_worker_launch_args, dict(os.environ)
    )
    startups = [_coerce_worker_startup(s) for s in (worker_startups or [])]
    if startups:
        worker_cli_plan = [
            (startups[i].worker_cli if i < len(startups) else None) or default_plan[i]
            for i in range(worker_count)
        ]
    else:
        worker_cli_plan = default_plan
    for unique_cli in set(worker_cli_plan):
        assert_team_worker_cli_binary_available(unique_cli)

    safe_team_name = sanitize_team_name(team_name)
    registered_resize_hook: tuple[str, str] | None = None
    registered_client_attached_hook: tuple[str, str] | None = None
    rollback_pane_ids: list[str] = []
    try:
        tmux_pane = os.environ.get("TMUX_PANE")
        display_args = (
            ["display-message", "-p", "-t", tmux_pane, "#S:#I #{pane_id}"]
            if tmux_pane
            else ["display-message", "-p", "#S:#I #{pane_id}"]
        )
        context = _run_tmux(display_args)
        if not context.ok:
            hint = f" (TMUX_PANE={tmux_pane})" if tmux_pane else ""
            raise RuntimeError(
                f"failed to detect current tmux target{hint}: {context.stderr}"
            )
        parts = context.stdout.split(" ")
        session_and_window = parts[0] if len(parts) > 0 else ""
        detected_leader_pane_id = parts[1] if len(parts) > 1 else ""
        sw = session_and_window.split(":") if session_and_window else []
        session_name = sw[0] if len(sw) > 0 else ""
        window_index = sw[1] if len(sw) > 1 else ""
        if (
            not session_name
            or not window_index
            or not detected_leader_pane_id
            or not detected_leader_pane_id.startswith("%")
        ):
            raise RuntimeError(f"failed to parse current tmux target: {context.stdout}")

        team_target = f"{session_name}:{window_index}"
        panes = _list_panes(team_target)
        leader_pane_id = choose_team_leader_pane_id(panes, detected_leader_pane_id)
        for hud_pane_id in _find_hud_pane_ids(team_target, leader_pane_id):
            _run_tmux(["kill-pane", "-t", hud_pane_id])

        worker_pane_ids: list[str] = []
        right_stack_root_pane_id: str | None = None
        for i in range(1, worker_count + 1):
            startup = startups[i - 1] if i - 1 < len(startups) else _WorkerStartup()
            worker_cwd = startup.cwd or cwd
            tmux_worker_cwd = translate_path_for_msys(worker_cwd)
            worker_env = startup.env or {}
            launch_args_for_worker = (
                startup.launch_args
                if startup.launch_args is not None
                else eff_worker_launch_args
            )
            cmd = build_worker_startup_command(
                safe_team_name,
                i,
                launch_args_for_worker,
                worker_cwd,
                worker_env,
                worker_cli_plan[i - 1],
                startup.initial_prompt,
                startup.worker_role,
            )
            split_direction = "-h" if i == 1 else "-v"
            split_target = (
                leader_pane_id
                if i == 1
                else (right_stack_root_pane_id or leader_pane_id)
            )
            split = _run_tmux(
                [
                    "split-window",
                    split_direction,
                    "-t",
                    split_target,
                    "-d",
                    "-P",
                    "-F",
                    "#{pane_id}",
                    "-c",
                    tmux_worker_cwd,
                    cmd,
                ]
            )
            if not split.ok:
                raise RuntimeError(f"failed to create worker pane {i}: {split.stderr}")
            pane_id = split.stdout.splitlines()[0].strip() if split.stdout else ""
            if not pane_id or not pane_id.startswith("%"):
                raise RuntimeError(f"failed to capture worker pane id for worker {i}")
            rollback_pane_ids.append(pane_id)
            if is_native_windows() and not _wait_for_pane_to_remain_present(
                team_target, pane_id
            ):
                raise RuntimeError(
                    f"worker pane {i} did not remain present after tmux split-window returned {pane_id}"
                )
            worker_pane_ids.append(pane_id)
            if i == 1:
                right_stack_root_pane_id = pane_id

        _run_tmux(["select-layout", "-t", team_target, "main-vertical"])

        # Force leader pane to use half the window width.
        width_result = _run_tmux(
            ["display-message", "-p", "-t", team_target, "#{window_width}"]
        )
        if width_result.ok:
            try:
                width = int((width_result.stdout.splitlines() or [""])[0].strip())
                if width >= 40:
                    half = str(width // 2)
                    _run_tmux(
                        [
                            "set-window-option",
                            "-t",
                            team_target,
                            "main-pane-width",
                            half,
                        ]
                    )
                    _run_tmux(["select-layout", "-t", team_target, "main-vertical"])
            except ValueError:
                pass

        # HUD pane is optional and depends on the omx CLI entry being known.
        # Without resolveOmxCliEntryPath ported, we treat HUD as best-effort
        # via an env override (OMX_HUD_COMMAND).
        hud_pane_id: str | None = None
        resize_hook_name: str | None = None
        resize_hook_target: str | None = None
        hud_command = os.environ.get("OMX_HUD_COMMAND")
        if hud_command:
            hud_result = _run_tmux(
                [
                    "split-window",
                    "-v",
                    "-f",
                    "-l",
                    str(HUD_TMUX_TEAM_HEIGHT_LINES),
                    "-t",
                    team_target,
                    "-d",
                    "-P",
                    "-F",
                    "#{pane_id}",
                    "-c",
                    translate_path_for_msys(cwd),
                    hud_command,
                ]
            )
            if hud_result.ok:
                hud_id = (
                    hud_result.stdout.splitlines()[0].strip()
                    if hud_result.stdout
                    else ""
                )
                if hud_id.startswith("%"):
                    rollback_pane_ids.append(hud_id)
                    if is_native_windows() and not _wait_for_pane_to_remain_present(
                        team_target, hud_id
                    ):
                        raise RuntimeError(
                            f"HUD pane did not remain present after tmux split-window returned {hud_id}"
                        )
                    hud_pane_id = hud_id
                    if is_native_windows():
                        reconcile = _run_tmux(_build_hud_resize_args(hud_pane_id))
                        if not reconcile.ok:
                            raise RuntimeError(
                                f"failed to reconcile HUD resize: {reconcile.stderr}"
                            )
                    else:
                        resize_hook_target = build_resize_hook_target(
                            session_name, window_index
                        )
                        resize_hook_name = build_resize_hook_name(
                            safe_team_name, session_name, window_index, hud_pane_id
                        )
                        register_hook = _run_tmux(
                            build_register_resize_hook_args(
                                resize_hook_target, resize_hook_name, hud_pane_id
                            )
                        )
                        if not register_hook.ok:
                            raise RuntimeError(
                                f"failed to register resize hook {resize_hook_name}: "
                                f"{register_hook.stderr}"
                            )
                        registered_resize_hook = (
                            resize_hook_name,
                            resize_hook_target,
                        )
                        client_attached_hook_name = (
                            build_client_attached_reconcile_hook_name(
                                safe_team_name,
                                session_name,
                                window_index,
                                hud_pane_id,
                            )
                        )
                        register_attached = _run_tmux(
                            build_register_client_attached_reconcile_args(
                                resize_hook_target,
                                client_attached_hook_name,
                                hud_pane_id,
                            )
                        )
                        if not register_attached.ok:
                            raise RuntimeError(
                                f"failed to register client-attached reconcile hook "
                                f"{client_attached_hook_name}: {register_attached.stderr}"
                            )
                        registered_client_attached_hook = (
                            client_attached_hook_name,
                            resize_hook_target,
                        )
                        delayed = _run_tmux(
                            build_schedule_delayed_hud_resize_args(hud_pane_id)
                        )
                        if not delayed.ok:
                            raise RuntimeError(
                                f"failed to schedule delayed HUD resize: {delayed.stderr}"
                            )
                        reconcile = _run_tmux(
                            build_reconcile_hud_resize_args(hud_pane_id)
                        )
                        if not reconcile.ok:
                            raise RuntimeError(
                                f"failed to reconcile HUD resize: {reconcile.stderr}"
                            )

        _run_tmux(["select-pane", "-t", leader_pane_id])
        _sleep_seconds(0.5)
        if os.environ.get("OMX_TEAM_MOUSE") != "0":
            enable_mouse_scrolling(session_name)

        return TeamSession(
            name=team_target,
            worker_count=worker_count,
            cwd=cwd,
            worker_pane_ids=worker_pane_ids,
            leader_pane_id=leader_pane_id,
            hud_pane_id=hud_pane_id,
            resize_hook_name=resize_hook_name,
            resize_hook_target=resize_hook_target,
        )
    except Exception:
        if registered_client_attached_hook is not None:
            name, target_ = registered_client_attached_hook
            _run_tmux(build_unregister_client_attached_reconcile_args(target_, name))
        if registered_resize_hook is not None:
            name, target_ = registered_resize_hook
            _run_tmux(build_unregister_resize_hook_args(target_, name))
        for pane_id in rollback_pane_ids:
            _run_tmux(["kill-pane", "-t", pane_id])
        raise


def restore_standalone_hud_pane(leader_pane_id: str | None, cwd: str) -> str | None:
    """Re-create a standalone HUD pane below the leader.

    Mirrors TS ``restoreStandaloneHudPane`` (tmux-session.ts:1235-1275).

    Returns the created HUD pane id, or ``None`` if creation failed or no
    HUD command is available. Like the TS version, the HUD command is
    discovered from the environment (here: ``OMX_HUD_COMMAND``) since the
    ``resolveOmxCliEntryPath`` helper is not yet ported.
    """
    normalized = leader_pane_id.strip() if isinstance(leader_pane_id, str) else ""
    if not normalized or not normalized.startswith("%"):
        return None
    hud_command = os.environ.get("OMX_HUD_COMMAND")
    if not hud_command:
        return None

    result = _run_tmux(
        [
            "split-window",
            "-v",
            "-l",
            str(HUD_TMUX_TEAM_HEIGHT_LINES),
            "-t",
            normalized,
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-c",
            translate_path_for_msys(cwd),
            hud_command,
        ]
    )
    if not result.ok:
        return None
    pane_id = result.stdout.splitlines()[0].strip() if result.stdout else ""
    if not pane_id.startswith("%"):
        return None
    if is_native_windows():
        _run_tmux(_build_hud_resize_args(pane_id))
    else:
        _run_tmux(build_schedule_delayed_hud_resize_args(pane_id))
        _run_tmux(build_reconcile_hud_resize_args(pane_id))
    _run_tmux(["select-pane", "-t", normalized])
    return pane_id


__all__ = [
    # constants
    "CLAUDE_SKIP_PERMISSIONS_FLAG",
    "GEMINI_APPROVAL_MODE_FLAG",
    "GEMINI_APPROVAL_MODE_YOLO",
    "GEMINI_PROMPT_INTERACTIVE_FLAG",
    "HUD_RESIZE_RECONCILE_DELAY_SECONDS",
    "HUD_TMUX_TEAM_HEIGHT_LINES",
    "INJECTION_MARKER",
    "MODEL_INSTRUCTIONS_FILE_KEY",
    "OMX_BYPASS_DEFAULT_SYSTEM_PROMPT_ENV",
    "OMX_LEADER_CLI_PATH_ENV",
    "OMX_LEADER_NODE_PATH_ENV",
    "OMX_MODEL_INSTRUCTIONS_FILE_ENV",
    "OMX_TEAM_AUTO_INTERRUPT_RETRY_ENV",
    "OMX_TEAM_WORKER_CLI_ENV",
    "OMX_TEAM_WORKER_CLI_MAP_ENV",
    "OMX_TEAM_WORKER_LAUNCH_MODE_ENV",
    "TMUX_COPY_MODE_STYLE_OPTIONS",
    "TMUX_HOOK_INDEX_MAX",
    "TMUX_NO_UNDERLINE_STYLE_FLAGS",
    "TMUX_PANE_STABILITY_POLL_MS",
    "TMUX_PANE_STABILITY_POLLS_REQUIRED",
    "TMUX_PANE_STABILITY_TIMEOUT_MS",
    "TMUX_WORKER_AMBIENT_ENV_ALLOWLIST",
    # types
    "TeamSession",
    "TeamWorkerCli",
    "TeamWorkerCliMode",
    "TeamWorkerLaunchMode",
    "TmuxPaneInfo",
    "WorkerProcessLaunchSpec",
    "WorkerSubmitPlan",
    # platform detection
    "has_current_tmux_client_context",
    "is_msys_or_git_bash",
    "is_native_windows",
    "is_tmux_available",
    "is_wsl2",
    "translate_path_for_msys",
    # sanitization + sleep
    "sanitize_team_name",
    "sleep_fractional_seconds",
    # hud / resize hooks
    "build_client_attached_reconcile_hook_name",
    "build_hud_pane_target",
    "build_reconcile_hud_resize_args",
    "build_register_client_attached_reconcile_args",
    "build_register_resize_hook_args",
    "build_resize_hook_name",
    "build_resize_hook_target",
    "build_schedule_delayed_hud_resize_args",
    "build_unregister_client_attached_reconcile_args",
    "build_unregister_resize_hook_args",
    "unregister_resize_hook",
    # copy-mode mitigation
    "mitigate_copy_mode_underline_artifacts",
    "enable_mouse_scrolling",
    # cli resolution
    "assert_team_worker_cli_binary_available",
    "resolve_team_worker_cli",
    "resolve_team_worker_cli_plan",
    "resolve_team_worker_launch_mode",
    "resolve_worker_cli_for_send",
    "translate_worker_launch_args_for_cli",
    # worker launch
    "build_worker_process_launch_spec",
    "build_worker_startup_command",
    # session lifecycle
    "capture_pane",
    "capture_visible_pane",
    "create_team_session",
    "destroy_team_session",
    "is_session_alive",
    "kill_team_session",
    "list_pane_ids",
    "list_team_sessions",
    "choose_team_leader_pane_id",
    "restore_standalone_hud_pane",
    # worker process / liveness
    "dismiss_trust_prompt_if_present",
    "get_worker_pane_pid",
    "is_worker_alive",
    "is_worker_pane_open",
    "kill_worker",
    "kill_worker_by_pane_id",
    "wait_for_worker_ready",
    # submit
    "build_worker_submit_plan",
    "send_to_worker",
    "send_to_worker_stdin",
    "should_attempt_adaptive_retry",
    "normalize_tmux_capture",
    "notify_leader_status",
]
