"""Wiki Storage.

File I/O layer for the OMX wiki knowledge base.

Port of src/wiki/storage.ts.
"""

from __future__ import annotations

import os
import re
import shutil
import time
import unicodedata
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

from omx.utils.paths import omx_wiki_dir
from omx.wiki.types import (
    WIKI_SCHEMA_VERSION,
    WikiLogEntry,
    WikiPage,
    WikiPageFrontmatter,
)

INDEX_FILE = "index.md"
LOG_FILE = "log.md"
ENVIRONMENT_FILE = "environment.md"
RESERVED_FILES = frozenset({INDEX_FILE, LOG_FILE, ENVIRONMENT_FILE})

T = TypeVar("T")


def _atomic_write(path: Path, content: str) -> None:
    """Write a file atomically via a temp file and rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f".{os.getpid()}.{int(time.time() * 1000)}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def _lock_path_for(path: Path) -> Path:
    return path.with_suffix(".lock")


def _with_file_lock(
    lock_path: Path,
    fn: Callable[[], T],
    timeout_ms: int = 5_000,
    retry_delay_ms: int = 50,
) -> T:
    """Acquire a directory-based lock, run fn, then release."""
    deadline = time.monotonic() + timeout_ms / 1000.0

    while True:
        try:
            lock_path.mkdir(parents=False, exist_ok=False)
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out acquiring wiki lock at {lock_path}")
            time.sleep(retry_delay_ms / 1000.0)
        except FileNotFoundError:
            lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        return fn()
    finally:
        shutil.rmtree(lock_path, ignore_errors=True)


def get_wiki_dir(root: str) -> Path:
    """Get the wiki directory path for a project root.

    Args:
        root: Project root directory path.

    Returns:
        Path to the wiki directory.
    """
    return omx_wiki_dir(Path(root))


def ensure_wiki_dir(root: str) -> Path:
    """Ensure the wiki directory exists and .gitignore is set up.

    Args:
        root: Project root directory path.

    Returns:
        Path to the wiki directory.
    """
    wiki_dir = get_wiki_dir(root)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    omx_root = Path(root) / ".omx"
    omx_root.mkdir(parents=True, exist_ok=True)
    gitignore_path = omx_root / ".gitignore"
    if gitignore_path.exists():
        content = gitignore_path.read_text(encoding="utf-8")
        if "wiki/" not in content:
            _atomic_write(gitignore_path, f"{content.rstrip()}\nwiki/\n")
    else:
        _atomic_write(gitignore_path, "wiki/\n")
    return wiki_dir


def with_wiki_lock(root: str, fn: Callable[[], T]) -> T:
    """Execute fn while holding the wiki lock.

    Args:
        root: Project root directory path.
        fn: Callable to execute under lock.

    Returns:
        Return value of fn.
    """
    wiki_dir = ensure_wiki_dir(root)
    lock = _lock_path_for(wiki_dir / ".wiki-lock")
    return _with_file_lock(lock, fn, timeout_ms=5_000, retry_delay_ms=50)


def _parse_simple_yaml(yaml_text: str) -> dict[str, str]:
    """Parse a simple YAML key: value block (no nesting)."""
    result: dict[str, str] = {}
    for line in yaml_text.split("\n"):
        colon_idx = line.find(":")
        if colon_idx == -1:
            continue
        key = line[:colon_idx].strip()
        value = line[colon_idx + 1 :].strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
            value = value.replace("\\\\", "\x00ESCAPE\x00")
            value = value.replace('\\"', '"')
            value = value.replace("\\n", "\n")
            value = value.replace("\\r", "\r")
            value = value.replace("\x00ESCAPE\x00", "\\")
        if key:
            result[key] = value
    return result


def _parse_yaml_array(value: str | None) -> list[str]:
    """Parse a YAML inline array like [a, b, c]."""
    if not value:
        return []
    trimmed = value.strip()
    if trimmed.startswith("[") and trimmed.endswith("]"):
        items = trimmed[1:-1].split(",")
        results: list[str] = []
        for item in items:
            item = item.strip().strip("\"'")
            item = item.replace("\\\\", "\x00ESCAPE\x00")
            item = item.replace('\\"', '"')
            item = item.replace("\\n", "\n")
            item = item.replace("\\r", "\r")
            item = item.replace("\x00ESCAPE\x00", "\\")
            if item:
                results.append(item)
        return results
    return [trimmed] if trimmed else []


def _escape_yaml(value: str) -> str:
    """Escape a string for YAML output."""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def parse_frontmatter(
    raw: str,
) -> tuple[WikiPageFrontmatter, str] | None:
    """Parse frontmatter and content from a wiki page file.

    Args:
        raw: Raw file content with YAML frontmatter.

    Returns:
        Tuple of (frontmatter, content) or None if parsing fails.
    """
    normalized = raw.replace("\r\n", "\n")
    match = re.match(r"^---\n([\s\S]*?)\n---\n([\s\S]*)$", normalized)
    if not match:
        return None

    yaml_block = match.group(1)
    content = match.group(2)

    try:
        fm = _parse_simple_yaml(yaml_block)
        now = datetime.now(timezone.utc).isoformat()
        return (
            WikiPageFrontmatter(
                title=fm.get("title", ""),
                tags=_parse_yaml_array(fm.get("tags")),
                created=fm.get("created", now),
                updated=fm.get("updated", now),
                sources=_parse_yaml_array(fm.get("sources")),
                links=_parse_yaml_array(fm.get("links")),
                category=fm.get("category", "reference"),  # type: ignore[arg-type]
                confidence=fm.get("confidence", "medium"),  # type: ignore[arg-type]
                schema_version=int(fm.get("schemaVersion", str(WIKI_SCHEMA_VERSION))),
            ),
            content,
        )
    except Exception:
        return None


def serialize_page(page: WikiPage) -> str:
    """Serialize a wiki page to markdown with YAML frontmatter.

    Args:
        page: Wiki page to serialize.

    Returns:
        Markdown string with frontmatter.
    """
    fm = page.frontmatter
    tags_str = ", ".join(f'"{_escape_yaml(t)}"' for t in fm.tags)
    sources_str = ", ".join(f'"{_escape_yaml(s)}"' for s in fm.sources)
    links_str = ", ".join(f'"{_escape_yaml(lnk)}"' for lnk in fm.links)
    yaml = "\n".join(
        [
            f'title: "{_escape_yaml(fm.title)}"',
            f"tags: [{tags_str}]",
            f"created: {fm.created}",
            f"updated: {fm.updated}",
            f"sources: [{sources_str}]",
            f"links: [{links_str}]",
            f"category: {fm.category}",
            f"confidence: {fm.confidence}",
            f"schemaVersion: {fm.schema_version}",
        ]
    )
    return f"---\n{yaml}\n---\n{page.content}"


def _safe_wiki_path(wiki_dir: Path, filename: str) -> Path | None:
    """Validate that a filename resolves safely within the wiki dir."""
    if "/" in filename or "\\" in filename or ".." in filename:
        return None
    file_path = wiki_dir / filename
    resolved = file_path.resolve()
    resolved_wiki = wiki_dir.resolve()
    if resolved != resolved_wiki and not str(resolved).startswith(
        str(resolved_wiki) + os.sep
    ):
        return None
    return file_path


def read_page(root: str, filename: str) -> WikiPage | None:
    """Read a wiki page by filename.

    Args:
        root: Project root directory path.
        filename: Wiki page filename (e.g. "my-page.md").

    Returns:
        WikiPage or None if not found/parseable.
    """
    wiki_dir = get_wiki_dir(root)
    file_path = _safe_wiki_path(wiki_dir, filename)
    if file_path is None or not file_path.exists():
        return None
    try:
        result = parse_frontmatter(file_path.read_text(encoding="utf-8"))
        if result is None:
            return None
        frontmatter, content = result
        return WikiPage(filename=filename, frontmatter=frontmatter, content=content)
    except Exception:
        return None


def list_pages(root: str) -> list[str]:
    """List all wiki page filenames (excluding reserved files).

    Args:
        root: Project root directory path.

    Returns:
        Sorted list of page filenames.
    """
    wiki_dir = get_wiki_dir(root)
    if not wiki_dir.exists():
        return []
    return sorted(
        entry.name
        for entry in wiki_dir.iterdir()
        if entry.name.endswith(".md") and entry.name not in RESERVED_FILES
    )


def read_all_pages(root: str) -> list[WikiPage]:
    """Read all wiki pages.

    Args:
        root: Project root directory path.

    Returns:
        List of successfully parsed wiki pages.
    """
    pages: list[WikiPage] = []
    for filename in list_pages(root):
        page = read_page(root, filename)
        if page is not None:
            pages.append(page)
    return pages


def read_index(root: str) -> str | None:
    """Read the wiki index file.

    Args:
        root: Project root directory path.

    Returns:
        Index content or None.
    """
    index_path = get_wiki_dir(root) / INDEX_FILE
    return index_path.read_text(encoding="utf-8") if index_path.exists() else None


def read_log(root: str) -> str | None:
    """Read the wiki log file.

    Args:
        root: Project root directory path.

    Returns:
        Log content or None.
    """
    log_path = get_wiki_dir(root) / LOG_FILE
    return log_path.read_text(encoding="utf-8") if log_path.exists() else None


def write_page_unsafe(
    root: str, page: WikiPage, *, allow_reserved: bool = False
) -> None:
    """Write a wiki page without acquiring the lock.

    Args:
        root: Project root directory path.
        page: Wiki page to write.
        allow_reserved: Allow writing to reserved files like index.md.

    Raises:
        ValueError: If filename is reserved or invalid.
    """
    if not allow_reserved and page.filename in RESERVED_FILES:
        raise ValueError(f"Cannot write to reserved wiki file: {page.filename}")
    wiki_dir = ensure_wiki_dir(root)
    file_path = _safe_wiki_path(wiki_dir, page.filename)
    if file_path is None:
        raise ValueError(f"Invalid wiki page filename: {page.filename}")
    _atomic_write(file_path, serialize_page(page))


def delete_page_unsafe(root: str, filename: str) -> bool:
    """Delete a wiki page without acquiring the lock.

    Args:
        root: Project root directory path.
        filename: Page filename to delete.

    Returns:
        True if the page was deleted, False otherwise.
    """
    if filename in RESERVED_FILES:
        return False
    wiki_dir = get_wiki_dir(root)
    file_path = _safe_wiki_path(wiki_dir, filename)
    if file_path is None or not file_path.exists():
        return False
    file_path.unlink()
    return True


def update_index_unsafe(root: str) -> None:
    """Rebuild the wiki index file (must be called under lock).

    Args:
        root: Project root directory path.
    """
    pages = read_all_pages(root)
    by_category: dict[str, list[WikiPage]] = {}
    for page in pages:
        cat = page.frontmatter.category
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(page)

    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "# Wiki Index",
        "",
        f"> {len(pages)} pages | Last updated: {now}",
        "",
    ]

    for category in sorted(by_category.keys()):
        lines.append(f"## {category}")
        lines.append("")
        for page in by_category[category]:
            first_line = ""
            for line in page.content.split("\n"):
                if line.strip():
                    first_line = line.strip()
                    break
            summary = first_line[:77] + "..." if len(first_line) > 80 else first_line
            lines.append(f"- [{page.frontmatter.title}]({page.filename}) — {summary}")
        lines.append("")

    _atomic_write(ensure_wiki_dir(root) / INDEX_FILE, "\n".join(lines))


def append_log_unsafe(root: str, entry: WikiLogEntry) -> None:
    """Append an entry to the wiki log (must be called under lock).

    Args:
        root: Project root directory path.
        entry: Log entry to append.
    """
    wiki_dir = ensure_wiki_dir(root)
    log_path = wiki_dir / LOG_FILE
    existing = (
        log_path.read_text(encoding="utf-8") if log_path.exists() else "# Wiki Log\n\n"
    )
    pages_str = ", ".join(entry.pages_affected) if entry.pages_affected else "none"
    log_line = (
        f"## [{entry.timestamp}] {entry.operation}\n"
        f"- **Pages:** {pages_str}\n"
        f"- **Summary:** {entry.summary}\n\n"
    )
    _atomic_write(log_path, f"{existing}{log_line}")


def write_page(root: str, page: WikiPage, *, allow_reserved: bool = False) -> None:
    """Write a wiki page (acquires lock, updates index).

    Args:
        root: Project root directory path.
        page: Wiki page to write.
        allow_reserved: Allow writing to reserved files.
    """

    def _do() -> None:
        write_page_unsafe(root, page, allow_reserved=allow_reserved)
        update_index_unsafe(root)

    with_wiki_lock(root, _do)


def delete_page(root: str, filename: str) -> bool:
    """Delete a wiki page (acquires lock, updates index).

    Args:
        root: Project root directory path.
        filename: Page filename to delete.

    Returns:
        True if the page was deleted.
    """
    result: list[bool] = []

    def _do() -> None:
        deleted = delete_page_unsafe(root, filename)
        if deleted:
            update_index_unsafe(root)
        result.append(deleted)

    with_wiki_lock(root, _do)
    return result[0] if result else False


def append_log(root: str, entry: WikiLogEntry) -> None:
    """Append to the wiki log (acquires lock).

    Args:
        root: Project root directory path.
        entry: Log entry to append.
    """
    with_wiki_lock(root, lambda: append_log_unsafe(root, entry))


def title_to_slug(title: str) -> str:
    """Convert a title to a URL-safe slug filename.

    Args:
        title: Page title.

    Returns:
        Slug filename ending in .md.
    """
    base = unicodedata.normalize("NFC", title.lower())
    # Replace non-alphanumeric/non-letter chars with hyphens
    base = re.sub(r"[^\w]+", "-", base, flags=re.UNICODE)
    base = base.strip("-")[:64]

    if not base:
        h = 0
        for ch in title:
            h = ((h << 5) - h + ord(ch)) & 0xFFFFFFFF
            # Simulate JS 32-bit signed integer overflow
            if h >= 0x80000000:
                h -= 0x100000000
        return f"page-{abs(h):08x}.md"

    return f"{base}.md"


def normalize_wiki_page_name(page: str) -> str:
    """Ensure a page name ends with .md.

    Args:
        page: Page name or slug.

    Returns:
        Page name with .md extension.
    """
    return page if page.endswith(".md") else f"{page}.md"
