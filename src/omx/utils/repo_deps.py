"""Repository dependency detection.

Port of src/utils/repo-deps.ts. Detects node_modules availability
and supports worktree symlink reuse.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

REQUIRED_NODE_MODULE_MARKERS = [
    os.path.join("typescript", "package.json"),
    os.path.join("@iarna", "toml", "package.json"),
    os.path.join("@modelcontextprotocol", "sdk", "package.json"),
    os.path.join("zod", "package.json"),
]


def has_usable_node_modules(repo_root: str) -> bool:
    """Check whether all required node_modules markers exist.

    Args:
        repo_root: Repository root directory.

    Returns:
        True if all required markers are present.
    """
    nm = Path(repo_root) / "node_modules"
    return all((nm / marker).exists() for marker in REQUIRED_NODE_MODULE_MARKERS)


def resolve_git_common_dir(
    cwd: str,
    git_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str | None:
    """Resolve the git common-dir via ``git rev-parse --git-common-dir``.

    Args:
        cwd: Working directory.
        git_runner: Optional subprocess runner (defaults to ``subprocess.run``).

    Returns:
        Resolved common-dir path or ``None``.
    """
    runner = git_runner or subprocess.run
    try:
        result = runner(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    value = (result.stdout or "").strip()
    if not value:
        return None
    return str(Path(cwd, value).resolve())


def resolve_reusable_node_modules_source(
    repo_root: str,
    git_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> str | None:
    """Find a reusable node_modules from the primary worktree.

    Args:
        repo_root: Current repo/worktree root.
        git_runner: Optional subprocess runner.

    Returns:
        Path to reusable node_modules, or ``None``.
    """
    common_dir = resolve_git_common_dir(repo_root, git_runner)
    if not common_dir or Path(common_dir).name != ".git":
        return None
    primary_root = str(Path(common_dir).parent)
    if Path(primary_root).resolve() == Path(repo_root).resolve():
        return None
    if not has_usable_node_modules(primary_root):
        return None
    return str(Path(primary_root) / "node_modules")


@dataclass
class EnsureReusableNodeModulesResult:
    """Result of ``ensure_reusable_node_modules``.

    Attributes:
        strategy: Resolution strategy used ("existing", "symlink", "missing").
        node_modules_path: Resolved node_modules path.
        source_node_modules_path: Source path when symlinked.
        warning: Optional warning message.
    """

    strategy: str  # "existing" | "symlink" | "missing"
    node_modules_path: str
    source_node_modules_path: str | None = None
    warning: str | None = None


def ensure_reusable_node_modules(
    repo_root: str,
    git_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> EnsureReusableNodeModulesResult:
    """Ensure node_modules is available, symlinking from primary worktree if needed.

    Args:
        repo_root: Repository root directory.
        git_runner: Optional subprocess runner.

    Returns:
        Result describing the strategy used.
    """
    target = str(Path(repo_root) / "node_modules")

    if has_usable_node_modules(repo_root):
        return EnsureReusableNodeModulesResult(
            strategy="existing", node_modules_path=target
        )

    target_path = Path(target)
    if target_path.exists() or target_path.is_symlink():
        import shutil

        shutil.rmtree(target, ignore_errors=True)

    source = resolve_reusable_node_modules_source(repo_root, git_runner)
    if not source:
        return EnsureReusableNodeModulesResult(
            strategy="missing",
            node_modules_path=target,
            warning=(
                f"No reusable parent-repo node_modules was found for worktree {repo_root}. "
                "Downstream build/test verification may fail until dependencies are bootstrapped manually."
            ),
        )

    # Use junction on Windows, symlink elsewhere
    if os.name == "nt":
        # os.symlink with target_is_directory=True creates junction-like link on Windows
        os.symlink(source, target, target_is_directory=True)
    else:
        os.symlink(source, target)

    return EnsureReusableNodeModulesResult(
        strategy="symlink",
        node_modules_path=target,
        source_node_modules_path=source,
    )
