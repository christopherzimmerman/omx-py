"""Thin wrapper around tomllib for TOML reading."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any


def read_toml(path: Path) -> dict[str, Any]:
    """Read and parse a TOML file, returning an empty dict if it doesn't exist.

    Args:
        path: Path to the TOML file.

    Returns:
        Parsed dict, or empty dict if the file is absent.
    """
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def parse_toml(text: str) -> dict[str, Any]:
    """Parse a TOML string into a dict.

    Args:
        text: TOML-formatted string.

    Returns:
        Parsed configuration dict.
    """
    return tomllib.loads(text)
