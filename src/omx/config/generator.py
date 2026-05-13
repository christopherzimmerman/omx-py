"""Config.toml generation, reading, and merging.

Port of ``src/config/generator.ts``.

TOML structure reminder: bare ``key = value`` pairs after a ``[table]`` header
belong to that table. Top-level (root-table) keys MUST appear before the
first ``[table]`` header. This module therefore splits its output into:

1. Top-level keys (``notify``, ``model_reasoning_effort``,
   ``developer_instructions``).
2. ``[features]`` flags.
3. ``[table]`` sections (``env``, ``mcp_servers``, ``tui``).

The Python port works on raw TOML strings (mirroring the TS implementation)
to preserve user comments and ordering. ``tomllib`` is used only for
validation in ``parseStandaloneToml`` / launcher-timeout repair targets.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from omx.agents.roles import AGENT_BY_NAME
from omx.config.mcp_registry import UnifiedMcpRegistryServer
from omx.config.omx_first_party_mcp import get_omx_first_party_setup_mcp_servers
from omx.config.toml_writer import dumps
from omx.utils.paths import codex_config_path
from omx.utils.toml_read import read_toml

# ---------------------------------------------------------------------------
# Constants (mirror generator.ts)
# ---------------------------------------------------------------------------

# The TS port reads ``DEFAULT_FRONTIER_MODEL`` from ``config/models.ts``.
# The Python ``omx.config.models`` module is intentionally not extended here
# (scope-locked); the literal is mirrored from ``team/runtime_helpers``.
_DEFAULT_SETUP_MODEL = "gpt-5.5"
_DEFAULT_SETUP_MODEL_CONTEXT_WINDOW = 250000
_DEFAULT_SETUP_MODEL_AUTO_COMPACT_TOKEN_LIMIT = 200000

_OMX_TOP_LEVEL_KEYS: tuple[str, ...] = (
    "notify",
    "model_reasoning_effort",
    "developer_instructions",
)

_OMX_TOP_LEVEL_HEADER_COMMENT = (
    "# oh-my-codex top-level settings (must be before any [table])"
)

_OMX_SEEDED_BEHAVIORAL_DEFAULTS_START_MARKER = (
    "# oh-my-codex seeded behavioral defaults (uninstall removes unchanged defaults)"
)
_OMX_SEEDED_BEHAVIORAL_DEFAULTS_END_MARKER = (
    "# End oh-my-codex seeded behavioral defaults"
)

OMX_DEVELOPER_INSTRUCTIONS = (
    "You have oh-my-codex installed. AGENTS.md is your orchestration brain "
    "and the main orchestration surface. Use skill/keyword routing like "
    "$name plus spawned role-specialized subagents for specialized work. "
    "Codex native subagents are available via .codex/agents and may be used "
    "for independent parallel subtasks within a single session or team "
    "pane. Skills are loaded from installed SKILL.md files under "
    ".codex/skills, not from native agent TOMLs. Use workflow skills via "
    "$name when explicitly invoked or clearly routed by AGENTS.md. Treat "
    "installed prompts as narrower internal execution surfaces under "
    "AGENTS.md authority, even when user-facing docs prefer $name keywords."
)

_SHARED_MCP_REGISTRY_MARKER = "oh-my-codex (OMX) Shared MCP Registry Sync"
_SHARED_MCP_REGISTRY_END_MARKER = "# End oh-my-codex shared MCP registry sync"
_OMX_CONFIG_MARKER = "oh-my-codex (OMX) Configuration"
_OMX_CONFIG_END_MARKER = "# End oh-my-codex"

_OMX_AGENTS_MAX_THREADS = 6
_OMX_AGENTS_MAX_DEPTH = 2
_OMX_EXPLORE_ROUTING_DEFAULT = "1"
_OMX_EXPLORE_CMD_ENV = "USE_OMX_EXPLORE_CMD"
_DEFAULT_LAUNCHER_MCP_STARTUP_TIMEOUT_SEC = 15

_OMX_TUI_STATUS_LINE = (
    'status_line = ["model-with-reasoning", "git-branch", '
    '"context-remaining", "total-input-tokens", "total-output-tokens", '
    '"five-hour-limit", "weekly-limit"]'
)

_LEGACY_OMX_TEAM_RUN_TABLE_PATTERN = re.compile(
    r'^\s*\[mcp_servers\.(?:"omx_team_run"|omx_team_run)\]\s*$',
    re.MULTILINE,
)
_ROOT_TABLE_HEADER_PATTERN = re.compile(r"^\s*\[\[?[^\]]+\]?\]\s*$")
_ROOT_KEY_ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Za-z0-9_-]+)\s*=\s*(.*)$")
_TABLE_HEADER_AT_LINE_PATTERN = re.compile(r"^\s*\[")
_LEADING_TABLE_HEADER_PATTERN = re.compile(r"^\s*\[[^\]]+\]\s*$")
_LEGACY_AGENT_TABLE_PATTERN = re.compile(r'^agents\.(?:"([^"]+)"|(\w[\w-]*))$')
_OMX_HEADER_COMMENT_PATTERN = re.compile(r"^#\s*(OMX|oh-my-codex)", re.IGNORECASE)
_NOTIFY_INLINE_PATTERN = re.compile(
    r'^\s*notify\s*=\s*\["node",\s*".*notify-hook\.js"\]\s*$(\n)?',
    re.MULTILINE,
)
_NOTIFY_ORPHAN_FRAGMENT_PATTERN = re.compile(
    r'\n?\s*"node",\s*\n\s*".*notify-hook\.js",\s*\n\s*\]\s*(?=\n|$)'
)
_TUI_HEADER_PATTERN = re.compile(r"^\s*\[tui\]\s*$")
_FEATURES_HEADER_PATTERN = re.compile(r"^\s*\[features\]\s*$")
_ENV_HEADER_PATTERN = re.compile(r"^\s*\[env\]\s*$")
_AGENTS_HEADER_PATTERN = re.compile(r"^\s*\[agents\]\s*$")
_MCP_SERVERS_OMX_PREFIX_PATTERN = re.compile(r"^mcp_servers\.omx_")
_BLANK_LINE_PATTERN = re.compile(r"\n{3,}")


# ---------------------------------------------------------------------------
# Public API: file read/write (existing helpers preserved)
# ---------------------------------------------------------------------------


def read_config(path: Path | None = None) -> dict[str, Any]:
    """Read the Codex ``config.toml`` file as a dict."""
    return read_toml(path or codex_config_path())


def write_config(config: dict[str, Any], path: Path | None = None) -> None:
    """Write a config dict to ``config.toml``."""
    target = path or codex_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(dumps(config), encoding="utf-8")


def deep_merge_dicts(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``overlay`` into ``base``, returning a new dict.

    Nested dicts are merged recursively; all other values are overwritten.
    Overlay values take precedence.

    Args:
        base: Base configuration dict.
        overlay: Overlay dict whose values take precedence.

    Returns:
        New merged dict (does not mutate inputs).
    """
    merged = dict(base)
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


