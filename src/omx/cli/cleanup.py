"""Kill orphaned MCP processes.

Port of src/cli/cleanup.ts.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys


def run_cleanup() -> None:
    """Find and kill orphaned OMX MCP server processes.

    Scans running processes for OMX MCP servers whose parent PID is 1
    (orphaned) and sends SIGTERM to each. Only supported on Unix systems.
    """
    if sys.platform == "win32":
        print("Cleanup is not supported on Windows.")
        return

    try:
        result = subprocess.run(
            ["ps", "axww", "-o", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        print("Cannot list processes: 'ps' not found.")
        return

    killed = 0
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # Look for OMX MCP server processes
        if "omx" not in line.lower() or "mcp" not in line.lower():
            continue
        if "server" not in line.lower():
            continue

        parts = line.split(None, 2)
        if len(parts) < 3:
            continue

        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue

        # Check if parent is dead (orphaned)
        if ppid <= 1:
            try:
                os.kill(pid, signal.SIGTERM)
                print(f"  Killed orphaned MCP process: PID {pid}")
                killed += 1
            except OSError:
                pass

    if killed == 0:
        print("No orphaned MCP processes found.")
    else:
        print(f"\nKilled {killed} orphaned process(es).")
