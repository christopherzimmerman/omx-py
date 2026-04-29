"""Wiki Lint.

Health checks for the wiki knowledge base.
Detects orphan pages, stale content, broken cross-references,
oversized pages, and structural contradictions.

Port of src/wiki/lint.ts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from omx.wiki.storage import append_log, read_all_pages
from omx.wiki.types import (
    DEFAULT_WIKI_CONFIG,
    WikiConfig,
    WikiLintIssue,
    WikiLintReport,
    WikiLogEntry,
    WikiPage,
)


def lint_wiki(root: str, config: WikiConfig | None = None) -> WikiLintReport:
    """Run health checks on the wiki.

    Checks performed:
    1. Orphan pages -- no incoming [[links]] from other pages
    2. Stale pages -- not updated in staleDays days
    3. Broken cross-references -- [[links]] to non-existent pages
    4. Low confidence -- pages marked as confidence: low
    5. Oversized -- content exceeds maxPageSize bytes
    6. Structural contradictions -- same topic with conflicting confidence/category

    Args:
        root: Project root directory.
        config: Wiki configuration (uses defaults if not provided).

    Returns:
        Lint report with issues and stats.
    """
    if config is None:
        config = DEFAULT_WIKI_CONFIG

    pages = read_all_pages(root)
    issues: list[WikiLintIssue] = []
    page_filenames = {p.filename for p in pages}

    # Build incoming link map
    incoming_links: dict[str, set[str]] = {}
    for page in pages:
        for link in page.frontmatter.links:
            if link not in incoming_links:
                incoming_links[link] = set()
            incoming_links[link].add(page.filename)

    now_ts = datetime.now(timezone.utc).timestamp() * 1000
    stale_threshold_ms = config.stale_days * 24 * 60 * 60 * 1000

    for page in pages:
        # 1. Orphan detection
        page_incoming = incoming_links.get(page.filename, set())
        if not page_incoming:
            issues.append(
                WikiLintIssue(
                    page=page.filename,
                    severity="info",
                    issue_type="orphan",
                    message=f'No other pages link to "{page.frontmatter.title}"',
                )
            )

        # 2. Stale detection
        try:
            updated_at = (
                datetime.fromisoformat(
                    page.frontmatter.updated.replace("Z", "+00:00")
                ).timestamp()
                * 1000
            )
        except (ValueError, AttributeError):
            updated_at = 0.0
        if now_ts - updated_at > stale_threshold_ms:
            days_since = int((now_ts - updated_at) / (24 * 60 * 60 * 1000))
            issues.append(
                WikiLintIssue(
                    page=page.filename,
                    severity="warning",
                    issue_type="stale",
                    message=f'"{page.frontmatter.title}" not updated in {days_since} days',
                )
            )

        # 3. Broken cross-references
        for link in page.frontmatter.links:
            if link not in page_filenames:
                issues.append(
                    WikiLintIssue(
                        page=page.filename,
                        severity="error",
                        issue_type="broken-ref",
                        message=f'Broken link to "{link}" from "{page.frontmatter.title}"',
                    )
                )

        # 4. Low confidence
        if page.frontmatter.confidence == "low":
            issues.append(
                WikiLintIssue(
                    page=page.filename,
                    severity="info",
                    issue_type="low-confidence",
                    message=f'"{page.frontmatter.title}" has low confidence -- consider verifying or removing',
                )
            )

        # 5. Oversized pages
        content_size = len(page.content.encode("utf-8"))
        if content_size > config.max_page_size:
            size_kb = f"{content_size / 1024:.1f}"
            issues.append(
                WikiLintIssue(
                    page=page.filename,
                    severity="warning",
                    issue_type="oversized",
                    message=f'"{page.frontmatter.title}" is {size_kb}KB -- consider splitting into smaller pages',
                )
            )

    # 6. Structural contradictions
    _detect_structural_contradictions(pages, issues)

    # Build stats
    report = WikiLintReport(
        issues=issues,
        total_pages=len(pages),
        orphan_count=sum(1 for i in issues if i.issue_type == "orphan"),
        stale_count=sum(1 for i in issues if i.issue_type == "stale"),
        broken_ref_count=sum(1 for i in issues if i.issue_type == "broken-ref"),
        low_confidence_count=sum(1 for i in issues if i.issue_type == "low-confidence"),
        oversized_count=sum(1 for i in issues if i.issue_type == "oversized"),
        contradiction_count=sum(
            1 for i in issues if i.issue_type == "structural-contradiction"
        ),
    )

    # Log the lint operation
    append_log(
        root,
        WikiLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            operation="lint",
            pages_affected=list({i.page for i in issues}),
            summary=(
                f"Lint: {len(issues)} issues "
                f"({report.orphan_count} orphan, {report.stale_count} stale, "
                f"{report.broken_ref_count} broken, {report.contradiction_count} contradictions)"
            ),
        ),
    )

    return report


def _detect_structural_contradictions(
    pages: list[WikiPage], issues: list[WikiLintIssue]
) -> None:
    """Detect structural contradictions between related pages."""
    # Group by slug prefix (first two hyphen-separated segments)
    slug_groups: dict[str, list[WikiPage]] = {}
    for page in pages:
        prefix = "-".join(page.filename.split("-")[:2])
        if prefix not in slug_groups:
            slug_groups[prefix] = []
        slug_groups[prefix].append(page)

    for group in slug_groups.values():
        if len(group) < 2:
            continue

        # Check for conflicting confidence on same topic
        confidences = {p.frontmatter.confidence for p in group}
        if len(confidences) > 1 and "high" in confidences and "low" in confidences:
            titles = ", ".join(f'"{p.frontmatter.title}"' for p in group)
            issues.append(
                WikiLintIssue(
                    page=group[0].filename,
                    severity="warning",
                    issue_type="structural-contradiction",
                    message=f"Conflicting confidence levels for related pages: {titles}",
                )
            )

        # Check for overlapping tags with different categories
        tag_categories: dict[str, set[str]] = {}
        for page in group:
            for tag in page.frontmatter.tags:
                if tag not in tag_categories:
                    tag_categories[tag] = set()
                tag_categories[tag].add(page.frontmatter.category)

        for tag, categories in tag_categories.items():
            if len(categories) > 1:
                issues.append(
                    WikiLintIssue(
                        page=group[0].filename,
                        severity="info",
                        issue_type="structural-contradiction",
                        message=f'Tag "{tag}" appears in pages with different categories: {", ".join(sorted(categories))}',
                    )
                )
                break  # One contradiction per group is enough
