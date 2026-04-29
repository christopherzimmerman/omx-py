"""Catalog metadata schema.

Port of src/catalog/schema.ts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CatalogEntry:
    """A single entry in the skill/agent/prompt catalog.

    Attributes:
        name: Unique entry name.
        kind: Entry type ("skill", "agent", or "prompt").
        status: Lifecycle status ("active", "internal", "deprecated", "alias", "merged").
        description: Human-readable description.
        canonical: Target name for alias/merged entries.
    """

    name: str
    kind: str  # "skill", "agent", "prompt"
    status: str = "active"  # "active", "internal", "deprecated", "alias", "merged"
    description: str = ""
    canonical: str | None = None  # For alias/merged entries

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
        }
        if self.description:
            d["description"] = self.description
        if self.canonical:
            d["canonical"] = self.canonical
        return d
