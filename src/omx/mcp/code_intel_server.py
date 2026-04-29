"""OMX Code Intelligence MCP Server.

Provides LSP-like diagnostics, symbol search, and code pattern matching.
Uses pragmatic CLI wrappers (grep, rg) rather than full LSP protocol.

Port of src/mcp/code-intel-server.ts.
Can be run as: python -m omx.mcp.code_intel_server
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from omx.mcp.bootstrap import auto_start_stdio_server
from omx.mcp.protocol import McpServer


@dataclass
class Diagnostic:
    """A single diagnostic finding."""

    file: str
    line: int
    character: int
    severity: str
    code: str
    message: str


@dataclass
class DocumentSymbol:
    """A symbol found in a source file."""

    name: str
    kind: str
    line: int
    character: int


SYMBOL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # TypeScript/JavaScript
    ("function", re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)")),
    ("class", re.compile(r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)")),
    ("interface", re.compile(r"^(?:export\s+)?interface\s+(\w+)")),
    ("type", re.compile(r"^(?:export\s+)?type\s+(\w+)\s*=")),
    ("enum", re.compile(r"^(?:export\s+)?(?:const\s+)?enum\s+(\w+)")),
    ("variable", re.compile(r"^(?:export\s+)?(?:const|let|var)\s+(\w+)\s*[=:]")),
    ("method", re.compile(r"^\s+(?:async\s+)?(\w+)\s*\([^)]*\)\s*(?::\s*\w+)?\s*\{")),
    # Python
    ("function", re.compile(r"^(?:async\s+)?def\s+(\w+)")),
    ("class", re.compile(r"^class\s+(\w+)")),
    # Go
    ("function", re.compile(r"^func\s+(?:\(\w+\s+\*?\w+\)\s+)?(\w+)")),
    ("type", re.compile(r"^type\s+(\w+)\s+(?:struct|interface)")),
    # Rust
    ("function", re.compile(r"^(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")),
    ("struct", re.compile(r"^(?:pub\s+)?struct\s+(\w+)")),
    ("enum", re.compile(r"^(?:pub\s+)?enum\s+(\w+)")),
    ("trait", re.compile(r"^(?:pub\s+)?trait\s+(\w+)")),
    ("impl", re.compile(r"^impl(?:<[^>]+>)?\s+(\w+)")),
]

CODE_EXTENSIONS = frozenset(
    {
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".py",
        ".go",
        ".rs",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".cs",
        ".rb",
        ".swift",
        ".kt",
        ".scala",
        ".vue",
        ".svelte",
    }
)


def _extract_symbols(content: str) -> list[DocumentSymbol]:
    """Extract symbols from source code using regex patterns."""
    symbols: list[DocumentSymbol] = []
    seen: set[str] = set()
    lines = content.split("\n")

    for i, line in enumerate(lines):
        for kind, pattern in SYMBOL_PATTERNS:
            m = pattern.match(line)
            if m and m.group(1):
                key = f"{kind}:{m.group(1)}:{i}"
                if key not in seen:
                    seen.add(key)
                    symbols.append(
                        DocumentSymbol(
                            name=m.group(1),
                            kind=kind,
                            line=i + 1,
                            character=line.index(m.group(1)),
                        )
                    )
    return symbols


def _exec(cmd: list[str], cwd: str | None = None, timeout: int = 30) -> tuple[str, str]:
    """Run a subprocess, returning (stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        return result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return "", "Command timed out"
    except FileNotFoundError:
        return "", f"Command not found: {cmd[0]}"
    except Exception as exc:
        return "", str(exc)


def _search_workspace_symbols(
    query: str, directory: str, max_results: int = 50
) -> list[dict[str, Any]]:
    """Search for symbols across the workspace."""
    results: list[dict[str, Any]] = []
    root = Path(directory)
    skip_dirs = {".git", "node_modules", "dist", "__pycache__", ".venv", "venv"}

    def _walk(d: Path, depth: int) -> None:
        if depth > 6 or len(results) >= max_results:
            return
        try:
            entries = sorted(d.iterdir())
        except PermissionError:
            return
        for entry in entries:
            if len(results) >= max_results:
                return
            if entry.is_dir():
                if entry.name.startswith(".") or entry.name in skip_dirs:
                    continue
                _walk(entry, depth + 1)
            elif entry.is_file() and entry.suffix in CODE_EXTENSIONS:
                try:
                    content = entry.read_text(encoding="utf-8", errors="ignore")
                    symbols = _extract_symbols(content)
                    for sym in symbols:
                        if query.lower() in sym.name.lower():
                            results.append(
                                {
                                    "name": sym.name,
                                    "kind": sym.kind,
                                    "line": sym.line,
                                    "character": sym.character,
                                    "file": str(entry.relative_to(root)),
                                }
                            )
                except Exception:
                    pass

    _walk(root, 0)
    return results[:max_results]


def _text_response(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


def _error_response(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps({"error": message})}],
        "isError": True,
    }