# ---------------------------------------------------------------------------
# MergeOptions
# ---------------------------------------------------------------------------


@dataclass
class MergeOptions:
    """Options for ``build_merged_config`` / ``merge_config`` / ``repair_config_if_needed``.

    Mirrors the TS ``MergeOptions`` shape.

    Attributes:
        include_tui: When ``False``, no ``[tui]`` table is emitted.
        model_override: Force the top-level ``model`` key to this value.
        shared_mcp_servers: Servers to emit inside the Shared MCP Registry block.
        shared_mcp_registry_source: Source path emitted as a comment.
        verbose: When ``True``, ``merge_config`` prints progress messages.
        pkg_root: Package root used to build emitted file paths (notify hook +
            first-party MCP servers). Optional; falls back to the package
            install root when omitted.
    """

    include_tui: bool = True
    model_override: str | None = None
    shared_mcp_servers: list[UnifiedMcpRegistryServer] = field(default_factory=list)
    shared_mcp_registry_source: str | None = None
    verbose: bool = False
    pkg_root: str | None = None


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------


def _escape_toml_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _split_lines(text: str) -> list[str]:
    return re.split(r"\r?\n", text)


def _parse_standalone_toml(snippet: str) -> bool:
    try:
        tomllib.loads(snippet)
    except tomllib.TOMLDecodeError:
        return False
    return True


def _unwrap_toml_string(value: str | None) -> str | None:
    if value is None:
        return None
    match = re.match(r'^"(.*)"$', value)
    return match.group(1) if match else None


@dataclass
class _RootLevelEntry:
    lines: list[str]
    key: str | None = None


def _split_root_level_entries(config: str) -> tuple[list[_RootLevelEntry], list[str]]:
    """Split a config string into top-level entries and the table-section remainder.

    Mirrors ``splitRootLevelEntries`` in TS. Multiline values (e.g. triple-quoted
    strings, multi-line arrays) are kept together by greedily appending lines
    until the accumulated text parses as standalone TOML.
    """
    lines = _split_lines(config)
    entries: list[_RootLevelEntry] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if _ROOT_TABLE_HEADER_PATTERN.match(line):
            break

        match = _ROOT_KEY_ASSIGNMENT_PATTERN.match(line)
        if not match:
            entries.append(_RootLevelEntry(lines=[line]))
            index += 1
            continue

        entry_lines = [line]
        while not _parse_standalone_toml("\n".join(entry_lines)) and (
            index + len(entry_lines) < len(lines)
        ):
            entry_lines.append(lines[index + len(entry_lines)])

        entries.append(_RootLevelEntry(key=match.group(1), lines=entry_lines))
        index += len(entry_lines)

    return entries, lines[index:]


def _parse_root_key_values(config: str) -> dict[str, str]:
    values: dict[str, str] = {}
    entries, _ = _split_root_level_entries(config)
    for entry in entries:
        if entry.key is None:
            continue
        first_line = entry.lines[0]
        match = _ROOT_KEY_ASSIGNMENT_PATTERN.match(first_line)
        if not match:
            continue
        rest = entry.lines[1:]
        value = "\n".join([match.group(2), *rest]).strip()
        values[entry.key] = value
    return values


# ---------------------------------------------------------------------------
# Legacy/exported predicates (TS-equivalent)
# ---------------------------------------------------------------------------


def has_legacy_omx_team_run_table(config: str) -> bool:
    """Return ``True`` if the config still contains the retired team-run MCP table."""
    return bool(_LEGACY_OMX_TEAM_RUN_TABLE_PATTERN.search(config))


