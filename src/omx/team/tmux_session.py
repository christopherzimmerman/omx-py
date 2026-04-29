"""Tmux session management for team workers.

Port of src/team/tmux-session.ts.
Handles session creation, worker spawning, send-keys injection,
trust prompt dismissal, and worker readiness detection.
"""

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass, field

from omx.utils.platform import run_command


@dataclass
class TeamSession:
    """Describes an active tmux team session.

    Attributes:
        name: Tmux session name.
        worker_count: Number of worker panes created.
        cwd: Working directory for the session.
        worker_pane_ids: List of worker pane target strings.
        leader_pane_id: Leader pane target string.
        hud_pane_id: Optional HUD pane target string.
    """

    name: str
    worker_count: int
    cwd: str
    worker_pane_ids: list[str] = field(default_factory=list)
    leader_pane_id: str = ""
    hud_pane_id: str | None = None


def create_team_session(
    session_name: str,
    worker_count: int,
    cwd: str,
    *,
    worker_cli: str = "codex",
    model: str | None = None,
    team_name: str | None = None,
) -> TeamSession:
    """Create a tmux session, spawn CLI workers in each pane.

    Args:
        session_name: Name for the new tmux session.
        worker_count: Number of worker panes to create.
        cwd: Working directory for all panes.
        worker_cli: CLI tool to launch in worker panes ("codex", "claude", "gemini").
        model: Optional model override for workers.
        team_name: Logical team name for state directories.

    Returns:
        TeamSession describing the created session layout.
    """
    effective_team = team_name or session_name

    # Create new detached session
    run_command(["tmux", "new-session", "-d", "-s", session_name, "-c", cwd])

    # Leader is the initial pane
    leader_result = run_command(
        [
            "tmux",
            "list-panes",
            "-t",
            session_name,
            "-F",
            "#{session_name}:#{window_index}.#{pane_index}",
        ]
    )
    leader_pane_id = leader_result.stdout.strip().splitlines()[0]

    # Create worker panes with CLI running inside
    worker_pane_ids: list[str] = []
    for i in range(worker_count):
        worker_name = f"worker-{i + 1}"
        startup_cmd = _build_worker_command(
            worker_cli=worker_cli,
            worker_name=worker_name,
            team_name=effective_team,
            cwd=cwd,
            model=model,
        )
        # Split a new pane running the worker command
        result = run_command(
            [
                "tmux",
                "split-window",
                "-t",
                session_name,
                "-c",
                cwd,
                "-d",  # don't switch focus
                "-P",
                "-F",
                "#{pane_id}",  # print new pane ID
                startup_cmd,
            ]
        )
        pane_id = result.stdout.strip()
        if pane_id:
            worker_pane_ids.append(pane_id)

        run_command(["tmux", "select-layout", "-t", session_name, "tiled"])

    # If pane IDs weren't captured via -P, fall back to listing
    if len(worker_pane_ids) < worker_count:
        panes_result = run_command(
            [
                "tmux",
                "list-panes",
                "-t",
                session_name,
                "-F",
                "#{session_name}:#{window_index}.#{pane_index}",
            ]
        )
        all_panes = [
            p.strip() for p in panes_result.stdout.strip().splitlines() if p.strip()
        ]
        worker_pane_ids = [p for p in all_panes if p != leader_pane_id]

    return TeamSession(
        name=session_name,
        worker_count=worker_count,
        cwd=cwd,
        worker_pane_ids=worker_pane_ids,
        leader_pane_id=leader_pane_id,
    )


def _build_worker_command(
    worker_cli: str,
    worker_name: str,
    team_name: str,
    cwd: str,
    model: str | None = None,
) -> str:
    """Build the shell command to launch a CLI worker in a tmux pane."""
    cli_bin = shutil.which(worker_cli)
    if not cli_bin:
        # Fall back to just the name and hope PATH has it
        cli_bin = worker_cli

    env_vars = (
        f"OMX_TEAM_WORKER={team_name}/{worker_name} OMX_TEAM_WORKER_CLI={worker_cli} "
    )

    if model:
        return f"{env_vars}{cli_bin} --model {model}"
    return f"{env_vars}{cli_bin}"


def wait_for_worker_ready(
    pane_id: str,
    timeout_ms: int = 30_000,
    auto_trust: bool = True,
) -> bool:
    """Poll a worker pane until it shows a ready prompt.

    Args:
        pane_id: Tmux pane target string.
        timeout_ms: Maximum wait time in milliseconds.
        auto_trust: Whether to auto-dismiss trust prompts.

    Returns:
        True if the worker became ready within the timeout.
    """
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    delay = 0.15  # start at 150ms

    while time.monotonic() < deadline:
        captured = capture_pane(pane_id, lines=20)

        # Check for trust prompt and dismiss it
        if auto_trust and _pane_has_trust_prompt(captured):
            _dismiss_trust_prompt(pane_id)
            delay = 0.15  # reset backoff after dismissal
            time.sleep(delay)
            continue

        # Check for Claude bypass permissions prompt
        if auto_trust and _pane_has_bypass_prompt(captured):
            _accept_bypass_prompt(pane_id)
            delay = 0.15
            time.sleep(delay)
            continue

        # Check if pane looks ready (showing a prompt)
        if _pane_looks_ready(captured):
            return True

        time.sleep(delay)
        delay = min(delay * 2, 8.0)  # exponential backoff, max 8s

    return False


