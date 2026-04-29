"""MCP server lifecycle management.

Handles auto-start, parent watchdog, and duplicate detection.
Port of src/mcp/bootstrap.ts.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from typing import Any

from omx.mcp.protocol import McpServer

MCP_SERVER_NAMES = ("state", "memory", "code_intel", "trace", "wiki")

SERVER_DISABLE_ENV: dict[str, str] = {
    "state": "OMX_STATE_SERVER_DISABLE_AUTO_START",
    "memory": "OMX_MEMORY_SERVER_DISABLE_AUTO_START",
    "code_intel": "OMX_CODE_INTEL_SERVER_DISABLE_AUTO_START",
    "trace": "OMX_TRACE_SERVER_DISABLE_AUTO_START",
    "wiki": "OMX_WIKI_SERVER_DISABLE_AUTO_START",
}

GLOBAL_DISABLE_ENV = "OMX_MCP_SERVER_DISABLE_AUTO_START"
PARENT_WATCHDOG_INTERVAL_ENV = "OMX_MCP_PARENT_WATCHDOG_INTERVAL_MS"
DEFAULT_PARENT_WATCHDOG_INTERVAL_MS = 1000


def should_auto_start(server_name: str) -> bool:
    """Check if the MCP server should auto-start based on environment."""
    if os.environ.get(GLOBAL_DISABLE_ENV) == "1":
        return False
    disable_env = SERVER_DISABLE_ENV.get(server_name, "")
    if disable_env and os.environ.get(disable_env) == "1":
        return False
    return True


def is_parent_alive(parent_pid: int) -> bool:
    """Check if the parent process is still running."""
    if parent_pid <= 1:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, parent_pid
        )
        if handle == 0:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(parent_pid, 0)
        return True
    except PermissionError:
        return True  # process exists but we can't signal it
    except OSError:
        return False


def auto_start_stdio_server(server_name: str, server: McpServer) -> None:
    """Start the MCP server with parent watchdog."""
    if not should_auto_start(server_name):
        return

    parent_pid = os.getppid()
    interval_ms = _read_positive_int_env(
        PARENT_WATCHDOG_INTERVAL_ENV,
        DEFAULT_PARENT_WATCHDOG_INTERVAL_MS,
    )

    def watchdog() -> None:
        """Periodically check if parent is alive, exit if not."""
        import time

        while True:
            time.sleep(interval_ms / 1000.0)
            if not is_parent_alive(parent_pid):
                os._exit(0)

    if parent_pid > 1:
        t = threading.Thread(target=watchdog, daemon=True)
        t.start()

    def handle_signal(signum: int, frame: Any) -> None:
        sys.exit(0)

    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)

    server.run()


def _read_positive_int_env(name: str, fallback: int) -> int:
    raw = os.environ.get(name, "")
    if not raw.strip():
        return fallback
    try:
        val = int(raw)
        return val if val > 0 else fallback
    except ValueError:
        return fallback
