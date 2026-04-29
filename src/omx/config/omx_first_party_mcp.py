"""Built-in MCP service definitions.

Port of src/config/omx-first-party-mcp.ts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


OMX_PLUGIN_MCP_COMMAND = "omx"
OMX_PLUGIN_MCP_SERVE_SUBCOMMAND = "mcp-serve"


@dataclass(frozen=True)
class _OmxFirstPartyMcpSpec:
    name: str
    title: str
    entrypoint: str
    plugin_target: str
    startup_timeout_sec: int


_OMX_FIRST_PARTY_MCP_SPECS: tuple[_OmxFirstPartyMcpSpec, ...] = (
    _OmxFirstPartyMcpSpec(
        name="omx_state",
        title="# OMX State Management MCP Server",
        entrypoint="state-server.js",
        plugin_target="state",
        startup_timeout_sec=5,
    ),
    _OmxFirstPartyMcpSpec(
        name="omx_memory",
        title="# OMX Project Memory MCP Server",
        entrypoint="memory-server.js",
        plugin_target="memory",
        startup_timeout_sec=5,
    ),
    _OmxFirstPartyMcpSpec(
        name="omx_code_intel",
        title="# OMX Code Intelligence MCP Server (LSP diagnostics, AST search)",
        entrypoint="code-intel-server.js",
        plugin_target="code-intel",
        startup_timeout_sec=10,
    ),
    _OmxFirstPartyMcpSpec(
        name="omx_trace",
        title="# OMX Trace MCP Server (agent flow timeline & statistics)",
        entrypoint="trace-server.js",
        plugin_target="trace",
        startup_timeout_sec=5,
    ),
    _OmxFirstPartyMcpSpec(
        name="omx_wiki",
        title="# OMX Wiki MCP Server (persistent project knowledge base)",
        entrypoint="wiki-server.js",
        plugin_target="wiki",
        startup_timeout_sec=5,
    ),
)

OMX_FIRST_PARTY_MCP_SERVER_NAMES = [s.name for s in _OMX_FIRST_PARTY_MCP_SPECS]
OMX_FIRST_PARTY_MCP_ENTRYPOINTS = [s.entrypoint for s in _OMX_FIRST_PARTY_MCP_SPECS]
OMX_FIRST_PARTY_MCP_PLUGIN_TARGETS = [
    s.plugin_target for s in _OMX_FIRST_PARTY_MCP_SPECS
]


def resolve_omx_first_party_mcp_entrypoint_for_plugin_target(
    target: str | None,
) -> str | None:
    """Resolve the entrypoint filename for a given plugin target.

    Args:
        target: Plugin target string (e.g. "state", "memory").

    Returns:
        Entrypoint filename or ``None``.
    """
    if not isinstance(target, str):
        return None
    normalized = target.strip().lower()
    if not normalized:
        return None
    for spec in _OMX_FIRST_PARTY_MCP_SPECS:
        if spec.plugin_target == normalized or spec.entrypoint == normalized:
            return spec.entrypoint
    return None


def get_omx_first_party_setup_mcp_servers(
    pkg_root: str,
) -> list[dict[str, Any]]:
    """Get setup-time MCP server configs for first-party OMX servers.

    Args:
        pkg_root: Package root directory.

    Returns:
        List of server config dicts with title, name, command, args, enabled, and startup_timeout_sec.
    """
    return [
        {
            "name": spec.name,
            "title": spec.title,
            "command": "node",
            "args": [os.path.join(pkg_root, "dist", "mcp", spec.entrypoint)],
            "enabled": True,
            "startup_timeout_sec": spec.startup_timeout_sec,
        }
        for spec in _OMX_FIRST_PARTY_MCP_SPECS
    ]


def build_omx_plugin_mcp_manifest() -> dict[str, Any]:
    """Build the MCP manifest for the omx plugin command.

    Returns:
        Dict with ``mcpServers`` key containing all first-party server entries.
    """
    return {
        "mcpServers": {
            spec.name: {
                "command": OMX_PLUGIN_MCP_COMMAND,
                "args": [OMX_PLUGIN_MCP_SERVE_SUBCOMMAND, spec.plugin_target],
                "enabled": True,
            }
            for spec in _OMX_FIRST_PARTY_MCP_SPECS
        },
    }
