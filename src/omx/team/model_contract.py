"""Worker model selection and inheritance.

Port of src/team/model-contract.ts.
"""

from __future__ import annotations

import os

DEFAULT_WORKER_CLI = "codex"
DEFAULT_WORKER_MODEL = "o4-mini"


def resolve_worker_cli(explicit: str | None = None) -> str:
    """Resolve the CLI tool for a worker."""
    if explicit:
        return explicit.strip().lower()
    env_val = os.environ.get("OMX_TEAM_WORKER_CLI", "").strip().lower()
    return env_val or DEFAULT_WORKER_CLI


def resolve_worker_model(explicit: str | None = None) -> str:
    """Resolve the model for a worker."""
    if explicit:
        return explicit
    return os.environ.get("OMX_TEAM_WORKER_MODEL", DEFAULT_WORKER_MODEL)
