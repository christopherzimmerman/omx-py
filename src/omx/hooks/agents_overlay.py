"""AGENTS.md runtime overlay for oh-my-codex.

Port of src/hooks/agents-overlay.ts. Dynamically injects session-specific
context into AGENTS.md using marker-bounded sections for idempotent
apply/strip cycles.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from omx.hooks.codebase_map import generate_codebase_map
from omx.state.paths import get_state_dir
from omx.utils.paths import (
    codex_home,
    omx_notepad_path,
    omx_project_memory_path,
)

START_MARKER = "<!-- OMX:RUNTIME:START -->"
END_MARKER = "<!-- OMX:RUNTIME:END -->"
WORKER_START_MARKER = "<!-- OMX:TEAM:WORKER:START -->"
WORKER_END_MARKER = "<!-- OMX:TEAM:WORKER:END -->"
MAX_OVERLAY_SIZE = 3500
MAX_STRIP_ITERATIONS = 50


# ── Lock helpers ──────────────────────────────────────────────────────────────


def _lock_path(cwd: str) -> Path:
    return Path(cwd) / ".omx" / "state" / "agents-md.lock"


def _acquire_lock(cwd: str, timeout_s: float = 5.0) -> None:
    """Acquire a directory-based lock for AGENTS.md modification.

    Args:
        cwd: Working directory.
        timeout_s: Maximum seconds to wait for the lock.

    Raises:
        TimeoutError: If the lock cannot be acquired within the timeout.
    """
    lock = _lock_path(cwd)
    lock.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()

    while time.monotonic() - start < timeout_s:
        try:
            lock.mkdir(parents=False, exist_ok=False)
            # Write owner metadata
            owner_file = lock / "owner.json"
            owner_file.write_text(
                json.dumps({"pid": os.getpid(), "ts": time.time()}),
                encoding="utf-8",
            )
            return
        except FileExistsError:
            # Lock exists, check if owner is dead
            try:
                owner_file = lock / "owner.json"
                owner_data = json.loads(owner_file.read_text(encoding="utf-8"))
                pid = owner_data.get("pid")
                if pid:
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        # Owner dead, reap lock
                        import shutil

                        shutil.rmtree(str(lock), ignore_errors=True)
                        continue
            except (OSError, json.JSONDecodeError, KeyError):
                pass
            time.sleep(0.1)

    raise TimeoutError("Failed to acquire AGENTS.md lock within timeout")


def _release_lock(cwd: str) -> None:
    """Release the AGENTS.md lock."""
    import shutil

    lock = _lock_path(cwd)
    shutil.rmtree(str(lock), ignore_errors=True)


# ── Truncation helpers ────────────────────────────────────────────────────────


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _join_sections(sections: list[dict[str, Any]]) -> str:
    return "\n\n".join(s["text"] for s in sections)


def _cap_body_to_max(sections: list[dict[str, Any]], max_body: int) -> str:
    """Cap overlay body to max size, dropping optional sections as needed.

    Args:
        sections: List of section dicts with 'text', 'optional', 'key'.
        max_body: Maximum body length in chars.

    Returns:
        Body string within the max size limit.
    """
    body = _join_sections(sections)
    if len(body) <= max_body:
        return body

    # Drop optional sections from the end
    current = list(sections)
    for i in range(len(current) - 1, -1, -1):
        if current[i].get("optional"):
            current.pop(i)
            body = _join_sections(current)
            if len(body) <= max_body:
                return body

    # Hard truncate
    if len(body) > max_body:
        if max_body <= 3:
            return "." * max(0, max_body)
        body = body[: max_body - 3] + "..."
    return body


# ── Overlay content readers ───────────────────────────────────────────────────


def _read_notepad_priority(cwd: str) -> str:
    """Read the PRIORITY section from the notepad file.

    Args:
        cwd: Working directory.

    Returns:
        Priority section content, or empty string.
    """
    note_path = omx_notepad_path(Path(cwd))
    if not note_path.exists():
        return ""
    try:
        content = note_path.read_text(encoding="utf-8")
        header = "## PRIORITY"
        idx = content.find(header)
        if idx < 0:
            return ""
        after_header = idx + len(header)
        next_header = content.find("\n## ", after_header)
        section = (
            content[after_header:next_header].strip()
            if next_header >= 0
            else content[after_header:].strip()
        )
        return section
    except OSError:
        return ""


def _read_project_memory_summary(cwd: str) -> str:
    """Read project memory summary for overlay injection.

    Args:
        cwd: Working directory.

    Returns:
        Formatted project memory summary, or empty string.
    """
    mem_path = omx_project_memory_path(Path(cwd))
    if not mem_path.exists():
        return ""
    try:
        data = json.loads(mem_path.read_text(encoding="utf-8"))
        parts: list[str] = []
        if data.get("techStack"):
            parts.append(f"- Stack: {data['techStack']}")
        if data.get("conventions"):
            parts.append(f"- Conventions: {data['conventions']}")
        if data.get("build"):
            parts.append(f"- Build: {data['build']}")
        directives = data.get("directives", [])
        if isinstance(directives, list):
            high_priority = [
                d
                for d in directives
                if isinstance(d, dict) and d.get("priority") == "high"
            ]
            for d in high_priority[:3]:
                parts.append(f"- Directive: {d.get('directive', '')}")
        return "\n".join(parts)
    except (OSError, json.JSONDecodeError):
        return ""


def _get_compaction_instructions() -> str:
    return "\n".join(
        [
            "Before context compaction, preserve critical state:",
            "1. Write progress checkpoint via state_write MCP tool",
            "2. Save key decisions to notepad via notepad_write_working",
            "3. If context is >80% full, proactively checkpoint state",
        ]
    )


# ── Overlay generation ────────────────────────────────────────────────────────


def generate_overlay(
    cwd: str,
    session_id: str | None = None,
    *,
    active_modes: str = "",
    codebase_map: str = "",
) -> str:
    """Generate the overlay content to inject into AGENTS.md.

    Total output is capped at MAX_OVERLAY_SIZE chars.

    Args:
        cwd: Working directory.
        session_id: Optional session identifier.
        active_modes: Pre-rendered active modes string.
        codebase_map: Pre-generated codebase map string.

    Returns:
        Complete overlay text with start/end markers.
    """
    if not codebase_map:
        codebase_map = generate_codebase_map(cwd)
    notepad_priority = _read_notepad_priority(cwd)
    project_memory = _read_project_memory_summary(cwd)

    sections: list[dict[str, Any]] = []

    # Session metadata
    from datetime import datetime, timezone

    session_meta = f"**Session:** {session_id or 'unknown'} | {datetime.now(timezone.utc).isoformat()}"
    sections.append(
        {"key": "session", "text": _truncate(session_meta, 200), "optional": False}
    )

    # Codebase map
    if codebase_map:
        sections.append(
            {
                "key": "codebase_map",
                "text": f"**Codebase Map:**\n{_truncate(codebase_map, 1000)}",
                "optional": True,
            }
        )

    # Active modes
    if active_modes:
        sections.append(
            {
                "key": "active_modes",
                "text": f"**Active Modes:**\n{_truncate(active_modes, 600)}",
                "optional": True,
            }
        )

    # Priority notepad
    if notepad_priority:
        sections.append(
            {
                "key": "priority_notes",
                "text": f"**Priority Notes:**\n{_truncate(notepad_priority, 600)}",
                "optional": True,
            }
        )

    # Project memory
    if project_memory:
        sections.append(
            {
                "key": "project_context",
                "text": f"**Project Context:**\n{_truncate(project_memory, 1000)}",
                "optional": True,
            }
        )

    # Compaction protocol
    sections.append(
        {
            "key": "compaction",
            "text": f"**Compaction Protocol:**\n{_truncate(_get_compaction_instructions(), 380)}",
            "optional": False,
        }
    )

    prefix = f"{START_MARKER}\n<session_context>\n"
    suffix = f"\n</session_context>\n{END_MARKER}"
    max_body = max(0, MAX_OVERLAY_SIZE - len(prefix) - len(suffix))
    body = _cap_body_to_max(sections, max_body)

    overlay = f"{prefix}{body}{suffix}"
    if len(overlay) <= MAX_OVERLAY_SIZE:
        return overlay

    # Fallback: only session + compaction
    safe_sections = [
        {"key": "session", "text": _truncate(session_meta, 200), "optional": False},
        {
            "key": "compaction",
            "text": f"**Compaction Protocol:**\n{_truncate(_get_compaction_instructions(), 380)}",
            "optional": False,
        },
    ]
    safe_body = _cap_body_to_max(safe_sections, max_body)
    return f"{prefix}{safe_body}{suffix}"[:MAX_OVERLAY_SIZE]


# ── Apply/strip operations ────────────────────────────────────────────────────


def strip_overlay_content(content: str) -> str:
    """Remove overlay markers and content from a string (pure function).

    Args:
        content: File content potentially containing overlay markers.

    Returns:
        Content with all overlay segments removed.
    """
    result = content
    iterations = 0

    while iterations < MAX_STRIP_ITERATIONS:
        start_idx = result.find(START_MARKER)
        if start_idx < 0:
            break

        end_idx = result.find(END_MARKER, start_idx)
        if end_idx < 0:
            # Malformed block — find next known marker
            candidates = [
                result.find(START_MARKER, start_idx + len(START_MARKER)),
                result.find(WORKER_START_MARKER, start_idx + len(START_MARKER)),
                result.find(WORKER_END_MARKER, start_idx + len(START_MARKER)),
            ]
            valid = [c for c in candidates if c >= 0]
            if not valid:
                result = result[:start_idx].rstrip() + "\n"
                break
            next_marker = min(valid)
            before = result[:start_idx].rstrip()
            after = result[next_marker:].lstrip()
            result = f"{before}\n{after}" if after else f"{before}\n"
            iterations += 1
            continue

        before = result[:start_idx].rstrip()
        after = result[end_idx + len(END_MARKER) :].lstrip()
        result = f"{before}\n{after}" if after else f"{before}\n"
        iterations += 1

    return result


def has_overlay(content: str) -> bool:
    """Check if content contains an overlay.

    Args:
        content: File content to check.

    Returns:
        True if overlay markers are present.
    """
    return START_MARKER in content and END_MARKER in content


def apply_overlay(agents_md_path: str, overlay: str, cwd: str | None = None) -> None:
    """Apply overlay to AGENTS.md with file locking.

    Strips any existing overlay first (idempotent).

    Args:
        agents_md_path: Path to AGENTS.md file.
        overlay: Overlay content to inject.
        cwd: Working directory for lock resolution.
    """
    path = Path(agents_md_path)
    lock_dir = cwd or str(path.parent)

    _acquire_lock(lock_dir)
    try:
        content = ""
        if path.exists():
            content = path.read_text(encoding="utf-8")

        content = strip_overlay_content(content)
        content = content.rstrip() + "\n\n" + overlay + "\n"

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    finally:
        _release_lock(lock_dir)


def strip_overlay(agents_md_path: str, cwd: str | None = None) -> None:
    """Strip overlay from AGENTS.md with file locking.

    Args:
        agents_md_path: Path to AGENTS.md file.
        cwd: Working directory for lock resolution.
    """
    path = Path(agents_md_path)
    if not path.exists():
        return

    lock_dir = cwd or str(path.parent)

    _acquire_lock(lock_dir)
    try:
        content = path.read_text(encoding="utf-8")
        stripped = strip_overlay_content(content)
        if stripped != content:
            path.write_text(stripped, encoding="utf-8")
    finally:
        _release_lock(lock_dir)


def session_model_instructions_path(cwd: str, session_id: str) -> Path:
    """Get path for session-scoped AGENTS.md.

    Args:
        cwd: Working directory.
        session_id: Session identifier.

    Returns:
        Path to session-scoped AGENTS.md.
    """
    return get_state_dir(cwd, session_id) / "AGENTS.md"


def write_session_model_instructions_file(
    cwd: str,
    session_id: str,
    overlay: str,
) -> Path:
    """Build and write a session-scoped AGENTS.md.

    Combines user-level and project-level instructions with the
    runtime overlay into a single session-scoped file.

    Args:
        cwd: Working directory.
        session_id: Session identifier.
        overlay: Generated overlay content.

    Returns:
        Path to the written session instructions file.
    """
    session_path = session_model_instructions_path(cwd, session_id)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    base_parts: list[str] = []
    source_paths = [codex_home() / "AGENTS.md", Path(cwd) / "AGENTS.md"]
    seen: set[str] = set()

    for source in source_paths:
        key = str(source.resolve())
        if key in seen or not source.exists():
            continue
        seen.add(key)
        content = source.read_text(encoding="utf-8")
        content = strip_overlay_content(content).strip()
        if content:
            base_parts.append(content)

    base = "\n\n".join(base_parts)
    composed = f"{base}\n\n{overlay}\n" if base.strip() else f"{overlay}\n"
    session_path.write_text(composed, encoding="utf-8")
    return session_path


def remove_session_model_instructions_file(cwd: str, session_id: str) -> None:
    """Remove session-scoped model instructions file.

    Args:
        cwd: Working directory.
        session_id: Session identifier.
    """
    session_path = session_model_instructions_path(cwd, session_id)
    if session_path.exists():
        session_path.unlink(missing_ok=True)
