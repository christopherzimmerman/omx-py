"""Health diagnostics for OMX installation.

Port of src/cli/doctor.ts.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from omx.utils.paths import (
    codex_agents_dir,
    codex_config_path,
    codex_home,
    codex_prompts_dir,
    user_skills_dir,
)
from omx.utils.platform import which
from omx.utils.toml_read import read_toml


def run_doctor(
    team: bool = False,
    verbose: bool = False,
    force: bool = False,
) -> None:
    """Run OMX installation health diagnostics and print results.

    Args:
        team: If True, run team/swarm-specific diagnostics instead.
        verbose: Show additional detail for each check.
        force: Show remediation guidance even on pass.
    """
    if team:
        _doctor_team(verbose=verbose)
        return

    print("OMX Doctor — checking installation health\n")
    checks_passed = 0
    checks_failed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal checks_passed, checks_failed
        status = "OK" if ok else "FAIL"
        suffix = f" — {detail}" if detail else ""
        print(f"  [{status}] {name}{suffix}")
        if ok:
            checks_passed += 1
        else:
            checks_failed += 1

    # Check codex CLI
    codex_path = which("codex")
    check(
        "Codex CLI installed",
        codex_path is not None,
        str(codex_path) if codex_path else "not found on PATH",
    )

    # Check Python version
    py_version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    check("Python >= 3.12", sys.version_info >= (3, 12), py_version)

    # Check tmux
    tmux_path = which("tmux")
    check(
        "tmux installed",
        tmux_path is not None,
        str(tmux_path) if tmux_path else "not found (required for team mode)",
    )

    # Check codex home
    home = codex_home()
    check("Codex home directory", home.exists(), str(home))

    # Check config file
    config_path = codex_config_path()
    config_exists = config_path.exists()
    config_detail = ""
    if config_exists:
        try:
            config = read_toml(config_path)
            model = config.get("model", "not set")
            config_detail = f"model={model}"
        except Exception as e:
            config_detail = f"parse error: {e}"
            config_exists = False
    check("Config file", config_exists, config_detail or str(config_path))

    # Check prompts directory
    prompts_dir = codex_prompts_dir()
    prompts_count = len(list(prompts_dir.glob("*.md"))) if prompts_dir.exists() else 0
    check(
        "Prompts installed",
        prompts_count > 0,
        f"{prompts_count} prompts in {prompts_dir}",
    )

    # Check skills directory
    skills_dir = user_skills_dir()
    skills_count = 0
    if skills_dir.exists():
        skills_count = sum(
            1 for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()
        )
    check(
        "Skills installed", skills_count > 0, f"{skills_count} skills in {skills_dir}"
    )

    # Check native agents directory
    agents_dir = codex_agents_dir()
    agents_count = len(list(agents_dir.glob("*.toml"))) if agents_dir.exists() else 0
    check("Native agents", agents_count > 0, f"{agents_count} agents in {agents_dir}")

    # Check state directory
    state_dir = Path.cwd() / ".omx" / "state"
    check("State directory", state_dir.exists(), str(state_dir))

    print()
    total = checks_passed + checks_failed
    if checks_failed == 0:
        print(f"All {total} checks passed.")
    else:
        print(f"{checks_passed}/{total} checks passed, {checks_failed} failed.")
        if not force:
            print("Run 'omx setup' to fix issues, or 'omx doctor --force' for details.")


def _doctor_team(verbose: bool = False) -> None:
    """Run team/swarm-specific diagnostics."""
    print("OMX Doctor — team diagnostics\n")

    tmux_path = which("tmux")
    if not tmux_path:
        print("  [FAIL] tmux not installed — required for team mode")
        return

    print(f"  [OK] tmux installed at {tmux_path}")

    # Check for active team state
    state_dir = Path.cwd() / ".omx" / "state"
    team_state_path = state_dir / "team-state.json"
    if team_state_path.exists():
        try:
            data = json.loads(team_state_path.read_text(encoding="utf-8"))
            active = data.get("active", False)
            phase = data.get("current_phase", "unknown")
            print(f"  [INFO] Team state: active={active}, phase={phase}")
        except (json.JSONDecodeError, OSError):
            print("  [WARN] Team state file exists but is malformed")
    else:
        print("  [INFO] No active team state")

    # Check for team runtime files
    team_dir = Path.cwd() / ".omx" / "team"
    if team_dir.exists():
        files = list(team_dir.iterdir())
        print(f"  [INFO] Team runtime directory: {len(files)} files")
    else:
        print("  [INFO] No team runtime directory")
