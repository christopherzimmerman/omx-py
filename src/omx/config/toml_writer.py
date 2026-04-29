"""Minimal TOML serializer for the subset OMX uses.

Handles: strings, ints, floats, bools, lists, and tables (dicts).
Does not handle inline tables, datetime, or multi-line strings.
"""

from __future__ import annotations

from typing import Any


def dumps(data: dict[str, Any]) -> str:
    """Serialize a dict to a TOML-formatted string.

    Handles strings, ints, floats, bools, lists, and nested tables.

    Args:
        data: Configuration dict to serialize.

    Returns:
        TOML-formatted string with trailing newline.
    """
    lines: list[str] = []
    _write_table(data, lines, prefix="")
    return "\n".join(lines) + "\n" if lines else ""


def _write_table(data: dict[str, Any], lines: list[str], prefix: str) -> None:
    # First pass: write simple key-value pairs
    for key, value in data.items():
        if isinstance(value, dict):
            continue
        lines.append(f"{_escape_key(key)} = {_format_value(value)}")

    # Second pass: write sub-tables
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        full_key = f"{prefix}.{key}" if prefix else key
        if lines:
            lines.append("")
        lines.append(f"[{full_key}]")
        _write_table(value, lines, prefix=full_key)


def _escape_key(key: str) -> str:
    if all(c.isalnum() or c in "-_" for c in key) and key:
        return key
    return f'"{_escape_string(key)}"'


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, str):
        return f'"{_escape_string(value)}"'
    if isinstance(value, dict):
        # Inline table: {key = "val", key2 = 123}
        items = ", ".join(
            f"{_escape_key(k)} = {_format_value(v)}" for k, v in value.items()
        )
        return f"{{{items}}}"
    if isinstance(value, list):
        items = ", ".join(_format_value(item) for item in value)
        return f"[{items}]"
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__}")


def _escape_string(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )
