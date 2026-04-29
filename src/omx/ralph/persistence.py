"""Ralph persistence — state and artifact management.

Port of src/ralph/persistence.ts.
"""

from __future__ import annotations


from omx.state.paths import get_state_dir


def ensure_canonical_ralph_artifacts(cwd: str, session_id: str | None = None) -> None:
    """Ensure ralph artifact directories (plans/, evidence/, checkpoints/) exist."""
    state_dir = get_state_dir(cwd, session_id)
    ralph_dir = state_dir.parent / "ralph"
    ralph_dir.mkdir(parents=True, exist_ok=True)

    for subdir in ("plans", "evidence", "checkpoints"):
        (ralph_dir / subdir).mkdir(exist_ok=True)


def read_ralph_plan(cwd: str, session_id: str | None = None) -> str | None:
    """Read the current ralph plan."""
    state_dir = get_state_dir(cwd, session_id)
    plan_path = state_dir.parent / "ralph" / "plans" / "current.md"
    if plan_path.exists():
        return plan_path.read_text(encoding="utf-8")
    return None


def write_ralph_plan(cwd: str, content: str, session_id: str | None = None) -> None:
    """Write the current ralph plan."""
    ensure_canonical_ralph_artifacts(cwd, session_id)
    state_dir = get_state_dir(cwd, session_id)
    plan_path = state_dir.parent / "ralph" / "plans" / "current.md"
    plan_path.write_text(content, encoding="utf-8")