def get_root_model_name(config: str) -> str | None:
    """Return the value of the root-level ``model = "..."`` assignment if present."""
    return _unwrap_toml_string(_parse_root_key_values(config).get("model"))


# ---------------------------------------------------------------------------
# Behavioral-defaults block
# ---------------------------------------------------------------------------


def _is_unchanged_omx_seeded_behavioral_defaults_block(lines: list[str]) -> bool:
    relevant = [
        line for line in lines if line.strip() and not line.strip().startswith("#")
    ]
    if len(relevant) != 2:
        return False
    parsed = _parse_root_key_values("\n".join(relevant))
    return (
        len(parsed) == 2
        and parsed.get("model_context_window")
        == str(_DEFAULT_SETUP_MODEL_CONTEXT_WINDOW)
        and parsed.get("model_auto_compact_token_limit")
        == str(_DEFAULT_SETUP_MODEL_AUTO_COMPACT_TOKEN_LIMIT)
    )


def strip_omx_seeded_behavioral_defaults(config: str) -> str:
    """Strip the OMX-managed behavioral defaults marker block.

    User-modified values inside the block are preserved (the markers are
    removed but the lines themselves survive). Lines exactly matching the
    seeded defaults are removed entirely.
    """
    lines = _split_lines(config)
    first_table = next(
        (
            i
            for i, line in enumerate(lines)
            if _TABLE_HEADER_AT_LINE_PATTERN.match(line)
        ),
        -1,
    )
    boundary = first_table if first_table >= 0 else len(lines)
    result: list[str] = []
    index = 0

    while index < len(lines):
        trimmed = lines[index].strip()

        if index < boundary and trimmed == _OMX_SEEDED_BEHAVIORAL_DEFAULTS_START_MARKER:
            end_index = -1
            for candidate in range(index + 1, boundary):
                if (
                    lines[candidate].strip()
                    == _OMX_SEEDED_BEHAVIORAL_DEFAULTS_END_MARKER
                ):
                    end_index = candidate
                    break

            if end_index < 0:
                index += 1
                continue

            block_lines = lines[index + 1 : end_index]
            if not _is_unchanged_omx_seeded_behavioral_defaults_block(block_lines):
                result.extend(block_lines)
            index = end_index + 1
            continue

        if index < boundary and trimmed == _OMX_SEEDED_BEHAVIORAL_DEFAULTS_END_MARKER:
            index += 1
            continue

        result.append(lines[index])
        index += 1

    return "\n".join(result)


# ---------------------------------------------------------------------------
# Root-level key strippers
# ---------------------------------------------------------------------------


def _strip_root_level_keys(config: str, keys: tuple[str, ...]) -> str:
    entries, remainder = _split_root_level_entries(config)

    targeting_omx_key = any(key in _OMX_TOP_LEVEL_KEYS for key in keys)

    filtered: list[_RootLevelEntry] = []
    for entry in entries:
        if (
            targeting_omx_key
            and len(entry.lines) == 1
            and entry.lines[0].strip() == _OMX_TOP_LEVEL_HEADER_COMMENT
        ):
            continue
        if entry.key and entry.key in keys:
            continue
        filtered.append(entry)

    out: list[str] = []
    for entry in filtered:
        out.extend(entry.lines)
    out.extend(remainder)

    if not out:
        return ""
    return "\n".join(out)


def _strip_orphaned_managed_notify(config: str) -> str:
    config = _NOTIFY_INLINE_PATTERN.sub("", config)
    config = _NOTIFY_ORPHAN_FRAGMENT_PATTERN.sub("", config)
    return config


def strip_omx_top_level_keys(config: str) -> str:
    """Remove OMX-owned top-level keys (notify, reasoning effort, instructions).

    Also removes the comment-line header that precedes them so the upsert
    layer can reinsert it cleanly.
    """
    return _strip_root_level_keys(config, _OMX_TOP_LEVEL_KEYS)


# ---------------------------------------------------------------------------
# [features] upsert / strip
# ---------------------------------------------------------------------------


def _upsert_feature_flags(config: str) -> str:
    lines = _split_lines(config)
    features_start = next(
        (i for i, line in enumerate(lines) if _FEATURES_HEADER_PATTERN.match(line)),
        -1,
    )

    if features_start < 0:
        base = config.rstrip()
        feature_block = "\n".join(
            [
                "[features]",
                "multi_agent = true",
                "child_agents_md = true",
                "codex_hooks = true",
                "",
            ]
        )
        if not base:
            return feature_block
        return f"{base}\n{feature_block}"

    section_end = len(lines)
    for i in range(features_start + 1, len(lines)):
        if _ROOT_TABLE_HEADER_PATTERN.match(lines[i]):
            section_end = i
            break

    # Remove deprecated 'collab' key (superseded by multi_agent)
    for i in range(section_end - 1, features_start, -1):
        if re.match(r"^\s*collab\s*=", lines[i]):
            del lines[i]
            section_end -= 1

    multi_agent_idx = -1
    child_agents_idx = -1
    codex_hooks_idx = -1
    for i in range(features_start + 1, section_end):
        if re.match(r"^\s*multi_agent\s*=", lines[i]):
            multi_agent_idx = i
        elif re.match(r"^\s*child_agents_md\s*=", lines[i]):
            child_agents_idx = i
        elif re.match(r"^\s*codex_hooks\s*=", lines[i]):
            codex_hooks_idx = i

    if multi_agent_idx >= 0:
        lines[multi_agent_idx] = "multi_agent = true"
    else:
        lines.insert(section_end, "multi_agent = true")
        section_end += 1

    if child_agents_idx >= 0:
        lines[child_agents_idx] = "child_agents_md = true"
    else:
        lines.insert(section_end, "child_agents_md = true")
        section_end += 1

    if codex_hooks_idx >= 0:
        lines[codex_hooks_idx] = "codex_hooks = true"
    else:
        lines.insert(section_end, "codex_hooks = true")

    return "\n".join(lines)


