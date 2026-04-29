"""MCP server configuration for config.toml / settings.json.

Port of src/config/mcp-registry.ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class UnifiedMcpRegistryServer:
    """A normalized MCP server entry from the unified registry.

    Attributes:
        name: Server name.
        command: Command to start the server.
        args: Arguments for the command.
        enabled: Whether the server is enabled.
        startup_timeout_sec: Optional startup timeout in seconds.
        approval_mode: Optional approval mode string.
    """

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    enabled: bool = True
    startup_timeout_sec: int | None = None
    approval_mode: str | None = None


@dataclass
class UnifiedMcpRegistryLoadResult:
    """Result of loading the unified MCP registry.

    Attributes:
        servers: List of normalized server entries.
        source_path: Path the registry was loaded from.
        warnings: Any warnings generated during loading.
    """

    servers: list[UnifiedMcpRegistryServer] = field(default_factory=list)
    source_path: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class ClaudeCodeSettingsSyncPlan:
    """Plan for syncing MCP servers to Claude Code settings.

    Attributes:
        content: Updated settings.json content (``None`` if no changes).
        added: Names of newly added servers.
        unchanged: Names of already-present servers.
        warnings: Any warnings generated.
    """

    content: str | None = None
    added: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _normalize_timeout(value: Any, name: str, warnings: list[str]) -> int | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or not (value > 0):
        warnings.append(
            f'registry entry "{name}" has invalid timeout; ignoring timeout'
        )
        return None
    return int(value)


def _normalize_approval_mode(value: Any, name: str, warnings: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        warnings.append(
            f'registry entry "{name}" has non-string approval_mode; ignoring approval_mode'
        )
        return None
    return value


def _normalize_entry(
    name: str,
    value: Any,
    warnings: list[str],
) -> UnifiedMcpRegistryServer | None:
    if not _is_record(value):
        warnings.append(f'registry entry "{name}" is not an object; skipping')
        return None
    command = value.get("command")
    if not isinstance(command, str) or not command.strip():
        warnings.append(f'registry entry "{name}" is missing command; skipping')
        return None
    args_value = value.get("args")
    if args_value is not None:
        if not isinstance(args_value, list) or any(
            not isinstance(a, str) for a in args_value
        ):
            warnings.append(f'registry entry "{name}" has non-string args; skipping')
            return None
    enabled_value = value.get("enabled")
    if enabled_value is not None and not isinstance(enabled_value, bool):
        warnings.append(f'registry entry "{name}" has non-boolean enabled; skipping')
        return None

    timeout_candidate = (
        value.get("timeout")
        or value.get("startup_timeout_sec")
        or value.get("startupTimeoutSec")
    )
    approval_mode = _normalize_approval_mode(value.get("approval_mode"), name, warnings)

    return UnifiedMcpRegistryServer(
        name=name,
        command=command,
        args=list(args_value) if args_value else [],
        enabled=enabled_value if enabled_value is not None else True,
        startup_timeout_sec=_normalize_timeout(timeout_candidate, name, warnings),
        approval_mode=approval_mode,
    )


def get_unified_mcp_registry_candidates(home_dir: str | None = None) -> list[str]:
    """Get candidate paths for the unified MCP registry file.

    Args:
        home_dir: Home directory (defaults to user home).

    Returns:
        List of candidate file paths.
    """
    home = home_dir or str(Path.home())
    return [str(Path(home) / ".omx" / "mcp-registry.json")]


def get_legacy_unified_mcp_registry_candidate(home_dir: str | None = None) -> str:
    """Get the legacy MCP registry candidate path.

    Args:
        home_dir: Home directory (defaults to user home).

    Returns:
        Legacy registry file path.
    """
    home = home_dir or str(Path.home())
    return str(Path(home) / ".omc" / "mcp-registry.json")


def load_unified_mcp_registry(
    candidates: list[str] | None = None,
    home_dir: str | None = None,
) -> UnifiedMcpRegistryLoadResult:
    """Load the unified MCP registry from the first available candidate path.

    Args:
        candidates: Explicit candidate paths (overrides default).
        home_dir: Home directory for default candidates.

    Returns:
        Load result with servers, source path, and warnings.
    """
    paths = candidates or get_unified_mcp_registry_candidates(home_dir)
    source_path: str | None = None
    for p in paths:
        if Path(p).exists():
            source_path = p
            break
    if not source_path:
        return UnifiedMcpRegistryLoadResult()

    warnings: list[str] = []
    try:
        parsed = json.loads(Path(source_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        warnings.append(f"failed to parse shared MCP registry at {source_path}: {exc}")
        return UnifiedMcpRegistryLoadResult(source_path=source_path, warnings=warnings)

    if not _is_record(parsed):
        warnings.append(f"shared MCP registry at {source_path} must be a JSON object")
        return UnifiedMcpRegistryLoadResult(source_path=source_path, warnings=warnings)

    servers: list[UnifiedMcpRegistryServer] = []
    for name, value in parsed.items():
        entry = _normalize_entry(name, value, warnings)
        if entry:
            servers.append(entry)

    return UnifiedMcpRegistryLoadResult(
        servers=servers,
        source_path=source_path,
        warnings=warnings,
    )


def _to_claude_code_mcp_config(server: UnifiedMcpRegistryServer) -> dict[str, Any]:
    config: dict[str, Any] = {
        "command": server.command,
        "args": list(server.args),
        "enabled": server.enabled,
    }
    if server.approval_mode is not None:
        config["approval_mode"] = server.approval_mode
    return config


def plan_claude_code_mcp_settings_sync(
    existing_content: str,
    servers: list[UnifiedMcpRegistryServer],
) -> ClaudeCodeSettingsSyncPlan:
    """Plan MCP server entries to sync into Claude Code settings.json.

    Args:
        existing_content: Existing settings.json content.
        servers: Servers to sync.

    Returns:
        Sync plan with content, added, unchanged, and warnings.
    """
    if not servers:
        return ClaudeCodeSettingsSyncPlan()

    trimmed = existing_content.strip()
    if trimmed:
        try:
            parsed = json.loads(existing_content)
        except (json.JSONDecodeError, TypeError) as exc:
            return ClaudeCodeSettingsSyncPlan(
                warnings=[f"failed to parse Claude settings.json: {exc}"],
            )
    else:
        parsed = {}

    if not _is_record(parsed):
        return ClaudeCodeSettingsSyncPlan(
            warnings=["Claude settings.json must contain a JSON object"],
        )

    current_mcp = parsed.get("mcpServers")
    if current_mcp is not None and not _is_record(current_mcp):
        return ClaudeCodeSettingsSyncPlan(
            warnings=['Claude settings.json field "mcpServers" must be an object'],
        )

    next_mcp: dict[str, Any] = dict(current_mcp) if current_mcp else {}
    added: list[str] = []
    unchanged: list[str] = []

    for server in servers:
        if server.name in next_mcp:
            unchanged.append(server.name)
        else:
            next_mcp[server.name] = _to_claude_code_mcp_config(server)
            added.append(server.name)

    if not added:
        return ClaudeCodeSettingsSyncPlan(added=added, unchanged=unchanged)

    result = {**parsed, "mcpServers": next_mcp}
    return ClaudeCodeSettingsSyncPlan(
        content=json.dumps(result, indent=2) + "\n",
        added=added,
        unchanged=unchanged,
    )
