"""Team policy and governance — normalization of partial/unknown shapes.

Port of `normalizeTeamPolicy` and `normalizeTeamGovernance` from
`src/team/state.ts` (lines 167-185, 411-469).

Policy covers transport/runtime concerns (display, worker launch,
dispatch). Governance covers lifecycle/workflow guardrails. They are
kept separate so each layer has a single owner.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Dispatch ack timeout bounds (mirror TS state.ts:367-369).
DEFAULT_DISPATCH_ACK_TIMEOUT_MS = 2_000
MIN_DISPATCH_ACK_TIMEOUT_MS = 100
MAX_DISPATCH_ACK_TIMEOUT_MS = 10_000


class TeamDisplayMode(StrEnum):
    """How the team UI is presented."""

    SPLIT_PANE = "split_pane"
    AUTO = "auto"


class TeamWorkerLaunchMode(StrEnum):
    """How worker panes are launched."""

    INTERACTIVE = "interactive"
    PROMPT = "prompt"


class TeamDispatchMode(StrEnum):
    """Default dispatch transport strategy for the team."""

    HOOK_PREFERRED_WITH_FALLBACK = "hook_preferred_with_fallback"
    TRANSPORT_DIRECT = "transport_direct"


@dataclass
class TeamPolicy:
    """Transport/runtime policy persisted alongside the team manifest.

    Attributes:
        display_mode: How the team UI is rendered (split_pane | auto).
        worker_launch_mode: How worker panes are launched (interactive | prompt).
        dispatch_mode: Default dispatch transport strategy.
        dispatch_ack_timeout_ms: Bounded timeout for dispatch ACKs (ms).
    """

    display_mode: TeamDisplayMode = TeamDisplayMode.AUTO
    worker_launch_mode: TeamWorkerLaunchMode = TeamWorkerLaunchMode.INTERACTIVE
    dispatch_mode: TeamDispatchMode = TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK
    dispatch_ack_timeout_ms: int = DEFAULT_DISPATCH_ACK_TIMEOUT_MS

    def to_dict(self) -> dict[str, Any]:
        return {
            "display_mode": self.display_mode.value,
            "worker_launch_mode": self.worker_launch_mode.value,
            "dispatch_mode": self.dispatch_mode.value,
            "dispatch_ack_timeout_ms": self.dispatch_ack_timeout_ms,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamPolicy:
        return normalize_team_policy(d)


@dataclass
class TeamGovernance:
    """Lifecycle/workflow guardrails persisted alongside the manifest.

    Kept separate from transport/runtime policy so each layer has a
    single owner.

    Attributes:
        delegation_only: When True, leader must delegate (cannot self-execute).
        plan_approval_required: When True, plans must be approved before exec.
        nested_teams_allowed: When True, workers may launch sub-teams.
        one_team_per_leader_session: When True, leader session is exclusive.
        cleanup_requires_all_workers_inactive: When True, all workers must be
            inactive before cleanup proceeds.
    """

    delegation_only: bool = False
    plan_approval_required: bool = False
    nested_teams_allowed: bool = False
    one_team_per_leader_session: bool = True
    cleanup_requires_all_workers_inactive: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "delegation_only": self.delegation_only,
            "plan_approval_required": self.plan_approval_required,
            "nested_teams_allowed": self.nested_teams_allowed,
            "one_team_per_leader_session": self.one_team_per_leader_session,
            "cleanup_requires_all_workers_inactive": (
                self.cleanup_requires_all_workers_inactive
            ),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TeamGovernance:
        return normalize_team_governance(d)


# --- normalization helpers -------------------------------------------------


def _coerce_finite_float(raw: Any) -> float | None:
    """Mirror TS `Number(raw)` + `Number.isFinite` semantics.

    Returns the numeric value if finite, else None. Booleans coerce to
    0/1 like JS, but are accepted (matching the TS path where booleans
    pass through Number()).
    """
    if isinstance(raw, bool):
        # JS Number(true) === 1, Number(false) === 0
        return float(raw)
    if isinstance(raw, (int, float)):
        f = float(raw)
        if math.isfinite(f):
            return f
        return None
    if isinstance(raw, str):
        try:
            f = float(raw)
        except ValueError:
            return None
        if math.isfinite(f):
            return f
        return None
    # Any other type (dict, list, None, object) → NaN in JS → None here.
    return None


def _clamp_dispatch_ack_timeout_ms(raw: Any) -> int:
    """Clamp to [MIN, MAX] after flooring; fall back to DEFAULT if non-finite.

    Mirrors TS clampDispatchAckTimeoutMs (state.ts:433-438).
    """
    as_num = _coerce_finite_float(raw)
    if as_num is None:
        return DEFAULT_DISPATCH_ACK_TIMEOUT_MS
    floored = math.floor(as_num)
    return max(MIN_DISPATCH_ACK_TIMEOUT_MS, min(MAX_DISPATCH_ACK_TIMEOUT_MS, floored))


def normalize_team_policy(
    policy: dict[str, Any] | None,
    defaults: dict[str, Any] | None = None,
) -> TeamPolicy:
    """Collapse a partial/unknown policy shape into a canonical TeamPolicy.

    Mirrors TS normalizeTeamPolicy (state.ts:440-455).

    Args:
        policy: Raw partial policy dict (or None). Unknown fields are
            ignored; invalid enum values fall back to defaults.
        defaults: Optional dict with `display_mode` and `worker_launch_mode`
            overrides used as the base when the raw policy is missing or
            has invalid values for those fields. Defaults to
            {"display_mode": "auto", "worker_launch_mode": "interactive"}.

    Returns:
        A fully-populated TeamPolicy.
    """
    defaults = defaults or {}
    base_display = _coerce_display_mode(
        defaults.get("display_mode"), fallback=TeamDisplayMode.AUTO
    )
    base_worker_launch = _coerce_worker_launch_mode(
        defaults.get("worker_launch_mode"),
        fallback=TeamWorkerLaunchMode.INTERACTIVE,
    )

    raw: dict[str, Any] = policy or {}

    # display_mode: only the explicit string "split_pane" overrides the base.
    display_mode = (
        TeamDisplayMode.SPLIT_PANE
        if raw.get("display_mode") == TeamDisplayMode.SPLIT_PANE.value
        else base_display
    )

    # worker_launch_mode: only the explicit string "prompt" overrides the base.
    worker_launch_mode = (
        TeamWorkerLaunchMode.PROMPT
        if raw.get("worker_launch_mode") == TeamWorkerLaunchMode.PROMPT.value
        else base_worker_launch
    )

    # dispatch_mode: any value other than the explicit "transport_direct"
    # collapses to "hook_preferred_with_fallback" (matches TS).
    dispatch_mode = (
        TeamDispatchMode.TRANSPORT_DIRECT
        if raw.get("dispatch_mode") == TeamDispatchMode.TRANSPORT_DIRECT.value
        else TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK
    )

    return TeamPolicy(
        display_mode=display_mode,
        worker_launch_mode=worker_launch_mode,
        dispatch_mode=dispatch_mode,
        dispatch_ack_timeout_ms=_clamp_dispatch_ack_timeout_ms(
            raw.get("dispatch_ack_timeout_ms")
        ),
    )


def normalize_team_governance(
    governance: dict[str, Any] | None,
    legacy_policy: dict[str, Any] | None = None,
) -> TeamGovernance:
    """Collapse a partial/unknown governance shape into canonical form.

    Mirrors TS normalizeTeamGovernance (state.ts:457-469).

    Booleans default to False except for `one_team_per_leader_session`
    and `cleanup_requires_all_workers_inactive`, which default to True
    (only an explicit `False` flips them).

    Args:
        governance: Raw partial governance dict (or None).
        legacy_policy: Older payloads sometimes nested governance fields
            under policy; this preserves that compatibility path.

    Returns:
        A fully-populated TeamGovernance.
    """
    source: dict[str, Any] = (
        governance
        if governance is not None
        else (legacy_policy if legacy_policy is not None else {})
    )

    return TeamGovernance(
        delegation_only=source.get("delegation_only") is True,
        plan_approval_required=source.get("plan_approval_required") is True,
        nested_teams_allowed=source.get("nested_teams_allowed") is True,
        # `!== false` in TS: anything that isn't the literal False stays True.
        one_team_per_leader_session=(
            source.get("one_team_per_leader_session") is not False
        ),
        cleanup_requires_all_workers_inactive=(
            source.get("cleanup_requires_all_workers_inactive") is not False
        ),
    )


# --- private enum coercion -------------------------------------------------


def _coerce_display_mode(raw: Any, fallback: TeamDisplayMode) -> TeamDisplayMode:
    if isinstance(raw, TeamDisplayMode):
        return raw
    if isinstance(raw, str):
        try:
            return TeamDisplayMode(raw)
        except ValueError:
            return fallback
    return fallback


def _coerce_worker_launch_mode(
    raw: Any, fallback: TeamWorkerLaunchMode
) -> TeamWorkerLaunchMode:
    if isinstance(raw, TeamWorkerLaunchMode):
        return raw
    if isinstance(raw, str):
        try:
            return TeamWorkerLaunchMode(raw)
        except ValueError:
            return fallback
    return fallback


__all__ = [
    "DEFAULT_DISPATCH_ACK_TIMEOUT_MS",
    "MIN_DISPATCH_ACK_TIMEOUT_MS",
    "MAX_DISPATCH_ACK_TIMEOUT_MS",
    "TeamDisplayMode",
    "TeamWorkerLaunchMode",
    "TeamDispatchMode",
    "TeamPolicy",
    "TeamGovernance",
    "normalize_team_policy",
    "normalize_team_governance",
]