def upsert_codex_hooks_feature_flag(config: str) -> str:
    """Ensure ``codex_hooks = true`` exists in ``[features]``.

    Adds the section header if it is missing. Used by the bare-minimum hook
    install path that does not want to seed the full multi-agent flags.
    """
    lines = _split_lines(config)
    features_start = next(
        (i for i, line in enumerate(lines) if _FEATURES_HEADER_PATTERN.match(line)),
        -1,
    )

    if features_start < 0:
        base = config.rstrip()
        feature_block = "\n".join(["[features]", "codex_hooks = true", ""])
        if not base:
            return feature_block
        return f"{base}\n{feature_block}"

    section_end = len(lines)
    for i in range(features_start + 1, len(lines)):
        if _ROOT_TABLE_HEADER_PATTERN.match(lines[i]):
            section_end = i
            break

    codex_hooks_idx = -1
    for i in range(features_start + 1, section_end):
        if re.match(r"^\s*codex_hooks\s*=", lines[i]):
            codex_hooks_idx = i
            break

    if codex_hooks_idx >= 0:
        lines[codex_hooks_idx] = "codex_hooks = true"
    else:
        lines.insert(section_end, "codex_hooks = true")

    return "\n".join(lines)


def strip_omx_feature_flags(config: str) -> str:
    """Remove OMX-owned feature flags from ``[features]``; drop empty section."""
    lines = _split_lines(config)
    features_start = next(
        (i for i, line in enumerate(lines) if _FEATURES_HEADER_PATTERN.match(line)),
        -1,
    )

    if features_start < 0:
        return config

    section_end = len(lines)
    for i in range(features_start + 1, len(lines)):
        if _ROOT_TABLE_HEADER_PATTERN.match(lines[i]):
            section_end = i
            break

    omx_flags = ("multi_agent", "child_agents_md", "codex_hooks", "collab")
    filtered: list[str] = []
    for i, line in enumerate(lines):
        if features_start < i < section_end:
            is_omx_flag = any(re.match(rf"^\s*{flag}\s*=", line) for flag in omx_flags)
            if is_omx_flag:
                continue
        filtered.append(line)

    # If [features] section is empty after the strip, remove the header.
    new_features_start = next(
        (i for i, line in enumerate(filtered) if _FEATURES_HEADER_PATTERN.match(line)),
        -1,
    )
    if new_features_start >= 0:
        new_section_end = len(filtered)
        for i in range(new_features_start + 1, len(filtered)):
            if _ROOT_TABLE_HEADER_PATTERN.match(filtered[i]):
                new_section_end = i
                break
        section_content = filtered[new_features_start + 1 : new_section_end]
        if all(line.strip() == "" for line in section_content):
            del filtered[new_features_start:new_section_end]

    return "\n".join(filtered)


# ---------------------------------------------------------------------------
# [env] upsert / strip
# ---------------------------------------------------------------------------


def _upsert_env_settings(config: str) -> str:
    lines = _split_lines(config)
    env_start = next(
        (i for i, line in enumerate(lines) if _ENV_HEADER_PATTERN.match(line)),
        -1,
    )

    if env_start < 0:
        base = config.rstrip()
        env_block = "\n".join(
            [
                "[env]",
                f'{_OMX_EXPLORE_CMD_ENV} = "{_OMX_EXPLORE_ROUTING_DEFAULT}"',
                "",
            ]
        )
        if not base:
            return env_block
        return f"{base}\n\n{env_block}"

    section_end = len(lines)
    for i in range(env_start + 1, len(lines)):
        if _ROOT_TABLE_HEADER_PATTERN.match(lines[i]):
            section_end = i
            break

    explore_routing_idx = -1
    for i in range(env_start + 1, section_end):
        if re.match(rf"^\s*{_OMX_EXPLORE_CMD_ENV}\s*=", lines[i]):
            explore_routing_idx = i
            break

    if explore_routing_idx < 0:
        lines.insert(
            section_end,
            f'{_OMX_EXPLORE_CMD_ENV} = "{_OMX_EXPLORE_ROUTING_DEFAULT}"',
        )

    return "\n".join(lines)


