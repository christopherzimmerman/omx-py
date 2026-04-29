"""Setup and installation for OMX.

Port of src/cli/setup.ts.
Handles: prompts, skills, MCP servers, native agents, hooks registration,
config repair, scope migration, plugin detection, and state tracking.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from omx.config.generator import merge_config, read_config, write_config
from omx.config.toml_writer import dumps as toml_dumps
from omx.utils.paths import (
    codex_agents_dir,
    codex_config_path,
    codex_home,
    codex_prompts_dir,
    omx_logs_dir,
    omx_plans_dir,
    omx_state_dir,
    package_root,
    user_skills_dir,
)


# ---------------------------------------------------------------------------
# Enums & constants
# ---------------------------------------------------------------------------


class SetupScope(StrEnum):
    """Installation scope."""

    USER = "user"
    PROJECT = "project"


class SetupInstallMode(StrEnum):
    """Skill delivery mode."""

    LEGACY = "legacy"
    PLUGIN = "plugin"


LEGACY_SCOPE_MIGRATION: dict[str, SetupScope] = {"project-local": SetupScope.PROJECT}
HARD_DEPRECATED_SKILL_NAMES: set[str] = {"web-clone"}
DEFAULT_MODEL = "o4-mini"

PROJECT_GITIGNORE_ENTRIES: list[str] = [
    ".omx/",
    ".codex/*",
    "!.codex/agents/",
    "!.codex/agents/**",
    "!.codex/skills/",
    "!.codex/skills/**",
    ".codex/skills/.system/**",
    "!.codex/prompts/",
    "!.codex/prompts/**",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CategorySummary:
    """Per-category installation counts.

    Attributes:
        updated: Items written or updated.
        unchanged: Items already up-to-date.
        backed_up: Items backed up before overwrite.
        skipped: Items intentionally skipped.
        removed: Items removed (legacy cleanup).
    """

    updated: int = 0
    unchanged: int = 0
    backed_up: int = 0
    skipped: int = 0
    removed: int = 0


@dataclass
class RunSummary:
    """Aggregate setup run summary.

    Attributes:
        prompts: Prompt installation counts.
        skills: Skill installation counts.
        native_agents: Native agent config counts.
        agents_md: AGENTS.md generation counts.
        config: Config file counts.
    """

    prompts: CategorySummary = field(default_factory=CategorySummary)
    skills: CategorySummary = field(default_factory=CategorySummary)
    native_agents: CategorySummary = field(default_factory=CategorySummary)
    agents_md: CategorySummary = field(default_factory=CategorySummary)
    config: CategorySummary = field(default_factory=CategorySummary)


@dataclass
class ScopeDirectories:
    """Resolved directory paths for a setup scope.

    Attributes:
        codex_config_file: Path to config.toml.
        codex_home_dir: Codex home directory.
        codex_hooks_file: Path to hooks.json.
        native_agents_dir: Directory for native agent TOML files.
        prompts_dir: Directory for agent prompt files.
        skills_dir: Directory for skill directories.
    """

    codex_config_file: Path
    codex_home_dir: Path
    codex_hooks_file: Path
    native_agents_dir: Path
    prompts_dir: Path
    skills_dir: Path


@dataclass
class SkillFrontmatter:
    """Parsed SKILL.md frontmatter metadata.

    Attributes:
        name: Skill name from frontmatter.
        description: Skill description from frontmatter.
    """

    name: str
    description: str


# ---------------------------------------------------------------------------
# Scope / directory resolution
# ---------------------------------------------------------------------------


def resolve_scope_directories(
    scope: SetupScope, project_root: Path
) -> ScopeDirectories:
    """Resolve the directory layout for the given scope.

    Args:
        scope: User or project scope.
        project_root: Root of the current project.

    Returns:
        ScopeDirectories with all relevant paths populated.
    """
    if scope == SetupScope.PROJECT:
        home = project_root / ".codex"
        return ScopeDirectories(
            codex_config_file=home / "config.toml",
            codex_home_dir=home,
            codex_hooks_file=home / "hooks.json",
            native_agents_dir=home / "agents",
            prompts_dir=home / "prompts",
            skills_dir=home / "skills",
        )
    return ScopeDirectories(
        codex_config_file=codex_config_path(),
        codex_home_dir=codex_home(),
        codex_hooks_file=codex_home() / "hooks.json",
        native_agents_dir=codex_agents_dir(),
        prompts_dir=codex_prompts_dir(),
        skills_dir=user_skills_dir(),
    )


# ---------------------------------------------------------------------------
# Scope persistence & migration
# ---------------------------------------------------------------------------


def _read_persisted_preferences(
    project_root: Path,
) -> dict[str, str] | None:
    """Read persisted setup preferences, applying legacy migration.

    Args:
        project_root: Project root directory.

    Returns:
        Dict with 'scope' (and optional 'install_mode') or None.
    """
    path = project_root / ".omx" / "setup-scope.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    scope_str = raw.get("scope")
    if not isinstance(scope_str, str):
        return None

    # Direct match or legacy migration
    try:
        SetupScope(scope_str)
    except ValueError:
        migrated = LEGACY_SCOPE_MIGRATION.get(scope_str)
        if not migrated:
            return None
        print(f'[omx] Migrating persisted scope "{scope_str}" -> "{migrated}".')
        scope_str = migrated.value

    result: dict[str, str] = {"scope": scope_str}
    mode_str = raw.get("installMode") or raw.get("install_mode")
    if isinstance(mode_str, str):
        try:
            SetupInstallMode(mode_str)
            result["install_mode"] = mode_str
        except ValueError:
            pass
    return result


def _persist_preferences(
    project_root: Path,
    scope: str,
    install_mode: str | None,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Write setup preferences to .omx/setup-scope.json."""
    path = project_root / ".omx" / "setup-scope.json"
    if dry_run:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, str] = {"scope": scope}
    if install_mode:
        data["install_mode"] = install_mode
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if verbose:
        print(f"  Wrote {path}")


