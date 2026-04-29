"""Wiki Types.

Type definitions for the OMX wiki knowledge layer.

Port of src/wiki/types.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


WIKI_SCHEMA_VERSION = 1


class WikiCategory(StrEnum):
    """Wiki page category."""

    ARCHITECTURE = "architecture"
    DECISION = "decision"
    PATTERN = "pattern"
    DEBUGGING = "debugging"
    ENVIRONMENT = "environment"
    SESSION_LOG = "session-log"
    REFERENCE = "reference"
    CONVENTION = "convention"


WikiLintSeverity = Literal["error", "warning", "info"]
WikiLintIssueType = Literal[
    "orphan",
    "stale",
    "broken-ref",
    "low-confidence",
    "oversized",
    "structural-contradiction",
]
WikiConfidence = Literal["high", "medium", "low"]


@dataclass
class WikiPageFrontmatter:
    """Frontmatter metadata for a wiki page."""

    title: str
    tags: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    sources: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    category: WikiCategory = WikiCategory.REFERENCE
    confidence: WikiConfidence = "medium"
    schema_version: int = WIKI_SCHEMA_VERSION


@dataclass
class WikiPage:
    """A wiki page with frontmatter and markdown content."""

    filename: str
    frontmatter: WikiPageFrontmatter
    content: str


@dataclass
class WikiLogEntry:
    """An entry in the wiki activity log."""

    timestamp: str
    operation: str
    pages_affected: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class WikiIngestInput:
    """Input for wiki knowledge ingestion."""

    title: str
    content: str
    tags: list[str] = field(default_factory=list)
    category: WikiCategory = WikiCategory.REFERENCE
    sources: list[str] | None = None
    confidence: WikiConfidence | None = None


@dataclass
class WikiIngestResult:
    """Result of a wiki ingestion operation."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    total_affected: int = 0


@dataclass
class WikiQueryOptions:
    """Options for wiki search queries."""

    tags: list[str] | None = None
    category: WikiCategory | None = None
    limit: int | None = None
    log_query: bool = True


@dataclass
class WikiQueryMatch:
    """A search result with relevance scoring."""

    page: WikiPage
    snippet: str
    score: float


@dataclass
class WikiLintIssue:
    """A single lint issue found during health checks."""

    page: str
    severity: WikiLintSeverity
    issue_type: WikiLintIssueType
    message: str


@dataclass
class WikiLintReport:
    """Report from wiki health checks."""

    issues: list[WikiLintIssue] = field(default_factory=list)
    total_pages: int = 0
    orphan_count: int = 0
    stale_count: int = 0
    broken_ref_count: int = 0
    low_confidence_count: int = 0
    oversized_count: int = 0
    contradiction_count: int = 0


@dataclass
class WikiConfig:
    """Wiki configuration."""

    enabled: bool = True
    auto_capture: bool = True
    max_context_lines: int = 30
    stale_days: int = 30
    max_page_size: int = 10_240
    feed_project_memory_on_start: bool = False


DEFAULT_WIKI_CONFIG = WikiConfig()
