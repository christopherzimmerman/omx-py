"""Tests for MCP servers: memory, code_intel, trace, wiki."""

import json
import os
import tempfile
import unittest
from pathlib import Path


class TestMemoryValidation(unittest.TestCase):
    def test_none_returns_default(self):
        from omx.mcp.memory_validation import parse_notepad_prune_days_old

        ok, days, err = parse_notepad_prune_days_old(None)
        self.assertTrue(ok)
        self.assertEqual(days, 7)
        self.assertIsNone(err)

    def test_valid_int(self):
        from omx.mcp.memory_validation import parse_notepad_prune_days_old

        ok, days, err = parse_notepad_prune_days_old(3)
        self.assertTrue(ok)
        self.assertEqual(days, 3)

    def test_negative_rejected(self):
        from omx.mcp.memory_validation import parse_notepad_prune_days_old

        ok, _, err = parse_notepad_prune_days_old(-1)
        self.assertFalse(ok)
        self.assertIn("non-negative", err)

    def test_string_rejected(self):
        from omx.mcp.memory_validation import parse_notepad_prune_days_old

        ok, _, err = parse_notepad_prune_days_old("foo")
        self.assertFalse(ok)


class TestMemoryServer(unittest.TestCase):
    def test_build_tools(self):
        from omx.mcp.memory_server import build_memory_server_tools

        tools = build_memory_server_tools()
        names = [t["name"] for t in tools]
        self.assertIn("project_memory_read", names)
        self.assertIn("project_memory_write", names)
        self.assertIn("notepad_read", names)
        self.assertIn("notepad_write_priority", names)
        self.assertIn("notepad_write_working", names)
        self.assertIn("notepad_write_manual", names)
        self.assertIn("notepad_prune", names)
        self.assertIn("notepad_stats", names)
        self.assertEqual(len(tools), 10)

    def test_memory_read_nonexistent(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call(
                "project_memory_read", {"workingDirectory": tmpdir}
            )
            data = json.loads(result["content"][0]["text"])
            self.assertFalse(data.get("exists", True))

    def test_memory_write_then_read(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "project_memory_write",
                {
                    "memory": {"techStack": "Python 3.12"},
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "project_memory_read", {"workingDirectory": tmpdir}
            )
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["techStack"], "Python 3.12")

    def test_memory_merge(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "project_memory_write",
                {
                    "memory": {"techStack": "Python"},
                    "workingDirectory": tmpdir,
                },
            )
            handle_tool_call(
                "project_memory_write",
                {
                    "memory": {"build": "make"},
                    "merge": True,
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "project_memory_read", {"workingDirectory": tmpdir}
            )
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["techStack"], "Python")
            self.assertEqual(data["build"], "make")

    def test_memory_read_section(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "project_memory_write",
                {
                    "memory": {"techStack": "Rust", "build": "cargo"},
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "project_memory_read",
                {
                    "section": "techStack",
                    "workingDirectory": tmpdir,
                },
            )
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data, "Rust")

    def test_add_note(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call(
                "project_memory_add_note",
                {
                    "category": "build",
                    "content": "Use make for builds",
                    "workingDirectory": tmpdir,
                },
            )
            data = json.loads(result["content"][0]["text"])
            self.assertTrue(data["success"])
            self.assertEqual(data["noteCount"], 1)

    def test_add_directive(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call(
                "project_memory_add_directive",
                {
                    "directive": "Always run tests",
                    "priority": "high",
                    "workingDirectory": tmpdir,
                },
            )
            data = json.loads(result["content"][0]["text"])
            self.assertTrue(data["success"])

    def test_notepad_read_nonexistent(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call("notepad_read", {"workingDirectory": tmpdir})
            data = json.loads(result["content"][0]["text"])
            self.assertFalse(data.get("exists", True))

    def test_notepad_write_priority_then_read(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "notepad_write_priority",
                {
                    "content": "Focus on tests",
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "notepad_read",
                {
                    "section": "priority",
                    "workingDirectory": tmpdir,
                },
            )
            data = json.loads(result["content"][0]["text"])
            self.assertIn("Focus on tests", data["content"])

    def test_notepad_write_working(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "notepad_write_working",
                {
                    "content": "Started refactoring",
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call("notepad_read", {"workingDirectory": tmpdir})
            data = json.loads(result["content"][0]["text"])
            self.assertIn("Started refactoring", data["content"])

    def test_notepad_stats(self):
        from omx.mcp.memory_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "notepad_write_working",
                {
                    "content": "Entry 1",
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call("notepad_stats", {"workingDirectory": tmpdir})
            data = json.loads(result["content"][0]["text"])
            self.assertTrue(data["exists"])
            self.assertEqual(data["entryCount"], 1)

    def test_unknown_tool(self):
        from omx.mcp.memory_server import handle_tool_call

        result = handle_tool_call("nonexistent_tool", {})
        self.assertTrue(result.get("isError"))


class TestCodeIntelServer(unittest.TestCase):
    def test_build_tools(self):
        from omx.mcp.code_intel_server import build_code_intel_server_tools

        tools = build_code_intel_server_tools()
        names = [t["name"] for t in tools]
        self.assertIn("lsp_diagnostics", names)
        self.assertIn("lsp_document_symbols", names)
        self.assertIn("lsp_workspace_symbols", names)
        self.assertIn("lsp_find_references", names)
        self.assertIn("lsp_servers", names)

    def test_document_symbols(self):
        from omx.mcp.code_intel_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test_symbols.py")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("def hello():\n    pass\n\nclass Foo:\n    pass\n")
            result = handle_tool_call("lsp_document_symbols", {"file": file_path})
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["symbolCount"], 2)
            names = [s["name"] for s in data["symbols"]]
            self.assertIn("hello", names)
            self.assertIn("Foo", names)

    def test_document_symbols_file_not_found(self):
        from omx.mcp.code_intel_server import handle_tool_call

        result = handle_tool_call(
            "lsp_document_symbols", {"file": "/nonexistent/file.py"}
        )
        self.assertTrue(result.get("isError"))

    def test_servers(self):
        from omx.mcp.code_intel_server import handle_tool_call

        result = handle_tool_call("lsp_servers", {})
        data = json.loads(result["content"][0]["text"])
        self.assertIn("servers", data)

    def test_unknown_tool(self):
        from omx.mcp.code_intel_server import handle_tool_call

        result = handle_tool_call("nonexistent", {})
        self.assertTrue(result.get("isError"))


class TestTraceServer(unittest.TestCase):
    def test_build_tools(self):
        from omx.mcp.trace_server import build_trace_server_tools

        tools = build_trace_server_tools()
        names = [t["name"] for t in tools]
        self.assertIn("trace_timeline", names)
        self.assertIn("trace_summary", names)

    def test_timeline_empty(self):
        from omx.mcp.trace_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call("trace_timeline", {"workingDirectory": tmpdir})
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["entryCount"], 0)

    def test_timeline_with_data(self):
        from omx.mcp.trace_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir) / ".omx" / "logs"
            logs_dir.mkdir(parents=True)
            entries = [
                {"timestamp": "2025-01-01T00:00:00Z", "type": "user_turn"},
                {"timestamp": "2025-01-01T00:01:00Z", "type": "assistant_turn"},
            ]
            (logs_dir / "turns-2025-01-01.jsonl").write_text(
                "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
            )
            result = handle_tool_call("trace_timeline", {"workingDirectory": tmpdir})
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["entryCount"], 2)

    def test_timeline_last(self):
        from omx.mcp.trace_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir) / ".omx" / "logs"
            logs_dir.mkdir(parents=True)
            entries = [
                {"timestamp": f"2025-01-01T00:0{i}:00Z", "type": "turn"}
                for i in range(5)
            ]
            (logs_dir / "turns-2025-01-01.jsonl").write_text(
                "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
            )
            result = handle_tool_call(
                "trace_timeline",
                {
                    "workingDirectory": tmpdir,
                    "last": 2,
                },
            )
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["entryCount"], 2)

    def test_summary(self):
        from omx.mcp.trace_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir = Path(tmpdir) / ".omx" / "logs"
            logs_dir.mkdir(parents=True)
            entries = [
                {"timestamp": "2025-01-01T00:00:00Z", "type": "user_turn"},
                {"timestamp": "2025-01-01T00:05:00Z", "type": "assistant_turn"},
            ]
            (logs_dir / "turns-2025-01-01.jsonl").write_text(
                "\n".join(json.dumps(e) for e in entries), encoding="utf-8"
            )
            result = handle_tool_call("trace_summary", {"workingDirectory": tmpdir})
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["turns"]["total"], 2)
            self.assertIn("user_turn", data["turns"]["byType"])

    def test_unknown_tool(self):
        from omx.mcp.trace_server import handle_tool_call

        result = handle_tool_call("nonexistent", {})
        self.assertTrue(result.get("isError"))


class TestWikiServer(unittest.TestCase):
    def test_build_tools(self):
        from omx.mcp.wiki_server import build_wiki_server_tools

        tools = build_wiki_server_tools()
        names = [t["name"] for t in tools]
        self.assertIn("wiki_ingest", names)
        self.assertIn("wiki_query", names)
        self.assertIn("wiki_lint", names)
        self.assertIn("wiki_add", names)
        self.assertIn("wiki_list", names)
        self.assertIn("wiki_read", names)
        self.assertIn("wiki_delete", names)
        self.assertIn("wiki_refresh", names)

    def test_ingest_and_read(self):
        from omx.mcp.wiki_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call(
                "wiki_ingest",
                {
                    "title": "Test Page",
                    "content": "Hello world",
                    "tags": ["test"],
                    "category": "reference",
                    "workingDirectory": tmpdir,
                },
            )
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["totalAffected"], 1)
            self.assertEqual(len(data["created"]), 1)

            slug = data["created"][0]
            result = handle_tool_call(
                "wiki_read",
                {
                    "page": slug,
                    "workingDirectory": tmpdir,
                },
            )
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["frontmatter"]["title"], "Test Page")

    def test_list_empty(self):
        from omx.mcp.wiki_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call("wiki_list", {"workingDirectory": tmpdir})
            data = json.loads(result["content"][0]["text"])
            self.assertEqual(data["pages"], [])

    def test_add_duplicate_rejected(self):
        from omx.mcp.wiki_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "wiki_add",
                {
                    "title": "Unique Page",
                    "content": "Content",
                    "tags": ["test"],
                    "category": "reference",
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "wiki_add",
                {
                    "title": "Unique Page",
                    "content": "Duplicate",
                    "tags": ["test"],
                    "category": "reference",
                    "workingDirectory": tmpdir,
                },
            )
            self.assertTrue(result.get("isError"))

    def test_delete(self):
        from omx.mcp.wiki_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "wiki_ingest",
                {
                    "title": "To Delete",
                    "content": "Will be deleted",
                    "tags": ["temp"],
                    "category": "reference",
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "wiki_delete",
                {
                    "page": "to-delete",
                    "workingDirectory": tmpdir,
                },
            )
            data = json.loads(result["content"][0]["text"])
            self.assertTrue(data["deleted"])

    def test_query(self):
        from omx.mcp.wiki_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            handle_tool_call(
                "wiki_ingest",
                {
                    "title": "Python Guide",
                    "content": "Python is great for scripting",
                    "tags": ["python", "guide"],
                    "category": "reference",
                    "workingDirectory": tmpdir,
                },
            )
            result = handle_tool_call(
                "wiki_query",
                {
                    "query": "python",
                    "workingDirectory": tmpdir,
                },
            )
            data = json.loads(result["content"][0]["text"])
            self.assertGreater(len(data), 0)

    def test_read_not_found(self):
        from omx.mcp.wiki_server import handle_tool_call

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_tool_call(
                "wiki_read",
                {
                    "page": "nonexistent",
                    "workingDirectory": tmpdir,
                },
            )
            self.assertTrue(result.get("isError"))

    def test_unknown_tool(self):
        from omx.mcp.wiki_server import handle_tool_call

        result = handle_tool_call("nonexistent", {})
        self.assertTrue(result.get("isError"))


class TestMcpServeRouting(unittest.TestCase):
    def test_all_targets_known(self):
        """Verify all 5 MCP targets are recognized in the CLI."""
        from omx.mcp.bootstrap import MCP_SERVER_NAMES

        self.assertEqual(
            set(MCP_SERVER_NAMES),
            {"state", "memory", "code_intel", "trace", "wiki"},
        )


if __name__ == "__main__":
    unittest.main()