def _write_install_state(
    scope_dirs: ScopeDirectories,
    summary: RunSummary,
    *,
    dry_run: bool = False,
) -> None:
    """Write install-state.json tracking the last setup run."""
    if dry_run:
        return
    path = scope_dirs.codex_home_dir / ".omx" / "install-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "last_setup_at": datetime.now(timezone.utc).isoformat(),
        "prompts_updated": summary.prompts.updated,
        "skills_updated": summary.skills.updated,
        "native_agents_updated": summary.native_agents.updated,
        "config_updated": summary.config.updated,
    }
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Skill validation
# ---------------------------------------------------------------------------


def parse_skill_frontmatter(
    content: str, file_path: str = "SKILL.md"
) -> SkillFrontmatter:
    """Parse SKILL.md YAML frontmatter for name and description.

    Args:
        content: Full file content.
        file_path: Path for error messages.

    Returns:
        SkillFrontmatter with name and description.

    Raises:
        ValueError: If frontmatter is missing or malformed.
    """
    match = re.match(r"^---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)", content)
    if not match:
        raise ValueError(
            f"{file_path} must start with YAML frontmatter containing "
            "non-empty name and description fields"
        )

    name: str | None = None
    description: str | None = None

    for idx, raw_line in enumerate(match.group(1).split("\n")):
        line = raw_line.rstrip("\r").rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if raw_line[0:1] in (" ", "\t"):
            continue

        kv = re.match(r"^([A-Za-z0-9_-]+):(.*)$", line)
        if not kv:
            raise ValueError(
                f"{file_path} has invalid YAML frontmatter on line {idx + 2}: {stripped}"
            )

        key, raw_value = kv.group(1), kv.group(2)
        value = raw_value.strip()
        if not value:
            continue
        if value in ("|", ">"):
            raise ValueError(
                f'{file_path} frontmatter "{key}" must be a single-line string'
            )

        # Handle quoted strings
        if value[0] in ('"', "'"):
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise ValueError(
                    f'{file_path} frontmatter "{key}" has unterminated quote'
                )
            value = value[1:-1].strip()
        else:
            value = re.sub(r"\s+#.*$", "", value).strip()

        if not value:
            raise ValueError(f'{file_path} frontmatter "{key}" must not be empty')

        if key == "name":
            name = value
        elif key == "description":
            description = value

    if not name:
        raise ValueError(f'{file_path} is missing a non-empty frontmatter "name"')
    if not description:
        raise ValueError(
            f'{file_path} is missing a non-empty frontmatter "description"'
        )
    return SkillFrontmatter(name=name, description=description)


