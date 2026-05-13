"""Team state root path resolution.

Port of ``src/team/state-root.ts``.

This module is the **single source of truth** for the on-disk layout of the
team state tree. All ``team.state.*`` modules and ``team.team_ops`` callers
must route through :func:`team_dir` (or :func:`resolve_team_state_root` when
they need the env-aware base path) instead of hand-rolling
``Path(cwd) / ".omx" / "team" / team_name``.

Path layout
-----------

By default the Python port keeps the historical layout::

    .omx/team/{team_name}/

The TS implementation uses the canonical layout::

    .omx/state/team/{team_name}/

The ``OMX_TEAM_STATE_ROOT`` environment variable redirects the base path so
the Python port can be pointed at the TS layout (or any other root) without
touching call sites. The variable contains the **base** directory under which
``{team_name}/`` is appended — for example::

    OMX_TEAM_STATE_ROOT=.omx/state/team

would yield ``.omx/state/team/{team_name}/`` for every caller.

Known divergence (intentional, flagged for a future coordinated fix):

* ``team/worker_bootstrap.py`` writes the composed-instructions file under the
  TS canonical ``.omx/state/team/{team_name}/worker-agents.md`` directly. That
  path does **not** flow through this resolver yet; unifying it is a breaking
  change because it would move an existing on-disk artifact for every
  deployed install. Tracked separately.

Locked decisions
----------------

* **Sync only.** No asyncio.
* **Stdlib only.** Resolution is pure ``pathlib`` + ``os.environ`` lookups.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

__all__ = [
    "OMX_TEAM_STATE_ROOT_ENV",
    "resolve_team_state_root",
    "team_dir",
]


#: Environment variable that overrides the team state-root base path.
OMX_TEAM_STATE_ROOT_ENV = "OMX_TEAM_STATE_ROOT"

#: Historical Python default — kept as the default so existing call sites and
#: persisted state on disk are unaffected when the env override is absent.
_DEFAULT_BASE_SEGMENTS = (".omx", "team")


def resolve_team_state_root(
    cwd: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the env-aware **base** path under which ``{team_name}/`` lives.

    Resolution rules (mirroring TS ``resolveCanonicalTeamStateRoot``):

    1. If ``OMX_TEAM_STATE_ROOT`` is set in ``env`` (or ``os.environ`` when
       ``env`` is ``None``) and its trimmed value is non-empty, resolve it
       against ``cwd``. Absolute overrides are returned as-is; relative
       overrides are joined onto ``cwd``.
    2. Otherwise fall back to the Python default ``cwd/.omx/team``.

    The returned path is **not** appended with a team name — callers should
    use :func:`team_dir` for the per-team directory.

    Args:
        cwd: Leader working directory. May be a ``str`` or ``Path``.
        env: Optional environment mapping. When ``None``, ``os.environ`` is
            consulted. Pass an explicit mapping in tests to avoid leaking
            ``OMX_TEAM_STATE_ROOT`` between cases.

    Returns:
        Absolute-or-relative :class:`~pathlib.Path` to the base state root.
        The path is **not** required to exist; callers create it lazily.
    """
    env_map = os.environ if env is None else env
    raw_override = env_map.get(OMX_TEAM_STATE_ROOT_ENV)

    if raw_override is not None:
        trimmed = raw_override.strip()
        if trimmed:
            override = Path(trimmed)
            # Absolute overrides win outright; relative overrides are anchored
            # at cwd so behavior matches Node's ``path.resolve(cwd, override)``.
            if override.is_absolute():
                return override
            return Path(cwd) / override

    return Path(cwd).joinpath(*_DEFAULT_BASE_SEGMENTS)


def team_dir(team_name: str, cwd: str | os.PathLike[str]) -> Path:
    """Resolve the per-team state directory path.

    Equivalent to ``resolve_team_state_root(cwd) / team_name``. Callers in
    ``team.state.*`` and ``team.team_ops`` must use this helper rather than
    composing the path inline so the env override and the default layout
    stay in lock-step.

    Args:
        team_name: Team name (used verbatim as the final path segment).
        cwd: Leader working directory.

    Returns:
        :class:`~pathlib.Path` to ``<base>/{team_name}``.
    """
    return resolve_team_state_root(cwd) / team_name
