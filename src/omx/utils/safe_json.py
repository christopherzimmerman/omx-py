"""Safe JSON parse/stringify helpers.

Port of src/utils/safe-json.ts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

T = TypeVar("T")


def safe_json_parse(raw: str, fallback: T) -> Any | T:
    """Parse a JSON string, returning *fallback* on failure.

    Args:
        raw: Raw JSON string.
        fallback: Value to return when parsing fails.

    Returns:
        Parsed JSON value or *fallback*.
    """
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback


def safe_read_json_file(file_path: str | Path, fallback: T) -> Any | T:
    """Read and parse a JSON file, returning *fallback* on any error.

    Args:
        file_path: Path to the JSON file.
        fallback: Value to return when reading or parsing fails.

    Returns:
        Parsed JSON value or *fallback*.
    """
    try:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, TypeError, ValueError):
        return fallback
