"""Team state root path resolution.

Port of src/team/state-root.ts.
"""

from __future__ import annotations

from pathlib import Path


def team_dir(team_name: str, cwd: str) -> Path:
    """Resolve the team state directory path."""
    return Path(cwd) / ".omx" / "team" / team_name
