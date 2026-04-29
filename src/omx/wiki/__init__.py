"""Wiki Module -- Public API.

LLM Wiki: persistent, self-maintained markdown knowledge base
that compounds project and session knowledge across sessions.

Port of src/wiki/index.ts.
"""

from omx.wiki.types import (
    DEFAULT_WIKI_CONFIG,
    WIKI_SCHEMA_VERSION,
    WikiCategory,
    WikiConfig,
    WikiIngestInput,
    WikiIngestResult,
    WikiLintIssue,
    WikiLintReport,
    WikiLintSeverity,
    WikiLogEntry,
    WikiPage,
    WikiPageFrontmatter,
    WikiQueryMatch,
    WikiQueryOptions,
)
from omx.wiki.storage import (
    append_log,
    append_log_unsafe,
    delete_page,
    delete_page_unsafe,
    ensure_wiki_dir,
    get_wiki_dir,
    list_pages,
    normalize_wiki_page_name,
    parse_frontmatter,
    read_all_pages,
    read_index,
    read_log,
    read_page,
    serialize_page,
    title_to_slug,
    update_index_unsafe,
    with_wiki_lock,
    write_page,
    write_page_unsafe,
)
from omx.wiki.ingest import ingest_knowledge
from omx.wiki.query import query_wiki, tokenize
from omx.wiki.lint import lint_wiki
from omx.wiki.lifecycle import on_session_start, on_session_end, on_pre_compact

__all__ = [
    # Types
    "WIKI_SCHEMA_VERSION",
    "DEFAULT_WIKI_CONFIG",
    "WikiCategory",
    "WikiConfig",
    "WikiIngestInput",
    "WikiIngestResult",
    "WikiLintIssue",
    "WikiLintReport",
    "WikiLintSeverity",
    "WikiLogEntry",
    "WikiPage",
    "WikiPageFrontmatter",
    "WikiQueryMatch",
    "WikiQueryOptions",
    # Storage
    "get_wiki_dir",
    "ensure_wiki_dir",
    "with_wiki_lock",
    "read_page",
    "list_pages",
    "read_all_pages",
    "read_index",
    "read_log",
    "write_page",
    "delete_page",
    "append_log",
    "title_to_slug",
    "parse_frontmatter",
    "serialize_page",
    "write_page_unsafe",
    "delete_page_unsafe",
    "update_index_unsafe",
    "append_log_unsafe",
    "normalize_wiki_page_name",
    # Operations
    "ingest_knowledge",
    "query_wiki",
    "tokenize",
    "lint_wiki",
    "on_session_start",
    "on_session_end",
    "on_pre_compact",
]
