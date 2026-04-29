"""OMX Wiki MCP Server.

Provides wiki knowledge base tools for ingesting, querying, and
managing project knowledge.

Port of src/mcp/wiki-server.ts.
Can be run as: python -m omx.mcp.wiki_server
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from omx.mcp.bootstrap import auto_start_stdio_server
from omx.mcp.protocol import McpServer
from omx.state.paths import resolve_working_directory
from omx.wiki.ingest import ingest_knowledge
from omx.wiki.lint import lint_wiki
from omx.wiki.query import query_wiki
from omx.wiki.storage import (
    append_log,
    delete_page,
    list_pages,
    normalize_wiki_page_name,
    read_index,
    read_page,
    title_to_slug,
    update_index_unsafe,
    with_wiki_lock,
)
from omx.wiki.types import (
    WikiIngestInput,
    WikiLogEntry,
    WikiQueryOptions,
)


WIKI_CATEGORIES = [
    "architecture",
    "decision",
    "pattern",
    "debugging",
    "environment",
    "session-log",
    "reference",
    "convention",
]


def _text_response(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


def _error_response(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps({"error": message})}],
        "isError": True,
    }


def _resolve_root(args: dict[str, Any]) -> str:
    wd = args.get("workingDirectory")
    return str(resolve_working_directory(wd if isinstance(wd, str) else None))


def build_wiki_server_tools() -> list[dict[str, Any]]:
    """Build the tool definitions for the wiki server."""
    return [
        {
            "name": "wiki_ingest",
            "description": "Process knowledge into wiki pages. Creates new pages or merges into existing ones.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 200},
                    "content": {"type": "string", "maxLength": 50000},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 50},
                        "maxItems": 20,
                    },
                    "category": {"type": "string", "enum": WIKI_CATEGORIES},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 100},
                        "maxItems": 10,
                    },
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "workingDirectory": {"type": "string"},
                },
                "required": ["title", "content", "tags", "category"],
            },
        },
        {
            "name": "wiki_query",
            "description": "Search wiki pages by keywords and tags. Returns raw matches for synthesis.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "category": {"type": "string", "enum": WIKI_CATEGORIES},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                    "workingDirectory": {"type": "string"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "wiki_lint",
            "description": "Run health checks on the wiki.",
            "inputSchema": {
                "type": "object",
                "properties": {"workingDirectory": {"type": "string"}},
            },
        },
        {
            "name": "wiki_add",
            "description": "Quick-add a single wiki page. Rejects overwrites; use wiki_ingest to merge.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "maxLength": 200},
                    "content": {"type": "string", "maxLength": 50000},
                    "tags": {
                        "type": "array",
                        "items": {"type": "string", "maxLength": 50},
                        "maxItems": 20,
                    },
                    "category": {"type": "string", "enum": WIKI_CATEGORIES},
                    "workingDirectory": {"type": "string"},
                },
                "required": ["title", "content"],
            },
        },
        {
            "name": "wiki_list",
            "description": "List wiki pages and return the index when present.",
            "inputSchema": {
                "type": "object",
                "properties": {"workingDirectory": {"type": "string"}},
            },
        },
        {
            "name": "wiki_read",
            "description": "Read a specific wiki page.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page": {"type": "string"},
                    "workingDirectory": {"type": "string"},
                },
                "required": ["page"],
            },
        },
        {
            "name": "wiki_delete",
            "description": "Delete a wiki page and update the index.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "page": {"type": "string"},
                    "workingDirectory": {"type": "string"},
                },
                "required": ["page"],
            },
        },
        {
            "name": "wiki_refresh",
            "description": "Rebuild the wiki index and refresh derived metadata surfaces.",
            "inputSchema": {
                "type": "object",
                "properties": {"workingDirectory": {"type": "string"}},
            },
        },
    ]


def _wiki_page_to_dict(page: Any) -> dict[str, Any]:
    """Convert a WikiPage to a JSON-serializable dict."""
    return {
        "filename": page.filename,
        "frontmatter": {
            "title": page.frontmatter.title,
            "tags": page.frontmatter.tags,
            "created": page.frontmatter.created,
            "updated": page.frontmatter.updated,
            "sources": page.frontmatter.sources,
            "links": page.frontmatter.links,
            "category": page.frontmatter.category,
            "confidence": page.frontmatter.confidence,
            "schemaVersion": page.frontmatter.schema_version,
        },
        "content": page.content,
    }


def _ingest_result_to_dict(result: Any) -> dict[str, Any]:
    """Convert a WikiIngestResult to a JSON-serializable dict."""
    return {
        "created": result.created,
        "updated": result.updated,
        "totalAffected": result.total_affected,
    }


def _lint_report_to_dict(report: Any) -> dict[str, Any]:
    """Convert a WikiLintReport to a JSON-serializable dict."""
    return {
        "issues": [
            {
                "page": i.page,
                "severity": i.severity,
                "type": i.issue_type,
                "message": i.message,
            }
            for i in report.issues
        ],
        "stats": {
            "totalPages": report.total_pages,
            "orphanCount": report.orphan_count,
            "staleCount": report.stale_count,
            "brokenRefCount": report.broken_ref_count,
            "lowConfidenceCount": report.low_confidence_count,
            "oversizedCount": report.oversized_count,
            "contradictionCount": report.contradiction_count,
        },
    }


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Handle a wiki server tool call.

    Args:
        name: Tool name.
        args: Tool arguments.

    Returns:
        MCP tool response dict.
    """
    try:
        root = _resolve_root(args)

        match name:
            case "wiki_ingest":
                result = ingest_knowledge(
                    root,
                    WikiIngestInput(
                        title=str(args.get("title", "")),
                        content=str(args.get("content", "")),
                        tags=[str(t) for t in args.get("tags", [])],
                        category=str(args.get("category", "reference")),  # type: ignore[arg-type]
                        sources=[str(s) for s in args.get("sources", [])] or None,
                        confidence=args.get("confidence"),  # type: ignore[arg-type]
                    ),
                )
                return _text_response(_ingest_result_to_dict(result))

            case "wiki_query":
                matches = query_wiki(
                    root,
                    str(args.get("query", "")),
                    WikiQueryOptions(
                        tags=[str(t) for t in args.get("tags", [])] or None,
                        category=args.get("category"),  # type: ignore[arg-type]
                        limit=args.get("limit"),
                    ),
                )
                return _text_response(
                    [
                        {
                            "page": _wiki_page_to_dict(m.page),
                            "snippet": m.snippet,
                            "score": m.score,
                        }
                        for m in matches
                    ]
                )

            case "wiki_lint":
                report = lint_wiki(root)
                return _text_response(_lint_report_to_dict(report))

            case "wiki_add":
                title = str(args.get("title", ""))
                slug = title_to_slug(title)
                if read_page(root, slug):
                    return _error_response(
                        f'Page "{slug}" already exists. Use wiki_ingest to merge into it.'
                    )
                result = ingest_knowledge(
                    root,
                    WikiIngestInput(
                        title=title,
                        content=str(args.get("content", "")),
                        tags=[str(t) for t in args.get("tags", [])],
                        category=str(args.get("category", "reference")),  # type: ignore[arg-type]
                    ),
                )
                append_log(
                    root,
                    WikiLogEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        operation="add",
                        pages_affected=result.created,
                        summary=f"Created wiki page {slug}",
                    ),
                )
                return _text_response(_ingest_result_to_dict(result))

            case "wiki_list":
                return _text_response(
                    {
                        "pages": list_pages(root),
                        "index": read_index(root),
                    }
                )

            case "wiki_read":
                page = read_page(
                    root, normalize_wiki_page_name(str(args.get("page", "")))
                )
                if not page:
                    return _error_response("Wiki page not found")
                return _text_response(_wiki_page_to_dict(page))

            case "wiki_delete":
                filename = normalize_wiki_page_name(str(args.get("page", "")))
                deleted = delete_page(root, filename)
                if not deleted:
                    return _error_response(
                        f"Wiki page not found or reserved: {filename}"
                    )
                append_log(
                    root,
                    WikiLogEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        operation="delete",
                        pages_affected=[filename],
                        summary=f"Deleted wiki page {filename}",
                    ),
                )
                return _text_response({"deleted": True, "page": filename})

            case "wiki_refresh":
                with_wiki_lock(root, lambda: update_index_unsafe(root))
                append_log(
                    root,
                    WikiLogEntry(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        operation="add",
                        pages_affected=list_pages(root),
                        summary="Refreshed wiki index and derived metadata surfaces",
                    ),
                )
                return _text_response(
                    {
                        "refreshed": True,
                        "pages": list_pages(root),
                        "index": read_index(root),
                    }
                )

            case _:
                return _error_response(f"Unknown tool: {name}")

    except Exception as exc:
        return _error_response(str(exc))


def main() -> None:
    """Entry point for running as a standalone MCP server."""
    server = McpServer("omx-wiki", "0.1.0")
    server.set_tool_lister(build_wiki_server_tools)
    server.set_tool_handler(handle_tool_call)
    auto_start_stdio_server("wiki", server)


if __name__ == "__main__":
    main()
