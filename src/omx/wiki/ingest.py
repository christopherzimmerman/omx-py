"""Wiki Ingest.

Processes knowledge into wiki pages. A single ingest can create a new page
or merge into an existing one (append strategy -- never replaces content).

Port of src/wiki/ingest.ts.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from omx.wiki.storage import (
    append_log_unsafe,
    read_page,
    title_to_slug,
    update_index_unsafe,
    with_wiki_lock,
    write_page_unsafe,
)
from omx.wiki.types import (
    WIKI_SCHEMA_VERSION,
    WikiIngestInput,
    WikiIngestResult,
    WikiLogEntry,
    WikiPage,
    WikiPageFrontmatter,
)


def ingest_knowledge(root: str, input_data: WikiIngestInput) -> WikiIngestResult:
    """Ingest knowledge into the wiki.

    If a page with the same slug exists, merges content (append strategy):
    - Frontmatter: union tags, append sources, update timestamp, keep higher confidence
    - Content: append new content as a timestamped section (never replace)

    Args:
        root: Project root directory.
        input_data: Knowledge to ingest.

    Returns:
        Result with created/updated page lists.
    """
    slug = title_to_slug(input_data.title)
    now = datetime.now(timezone.utc).isoformat()
    result = WikiIngestResult()

    def _do() -> None:
        existing = read_page(root, slug)

        if existing:
            merged = _merge_page(existing, input_data, now)
            write_page_unsafe(root, merged)
            result.updated.append(slug)
        else:
            page = _create_page(slug, input_data, now)
            write_page_unsafe(root, page)
            result.created.append(slug)

        update_index_unsafe(root)

        append_log_unsafe(
            root,
            WikiLogEntry(
                timestamp=now,
                operation="ingest",
                pages_affected=[*result.created, *result.updated],
                summary=(
                    f'Updated "{input_data.title}" with new content'
                    if existing
                    else f'Created new page "{input_data.title}"'
                ),
            ),
        )

    with_wiki_lock(root, _do)
    result.total_affected = len(result.created) + len(result.updated)
    return result


def _create_page(slug: str, input_data: WikiIngestInput, now: str) -> WikiPage:
    """Create a new wiki page from ingest input."""
    frontmatter = WikiPageFrontmatter(
        title=input_data.title,
        tags=list(dict.fromkeys(input_data.tags)),
        created=now,
        updated=now,
        sources=input_data.sources or [],
        links=_extract_wiki_links(input_data.content),
        category=input_data.category,
        confidence=input_data.confidence or "medium",
        schema_version=WIKI_SCHEMA_VERSION,
    )
    return WikiPage(
        filename=slug,
        frontmatter=frontmatter,
        content=f"\n# {input_data.title}\n\n{input_data.content}\n",
    )


def _merge_page(existing: WikiPage, input_data: WikiIngestInput, now: str) -> WikiPage:
    """Merge new content into an existing page (append strategy)."""
    merged_tags = list(dict.fromkeys([*existing.frontmatter.tags, *input_data.tags]))
    merged_sources = list(
        dict.fromkeys([*existing.frontmatter.sources, *(input_data.sources or [])])
    )
    merged_links = list(
        dict.fromkeys(
            [*existing.frontmatter.links, *_extract_wiki_links(input_data.content)]
        )
    )

    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    existing_rank = confidence_rank.get(existing.frontmatter.confidence, 2)
    new_rank = confidence_rank.get(input_data.confidence or "medium", 2)
    merged_confidence = (
        (input_data.confidence or "medium")
        if new_rank >= existing_rank
        else existing.frontmatter.confidence
    )

    appended_content = (
        existing.content.rstrip()
        + f"\n\n---\n\n## Update ({now})\n\n{input_data.content}\n"
    )

    fm = WikiPageFrontmatter(
        title=existing.frontmatter.title,
        tags=merged_tags,
        created=existing.frontmatter.created,
        updated=now,
        sources=merged_sources,
        links=merged_links,
        category=existing.frontmatter.category,
        confidence=merged_confidence,
        schema_version=existing.frontmatter.schema_version,
    )

    return WikiPage(
        filename=existing.filename,
        frontmatter=fm,
        content=appended_content,
    )


def _extract_wiki_links(content: str) -> list[str]:
    """Extract [[wiki-link]] references from content."""
    matches = re.findall(r"\[\[([^\]]+)\]\]", content)
    if not matches:
        return []
    return list(dict.fromkeys(title_to_slug(m.strip()) for m in matches))
