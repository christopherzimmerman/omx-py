"""``omx star-prompt`` — one-time GitHub star prompt.

Port of ``src/cli/star-prompt.ts``. Sync, stdlib-only.

State stored under ``~/.omx/state/star-prompt.json`` so the prompt fires
at most once per user. Skipped when no TTY or when ``gh`` is missing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = "Yeachan-Heo/oh-my-codex"


def star_prompt_state_path() -> Path:
    """Return the per-user star prompt state file path."""
    return Path.home() / ".omx" / "state" / "star-prompt.json"


def has_been_prompted() -> bool:
    """Return ``True`` if the star prompt has already fired for this user."""
    path = star_prompt_state_path()
    if not path.exists():
        return False
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(state.get("prompted_at"), str)
    except (json.JSONDecodeError, OSError):
        return False


def mark_prompted() -> None:
    """Write the ``prompted_at`` timestamp so we never prompt again."""
    state_dir = Path.home() / ".omx" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    star_prompt_state_path().write_text(
        json.dumps(
            {"prompted_at": datetime.now(timezone.utc).isoformat()},
            indent=2,
        ),
        encoding="utf-8",
    )


def is_gh_installed() -> bool:
    """Return ``True`` if the ``gh`` CLI is installed and runs."""
    try:
        result = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def star_repo() -> tuple[bool, str]:
    """Run ``gh api -X PUT /user/starred/<repo>``.

    Returns:
        ``(ok, message)`` — ``ok=True`` on success, otherwise an error string.
    """
    try:
        result = subprocess.run(
            ["gh", "api", "-X", "PUT", f"/user/starred/{REPO}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    if result.returncode == 0:
        return True, ""
    err = (result.stderr or "").strip() or (result.stdout or "").strip()
    return False, err or f"gh exited {result.returncode}"


def handle_star_prompt(args: list[str]) -> None:
    """Run the star prompt.

    Args:
        args: CLI tokens. Supports ``--force`` to bypass the once-per-user
            gate and ``--status`` to report state without prompting.
    """
    if "--status" in args:
        print(
            json.dumps(
                {
                    "prompted": has_been_prompted(),
                    "gh_installed": is_gh_installed(),
                    "state_path": str(star_prompt_state_path()),
                }
            )
        )
        return

    force = "--force" in args

    if not force and not (sys.stdin.isatty() and sys.stdout.isatty()):
        return
    if not force and has_been_prompted():
        return
    if not is_gh_installed():
        print(
            "[omx] gh CLI not installed — install GitHub CLI to enable the star prompt.",
            file=sys.stderr,
        )
        return

    # Mark prompted FIRST so an interrupted prompt never re-fires.
    mark_prompted()

    try:
        answer = (
            input("[omx] Enjoying oh-my-codex? Star it on GitHub? [Y/n] ")
            .strip()
            .lower()
        )
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if answer not in ("", "y", "yes"):
        return

    ok, err = star_repo()
    if ok:
        print("[omx] Thanks for the star!")
        return
    print(f"[omx] Could not star repository automatically: {err}", file=sys.stderr)
