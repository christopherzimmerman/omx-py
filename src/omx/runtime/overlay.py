"""Overlay builder — compose model instructions for Codex sessions.

Port of src/runtime/overlay.ts.
Builds a composite instructions file from AGENTS.md, active modes,
project memory, notepad priorities, wiki context, and runtime env.
"""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from omx.utils.paths import (
    omx_notepad_path,
    omx_project_memory_path,
    omx_state_dir,
    omx_wiki_dir,
)


def build_session_instructions(cwd: str, session_id: str) -> str:
    """Build composite model instructions from all OMX sources.

    Composes context from AGENTS.md, active mode states, project memory,
    priority notes, wiki context, and runtime environment description.
    Writes the result to the session's model-instructions.md file.

    Args:
        cwd: Working directory for the project.
        session_id: Unique session identifier.

    Returns:
        Filesystem path to the written model-instructions.md file.
    """
    sections: list[str] = []

    # 1. AGENTS.md from cwd
    agents_md = Path(cwd) / "AGENTS.md"
    if agents_md.exists():
        try:
            content = agents_md.read_text(encoding="utf-8").strip()
            if content:
                sections.append(f"# Project Agent Instructions\n\n{content}")
        except OSError:
            pass

    # 2. Active mode states
    mode_summary = _summarize_active_modes(cwd)
    if mode_summary:
        sections.append(f"# Active Workflow Modes\n\n{mode_summary}")

    # 3. Project memory
    memory_summary = _read_project_memory(cwd)
    if memory_summary:
        sections.append(f"# Project Memory\n\n{memory_summary}")

    # 4. Priority notes from notepad
    priority_notes = _read_priority_notes(cwd)
    if priority_notes:
        sections.append(f"# Priority Notes\n\n{priority_notes}")

    # 5. Wiki context summary
    wiki_summary = _summarize_wiki(cwd)
    if wiki_summary:
        sections.append(f"# Wiki Context\n\n{wiki_summary}")

    # 6. Runtime environment
    env_desc = _describe_runtime_environment(session_id)
    sections.append(f"# Runtime Environment\n\n{env_desc}")

    composed = "\n\n---\n\n".join(sections) + "\n"

    output_path = write_session_model_instructions(cwd, session_id, composed)
    return str(output_path)


def write_session_model_instructions(cwd: str, session_id: str, content: str) -> Path:
    """Write model instructions content to the session directory.

    Args:
        cwd: Working directory for the project.
        session_id: Unique session identifier.
        content: The instructions content to write.

    Returns:
        Path to the written file.
    """
    session_dir = Path(cwd) / ".omx" / "state" / "sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    output_path = session_dir / "model-instructions.md"
    output_path.write_text(content, encoding="utf-8")
    return output_path


def _summarize_active_modes(cwd: str) -> str:
    """Read .omx/state/ and summarize active modes and their phase."""
    state_dir = omx_state_dir(Path(cwd))
    if not state_dir.exists():
        return ""

    lines: list[str] = []
    for state_file in sorted(state_dir.glob("*-state.json")):
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                continue
            if not data.get("active", False):
                continue
            mode_name = state_file.stem.replace("-state", "")
            phase = data.get("current_phase", data.get("phase", "active"))
            lines.append(f"- **{mode_name}**: phase={phase}")
        except (json.JSONDecodeError, OSError):
            continue

    return "\n".join(lines)


def _read_project_memory(cwd: str) -> str:
    """Read project memory and format notes/directives."""
    memory_path = omx_project_memory_path(Path(cwd))
    if not memory_path.exists():
        return ""

    try:
        data = json.loads(memory_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ""
    except (json.JSONDecodeError, OSError):
        return ""

    lines: list[str] = []

    notes = data.get("notes", [])
    if isinstance(notes, list):
        for note in notes:
            if isinstance(note, str):
                lines.append(f"- {note}")
            elif isinstance(note, dict) and "text" in note:
                lines.append(f"- {note['text']}")

    directives = data.get("directives", [])
    if isinstance(directives, list):
        for directive in directives:
            if isinstance(directive, str):
                lines.append(f"- [directive] {directive}")
            elif isinstance(directive, dict) and "text" in directive:
                lines.append(f"- [directive] {directive['text']}")

    return "\n".join(lines)


def _read_priority_notes(cwd: str) -> str:
    """Extract the PRIORITY section from notepad.md."""
    notepad_path = omx_notepad_path(Path(cwd))
    if not notepad_path.exists():
        return ""

    try:
        content = notepad_path.read_text(encoding="utf-8")
    except OSError:
        return ""

    # Find PRIORITY section
    in_priority = False
    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("# PRIORITY") or stripped.upper().startswith(
            "## PRIORITY"
        ):
            in_priority = True
            continue
        if in_priority:
            # Stop at next heading
            if stripped.startswith("#") and not stripped.upper().startswith(
                "# PRIORITY"
            ):
                break
            if stripped:
                lines.append(stripped)

    return "\n".join(lines)


def _summarize_wiki(cwd: str) -> str:
    """List wiki page titles if wiki directory exists."""
    wiki_dir = omx_wiki_dir(Path(cwd))
    if not wiki_dir.exists():
        return ""

    pages: list[str] = []
    for md_file in sorted(wiki_dir.glob("*.md")):
        pages.append(f"- {md_file.stem}")

    if not pages:
        return ""
    return "Available wiki pages:\n" + "\n".join(pages)


def _describe_runtime_environment(session_id: str) -> str:
    """Describe the runtime environment."""
    lines = [
        f"- Platform: {sys.platform} ({platform.machine()})",
        f"- Python: {platform.python_version()}",
        f"- Session ID: {session_id}",
        f"- Timestamp: {datetime.now(timezone.utc).isoformat()}",
    ]

    # tmux detection
    if os.environ.get("TMUX"):
        lines.append("- Tmux: active")
    else:
        lines.append("- Tmux: not detected")

    return "\n".join(lines)