def strip_omx_env_settings(config: str) -> str:
    """Remove the OMX-owned ``USE_OMX_EXPLORE_CMD`` key from ``[env]``.

    Drops the ``[env]`` header if the section is empty afterward.
    """
    lines = _split_lines(config)
    env_start = next(
        (i for i, line in enumerate(lines) if _ENV_HEADER_PATTERN.match(line)),
        -1,
    )

    if env_start < 0:
        return config

    section_end = len(lines)
    for i in range(env_start + 1, len(lines)):
        if _ROOT_TABLE_HEADER_PATTERN.match(lines[i]):
            section_end = i
            break

    filtered: list[str] = []
    for i, line in enumerate(lines):
        if env_start < i < section_end:
            if re.match(rf"^\s*{_OMX_EXPLORE_CMD_ENV}\s*=", line):
                continue
        filtered.append(line)

    new_env_start = next(
        (i for i, line in enumerate(filtered) if _ENV_HEADER_PATTERN.match(line)),
        -1,
    )
    if new_env_start >= 0:
        new_section_end = len(filtered)
        for i in range(new_env_start + 1, len(filtered)):
            if _ROOT_TABLE_HEADER_PATTERN.match(filtered[i]):
                new_section_end = i
                break
        env_content = filtered[new_env_start + 1 : new_section_end]
        if all(line.strip() == "" for line in env_content):
            del filtered[new_env_start:new_section_end]

    return "\n".join(filtered)


# ---------------------------------------------------------------------------
# [agents] upsert
# ---------------------------------------------------------------------------