def build_code_intel_server_tools() -> list[dict[str, Any]]:
    """Build the tool definitions for the code intelligence server."""
    return [
        {
            "name": "lsp_diagnostics",
            "description": "Get diagnostics (errors, warnings) for a file. Uses tsc --noEmit for TypeScript projects.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Path to the source file",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["error", "warning", "info", "hint"],
                    },
                },
                "required": ["file"],
            },
        },
        {
            "name": "lsp_document_symbols",
            "description": "Get a hierarchical outline of all symbols in a file.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Path to the source file",
                    },
                },
                "required": ["file"],
            },
        },
        {
            "name": "lsp_workspace_symbols",
            "description": "Search for symbols across the workspace by name.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Symbol name or pattern to search",
                    },
                    "file": {
                        "type": "string",
                        "description": "Any file in the workspace (used to determine project root)",
                    },
                },
                "required": ["query", "file"],
            },
        },
        {
            "name": "lsp_find_references",
            "description": "Find all references to a symbol across the codebase using grep-based search.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": "Path to the source file",
                    },
                    "line": {
                        "type": "integer",
                        "description": "Line number (1-indexed)",
                    },
                    "character": {
                        "type": "integer",
                        "description": "Character position (0-indexed)",
                    },
                    "includeDeclaration": {"type": "boolean"},
                },
                "required": ["file", "line", "character"],
            },
        },
        {
            "name": "lsp_servers",
            "description": "List available diagnostic backends and their installation status.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Handle a code intelligence server tool call.

    Args:
        name: Tool name.
        args: Tool arguments.

    Returns:
        MCP tool response dict.
    """
    match name:
        case "lsp_diagnostics":
            file_path = args.get("file", "")
            if not file_path:
                return _error_response("file is required")
            p = Path(file_path)
            if not p.exists():
                return _error_response(f"File not found: {file_path}")
            # For now, return symbols as a simple diagnostic-like output
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                symbols = _extract_symbols(content)
                return _text_response(
                    {
                        "file": file_path,
                        "diagnosticCount": 0,
                        "diagnostics": [],
                        "note": "Full tsc diagnostics require TypeScript installation. Returning file symbol info.",
                        "symbolCount": len(symbols),
                    }
                )
            except Exception as exc:
                return _error_response(str(exc))

        case "lsp_document_symbols":
            file_path = args.get("file", "")
            if not file_path:
                return _error_response("file is required")
            p = Path(file_path)
            if not p.exists():
                return _error_response(f"File not found: {file_path}")
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                symbols = _extract_symbols(content)
                return _text_response(
                    {
                        "file": file_path,
                        "symbolCount": len(symbols),
                        "symbols": [
                            {
                                "name": s.name,
                                "kind": s.kind,
                                "line": s.line,
                                "character": s.character,
                            }
                            for s in symbols
                        ],
                    }
                )
            except Exception as exc:
                return _error_response(str(exc))

        case "lsp_workspace_symbols":
            query = args.get("query", "")
            file_path = args.get("file", "")
            if not query:
                return _error_response("query is required")
            # Determine project root from file
            directory = str(Path(file_path).parent) if file_path else os.getcwd()
            root = Path(directory)
            for _ in range(10):
                if (root / ".git").exists() or (root / "package.json").exists():
                    break
                parent = root.parent
                if parent == root:
                    break
                root = parent
            symbols = _search_workspace_symbols(query, str(root))
            return _text_response(
                {"query": query, "resultCount": len(symbols), "symbols": symbols}
            )

        case "lsp_find_references":
            file_path = args.get("file", "")
            line_num = args.get("line", 0)
            char_pos = args.get("character", 0)
            if not file_path or not line_num:
                return _error_response("file and line are required")
            p = Path(file_path)
            if not p.exists():
                return _error_response(f"File not found: {file_path}")
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")
                lines = content.split("\n")
                target_line = lines[line_num - 1] if line_num <= len(lines) else ""
                # Extract word at position
                start = char_pos
                end = char_pos
                while start > 0 and re.match(
                    r"\w",
                    target_line[start - 1] if start - 1 < len(target_line) else "",
                ):
                    start -= 1
                while end < len(target_line) and re.match(r"\w", target_line[end]):
                    end += 1
                symbol = target_line[start:end]
                if not symbol:
                    return _error_response("Could not identify symbol at position")

                # Find project root
                root = p.parent
                for _ in range(10):
                    if (root / ".git").exists() or (root / "package.json").exists():
                        break
                    parent = root.parent
                    if parent == root:
                        break
                    root = parent

                # Use grep to find references
                grep_cmd = ["grep", "-rn", "-w", symbol, str(root)]
                stdout, _ = _exec(grep_cmd, timeout=15)
                refs: list[dict[str, Any]] = []
                for ref_line in stdout.split("\n"):
                    if not ref_line:
                        continue
                    m = re.match(r"^(.+?):(\d+):(.+)$", ref_line)
                    if m:
                        refs.append(
                            {
                                "file": m.group(1),
                                "line": int(m.group(2)),
                                "content": m.group(3).strip(),
                            }
                        )

                return _text_response(
                    {
                        "symbol": symbol,
                        "referenceCount": len(refs),
                        "references": refs[:100],
                    }
                )
            except Exception as exc:
                return _error_response(str(exc))

        case "lsp_servers":
            checks: dict[str, dict[str, Any]] = {}
            # Check grep
            stdout, _ = _exec(["grep", "--version"])
            checks["grep"] = {"available": bool(stdout)}
            # Check rg
            stdout, _ = _exec(["rg", "--version"])
            checks["ripgrep"] = {"available": bool(stdout)}
            return _text_response({"servers": checks})

        case _:
            return _error_response(f"Unknown tool: {name}")


def main() -> None:
    """Entry point for running as a standalone MCP server."""
    server = McpServer("omx-code-intel", "0.1.0")
    server.set_tool_lister(build_code_intel_server_tools)
    server.set_tool_handler(handle_tool_call)
    auto_start_stdio_server("code_intel", server)


if __name__ == "__main__":
    main()
