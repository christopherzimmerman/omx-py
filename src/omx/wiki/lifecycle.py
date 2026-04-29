"""Wiki lifecycle integration.

Port of src/wiki/lifecycle.ts.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.utils.paths import codex_home, omx_project_memory_path
from omx.wiki.storage import (
    append_log_unsafe,
    get_wiki_dir,
    list_pages,
    read_all_pages,
    read_index,
    read_page,
    update_index_unsafe,
    with_wiki_lock,
    write_page_unsafe,
)
from omx.wiki.types import (
    DEFAULT_WIKI_CONFIG,
    WIKI_SCHEMA_VERSION,
    WikiConfig,
    WikiLogEntry,
    WikiPageFrontmatter,
    WikiPage,
)


def _load_wiki_config(root: str) -> WikiConfig:
    """Load wiki configuration from config files.

    Args:
        root: Project root directory.

    Returns:
        Merged wiki configuration.
    """
    candidates = [
        Path(root) / ".omx-config.json",
        codex_home() / ".omx-config.json",
    ]

    for path in candidates:
        try:
            if not path.exists():
                continue
            parsed = json.loads(path.read_text(encoding="utf-8"))
            wiki_cfg = parsed.get("wiki")
            if isinstance(wiki_cfg, dict):
                cfg = WikiConfig()
                for field_name in (
                    "enabled",
                    "auto_capture",
                    "max_context_lines",
                    "stale_days",
                    "max_page_size",
                    "feed_project_memory_on_start",
                ):
                    # Also try camelCase keys
                    camel = field_name
                    for key in (field_name, camel):
                        if key in wiki_cfg:
                            setattr(cfg, field_name, wiki_cfg[key])
                            break
                return cfg
        except Exception:
            continue

    return DEFAULT_WIKI_CONFIG


def on_session_start(data: dict[str, Any]) -> dict[str, Any]:
    """Handle session start -- inject wiki context.

    Args:
        data: Session data with optional cwd key.

    Returns:
        Dict with optional additionalContext key.
    """
    try:
        root = data.get("cwd") or os.getcwd()
        config = _load_wiki_config(root)
        if not config.enabled:
            return {}

        wiki_dir = get_wiki_dir(root)
        if not wiki_dir.exists():
            return {}

        pages = list_pages(root)
        if not pages:
            return {}

        if not read_index(root):
            with_wiki_lock(root, lambda: update_index_unsafe(root))

        if config.feed_project_memory_on_start:
            _feed_project_memory(root)

        index = read_index(root)
        if not index:
            return {}

        lines = [
            f"[OMX Wiki: {len(pages)} pages at .omx/wiki/]",
            "",
            "Use wiki_query to search, wiki_list to browse, wiki_read to inspect pages.",
            "",
            *index.split("\n")[: config.max_context_lines],
        ]

        return {"additionalContext": "\n".join(lines)}
    except Exception:
        return {}


def on_session_end(data: dict[str, Any]) -> dict[str, bool]:
    """Handle session end -- auto-capture session log.

    Args:
        data: Session data with optional cwd and session_id keys.

    Returns:
        Dict with continue key.
    """
    started_at = time.monotonic()
    timeout_s = 3.0

    try:
        root = data.get("cwd") or os.getcwd()
        config = _load_wiki_config(root)
        if not config.enabled or not config.auto_capture:
            return {"continue": True}

        wiki_dir = get_wiki_dir(root)
        if not wiki_dir.exists():
            return {"continue": True}

        session_id = data.get("session_id") or f"session-{int(time.time() * 1000)}"
        now = datetime.now(timezone.utc).isoformat()
        date_slug = now.split("T")[0]
        filename = f"session-log-{date_slug}-{session_id[-8:]}.md"

        def _do() -> None:
            if time.monotonic() - started_at > timeout_s:
                return

            write_page_unsafe(
                root,
                WikiPage(
                    filename=filename,
                    frontmatter=WikiPageFrontmatter(
                        title=f"Session Log {date_slug}",
                        tags=["session-log", "auto-captured"],
                        created=now,
                        updated=now,
                        sources=[session_id],
                        links=[],
                        category="session-log",
                        confidence="medium",
                        schema_version=WIKI_SCHEMA_VERSION,
                    ),
                    content=(
                        f"\n# Session Log {date_slug}\n\n"
                        f"Auto-captured session metadata.\n"
                        f"Session ID: {session_id}\n\n"
                        "Review and promote significant findings to curated wiki pages via `wiki_ingest`.\n"
                    ),
                ),
            )

            append_log_unsafe(
                root,
                WikiLogEntry(
                    timestamp=now,
                    operation="session-end",
                    pages_affected=[filename],
                    summary=f"Auto-captured session log for {session_id}",
                ),
            )
            update_index_unsafe(root)

        with_wiki_lock(root, _do)
    except Exception:
        pass

    return {"continue": True}


def on_pre_compact(data: dict[str, Any]) -> dict[str, Any]:
    """Handle pre-compact -- provide wiki summary context.

    Args:
        data: Session data with optional cwd key.

    Returns:
        Dict with optional additionalContext key.
    """
    try:
        root = data.get("cwd") or os.getcwd()
        config = _load_wiki_config(root)
        if not config.enabled:
            return {}

        pages = list_pages(root)
        if not pages:
            return {}

        all_pages = read_all_pages(root)
        categories = list({p.frontmatter.category for p in all_pages})
        latest_update = "unknown"
        updates = sorted((p.frontmatter.updated for p in all_pages), reverse=True)
        if updates:
            latest_update = updates[0]

        return {
            "additionalContext": (
                f"[Wiki: {len(pages)} pages | categories: {', '.join(categories)} "
                f"| last updated: {latest_update}]"
            )
        }
    except Exception:
        return {}


def _feed_project_memory(root: str) -> None:
    """Sync project memory into the wiki environment page."""
    try:
        pm_path = omx_project_memory_path(Path(root))
        if not pm_path.exists():
            return

        parsed = json.loads(pm_path.read_text(encoding="utf-8"))
        existing = read_page(root, "environment.md")
        memory_mtime = pm_path.stat().st_mtime * 1000

        if existing:
            try:
                existing_updated = (
                    datetime.fromisoformat(
                        existing.frontmatter.updated.replace("Z", "+00:00")
                    ).timestamp()
                    * 1000
                )
            except (ValueError, AttributeError):
                existing_updated = 0.0
            if existing_updated >= memory_mtime:
                return

        sections: list[str] = ["\n# Project Environment\n"]
        string_fields = [
            ("Tech Stack", parsed.get("techStack")),
            ("Build", parsed.get("build")),
            ("Conventions", parsed.get("conventions")),
            ("Structure", parsed.get("structure")),
        ]

        for label, value in string_fields:
            if isinstance(value, str) and value.strip():
                sections.append(f"## {label}")
                sections.append(value.strip())
                sections.append("")

        notes = parsed.get("notes")
        if isinstance(notes, list) and notes:
            sections.append("## Notes")
            for note in notes[:20]:
                content = note.get("content", "") if isinstance(note, dict) else ""
                if isinstance(content, str) and content.strip():
                    sections.append(f"- {content.strip()}")
            sections.append("")

        directives = parsed.get("directives")
        if isinstance(directives, list) and directives:
            sections.append("## Directives")
            for directive in directives[:20]:
                content = (
                    directive.get("directive", "")
                    if isinstance(directive, dict)
                    else ""
                )
                if isinstance(content, str) and content.strip():
                    sections.append(f"- {content.strip()}")
            sections.append("")

        now = datetime.now(timezone.utc).isoformat()

        def _do() -> None:
            write_page_unsafe(
                root,
                WikiPage(
                    filename="environment.md",
                    frontmatter=WikiPageFrontmatter(
                        title="Project Environment",
                        tags=["environment", "auto-detected"],
                        created=(existing.frontmatter.created if existing else now),
                        updated=now,
                        sources=["project-memory-auto-detect"],
                        links=[],
                        category="environment",
                        confidence="high",
                        schema_version=WIKI_SCHEMA_VERSION,
                    ),
                    content="\n".join(sections),
                ),
                allow_reserved=True,
            )
            update_index_unsafe(root)
            append_log_unsafe(
                root,
                WikiLogEntry(
                    timestamp=now,
                    operation="session-start",
                    pages_affected=["environment.md"],
                    summary="Synced project memory into managed environment page",
                ),
            )

        with_wiki_lock(root, _do)
    except Exception:
        pass
