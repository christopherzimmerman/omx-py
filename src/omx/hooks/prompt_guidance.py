"""Backward-compatible alias for `omx.hooks.prompt_guidance_contract`.

The canonical port of `src/hooks/prompt-guidance-contract.ts` lives in
`omx.hooks.prompt_guidance_contract` (filename matches the TS 1:1). This
module is kept as a thin re-export shim so any legacy import paths keep
working.

New code should import from `omx.hooks.prompt_guidance_contract`.
"""

from __future__ import annotations

from omx.hooks.prompt_guidance_contract import (
    CATALOG_CONTRACTS,
    CORE_ROLE_CONTRACTS,
    GuidanceSurfaceContract,
    LEGACY_PROMPT_CONTRACTS,
    ROOT_TEMPLATE_CONTRACTS,
    SCENARIO_ROLE_CONTRACTS,
    SKILL_CONTRACTS,
    SPECIALIZED_PROMPT_CONTRACTS,
    WAVE_TWO_CONTRACTS,
)

__all__ = [
    "CATALOG_CONTRACTS",
    "CORE_ROLE_CONTRACTS",
    "GuidanceSurfaceContract",
    "LEGACY_PROMPT_CONTRACTS",
    "ROOT_TEMPLATE_CONTRACTS",
    "SCENARIO_ROLE_CONTRACTS",
    "SKILL_CONTRACTS",
    "SPECIALIZED_PROMPT_CONTRACTS",
    "WAVE_TWO_CONTRACTS",
]
