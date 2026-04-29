"""Tests for omx.wiki module."""

import tempfile
import unittest
from datetime import datetime, timezone


class TestWikiTypes(unittest.TestCase):
    def test_wiki_category_enum(self):
        from omx.wiki.types import WikiCategory

        self.assertEqual(WikiCategory.REFERENCE, "reference")
        self.assertEqual(WikiCategory.ARCHITECTURE, "architecture")
        self.assertEqual(WikiCategory.SESSION_LOG, "session-log")

    def test_default_config(self):
        from omx.wiki.types import DEFAULT_WIKI_CONFIG

        self.assertTrue(DEFAULT_WIKI_CONFIG.enabled)
        self.assertEqual(DEFAULT_WIKI_CONFIG.stale_days, 30)
        self.assertEqual(DEFAULT_WIKI_CONFIG.max_page_size, 10_240)

    def test_wiki_page_dataclass(self):
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        fm = WikiPageFrontmatter(title="Test", tags=["a"])
        page = WikiPage(filename="test.md", frontmatter=fm, content="Hello")
        self.assertEqual(page.filename, "test.md")
        self.assertEqual(page.frontmatter.title, "Test")


class TestWikiStorage(unittest.TestCase):
    def test_title_to_slug(self):
        from omx.wiki.storage import title_to_slug

        self.assertEqual(title_to_slug("Hello World"), "hello-world.md")
        self.assertEqual(title_to_slug("foo"), "foo.md")
        # Long title gets truncated
        slug = title_to_slug("a" * 100)
        self.assertTrue(slug.endswith(".md"))
        self.assertLessEqual(len(slug), 68)  # 64 + ".md"

    def test_normalize_wiki_page_name(self):
        from omx.wiki.storage import normalize_wiki_page_name

        self.assertEqual(normalize_wiki_page_name("test"), "test.md")
        self.assertEqual(normalize_wiki_page_name("test.md"), "test.md")

    def test_serialize_and_parse(self):
        from omx.wiki.storage import parse_frontmatter, serialize_page
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        now = datetime.now(timezone.utc).isoformat()
        fm = WikiPageFrontmatter(
            title="My Page",
            tags=["test", "demo"],
            created=now,
            updated=now,
            sources=["manual"],
            links=[],
            category="reference",
            confidence="high",
        )
        page = WikiPage(
            filename="my-page.md",
            frontmatter=fm,
            content="\n# My Page\n\nContent here.\n",
        )
        serialized = serialize_page(page)
        self.assertIn("---", serialized)
        self.assertIn("My Page", serialized)

        result = parse_frontmatter(serialized)
        self.assertIsNotNone(result)
        parsed_fm, parsed_content = result
        self.assertEqual(parsed_fm.title, "My Page")
        self.assertEqual(parsed_fm.tags, ["test", "demo"])
        self.assertEqual(parsed_fm.confidence, "high")
        self.assertIn("Content here", parsed_content)

    def test_parse_frontmatter_invalid(self):
        from omx.wiki.storage import parse_frontmatter

        self.assertIsNone(parse_frontmatter("no frontmatter here"))
        self.assertIsNone(parse_frontmatter(""))

    def test_write_read_page(self):
        from omx.wiki.storage import read_page, write_page, list_pages
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc).isoformat()
            fm = WikiPageFrontmatter(
                title="Test Page",
                tags=["test"],
                created=now,
                updated=now,
            )
            page = WikiPage(
                filename="test-page.md", frontmatter=fm, content="\n# Test\n"
            )
            write_page(tmpdir, page)

            result = read_page(tmpdir, "test-page.md")
            self.assertIsNotNone(result)
            self.assertEqual(result.frontmatter.title, "Test Page")

            pages = list_pages(tmpdir)
            self.assertIn("test-page.md", pages)

    def test_delete_page(self):
        from omx.wiki.storage import delete_page, write_page, list_pages
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc).isoformat()
            fm = WikiPageFrontmatter(title="Del", tags=[], created=now, updated=now)
            page = WikiPage(filename="del.md", frontmatter=fm, content="x")
            write_page(tmpdir, page)
            self.assertIn("del.md", list_pages(tmpdir))

            deleted = delete_page(tmpdir, "del.md")
            self.assertTrue(deleted)
            self.assertNotIn("del.md", list_pages(tmpdir))

    def test_delete_reserved_rejected(self):
        from omx.wiki.storage import delete_page

        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertFalse(delete_page(tmpdir, "index.md"))
            self.assertFalse(delete_page(tmpdir, "log.md"))

    def test_write_reserved_rejected(self):
        from omx.wiki.storage import write_page_unsafe
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        with tempfile.TemporaryDirectory() as tmpdir:
            fm = WikiPageFrontmatter(title="Index", tags=[])
            page = WikiPage(filename="index.md", frontmatter=fm, content="x")
            with self.assertRaises(ValueError):
                write_page_unsafe(tmpdir, page)

    def test_read_all_pages(self):
        from omx.wiki.storage import read_all_pages, write_page
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc).isoformat()
            for i in range(3):
                fm = WikiPageFrontmatter(
                    title=f"Page {i}", tags=["test"], created=now, updated=now
                )
                page = WikiPage(
                    filename=f"page-{i}.md", frontmatter=fm, content=f"Content {i}"
                )
                write_page(tmpdir, page)

            pages = read_all_pages(tmpdir)
            self.assertEqual(len(pages), 3)

    def test_read_index_and_log(self):
        from omx.wiki.storage import read_index, read_log, write_page, append_log
        from omx.wiki.types import WikiPage, WikiPageFrontmatter, WikiLogEntry

        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc).isoformat()
            fm = WikiPageFrontmatter(title="A", tags=[], created=now, updated=now)
            write_page(tmpdir, WikiPage(filename="a.md", frontmatter=fm, content="x"))

            index = read_index(tmpdir)
            self.assertIsNotNone(index)
            self.assertIn("Wiki Index", index)

            append_log(
                tmpdir,
                WikiLogEntry(
                    timestamp=now,
                    operation="test",
                    pages_affected=["a.md"],
                    summary="test entry",
                ),
            )
            log = read_log(tmpdir)
            self.assertIsNotNone(log)
            self.assertIn("test entry", log)

    def test_safe_path_rejects_traversal(self):
        from omx.wiki.storage import read_page

        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertIsNone(read_page(tmpdir, "../etc/passwd"))
            self.assertIsNone(read_page(tmpdir, "foo/bar.md"))


