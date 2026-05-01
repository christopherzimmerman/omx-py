"""HUD authority/permissions.

Port of src/hud/authority.ts.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class RunHudAuthorityTickOptions:
    """Options for running a HUD authority tick.

    Attributes:
        cwd: Working directory.
        node_path: Path to node executable.
        package_root: Package root directory.
        poll_ms: Polling interval in milliseconds.
        timeout_ms: Timeout in milliseconds.
        env: Additional environment variables.
    """

    cwd: str = ""
    node_path: str | None = None
    package_root: str | None = None
    poll_ms: int | None = None
    timeout_ms: int | None = None
    env: dict[str, str] | None = None


def run_hud_authority_tick(options: RunHudAuthorityTickOptions) -> None:
    """Run a single HUD authority tick.

    Writes authority owner state and optionally runs a watcher process.

    Args:
        options: Tick options.
    """
    cwd = options.cwd
    state_dir = Path(cwd) / ".omx" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    authority_owner_path = state_dir / "notify-fallback-authority-owner.json"
    try:
        authority_data = {
            "owner": "hud",
            "pid": os.getpid(),
            "cwd": cwd,
            "heartbeat_at": datetime.now(timezone.utc).isoformat(),
        }
        authority_owner_path.write_text(
            json.dumps(authority_data, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass

    # In the Python port, the watcher subprocess is a no-op structural stub
    # since the Node.js watcher scripts don't apply directly.
