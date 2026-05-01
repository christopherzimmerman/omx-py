"""Path utilities for oh-my-codex.

Resolves Codex CLI config, skills, prompts, and state directories.
"""

from __future__ import annotations

import os
from pathlib import Path


def codex_home() -> Path:
    """Codex CLI home directory (~/.codex/)."""
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def codex_config_path() -> Path:
    """Codex config file path (~/.codex/config.toml)."""
    return codex_home() / "config.toml"


def codex_prompts_dir() -> Path:
    """Codex prompts directory (~/.codex/prompts/)."""
    return codex_home() / "prompts"


def codex_agents_dir(codex_home_dir: Path | None = None) -> Path:
    """Codex native agents directory (~/.codex/agents/)."""
    return (codex_home_dir or codex_home()) / "agents"


def project_codex_agents_dir(project_root: Path | None = None) -> Path:
    """Project-level Codex native agents directory (.codex/agents/)."""
    return (project_root or Path.cwd()) / ".codex" / "agents"


def user_skills_dir() -> Path:
    """User-level skills directory ($CODEX_HOME/skills, defaults to ~/.codex/skills/)."""
    return codex_home() / "skills"


def project_skills_dir(project_root: Path | None = None) -> Path:
    """Project-level skills directory (.codex/skills/)."""
    return (project_root or Path.cwd()) / ".codex" / "skills"


def legacy_user_skills_dir() -> Path:
    """Historical legacy user-level skills directory (~/.agents/skills/)."""
    return Path.home() / ".agents" / "skills"


def claude_home() -> Path:
    """Claude CLI home directory (~/.claude/)."""
    return Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))


def claude_settings_path() -> Path:
    """Claude settings file path (~/.claude/settings.json)."""
    return claude_home() / "settings.json"


def claude_skills_dir() -> Path:
    """Claude user-level skills directory (~/.claude/skills/)."""
    return claude_home() / "skills"


def claude_agents_dir() -> Path:
    """Claude user-level subagents directory (~/.claude/agents/)."""
    return claude_home() / "agents"


def omx_state_dir(project_root: Path | None = None) -> Path:
    """oh-my-codex state directory (.omx/state/)."""
    return (project_root or Path.cwd()) / ".omx" / "state"


def omx_project_memory_path(project_root: Path | None = None) -> Path:
    """oh-my-codex project memory file (.omx/project-memory.json)."""
    return (project_root or Path.cwd()) / ".omx" / "project-memory.json"


def omx_notepad_path(project_root: Path | None = None) -> Path:
    """oh-my-codex notepad file (.omx/notepad.md)."""
    return (project_root or Path.cwd()) / ".omx" / "notepad.md"


def omx_wiki_dir(project_root: Path | None = None) -> Path:
    """oh-my-codex wiki directory (.omx/wiki/)."""
    return (project_root or Path.cwd()) / ".omx" / "wiki"


def omx_plans_dir(project_root: Path | None = None) -> Path:
    """oh-my-codex plans directory (.omx/plans/)."""
    return (project_root or Path.cwd()) / ".omx" / "plans"


def omx_logs_dir(project_root: Path | None = None) -> Path:
    """oh-my-codex logs directory (.omx/logs/)."""
    return (project_root or Path.cwd()) / ".omx" / "logs"


def omx_user_install_stamp_path(codex_home_dir: Path | None = None) -> Path:
    """User-scope install/update stamp path ($CODEX_HOME/.omx/install-state.json)."""
    return (codex_home_dir or codex_home()) / ".omx" / "install-state.json"


def package_root() -> Path:
    """Get the package root directory (where assets/ lives)."""
    return Path(__file__).resolve().parent.parent