class TestWikiQuery(unittest.TestCase):
    def _create_pages(self, tmpdir):
        from omx.wiki.storage import write_page
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        now = datetime.now(timezone.utc).isoformat()
        pages_data = [
            (
                "Python Guide",
                ["python", "guide"],
                "reference",
                "Python is a great language for scripting",
            ),
            (
                "Rust Patterns",
                ["rust", "patterns"],
                "pattern",
                "Rust offers safe concurrency",
            ),
            (
                "Build System",
                ["build", "python"],
                "convention",
                "We use make and pytest",
            ),
        ]
        for title, tags, category, content in pages_data:
            from omx.wiki.storage import title_to_slug

            slug = title_to_slug(title)
            fm = WikiPageFrontmatter(
                title=title,
                tags=tags,
                created=now,
                updated=now,
                category=category,
            )
            write_page(
                tmpdir,
                WikiPage(
                    filename=slug, frontmatter=fm, content=f"\n# {title}\n\n{content}\n"
                ),
            )

    def test_basic_query(self):
        from omx.wiki.query import query_wiki
        from omx.wiki.types import WikiQueryOptions

        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_pages(tmpdir)
            results = query_wiki(tmpdir, "python", WikiQueryOptions(log_query=False))
            self.assertGreater(len(results), 0)
            # Python Guide should score highest
            self.assertIn("python", results[0].page.frontmatter.title.lower())

    def test_tag_filter(self):
        from omx.wiki.query import query_wiki
        from omx.wiki.types import WikiQueryOptions

        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_pages(tmpdir)
            results = query_wiki(
                tmpdir,
                "language",
                WikiQueryOptions(
                    tags=["rust"],
                    log_query=False,
                ),
            )
            # Should find Rust Patterns due to tag match
            found = any("rust" in r.page.frontmatter.title.lower() for r in results)
            self.assertTrue(found)

    def test_category_filter(self):
        from omx.wiki.query import query_wiki
        from omx.wiki.types import WikiQueryOptions

        with tempfile.TemporaryDirectory() as tmpdir:
            self._create_pages(tmpdir)
            results = query_wiki(
                tmpdir,
                "python",
                WikiQueryOptions(
                    category="convention",
                    log_query=False,
                ),
            )
            for r in results:
                self.assertEqual(r.page.frontmatter.category, "convention")

    def test_empty_query(self):
        from omx.wiki.query import query_wiki
        from omx.wiki.types import WikiQueryOptions

        with tempfile.TemporaryDirectory() as tmpdir:
            results = query_wiki(tmpdir, "anything", WikiQueryOptions(log_query=False))
            self.assertEqual(len(results), 0)

    def test_tokenize(self):
        from omx.wiki.query import tokenize

        tokens = tokenize("Hello World 123")
        self.assertIn("hello", tokens)
        self.assertIn("world", tokens)
        self.assertIn("123", tokens)

    def test_tokenize_empty(self):
        from omx.wiki.query import tokenize

        self.assertEqual(tokenize(""), [])