def _upsert_agents_settings(config: str) -> str:
    lines = _split_lines(config)
    agents_start = next(
        (i for i, line in enumerate(lines) if _AGENTS_HEADER_PATTERN.match(line)),
        -1,
    )

    if agents_start < 0:
        base = config.rstrip()
        agents_block = "\n".join(
            [
                "[agents]",
                f"max_threads = {_OMX_AGENTS_MAX_THREADS}",
                f"max_depth = {_OMX_AGENTS_MAX_DEPTH}",
                "",
            ]
        )
        if not base:
            return agents_block
        return f"{base}\n\n{agents_block}"

    section_end = len(lines)
    for i in range(agents_start + 1, len(lines)):
        if _ROOT_TABLE_HEADER_PATTERN.match(lines[i]):
            section_end = i
            break

    max_threads_idx = -1
    max_depth_idx = -1
    for i in range(agents_start + 1, section_end):
        if re.match(r"^\s*max_threads\s*=", lines[i]):
            max_threads_idx = i
        elif re.match(r"^\s*max_depth\s*=", lines[i]):
            max_depth_idx = i

    if max_threads_idx < 0:
        lines.insert(section_end, f"max_threads = {_OMX_AGENTS_MAX_THREADS}")
        section_end += 1
    if max_depth_idx < 0:
        lines.insert(section_end, f"max_depth = {_OMX_AGENTS_MAX_DEPTH}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orphaned OMX table sections (no marker block)
# ---------------------------------------------------------------------------


def _is_legacy_omx_agent_section(table_name: str) -> bool:
    match = _LEGACY_AGENT_TABLE_PATTERN.match(table_name)
    if not match:
        return False
    name = match.group(1) or match.group(2) or ""
    return name in AGENT_BY_NAME


def _strip_orphaned_omx_sections(config: str) -> str:
    """Strip OMX-owned table sections living outside the marker block.

    Covers legacy configs written before markers existed and configs whose
    marker was accidentally removed. Targets ``[mcp_servers.omx_*]`` and
    legacy ``[agents.<role>]`` entries.

    ``[tui]`` is intentionally NOT touched here — only the marker-block-aware
    ``strip_existing_omx_blocks`` handles user-owned ``[tui]`` correctly.
    """
    lines = _split_lines(config)
    result: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        table_match = _LEADING_TABLE_HEADER_PATTERN.match(line)
        table_name = ""
        if table_match:
            table_name = re.match(r"^\s*\[([^\]]+)\]\s*$", line).group(1)

        if table_name:
            is_omx_section = bool(
                _MCP_SERVERS_OMX_PREFIX_PATTERN.match(table_name)
            ) or _is_legacy_omx_agent_section(table_name)
            if is_omx_section:
                while result:
                    last = result[-1]
                    if last.strip() == "" or _OMX_HEADER_COMMENT_PATTERN.match(last):
                        result.pop()
                    else:
                        break
                i += 1
                while i < len(lines) and not _TABLE_HEADER_AT_LINE_PATTERN.match(
                    lines[i]
                ):
                    i += 1
                continue

        result.append(line)
        i += 1

    return "\n".join(result)


# ---------------------------------------------------------------------------
# [tui] merge
# ---------------------------------------------------------------------------


@dataclass
class _TuiUpsertResult:
    cleaned: str
    had_existing_tui: bool


def _upsert_tui_status_line(config: str) -> _TuiUpsertResult:
    lines = _split_lines(config)
    sections: list[tuple[int, int]] = []

    i = 0
    while i < len(lines):
        if not _TUI_HEADER_PATTERN.match(lines[i]):
            i += 1
            continue

        end = len(lines)
        for j in range(i + 1, len(lines)):
            if _ROOT_TABLE_HEADER_PATTERN.match(lines[j]):
                end = j
                break
        sections.append((i, end))
        i = end

    if not sections:
        return _TuiUpsertResult(cleaned=config, had_existing_tui=False)

    preserved_key_lines: list[str] = []
    seen_keys: set[str] = set()

    for start, end in sections:
        for k in range(start + 1, end):
            trimmed = lines[k].strip()
            if not trimmed or trimmed.startswith("#"):
                continue
            key_match = re.match(r"^([A-Za-z0-9_-]+)\s*=", trimmed)
            if not key_match:
                continue
            key = key_match.group(1)
            if key == "status_line" or key in seen_keys:
                continue
            seen_keys.add(key)
            preserved_key_lines.append(trimmed)

    merged_section = ["[tui]", *preserved_key_lines, _OMX_TUI_STATUS_LINE]
    first_start = sections[0][0]
    rebuilt: list[str] = []

    i = 0
    while i < len(lines):
        section = next((s for s in sections if s[0] == i), None)
        if section is not None:
            if i == first_start:
                if rebuilt and rebuilt[-1].strip() != "":
                    rebuilt.append("")
                rebuilt.extend(merged_section)
                rebuilt.append("")
            i = section[1]
            continue
        rebuilt.append(lines[i])
        i += 1

    cleaned = _BLANK_LINE_PATTERN.sub("\n\n", "\n".join(rebuilt))
    return _TuiUpsertResult(cleaned=cleaned, had_existing_tui=True)


# ---------------------------------------------------------------------------
# Marker-bounded OMX block strippers
# ---------------------------------------------------------------------------


def _strip_marker_block(
    config: str,
    start_marker: str,
    end_marker: str,
) -> tuple[str, int]:
    cleaned = config
    removed = 0

    while True:
        marker_idx = cleaned.find(start_marker)
        if marker_idx < 0:
            break

        block_start = cleaned.rfind("\n", 0, marker_idx)
        block_start = block_start + 1 if block_start >= 0 else 0

        previous_line_end = block_start - 1
        if previous_line_end >= 0:
            previous_line_start = cleaned.rfind("\n", 0, previous_line_end)
            previous_line = cleaned[previous_line_start + 1 : previous_line_end]
            if re.match(r"^# =+$", previous_line.strip()):
                block_start = previous_line_start + 1 if previous_line_start >= 0 else 0

        block_end = len(cleaned)
        end_idx = cleaned.find(end_marker, marker_idx)
        if end_idx >= 0:
            end_line_break = cleaned.find("\n", end_idx)
            block_end = end_line_break + 1 if end_line_break >= 0 else len(cleaned)

        before = cleaned[:block_start].rstrip()
        after = cleaned[block_end:].lstrip()
        pieces = [piece for piece in (before, after) if piece]
        cleaned = "\n\n".join(pieces)
        removed += 1

    return cleaned, removed


@dataclass
class StripResult:
    """Result of stripping a marker-bounded block from a config string."""

    cleaned: str
    removed: int


def strip_existing_omx_blocks(config: str) -> StripResult:
    """Remove every ``oh-my-codex (OMX) Configuration`` marker block."""
    cleaned, removed = _strip_marker_block(
        config, _OMX_CONFIG_MARKER, _OMX_CONFIG_END_MARKER
    )
    return StripResult(cleaned=cleaned, removed=removed)


def strip_existing_shared_mcp_registry_block(config: str) -> StripResult:
    """Remove every ``Shared MCP Registry Sync`` marker block."""
    cleaned, removed = _strip_marker_block(
        config,
        _SHARED_MCP_REGISTRY_MARKER,
        _SHARED_MCP_REGISTRY_END_MARKER,
    )
    return StripResult(cleaned=cleaned, removed=removed)


# ---------------------------------------------------------------------------
# MCP launcher timeout repair
# ---------------------------------------------------------------------------


def _to_mcp_server_table_key(name: str) -> str:
    if re.match(r"^[A-Za-z0-9_-]+$", name):
        return f"mcp_servers.{name}"
    return f'mcp_servers."{_escape_toml_string(name)}"'


def _config_has_mcp_server(config: str, name: str) -> bool:
    table_name = _to_mcp_server_table_key(name)
    pattern = re.compile(rf"^\s*\[{re.escape(table_name)}\]\s*$", re.MULTILINE)
    return bool(pattern.search(config))


def _launcher_command_basename(command: str) -> str:
    normalized = command.replace("\\", "/").strip()
    tail = normalized.split("/")[-1] if normalized else ""
    return tail.lower()


def _is_launcher_backed_mcp_command(command: str, args: list[str]) -> bool:
    base = _launcher_command_basename(command)
    if base in ("npx", "uvx"):
        return True
    return base == "npm" and bool(args) and args[0].lower() == "exec"


def _find_launcher_timeout_repair_targets(config: str) -> list[int]:
    """Return insertion line indices where a startup_timeout_sec should be added."""
    lines = _split_lines(config)
    targets: list[int] = []

    start = 0
    while start < len(lines):
        is_mcp_section = bool(
            re.match(r"^\s*\[mcp_servers\.", lines[start] if start < len(lines) else "")
        )
        if not is_mcp_section:
            start += 1
            continue

        end = len(lines)
        for i in range(start + 1, len(lines)):
            if _ROOT_TABLE_HEADER_PATTERN.match(lines[i]):
                end = i
                break

        try:
            parsed = tomllib.loads("\n".join(lines[start:end]))
        except tomllib.TOMLDecodeError:
            start = end
            continue

        mcp_servers = parsed.get("mcp_servers") or {}
        if not isinstance(mcp_servers, dict) or not mcp_servers:
            start = end
            continue
        name, value = next(iter(mcp_servers.items()))
        if not name or name.startswith("omx_") or not isinstance(value, dict):
            start = end
            continue

        command = value.get("command")
        command = command if isinstance(command, str) else None
        args_value = value.get("args")
        args = (
            [a for a in args_value if isinstance(a, str)]
            if isinstance(args_value, list)
            and all(isinstance(a, str) for a in args_value)
            else []
        )
        has_startup_timeout = isinstance(
            value.get("startup_timeout_sec"), (int, float)
        ) or isinstance(value.get("startupTimeoutSec"), (int, float))

        if (
            not command
            or has_startup_timeout
            or not _is_launcher_backed_mcp_command(command, args)
        ):
            start = end
            continue

        insert_at = end
        while insert_at > start + 1 and (lines[insert_at - 1].strip() == ""):
            insert_at -= 1
        targets.append(insert_at)
        start = end

    return targets


def _add_default_launcher_mcp_startup_timeouts(config: str) -> str:
    targets = _find_launcher_timeout_repair_targets(config)
    if not targets:
        return config

    lines = _split_lines(config)
    for insert_at in reversed(targets):
        lines.insert(
            insert_at,
            f"startup_timeout_sec = {_DEFAULT_LAUNCHER_MCP_STARTUP_TIMEOUT_SEC}",
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Generators for the OMX top-level + tables blocks
# ---------------------------------------------------------------------------


def _get_omx_top_level_lines(
    pkg_root: str,
    existing_config: str,
    model_override: str | None,
) -> list[str]:
    notify_hook_path = os.path.join(pkg_root, "dist", "scripts", "notify-hook.js")
    escaped_path = _escape_toml_string(notify_hook_path)
    root_values = _parse_root_key_values(existing_config)

    lines: list[str] = [
        _OMX_TOP_LEVEL_HEADER_COMMENT,
        f'notify = ["node", "{escaped_path}"]',
        'model_reasoning_effort = "medium"',
        f'developer_instructions = "{_escape_toml_string(OMX_DEVELOPER_INSTRUCTIONS)}"',
    ]

    existing_model = root_values.get("model")
    existing_context_window = root_values.get("model_context_window")
    existing_auto_compact = root_values.get("model_auto_compact_token_limit")
    selected_model = (
        model_override or _unwrap_toml_string(existing_model) or _DEFAULT_SETUP_MODEL
    )

    if model_override or existing_model is None:
        lines.append(f'model = "{selected_model}"')

    if selected_model == _DEFAULT_SETUP_MODEL:
        seeded: list[str] = []
        if existing_context_window is None:
            seeded.append(
                f"model_context_window = {_DEFAULT_SETUP_MODEL_CONTEXT_WINDOW}"
            )
        if existing_auto_compact is None:
            seeded.append(
                "model_auto_compact_token_limit "
                f"= {_DEFAULT_SETUP_MODEL_AUTO_COMPACT_TOKEN_LIMIT}"
            )
        if seeded:
            lines.append(_OMX_SEEDED_BEHAVIORAL_DEFAULTS_START_MARKER)
            lines.extend(seeded)
            lines.append(_OMX_SEEDED_BEHAVIORAL_DEFAULTS_END_MARKER)

    return lines


def _get_shared_mcp_registry_block(
    servers: list[UnifiedMcpRegistryServer],
    source_path: str | None,
    existing_config: str,
) -> str:
    if not servers:
        return ""
    deduped = [
        server
        for server in servers
        if not _config_has_mcp_server(existing_config, server.name)
    ]
    if not deduped:
        return ""

    lines = [
        "# ============================================================",
        f"# {_SHARED_MCP_REGISTRY_MARKER}",
        "# Managed by omx setup - edit the registry file instead",
    ]
    if source_path:
        lines.append(f"# Source: {source_path}")
    lines.extend(
        [
            "# ============================================================",
            "",
        ]
    )

    for server in deduped:
        lines.append(f"# Shared MCP Server: {server.name}")
        lines.append(f"[{_to_mcp_server_table_key(server.name)}]")
        lines.append(f'command = "{_escape_toml_string(server.command)}"')
        formatted_args = ", ".join(
            f'"{_escape_toml_string(arg)}"' for arg in server.args
        )
        lines.append(f"args = [{formatted_args}]")
        lines.append(f"enabled = {'true' if server.enabled else 'false'}")
        if server.startup_timeout_sec is not None:
            lines.append(f"startup_timeout_sec = {server.startup_timeout_sec}")
        lines.append("")

    lines.append("# ============================================================")
    lines.append(_SHARED_MCP_REGISTRY_END_MARKER)
    return "\n".join(lines)


def _get_omx_tables_block(pkg_root: str, include_tui: bool = True) -> str:
    lines = [
        "",
        "# ============================================================",
        f"# {_OMX_CONFIG_MARKER}",
        "# Managed by omx setup - manual edits preserved on next setup",
        "# ============================================================",
    ]

    for server in get_omx_first_party_setup_mcp_servers(pkg_root):
        lines.append("")
        lines.append(server["title"])
        lines.append(f"[mcp_servers.{server['name']}]")
        lines.append('command = "node"')
        formatted_args = ", ".join(
            f'"{_escape_toml_string(arg)}"' for arg in server["args"]
        )
        lines.append(f"args = [{formatted_args}]")
        lines.append(f"enabled = {'true' if server['enabled'] else 'false'}")
        if server.get("startup_timeout_sec") is not None:
            lines.append(f"startup_timeout_sec = {server['startup_timeout_sec']}")

    if include_tui:
        lines.extend(
            [
                "",
                "# OMX TUI StatusLine (Codex CLI v0.101.0+)",
                "[tui]",
                _OMX_TUI_STATUS_LINE,
                "",
            ]
        )
    else:
        lines.append("")

    lines.append("# ============================================================")
    lines.append(_OMX_CONFIG_END_MARKER)
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API: buildMergedConfig / repairConfigIfNeeded / mergeConfig
# ---------------------------------------------------------------------------


def _resolve_pkg_root(options: MergeOptions | None, pkg_root: str | None) -> str:
    if pkg_root is not None:
        return pkg_root
    if options is not None and options.pkg_root is not None:
        return options.pkg_root
    return str(Path.cwd())


def build_merged_config(
    existing_config: str,
    pkg_root: str,
    options: MergeOptions | None = None,
) -> str:
    """Merge OMX config into an existing ``config.toml`` string.

    Layout produced:

    1. OMX top-level keys (``notify``, ``model_reasoning_effort``,
       ``developer_instructions``).
    2. ``[features]`` with ``multi_agent`` + ``child_agents_md`` +
       ``codex_hooks``.
    3. ``[env]`` with defaulted explore-routing opt-in.
    4. ... user sections ...
    5. OMX ``[table]`` sections (mcp_servers, tui) — appended at the end.
    """
    opts = options or MergeOptions()
    existing = existing_config
    include_tui = opts.include_tui

    if _OMX_CONFIG_MARKER in existing:
        stripped = strip_existing_omx_blocks(existing)
        existing = stripped.cleaned
    if _SHARED_MCP_REGISTRY_MARKER in existing:
        stripped = strip_existing_shared_mcp_registry_block(existing)
        existing = stripped.cleaned

    existing = strip_omx_top_level_keys(existing)
    existing = _strip_orphaned_managed_notify(existing)
    if opts.model_override:
        existing = _strip_root_level_keys(existing, ("model",))
    existing = _strip_orphaned_omx_sections(existing)
    existing = _upsert_feature_flags(existing)
    existing = _upsert_env_settings(existing)
    existing = _upsert_agents_settings(existing)
    tui_result = (
        _upsert_tui_status_line(existing)
        if include_tui
        else _TuiUpsertResult(cleaned=existing, had_existing_tui=False)
    )
    existing = tui_result.cleaned

    top_lines = _get_omx_top_level_lines(pkg_root, existing, opts.model_override)
    tables_block = _get_omx_tables_block(
        pkg_root, include_tui and not tui_result.had_existing_tui
    )
    shared_block = _get_shared_mcp_registry_block(
        opts.shared_mcp_servers,
        opts.shared_mcp_registry_source,
        existing,
    )

    body = existing.rstrip()
    if shared_block:
        body = f"{body}\n\n{shared_block}" if body else shared_block

    raw = "\n".join(top_lines) + "\n\n" + body + "\n" + tables_block
    return _add_default_launcher_mcp_startup_timeouts(raw)


def repair_config_if_needed(
    config_path: Path | str,
    pkg_root: str,
    options: MergeOptions | None = None,
) -> bool:
    """Detect + repair upgrade-era managed config issues in ``config.toml``.

    After an omx version upgrade the OLD setup code (still loaded in memory)
    may leave a config with duplicate ``[tui]`` sections or the retired
    ``[mcp_servers.omx_team_run]`` table. Codex rejects duplicate tables and
    newer OMX builds no longer ship the team MCP entrypoint, so we repair
    both before the CLI is spawned.

    Returns:
        ``True`` if a repair was performed, ``False`` otherwise.
    """
    path = Path(config_path)
    if not path.exists():
        return False

    content = path.read_text(encoding="utf-8")
    tui_count = len(re.findall(r"^\s*\[tui\]\s*$", content, re.MULTILINE))
    has_legacy_team_run = has_legacy_omx_team_run_table(content)
    has_launcher_timeout_gap = bool(_find_launcher_timeout_repair_targets(content))
    if tui_count <= 1 and not has_legacy_team_run and not has_launcher_timeout_gap:
        return False

    repaired = build_merged_config(content, pkg_root, options)
    if repaired == content:
        return False
    path.write_text(repaired, encoding="utf-8")
    return True


def merge_config(
    config_path: Path | str,
    pkg_root: str,
    options: MergeOptions | None = None,
) -> None:
    """Read ``config.toml``, merge OMX defaults, write it back.

    Mirrors TS ``mergeConfig``. Creates the file if it does not exist.
    """
    opts = options or MergeOptions()
    path = Path(config_path)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""

    if _OMX_CONFIG_MARKER in existing:
        stripped = strip_existing_omx_blocks(existing)
        if opts.verbose and stripped.removed > 0:
            print("  Updating existing OMX config block.")

    final_config = build_merged_config(existing, pkg_root, opts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(final_config, encoding="utf-8")
    if opts.verbose:
        print(f"  Written to {path}")
