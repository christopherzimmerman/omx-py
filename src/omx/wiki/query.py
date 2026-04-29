"""Wiki Query.

Keyword + tag search across all wiki pages.
Returns matching pages with relevance snippets.

NO vector embeddings -- search is keyword-based only (hard constraint).
The LLM caller synthesizes answers from returned matches.

Port of src/wiki/query.ts.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from omx.wiki.storage import append_log, read_all_pages
from omx.wiki.types import WikiLogEntry, WikiQueryMatch, WikiQueryOptions


def tokenize(text: str) -> list[str]:
    """Tokenize text for search, with CJK bi-gram support.

    Latin/numeric words: split on whitespace.
    CJK characters (Han, Hangul, Kana): bi-grams plus individual chars.
    Other scripts: whitespace split (fallback).

    Args:
        text: Text to tokenize.

    Returns:
        List of lowercase tokens.
    """
    lower = text.lower()
    tokens: list[str] = []

    # Latin/numeric tokens (including accented Latin)
    latin_matches = re.findall(r"[a-z0-9\u00C0-\u024F]+", lower)
    tokens.extend(latin_matches)

    # CJK segments (Hiragana + Katakana + CJK Unified Ideographs + Hangul)
    cjk_pattern = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uAC00-\uD7AF]+")
    cjk_matches = cjk_pattern.findall(lower)
    for segment in cjk_matches:
        for ch in segment:
            tokens.append(ch)
        for i in range(len(segment) - 1):
            tokens.append(segment[i : i + 2])

    # Fallback: other scripts
    remaining = re.sub(r"[a-z0-9\u00C0-\u024F]+", " ", lower)
    remaining = cjk_pattern.sub(" ", remaining)
    for t in remaining.split():
        if t and re.search(r"\w", t):
            tokens.append(t)

    return tokens


def query_wiki(
    root: str,
    query_text: str,
    options: WikiQueryOptions | None = None,
) -> list[WikiQueryMatch]:
    """Search wiki pages by keyword and/or tags.

    Matching strategy:
    1. Tag match: pages whose tags intersect with query tags (highest weight)
    2. Title match: pages whose title contains the query text
    3. Content match: pages whose content contains the query text

    Results are scored and sorted by relevance (descending).

    Args:
        root: Project root directory.
        query_text: Search text (matched against title + content).
        options: Optional filters (tags, category, limit).

    Returns:
        Matching pages with snippets, sorted by relevance.
    """
    if options is None:
        options = WikiQueryOptions()

    filter_tags = options.tags
    category = options.category
    limit = options.limit or 20
    log_query = options.log_query

    pages = read_all_pages(root)
    query_lower = query_text.lower()
    query_terms = tokenize(query_text)

    matches: list[WikiQueryMatch] = []

    for page in pages:
        # Category filter
        if category and page.frontmatter.category != category:
            continue

        score = 0.0
        snippet = ""

        # Tag matching (weight: 3 per matching tag)
        if filter_tags:
            for ft in filter_tags:
                if any(pt.lower() == ft.lower() for pt in page.frontmatter.tags):
                    score += 3

        # Match query terms against page tags
        for term in query_terms:
            if any(term in t.lower() for t in page.frontmatter.tags):
                score += 2

        # Title matching (weight: 5)
        title_lower = page.frontmatter.title.lower()
        if query_lower in title_lower:
            score += 5
        else:
            for term in query_terms:
                if term in title_lower:
                    score += 2

        # Content matching (weight: 1 per unique term match)
        content_lower = page.content.lower()
        for term in query_terms:
            idx = content_lower.find(term)
            if idx != -1:
                score += 1
                if not snippet:
                    start = max(0, idx - 40)
                    end = min(len(content_lower), idx + len(term) + 80)
                    raw = page.content[start:end].replace("\n", " ").strip()
                    prefix = "..." if start > 0 else ""
                    suffix = "..." if end < len(content_lower) else ""
                    snippet = f"{prefix}{raw}{suffix}"

        if score > 0:
            if not snippet:
                for line in page.content.split("\n"):
                    if line.strip():
                        snippet = line.strip()
                        break
                if len(snippet) > 120:
                    snippet = snippet[:117] + "..."

            matches.append(WikiQueryMatch(page=page, snippet=snippet, score=score))

    matches.sort(key=lambda m: m.score, reverse=True)
    limited = matches[:limit]

    if log_query:
        append_log(
            root,
            WikiLogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(),
                operation="query",
                pages_affected=[m.page.filename for m in limited],
                summary=f'Query "{query_text}" -> {len(limited)} results (of {len(matches)} total)',
            ),
        )

    return limited