class TestWikiIngest(unittest.TestCase):
    def test_create_new_page(self):
        from omx.wiki.ingest import ingest_knowledge
        from omx.wiki.storage import read_page
        from omx.wiki.types import WikiIngestInput

        with tempfile.TemporaryDirectory() as tmpdir:
            result = ingest_knowledge(
                tmpdir,
                WikiIngestInput(
                    title="New Page",
                    content="Brand new content",
                    tags=["new"],
                    category="reference",
                ),
            )
            self.assertEqual(len(result.created), 1)
            self.assertEqual(len(result.updated), 0)
            self.assertEqual(result.total_affected, 1)

            page = read_page(tmpdir, result.created[0])
            self.assertIsNotNone(page)
            self.assertEqual(page.frontmatter.title, "New Page")
            self.assertIn("Brand new content", page.content)

    def test_merge_existing_page(self):
        from omx.wiki.ingest import ingest_knowledge
        from omx.wiki.storage import read_page
        from omx.wiki.types import WikiIngestInput

        with tempfile.TemporaryDirectory() as tmpdir:
            result1 = ingest_knowledge(
                tmpdir,
                WikiIngestInput(
                    title="Merge Test",
                    content="Original content",
                    tags=["v1"],
                    category="reference",
                ),
            )
            slug = result1.created[0]

            result2 = ingest_knowledge(
                tmpdir,
                WikiIngestInput(
                    title="Merge Test",
                    content="Additional content",
                    tags=["v2"],
                    category="reference",
                ),
            )
            self.assertEqual(len(result2.updated), 1)
            self.assertEqual(result2.updated[0], slug)

            page = read_page(tmpdir, slug)
            self.assertIn("Original content", page.content)
            self.assertIn("Additional content", page.content)
            self.assertIn("v1", page.frontmatter.tags)
            self.assertIn("v2", page.frontmatter.tags)

    def test_wiki_links_extracted(self):
        from omx.wiki.ingest import ingest_knowledge
        from omx.wiki.storage import read_page
        from omx.wiki.types import WikiIngestInput

        with tempfile.TemporaryDirectory() as tmpdir:
            result = ingest_knowledge(
                tmpdir,
                WikiIngestInput(
                    title="Linked Page",
                    content="See also [[Other Page]] and [[More Info]]",
                    tags=["links"],
                    category="reference",
                ),
            )
            page = read_page(tmpdir, result.created[0])
            self.assertGreater(len(page.frontmatter.links), 0)