def send_to_worker(
    pane_id: str,
    text: str,
    worker_cli: str = "codex",
) -> bool:
    """Send text to a worker pane via tmux send-keys.

    Handles trust prompt dismissal, literal text injection, and
    multi-round Enter key submission.

    Args:
        pane_id: Tmux pane target string.
        text: The trigger text to send.
        worker_cli: CLI type for submit key behavior.

    Returns:
        True if text was likely consumed by the worker.
    """
    # Dismiss any trust prompts first
    captured = capture_pane(pane_id, lines=12)
    if _pane_has_trust_prompt(captured):
        _dismiss_trust_prompt(pane_id)
        time.sleep(0.3)
    if _pane_has_bypass_prompt(captured):
        _accept_bypass_prompt(pane_id)
        time.sleep(0.3)

    # Send literal text
    run_command(["tmux", "send-keys", "-t", pane_id, "-l", "--", text], check=False)
    time.sleep(0.1)

    # Submit with Enter key presses
    presses = 1 if worker_cli == "claude" else 2
    rounds = 6

    for rnd in range(rounds):
        for press in range(presses):
            run_command(["tmux", "send-keys", "-t", pane_id, "C-m"], check=False)
            if press < presses - 1:
                time.sleep(0.2)
        time.sleep(0.14)

        # Check if text was consumed
        post_capture = capture_pane(pane_id, lines=12)
        if _pane_has_active_task(post_capture) or text not in post_capture:
            return True

    return False


def capture_pane(pane_id: str, lines: int = 80) -> str:
    """Capture the visible content of a tmux pane.

    Args:
        pane_id: Tmux pane target string.
        lines: Number of lines to capture from the bottom.

    Returns:
        Captured pane text.
    """
    result = run_command(
        [
            "tmux",
            "capture-pane",
            "-t",
            pane_id,
            "-p",
            "-S",
            f"-{lines}",
        ],
        check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def kill_team_session(session_name: str) -> None:
    """Kill a tmux team session."""
    run_command(["tmux", "kill-session", "-t", session_name], check=False)


def is_session_alive(session_name: str) -> bool:
    """Check if a tmux session exists."""
    result = run_command(["tmux", "has-session", "-t", session_name], check=False)
    return result.returncode == 0


# --- Trust prompt detection & dismissal ---


def _pane_has_trust_prompt(captured: str) -> bool:
    """Detect Codex 'Do you trust this directory?' prompt."""
    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    tail = lines[-12:] if len(lines) > 12 else lines
    text = "\n".join(tail)
    has_question = bool(
        re.search(r"Do you trust the contents of this directory\?", text, re.IGNORECASE)
    )
    has_choices = bool(
        re.search(
            r"Yes,\s*continue|No,\s*quit|Press enter to continue", text, re.IGNORECASE
        )
    )
    return has_question and has_choices


def _dismiss_trust_prompt(pane_id: str) -> None:
    """Auto-dismiss trust prompt with Enter presses."""
    run_command(["tmux", "send-keys", "-t", pane_id, "C-m"], check=False)
    time.sleep(0.12)
    run_command(["tmux", "send-keys", "-t", pane_id, "C-m"], check=False)


def _pane_has_bypass_prompt(captured: str) -> bool:
    """Detect Claude 'Bypass Permissions mode' prompt."""
    return "Bypass Permissions mode" in captured and (
        "Yes, I accept" in captured or "Enter to confirm" in captured
    )


def _accept_bypass_prompt(pane_id: str) -> None:
    """Auto-accept Claude bypass permissions prompt."""
    run_command(["tmux", "send-keys", "-t", pane_id, "-l", "--", "2"], check=False)
    time.sleep(0.12)
    run_command(["tmux", "send-keys", "-t", pane_id, "C-m"], check=False)


def _pane_looks_ready(captured: str) -> bool:
    """Check if pane shows a CLI prompt (ready for input)."""
    lines = [ln.strip() for ln in captured.splitlines() if ln.strip()]
    if not lines:
        return False
    tail = "\n".join(lines[-5:])
    # Common ready indicators
    return bool(
        re.search(r"[>$#%]\s*$|What can I help|How can I help|Enter a prompt", tail)
    )


def _pane_has_active_task(captured: str) -> bool:
    """Check if the pane shows an active task being processed."""
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