def validate_skill_file(skill_md_path: Path) -> None:
    """Validate a SKILL.md file has proper frontmatter.

    Args:
        skill_md_path: Path to the SKILL.md file.

    Raises:
        ValueError: If frontmatter is missing or malformed.
    """
    content = skill_md_path.read_text(encoding="utf-8")
    parse_skill_frontmatter(content, str(skill_md_path))


# ---------------------------------------------------------------------------
# Config repair
# ---------------------------------------------------------------------------


def _repair_config_toml(path: Path, *, verbose: bool = False) -> bool:
    """Detect and fix malformed config.toml (duplicate tables, parse errors).

    Args:
        path: Path to config.toml.
        verbose: Print repair activity.

    Returns:
        True if a repair was applied.
    """
    if not path.exists():
        return False
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return False

    try:
        from omx.utils.toml_read import parse_toml

        parse_toml(raw)
        return False
    except Exception:
        pass

    # Attempt repair: deduplicate table headers
    lines = raw.splitlines(keepends=True)
    seen_tables: set[str] = set()
    repaired: list[str] = []
    skip = False

    for line in lines:
        m = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
        if m:
            tname = m.group(1).strip()
            if tname in seen_tables:
                skip = True
                continue
            seen_tables.add(tname)
            skip = False
        elif skip:
            if "=" in line and not line.strip().startswith("#"):
                repaired.append(line)
            continue
        repaired.append(line)

    fixed = "".join(repaired)
    try:
        from omx.utils.toml_read import parse_toml

        parse_toml(fixed)
    except Exception:
        if verbose:
            print(f"  config repair: unable to fix {path}")
        return False

    path.write_text(fixed, encoding="utf-8")
    if verbose:
        print(f"  config repair: fixed {path}")
    return True


# ---------------------------------------------------------------------------
# MCP server registration
# ---------------------------------------------------------------------------

MCP_TARGETS = ("state", "memory", "code_intel", "trace", "wiki")


def _build_mcp_servers_section() -> dict[str, Any]:
    """Build [mcp_servers] config entries for all OMX MCP servers."""
    return {
        f"omx_{t}": {"command": "omx", "args": ["mcp-serve", t]} for t in MCP_TARGETS
    }