class TestWikiLint(unittest.TestCase):
    def test_lint_empty(self):
        from omx.wiki.lint import lint_wiki

        with tempfile.TemporaryDirectory() as tmpdir:
            report = lint_wiki(tmpdir)
            self.assertEqual(report.total_pages, 0)
            self.assertEqual(len(report.issues), 0)

    def test_lint_orphan_detection(self):
        from omx.wiki.lint import lint_wiki
        from omx.wiki.storage import write_page
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc).isoformat()
            fm = WikiPageFrontmatter(
                title="Orphan",
                tags=[],
                created=now,
                updated=now,
            )
            write_page(
                tmpdir, WikiPage(filename="orphan.md", frontmatter=fm, content="x")
            )

            report = lint_wiki(tmpdir)
            orphans = [i for i in report.issues if i.issue_type == "orphan"]
            self.assertGreater(len(orphans), 0)

    def test_lint_broken_ref(self):
        from omx.wiki.lint import lint_wiki
        from omx.wiki.storage import write_page
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc).isoformat()
            fm = WikiPageFrontmatter(
                title="Broken",
                tags=[],
                created=now,
                updated=now,
                links=["nonexistent.md"],
            )
            write_page(
                tmpdir, WikiPage(filename="broken.md", frontmatter=fm, content="x")
            )

            report = lint_wiki(tmpdir)
            broken = [i for i in report.issues if i.issue_type == "broken-ref"]
            self.assertGreater(len(broken), 0)
            self.assertEqual(report.broken_ref_count, len(broken))

    def test_lint_low_confidence(self):
        from omx.wiki.lint import lint_wiki
        from omx.wiki.storage import write_page
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc).isoformat()
            fm = WikiPageFrontmatter(
                title="Uncertain",
                tags=[],
                created=now,
                updated=now,
                confidence="low",
            )
            write_page(
                tmpdir, WikiPage(filename="uncertain.md", frontmatter=fm, content="x")
            )

            report = lint_wiki(tmpdir)
            low = [i for i in report.issues if i.issue_type == "low-confidence"]
            self.assertGreater(len(low), 0)


class TestWikiLifecycle(unittest.TestCase):
    def test_on_session_start_no_wiki(self):
        from omx.wiki.lifecycle import on_session_start

        with tempfile.TemporaryDirectory() as tmpdir:
            result = on_session_start({"cwd": tmpdir})
            self.assertNotIn("additionalContext", result)

    def test_on_session_end_no_wiki(self):
        from omx.wiki.lifecycle import on_session_end

        with tempfile.TemporaryDirectory() as tmpdir:
            result = on_session_end({"cwd": tmpdir})
            self.assertTrue(result["continue"])

    def test_on_pre_compact_no_wiki(self):
        from omx.wiki.lifecycle import on_pre_compact

        with tempfile.TemporaryDirectory() as tmpdir:
            result = on_pre_compact({"cwd": tmpdir})
            self.assertNotIn("additionalContext", result)

    def test_on_session_start_with_pages(self):
        from omx.wiki.lifecycle import on_session_start
        from omx.wiki.storage import write_page
        from omx.wiki.types import WikiPage, WikiPageFrontmatter

        with tempfile.TemporaryDirectory() as tmpdir:
            now = datetime.now(timezone.utc).isoformat()
            fm = WikiPageFrontmatter(title="Test", tags=[], created=now, updated=now)
            write_page(
                tmpdir, WikiPage(filename="test.md", frontmatter=fm, content="x")
            )

            result = on_session_start({"cwd": tmpdir})
            self.assertIn("additionalContext", result)
            self.assertIn("Wiki", result["additionalContext"])


if __name__ == "__main__":
    unittest.main()
