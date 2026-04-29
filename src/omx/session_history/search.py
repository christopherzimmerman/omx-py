"""Session transcript search.

Port of src/session-history/search.ts.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omx.utils.paths import codex_home


@dataclass
class SessionSearchOptions:
    """Options for session history search.

    Attributes:
        query: Search query string.
        limit: Maximum number of results.
        session: Filter by session ID.
        since: Time filter (e.g. '7d', '24h', ISO date).
        project: Filter by project path.
        context: Snippet context length in characters.
        case_sensitive: Whether search is case-sensitive.
        cwd: Working directory.
        now: Current timestamp override.
        codex_home_dir: Codex home directory override.
    """

    query: str = ""
    limit: int | None = None
    session: str | None = None
    since: str | None = None
    project: str | None = None
    context: int | None = None
    case_sensitive: bool = False
    cwd: str | None = None
    now: int | None = None
    codex_home_dir: str | None = None


@dataclass
class SessionSearchResult:
    """A single search result.

    Attributes:
        session_id: Session identifier.
        timestamp: ISO timestamp or None.
        cwd: Working directory of the session.
        transcript_path: Full path to the transcript file.
        transcript_path_relative: Relative path from codex home.
        record_type: Type of the matched record.
        line_number: Line number of the match.
        snippet: Context snippet around the match.
    """

    session_id: str = ""
    timestamp: str | None = None
    cwd: str | None = None
    transcript_path: str = ""
    transcript_path_relative: str = ""
    record_type: str = ""
    line_number: int = 0
    snippet: str = ""


@dataclass
class SessionSearchReport:
    """Report from a session search.

    Attributes:
        query: The search query.
        searched_files: Number of files searched.
        matched_sessions: Number of sessions with matches.
        results: List of search results.
    """

    query: str = ""
    searched_files: int = 0
    matched_sessions: int = 0
    results: list[SessionSearchResult] = field(default_factory=list)


DEFAULT_LIMIT = 10
DEFAULT_CONTEXT = 80
MAX_LIMIT = 100
MAX_CONTEXT = 400
DURATION_RE = re.compile(r"^(\d+)([smhdw])$", re.IGNORECASE)


def _clamp_integer(value: int, fallback: int, maximum: int) -> int:
    """Clamp an integer to a valid range."""
    if not isinstance(value, int) or value < 0:
        return fallback
    return min(value, maximum)


def parse_since_spec(value: str | None, now: int | None = None) -> int | None:
    """Parse a --since value into an epoch millisecond cutoff.

    Args:
        value: Duration string (e.g. '7d', '24h') or ISO date.
        now: Current timestamp in milliseconds.

    Returns:
        Epoch milliseconds cutoff or None.

    Raises:
        ValueError: If the value is invalid.
    """
    if not value:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None

    now_ms = (
        now
        if now is not None
        else int(os.times()[4] * 1000)
        if hasattr(os.times, "__call__")
        else 0
    )
    # Use time module for reliable millisecond timestamp
    import time

    now_ms = now if now is not None else int(time.time() * 1000)

    match = DURATION_RE.match(trimmed)
    if match:
        amount = int(match.group(1))
        unit = match.group(2).lower()
        multipliers = {
            "s": 1000,
            "m": 60_000,
            "h": 3_600_000,
            "d": 86_400_000,
            "w": 604_800_000,
        }
        return now_ms - amount * multipliers[unit]

    # Try ISO date parse
    from datetime import datetime

    try:
        dt = datetime.fromisoformat(trimmed.replace("Z", "+00:00"))
        return int(dt.timestamp() * 1000)
    except ValueError:
        pass

    raise ValueError(
        f'Invalid --since value "{value}". Use formats like 7d, 24h, or 2026-03-10.'
    )


def _safe_parse_json(line: str) -> dict[str, Any] | None:
    """Parse a JSON line without raising."""
    try:
        parsed = json.loads(line)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _as_string(value: Any) -> str | None:
    """Return value as string if non-empty, else None."""
    return value if isinstance(value, str) and value.strip() else None


def _collect_text_fragments(value: Any, fragments: list[str]) -> None:
    """Recursively collect text fragments from a structure."""
    if isinstance(value, str):
        if value.strip():
            fragments.append(value)
        return
    if isinstance(value, list):
        for item in value:
            _collect_text_fragments(item, fragments)
        return
    if not isinstance(value, dict):
        return
    for key, child in value.items():
        if key in ("base_instructions", "developer_instructions"):
            continue
        _collect_text_fragments(child, fragments)


def _build_snippet(
    text: str,
    query: str,
    context: int,
    case_sensitive: bool,
) -> str | None:
    """Build a context snippet around a query match."""
    if not text:
        return None
    haystack = text if case_sensitive else text.lower()
    needle = query if case_sensitive else query.lower()
    index = haystack.find(needle)
    if index < 0:
        return None

    start = max(0, index - context)
    end = min(len(text), index + len(query) + context)
    prefix = "\u2026" if start > 0 else ""
    suffix = "\u2026" if end < len(text) else ""
    body = re.sub(r"\s+", " ", text[start:end]).strip()
    return f"{prefix}{body}{suffix}"


def _list_rollout_files(root: str) -> list[str]:
    """List rollout JSONL files under a directory."""
    root_path = Path(root)
    if not root_path.exists():
        return []
    files: list[str] = []
    queue = [root_path]
    while queue:
        d = queue.pop()
        try:
            for entry in d.iterdir():
                if entry.is_dir():
                    queue.append(entry)
                elif (
                    entry.is_file()
                    and entry.name.startswith("rollout-")
                    and entry.name.endswith(".jsonl")
                ):
                    files.append(str(entry))
        except OSError:
            continue
    return sorted(files, reverse=True)


def search_session_history(options: SessionSearchOptions) -> SessionSearchReport:
    """Search session history transcripts.

    Args:
        options: Search options.

    Returns:
        SessionSearchReport with results.

    Raises:
        ValueError: If the query is empty.
    """
    query = options.query.strip()
    if not query:
        raise ValueError("Search query must not be empty.")

    options.cwd or os.getcwd()
    codex_home_dir = options.codex_home_dir or str(codex_home())
    limit = (
        _clamp_integer(options.limit or DEFAULT_LIMIT, DEFAULT_LIMIT, MAX_LIMIT)
        or DEFAULT_LIMIT
    )
    context = (
        _clamp_integer(options.context or DEFAULT_CONTEXT, DEFAULT_CONTEXT, MAX_CONTEXT)
        or DEFAULT_CONTEXT
    )
    case_sensitive = options.case_sensitive

    import time

    now_ms = options.now or int(time.time() * 1000)
    since_cutoff = parse_since_spec(options.since, now_ms)

    rollout_root = str(Path(codex_home_dir) / "sessions")
    files = _list_rollout_files(rollout_root)

    results: list[SessionSearchResult] = []
    searched_files = 0
    matched_sessions: set[str] = set()

    for file_path in files:
        if len(results) >= limit:
            break

        try:
            mtime_ms = int(Path(file_path).stat().st_mtime * 1000)
        except OSError:
            continue
        if since_cutoff is not None and mtime_ms < since_cutoff:
            continue

        searched_files += 1

        try:
            with open(file_path, encoding="utf-8") as f:
                meta_session_id = Path(file_path).stem.replace("rollout-", "")
                meta_timestamp = None
                meta_cwd = None

                for line_number, line in enumerate(f, 1):
                    if len(results) >= limit:
                        break
                    parsed = _safe_parse_json(line)

                    if (
                        line_number == 1
                        and parsed
                        and parsed.get("type") == "session_meta"
                    ):
                        payload = parsed.get("payload", {})
                        if isinstance(payload, dict):
                            meta_session_id = (
                                _as_string(payload.get("id")) or meta_session_id
                            )
                            meta_timestamp = _as_string(payload.get("timestamp"))
                            meta_cwd = _as_string(payload.get("cwd"))

                    # Simple text-based search in line
                    snippet = _build_snippet(line, query, context, case_sensitive)
                    if snippet:
                        record_type = "raw"
                        if parsed:
                            record_type = _as_string(parsed.get("type")) or "unknown"
                        rel_path = os.path.relpath(file_path, codex_home_dir)
                        results.append(
                            SessionSearchResult(
                                session_id=meta_session_id,
                                timestamp=meta_timestamp,
                                cwd=meta_cwd,
                                transcript_path=file_path,
                                transcript_path_relative=rel_path,
                                record_type=record_type,
                                line_number=line_number,
                                snippet=snippet,
                            )
                        )
                        matched_sessions.add(meta_session_id)
        except OSError:
            continue

    return SessionSearchReport(
        query=query,
        searched_files=searched_files,
        matched_sessions=len(matched_sessions),
        results=results,
    )