def _ensure_mcp_servers(
    config_path: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> bool:
    """Register OMX MCP servers in config.toml [mcp_servers].

    Args:
        config_path: Path to config.toml.
        dry_run: If True, skip writing.
        verbose: Print activity detail.

    Returns:
        True if the config was modified.
    """
    config = read_config(config_path)
    mcp = config.get("mcp_servers", {})
    desired = _build_mcp_servers_section()
    changed = any(mcp.get(k) != v for k, v in desired.items())
    if not changed:
        return False
    config["mcp_servers"] = merge_config(mcp, desired)
    if not dry_run:
        write_config(config, config_path)
    if verbose:
        print(
            f"  {'would update' if dry_run else 'updated'} MCP servers in {config_path}"
        )
    return True


# ---------------------------------------------------------------------------
# Native agent TOML generation & installation
# ---------------------------------------------------------------------------


def _install_native_agents(
    agents_dir: Path,
    summary: CategorySummary,
    *,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Generate and install native agent TOML files.

    Args:
        agents_dir: Target agents directory.
        summary: Category summary to update.
        force: Force overwrite even if unchanged.
        dry_run: If True, skip writing.
        verbose: Print per-agent activity.
    """
    from omx.agents.roles import AGENT_DEFINITIONS

    if not dry_run:
        agents_dir.mkdir(parents=True, exist_ok=True)

    for agent in AGENT_DEFINITIONS:
        toml_content = toml_dumps(
            {
                "name": agent.name,
                "description": agent.description,
                "model_class": agent.model_class,
                "reasoning_effort": agent.reasoning_effort,
            }
        )
        dst = agents_dir / f"{agent.name}.toml"
        if dst.exists() and not force:
            if dst.read_text(encoding="utf-8") == toml_content:
                summary.unchanged += 1
                continue
        if verbose:
            print(
                f"  {'would write' if dry_run else 'writing'} native agent: {agent.name}.toml"
            )
        if not dry_run:
            dst.write_text(toml_content, encoding="utf-8")
        summary.updated += 1


# ---------------------------------------------------------------------------
# Hooks registration
# ---------------------------------------------------------------------------


def _ensure_hooks(
    hooks_path: Path,
    summary: CategorySummary,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Register OMX hooks in hooks.json (notify, stop, session).

    Merges managed hooks with existing user hooks, preserving
    non-OMX entries.

    Args:
        hooks_path: Path to hooks.json.
        summary: Category summary to update.
        dry_run: If True, skip writing.
        verbose: Print activity detail.
    """

    def omx_hook(event):
        return {
            "command": ["omx", "mcp-serve", "state"],
            "event": event,
            "managed_by": "omx",
        }

    managed_events = {"notify", "stop", "session"}

    existing: dict[str, Any] = {}
    if hooks_path.exists():
        try:
            existing = json.loads(hooks_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    merged_hooks: dict[str, list[Any]] = {}
    for event in managed_events:
        prev = existing.get("hooks", {}).get(event, [])
        user = [
            e
            for e in prev
            if not (isinstance(e, dict) and e.get("managed_by") == "omx")
        ]
        merged_hooks[event] = user + [omx_hook(event)]

    result = {**existing, "hooks": merged_hooks, "managed_by": "omx"}
    result_text = json.dumps(result, indent=2) + "\n"

    if hooks_path.exists() and hooks_path.read_text(encoding="utf-8") == result_text:
        summary.unchanged += 1
        return

    if verbose:
        print(f"  {'would write' if dry_run else 'writing'} hooks: {hooks_path}")
    if not dry_run:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        hooks_path.write_text(result_text, encoding="utf-8")
    summary.updated += 1


# ---------------------------------------------------------------------------
# Gitignore management
# ---------------------------------------------------------------------------


def _ensure_project_gitignore(
    project_root: Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> str:
    """Ensure .gitignore has OMX project ignore rules.

    Args:
        project_root: Project root directory.
        dry_run: If True, skip writing.
        verbose: Print activity detail.

    Returns:
        "created", "updated", or "unchanged".
    """
    gi = project_root / ".gitignore"
    exists = gi.exists()
    content = gi.read_text(encoding="utf-8") if exists else ""

    # Strip legacy entries
    legacy = {".codex/"}
    lines = content.splitlines()
    filtered = [ln for ln in lines if ln.strip() not in legacy]
    was_stripped = len(filtered) != len(lines)
    existing_set = {ln.strip() for ln in filtered}
    missing = [e for e in PROJECT_GITIGNORE_ENTRIES if e not in existing_set]

    if not missing and not was_stripped:
        return "unchanged"

    base = "\n".join(filtered)
    if base and not base.endswith("\n"):
        base += "\n"
    next_content = base + "\n".join(missing) + ("\n" if missing else "")

    if not dry_run:
        gi.write_text(next_content, encoding="utf-8")
    if verbose:
        print(
            f"  {'would update' if dry_run else 'created' if not exists else 'updated'} .gitignore"
        )
    return "created" if not exists else "updated"


# ---------------------------------------------------------------------------
# Plugin install-mode detection
# ---------------------------------------------------------------------------


def _detect_plugin_install(codex_home_dir: Path) -> bool:
    """Detect if oh-my-codex is installed as a Codex plugin.

    Args:
        codex_home_dir: Codex home directory.

    Returns:
        True if the plugin cache directory exists with oh-my-codex manifest.
    """
    cache_root = codex_home_dir / "plugins" / "cache"
    if not cache_root.exists():
        return False
    queue: list[tuple[Path, int]] = [(cache_root, 0)]
    while queue:
        current, depth = queue.pop(0)
        manifest = current / ".codex-plugin" / "plugin.json"
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("name") == "oh-my-codex":
                    return True
            except (json.JSONDecodeError, OSError):
                pass
        if depth < 5:
            try:
                for child in current.iterdir():
                    if child.is_dir() and child.name not in (".git", "node_modules"):
                        queue.append((child, depth + 1))
            except OSError:
                pass
    return False


# ---------------------------------------------------------------------------
# Install helpers
# ---------------------------------------------------------------------------


def _install_prompts(
    src: Path,
    dest: Path,
    summary: CategorySummary,
    *,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Copy prompt .md files from src to dest."""
    if not src.exists():
        return
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    for md in sorted(src.glob("*.md")):
        dst = dest / md.name
        if dst.exists() and not force and md.read_bytes() == dst.read_bytes():
            summary.unchanged += 1
            continue
        if verbose:
            print(f"  {'would copy' if dry_run else 'copying'} prompt: {md.name}")
        if not dry_run:
            shutil.copy2(md, dst)
        summary.updated += 1


def _install_skills(
    src: Path,
    dest: Path,
    summary: CategorySummary,
    *,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Copy and validate skill directories from src to dest.

    Validates SKILL.md frontmatter. Skips deprecated skills.
    """
    if not src.exists():
        return
    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)
    for skill_dir in sorted(src.iterdir()):
        if not skill_dir.is_dir():
            continue
        if skill_dir.name in HARD_DEPRECATED_SKILL_NAMES:
            summary.skipped += 1
            continue
        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            summary.skipped += 1
            continue
        try:
            validate_skill_file(skill_md)
        except ValueError as exc:
            summary.skipped += 1
            if verbose:
                print(f"  skipped skill (invalid): {skill_dir.name}: {exc}")
            continue
        dest_dir = dest / skill_dir.name
        if dest_dir.exists() and not force:
            dest_md = dest_dir / "SKILL.md"
            if dest_md.exists() and skill_md.read_bytes() == dest_md.read_bytes():
                summary.unchanged += 1
                continue
        if verbose:
            print(f"  {'would copy' if dry_run else 'copying'} skill: {skill_dir.name}")
        if not dry_run:
            if dest_dir.exists():
                shutil.rmtree(dest_dir)
            shutil.copytree(skill_dir, dest_dir)
        summary.updated += 1


def _ensure_config(
    scope_dirs: ScopeDirectories,
    summary: CategorySummary,
    *,
    force: bool = False,
    dry_run: bool = False,
    verbose: bool = False,
) -> None:
    """Ensure config.toml exists with required fields and MCP servers.

    Runs config repair, merges defaults, and registers MCP servers.
    """
    config_path = scope_dirs.codex_config_file
    _repair_config_toml(config_path, verbose=verbose)

    if config_path.exists() and not force:
        existing = read_config(config_path)
        if "model" in existing:
            if _ensure_mcp_servers(config_path, dry_run=dry_run, verbose=verbose):
                summary.updated += 1
            else:
                summary.unchanged += 1
            return

    defaults: dict[str, Any] = {"model": DEFAULT_MODEL, "approval_mode": "suggest"}
    existing = read_config(config_path) if config_path.exists() else {}
    merged = merge_config(defaults, existing)
    merged["mcp_servers"] = merge_config(
        merged.get("mcp_servers", {}),
        _build_mcp_servers_section(),
    )
    if verbose:
        print(f"  {'would write' if dry_run else 'writing'} config: {config_path}")
    if not dry_run:
        write_config(merged, config_path)
    summary.updated += 1


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _print_summary(summary: RunSummary) -> None:
    """Print full setup run summary with per-category counts."""
    print("\nSetup refresh summary:")
    for name, cat in [
        ("prompts", summary.prompts),
        ("skills", summary.skills),
        ("native_agents", summary.native_agents),
        ("agents_md", summary.agents_md),
        ("config", summary.config),
    ]:
        print(
            f"  {name}: updated={cat.updated}, unchanged={cat.unchanged}, "
            f"backed_up={cat.backed_up}, skipped={cat.skipped}, removed={cat.removed}"
        )
    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def run_setup(
    force: bool = False,
    dry_run: bool = False,
    scope: str = "user",
    verbose: bool = False,
    install_mode: str | None = None,
) -> None:
    """Run the OMX setup process.

    Installs prompts, skills, MCP server config, native agents,
    hooks, and AGENTS.md.  Handles scope migration, config repair,
    plugin install-mode detection, and state tracking.

    Args:
        force: Force reinstall even if assets are up-to-date.
        dry_run: Preview changes without writing files.
        scope: Installation scope ("user" or "project").
        verbose: Print per-file activity detail.
        install_mode: Override install mode ("legacy" or "plugin").
    """
    project_root = Path.cwd()

    # Resolve scope with migration
    try:
        resolved_scope = SetupScope(scope)
    except ValueError:
        migrated = LEGACY_SCOPE_MIGRATION.get(scope)
        resolved_scope = migrated if migrated else SetupScope.USER

    # Check persisted preferences
    persisted = _read_persisted_preferences(project_root)
    scope_source = "cli"
    if persisted and scope == "user" and persisted["scope"] != "user":
        resolved_scope = SetupScope(persisted["scope"])
        scope_source = "persisted"

    # Resolve install mode
    resolved_mode: SetupInstallMode | None = None
    if install_mode:
        try:
            resolved_mode = SetupInstallMode(install_mode)
        except ValueError:
            pass
    elif persisted and persisted.get("install_mode"):
        try:
            resolved_mode = SetupInstallMode(persisted["install_mode"])
        except ValueError:
            pass
    elif resolved_scope == SetupScope.USER:
        resolved_mode = (
            SetupInstallMode.PLUGIN
            if _detect_plugin_install(codex_home())
            else SetupInstallMode.LEGACY
        )

    is_plugin = resolved_mode == SetupInstallMode.PLUGIN
    scope_dirs = resolve_scope_directories(resolved_scope, project_root)

    print("oh-my-codex setup\n=================\n")
    tag = " (from .omx/setup-scope.json)" if scope_source == "persisted" else ""
    print(f"Using setup scope: {resolved_scope.value}{tag}")
    if resolved_mode:
        print(f"Using install mode: {resolved_mode.value}")
    print()

    root = package_root()
    assets = root / "assets"
    summary = RunSummary()

    # [1/8] Create directories
    print("[1/8] Creating directories...")
    dirs = [
        scope_dirs.codex_home_dir,
        omx_state_dir(project_root),
        omx_plans_dir(project_root),
        omx_logs_dir(project_root),
    ]
    if not is_plugin:
        dirs += [
            scope_dirs.prompts_dir,
            scope_dirs.skills_dir,
            scope_dirs.native_agents_dir,
        ]
    for d in dirs:
        if not dry_run:
            d.mkdir(parents=True, exist_ok=True)
        if verbose:
            print(f"  mkdir {d}")
    _persist_preferences(
        project_root,
        resolved_scope.value,
        resolved_mode.value if resolved_mode else None,
        dry_run=dry_run,
        verbose=verbose,
    )
    print("  Done.\n")

    if resolved_scope == SetupScope.PROJECT:
        r = _ensure_project_gitignore(project_root, dry_run=dry_run, verbose=verbose)
        if r != "unchanged":
            print(f"  {r.title()} .gitignore with OMX project ignore rules.\n")

    # [2/8] Prompts
    print("[2/8] Installing agent prompts...")
    if not is_plugin:
        _install_prompts(
            assets / "prompts",
            scope_dirs.prompts_dir,
            summary.prompts,
            force=force,
            dry_run=dry_run,
            verbose=verbose,
        )
    else:
        summary.prompts.skipped += 1
    print("  Prompt refresh complete.\n")

    # [3/8] Skills
    print("[3/8] Installing skills...")
    if not is_plugin:
        _install_skills(
            assets / "skills",
            scope_dirs.skills_dir,
            summary.skills,
            force=force,
            dry_run=dry_run,
            verbose=verbose,
        )
    else:
        summary.skills.skipped += 1
    print("  Skill refresh complete.\n")

    # [4/8] Native agents
    print("[4/8] Installing native agent configs...")
    if not is_plugin:
        _install_native_agents(
            scope_dirs.native_agents_dir,
            summary.native_agents,
            force=force,
            dry_run=dry_run,
            verbose=verbose,
        )
        print(f"  Native agent refresh complete ({scope_dirs.native_agents_dir}).\n")
    else:
        summary.native_agents.skipped += 1
        print("  Skipped for plugin skill delivery mode.\n")

    # [5/8] Config
    print("[5/8] Updating config.toml...")
    _ensure_config(
        scope_dirs, summary.config, force=force, dry_run=dry_run, verbose=verbose
    )
    print(f"  Config refresh complete ({scope_dirs.codex_config_file}).\n")

    # [6/8] Hooks
    print("[6/8] Configuring hooks...")
    _ensure_hooks(
        scope_dirs.codex_hooks_file, summary.config, dry_run=dry_run, verbose=verbose
    )
    print(f"  Hooks refresh complete ({scope_dirs.codex_hooks_file}).\n")

    # [7/8] AGENTS.md
    print("[7/8] Generating AGENTS.md...")
    tpl = assets / "templates" / "AGENTS.md"
    md_dst = (
        project_root / "AGENTS.md"
        if resolved_scope == SetupScope.PROJECT
        else scope_dirs.codex_home_dir / "AGENTS.md"
    )
    if tpl.exists():
        content = tpl.read_text(encoding="utf-8")
        if resolved_scope == SetupScope.PROJECT:
            content = content.replace("~/.codex", "./.codex")
        if md_dst.exists() and not force:
            if md_dst.read_text(encoding="utf-8") == content:
                summary.agents_md.unchanged += 1
                print("  AGENTS.md already up to date.\n")
            else:
                summary.agents_md.skipped += 1
                print(f"  AGENTS.md exists at {md_dst}. Use --force to overwrite.\n")
        else:
            if not dry_run:
                md_dst.parent.mkdir(parents=True, exist_ok=True)
                md_dst.write_text(content, encoding="utf-8")
            summary.agents_md.updated += 1
            print(f"  Generated AGENTS.md at {md_dst}.\n")
    else:
        summary.agents_md.skipped += 1
        print("  AGENTS.md template not found, skipping.\n")

    # [8/8] HUD
    print("[8/8] Configuring HUD...")
    hud = project_root / ".omx" / "hud-config.json"
    if force or not hud.exists():
        if not dry_run:
            hud.parent.mkdir(parents=True, exist_ok=True)
            hud.write_text(
                json.dumps({"preset": "focused"}, indent=2), encoding="utf-8"
            )
        print("  HUD config created (preset: focused).")
    else:
        print("  HUD config already exists (use --force to overwrite).")
    print()

    _write_install_state(scope_dirs, summary, dry_run=dry_run)
    _print_summary(summary)

    print('Setup complete! Run "omx doctor" to verify installation.')
    print("\nNext steps:")
    print("  1. Start Codex CLI in your project directory")
    if is_plugin:
        print("  2. Codex plugin discovery supplies OMX skills and workflow surfaces")
        print("  3. Browse plugin-provided skills with /skills")
    else:
        print("  2. Use role/workflow keywords like $architect, $executor, $plan")
        print("  3. Browse skills with /skills; AGENTS keyword routing activates them")
        print("  4. The AGENTS.md orchestration brain is loaded automatically")
        print("  5. Native agent defaults in config.toml and .codex/agents/")
