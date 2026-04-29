"""OMX Project Memory & Notepad MCP Server.

Provides persistent project memory and session notepad tools.
Storage: .omx/project-memory.json, .omx/notepad.md

Port of src/mcp/memory-server.ts.
Can be run as: python -m omx.mcp.memory_server
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from omx.mcp.bootstrap import auto_start_stdio_server
from omx.mcp.memory_validation import parse_notepad_prune_days_old
from omx.mcp.protocol import McpServer
from omx.state.paths import resolve_working_directory


def _get_memory_path(wd: str) -> Path:
    return Path(wd) / ".omx" / "project-memory.json"


def _get_notepad_path(wd: str) -> Path:
    return Path(wd) / ".omx" / "notepad.md"


def build_memory_server_tools() -> list[dict[str, Any]]:
    """Build the tool definitions for the memory server."""
    return [
        {
            "name": "project_memory_read",
            "description": "Read project memory. Can read full memory or a specific section.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": [
                            "all",
                            "techStack",
                            "build",
                            "conventions",
                            "structure",
                            "notes",
                            "directives",
                        ],
                    },
                    "workingDirectory": {"type": "string"},
                },
            },
        },
        {
            "name": "project_memory_write",
            "description": "Write/update project memory. Can replace entirely or merge.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "memory": {
                        "type": "object",
                        "description": "Memory object to write",
                    },
                    "merge": {
                        "type": "boolean",
                        "description": "Merge with existing (true) or replace (false)",
                    },
                    "workingDirectory": {"type": "string"},
                },
                "required": ["memory"],
            },
        },
        {
            "name": "project_memory_add_note",
            "description": "Add a categorized note to project memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Note category (build, test, deploy, env, architecture)",
                    },
                    "content": {"type": "string", "description": "Note content"},
                    "workingDirectory": {"type": "string"},
                },
                "required": ["category", "content"],
            },
        },
        {
            "name": "project_memory_add_directive",
            "description": "Add a persistent directive to project memory.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "directive": {
                        "type": "string",
                        "description": "The directive text",
                    },
                    "priority": {"type": "string", "enum": ["high", "normal"]},
                    "context": {"type": "string"},
                    "workingDirectory": {"type": "string"},
                },
                "required": ["directive"],
            },
        },
        {
            "name": "notepad_read",
            "description": "Read notepad content. Can read full or a specific section (priority, working, manual).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "section": {
                        "type": "string",
                        "enum": ["all", "priority", "working", "manual"],
                    },
                    "workingDirectory": {"type": "string"},
                },
            },
        },
        {
            "name": "notepad_write_priority",
            "description": "Write to Priority Context section. Replaces existing. Keep under 500 chars.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Priority content (under 500 chars)",
                    },
                    "workingDirectory": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "notepad_write_working",
            "description": "Add timestamped entry to Working Memory section.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Working memory entry",
                    },
                    "workingDirectory": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "notepad_write_manual",
            "description": "Add entry to Manual section. Never auto-pruned.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Manual entry content",
                    },
                    "workingDirectory": {"type": "string"},
                },
                "required": ["content"],
            },
        },
        {
            "name": "notepad_prune",
            "description": "Prune Working Memory entries older than N days (default: 7).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "daysOld": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Prune entries older than this many days (default: 7)",
                    },
                    "workingDirectory": {"type": "string"},
                },
            },
        },
        {
            "name": "notepad_stats",
            "description": "Get statistics about the notepad (size, entry count, oldest entry).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workingDirectory": {"type": "string"},
                },
            },
        },
    ]


def _extract_section(content: str, section: str) -> str:
    """Extract a section from notepad markdown content."""
    header = f"## {section.upper()}"
    idx = content.find(header)
    if idx < 0:
        return ""
    start = idx + len(header)
    next_header = content.find("\n## ", start)
    if next_header < 0:
        return content[start:].strip()
    return content[start:next_header].strip()


def _replace_section(content: str, section: str, new_content: str) -> str:
    """Replace a section in notepad markdown content."""
    header = f"## {section}"
    idx = content.find(header)
    if idx < 0:
        return content + f"\n\n{header}\n{new_content}\n"
    next_header = content.find("\n## ", idx + len(header))
    if next_header < 0:
        return content[:idx] + f"{header}\n{new_content}\n"
    return content[:idx] + f"{header}\n{new_content}\n" + content[next_header:]


def _append_to_section(content: str, section: str, entry: str) -> str:
    """Append text to a section in notepad markdown content."""
    header = f"## {section}"
    idx = content.find(header)
    if idx < 0:
        return content + f"\n\n{header}{entry}\n"
    next_header = content.find("\n## ", idx + len(header))
    if next_header < 0:
        return content + entry
    return content[:next_header] + entry + content[next_header:]


def _text_response(data: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, indent=2)}]}


def _error_response(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps({"error": message})}],
        "isError": True,
    }


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Handle a memory/notepad server tool call.

    Args:
        name: Tool name.
        args: Tool arguments.

    Returns:
        MCP tool response dict.
    """
    try:
        wd = str(resolve_working_directory(args.get("workingDirectory")))
    except ValueError as exc:
        return _error_response(str(exc))

    match name:
        # === Project Memory ===
        case "project_memory_read":
            mem_path = _get_memory_path(wd)
            if not mem_path.exists():
                return _text_response({"exists": False})
            try:
                data = json.loads(mem_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            section = args.get("section")
            if section and section != "all" and section in data:
                return _text_response(data[section])
            return _text_response(data)

        case "project_memory_write":
            mem_path = _get_memory_path(wd)
            mem_path.parent.mkdir(parents=True, exist_ok=True)
            merge = args.get("merge", False)
            new_mem = args.get("memory", {})
            if merge and mem_path.exists():
                try:
                    existing = json.loads(mem_path.read_text(encoding="utf-8"))
                except Exception:
                    existing = {}
                merged = {**existing, **new_mem}
                mem_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
            else:
                mem_path.write_text(json.dumps(new_mem, indent=2), encoding="utf-8")
            return _text_response({"success": True})

        case "project_memory_add_note":
            mem_path = _get_memory_path(wd)
            mem_path.parent.mkdir(parents=True, exist_ok=True)
            data: dict[str, Any] = {}
            if mem_path.exists():
                try:
                    data = json.loads(mem_path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            if "notes" not in data:
                data["notes"] = []
            data["notes"].append(
                {
                    "category": args.get("category", ""),
                    "content": args.get("content", ""),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            mem_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return _text_response({"success": True, "noteCount": len(data["notes"])})

        case "project_memory_add_directive":
            mem_path = _get_memory_path(wd)
            mem_path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            if mem_path.exists():
                try:
                    data = json.loads(mem_path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            if "directives" not in data:
                data["directives"] = []
            entry: dict[str, Any] = {
                "directive": args.get("directive", ""),
                "priority": args.get("priority", "normal"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if args.get("context"):
                entry["context"] = args["context"]
            data["directives"].append(entry)
            mem_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            return _text_response(
                {"success": True, "directiveCount": len(data["directives"])}
            )

        # === Notepad ===
        case "notepad_read":
            note_path = _get_notepad_path(wd)
            if not note_path.exists():
                return _text_response({"exists": False, "content": ""})
            content = note_path.read_text(encoding="utf-8")
            section = args.get("section")
            if section and section != "all":
                section_content = _extract_section(content, section)
                return _text_response({"section": section, "content": section_content})
            return _text_response({"content": content})

        case "notepad_write_priority":
            note_path = _get_notepad_path(wd)
            note_path.parent.mkdir(parents=True, exist_ok=True)
            priority_content = (args.get("content") or "")[:500]
            existing = (
                note_path.read_text(encoding="utf-8") if note_path.exists() else ""
            )
            existing = _replace_section(existing, "PRIORITY", priority_content)
            _atomic_notepad_write(note_path, existing)
            return _text_response({"success": True})

        case "notepad_write_working":
            note_path = _get_notepad_path(wd)
            note_path.parent.mkdir(parents=True, exist_ok=True)
            entry = f"\n[{datetime.now(timezone.utc).isoformat()}] {args.get('content', '')}"
            existing = (
                note_path.read_text(encoding="utf-8") if note_path.exists() else ""
            )
            existing = _append_to_section(existing, "WORKING MEMORY", entry)
            _atomic_notepad_write(note_path, existing)
            return _text_response({"success": True})

        case "notepad_write_manual":
            note_path = _get_notepad_path(wd)
            note_path.parent.mkdir(parents=True, exist_ok=True)
            entry = f"\n{args.get('content', '')}"
            existing = (
                note_path.read_text(encoding="utf-8") if note_path.exists() else ""
            )
            existing = _append_to_section(existing, "MANUAL", entry)
            _atomic_notepad_write(note_path, existing)
            return _text_response({"success": True})

        case "notepad_prune":
            note_path = _get_notepad_path(wd)
            if not note_path.exists():
                return _text_response({"pruned": 0, "message": "No notepad file found"})
            ok, days, error = parse_notepad_prune_days_old(args.get("daysOld"))
            if not ok:
                return _error_response(error or "Invalid daysOld")
            assert days is not None
            cutoff = time.time() * 1000 - days * 24 * 60 * 60 * 1000
            content = note_path.read_text(encoding="utf-8")
            working_section = _extract_section(content, "WORKING MEMORY")
            if not working_section:
                return _text_response(
                    {"pruned": 0, "message": "No working memory entries found"}
                )
            lines = working_section.split("\n")
            pruned = 0
            kept: list[str] = []
            ts_pattern = re.compile(
                r"^\[(\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?)\]"
            )
            for line in lines:
                m = ts_pattern.match(line)
                if m:
                    try:
                        entry_time = (
                            datetime.fromisoformat(
                                m.group(1).replace("Z", "+00:00")
                            ).timestamp()
                            * 1000
                        )
                    except ValueError:
                        kept.append(line)
                        continue
                    if entry_time < cutoff:
                        pruned += 1
                        continue
                kept.append(line)
            if pruned > 0:
                updated = _replace_section(content, "WORKING MEMORY", "\n".join(kept))
                note_path.write_text(updated, encoding="utf-8")
            remaining = sum(1 for line in kept if ts_pattern.match(line))
            return _text_response({"pruned": pruned, "remaining": remaining})

        case "notepad_stats":
            note_path = _get_notepad_path(wd)
            if not note_path.exists():
                return _text_response(
                    {"exists": False, "size": 0, "entryCount": 0, "oldestEntry": None}
                )
            content = note_path.read_text(encoding="utf-8")
            size = note_path.stat().st_size
            working_section = _extract_section(content, "WORKING MEMORY")
            timestamps: list[str] = []
            if working_section:
                ts_pattern = re.compile(
                    r"^\[(\d{4}-\d{2}-\d{2}T[\d:.]+(?:Z|[+-]\d{2}:\d{2})?)\]"
                )
                for line in working_section.split("\n"):
                    m = ts_pattern.match(line)
                    if m:
                        timestamps.append(m.group(1))
            priority_section = _extract_section(content, "PRIORITY")
            manual_section = _extract_section(content, "MANUAL")
            return _text_response(
                {
                    "exists": True,
                    "size": size,
                    "sections": {
                        "priority": len(priority_section) if priority_section else 0,
                        "working": len(timestamps),
                        "manual": len(
                            [ln for ln in manual_section.split("\n") if ln.strip()]
                        )
                        if manual_section
                        else 0,
                    },
                    "entryCount": len(timestamps),
                    "oldestEntry": timestamps[0] if timestamps else None,
                    "newestEntry": timestamps[-1] if timestamps else None,
                }
            )

        case _:
            return _error_response(f"Unknown tool: {name}")


def _atomic_notepad_write(path: Path, content: str) -> None:
    """Write notepad file atomically."""
    tmp_path = path.with_suffix(f".tmp.{os.getpid()}")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def main() -> None:
    """Entry point for running as a standalone MCP server."""
    server = McpServer("omx-memory", "0.1.0")
    server.set_tool_lister(build_memory_server_tools)
    server.set_tool_handler(handle_tool_call)
    auto_start_stdio_server("memory", server)


if __name__ == "__main__":
    main()
