"""Team worker model/CLI contract.

Port of ``src/team/model-contract.ts`` covering all 13 exports plus the
existing back-compat helpers (``resolve_worker_cli``, ``resolve_worker_model``)
that the original Python stub exposed.

This module owns:

* Parsing/normalizing the ``OMX_TEAM_WORKER_LAUNCH_ARGS`` shell-style flag
  string into a canonical argv list.
* Inheriting ``--model`` / ``-c model_reasoning_effort=...`` / ``--madmax``
  from the parent Codex process.
* Resolving the default model for a given agent type, honouring
  ``OMX_DEFAULT_FRONTIER_MODEL`` / ``OMX_DEFAULT_STANDARD_MODEL`` /
  ``OMX_DEFAULT_SPARK_MODEL`` (plus the legacy ``OMX_SPARK_MODEL`` alias)
  per ``AGENTS.md`` model routing.

Resolution order for env-backed defaults (mirrors
``src/config/models.ts``):

* Frontier: ``OMX_DEFAULT_FRONTIER_MODEL`` -> ``$CODEX_HOME/.omx-config.json``
  ``env`` block -> ``$CODEX_HOME/config.toml`` ``model`` -> ``DEFAULT_FRONTIER_MODEL``.
* Standard: ``OMX_DEFAULT_STANDARD_MODEL`` (env / config) -> Frontier chain.
* Spark / low-complexity: ``OMX_DEFAULT_SPARK_MODEL`` -> ``OMX_SPARK_MODEL``
  -> config ``env`` block -> ``models.team_low_complexity`` override
  -> ``DEFAULT_SPARK_MODEL``.

TOML parsing uses the stdlib ``tomllib`` module (Python 3.11+) per the
"stdlib only" locked decision.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from omx.agents.roles import get_agent
from omx.utils.paths import codex_home

# --- flag tokens (mirror TS) ------------------------------------------------

MADMAX_FLAG = "--madmax"
CODEX_BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"
MODEL_FLAG = "--model"
CONFIG_FLAG = "-c"
REASONING_KEY = "model_reasoning_effort"

# --- env var names (canonical + legacy) -------------------------------------

OMX_DEFAULT_FRONTIER_MODEL_ENV = "OMX_DEFAULT_FRONTIER_MODEL"
OMX_DEFAULT_STANDARD_MODEL_ENV = "OMX_DEFAULT_STANDARD_MODEL"
OMX_DEFAULT_SPARK_MODEL_ENV = "OMX_DEFAULT_SPARK_MODEL"
OMX_SPARK_MODEL_ENV = "OMX_SPARK_MODEL"  # legacy alias for spark default

# --- canonical model defaults (mirror config/models.ts) ---------------------

DEFAULT_FRONTIER_MODEL = "gpt-5.5"
DEFAULT_STANDARD_MODEL = "gpt-5.4-mini"
DEFAULT_SPARK_MODEL = "gpt-5.3-codex-spark"

#: Canonical default only; effective low-complexity resolution flows
#: through :func:`resolve_team_low_complexity_default_model`.
TEAM_LOW_COMPLEXITY_DEFAULT_MODEL = DEFAULT_SPARK_MODEL

#: Keys searched (in order) in the ``models`` block of ``.omx-config.json``
#: for a low-complexity team worker override.
TEAM_LOW_COMPLEXITY_MODEL_KEYS = (
    "team_low_complexity",
    "team-low-complexity",
    "teamLowComplexity",
)

#: Low-complexity agent types per AGENTS.md model routing.
LOW_COMPLEXITY_AGENT_TYPES = frozenset({"explore", "explorer", "style-reviewer"})

#: Valid reasoning-effort strings (matches TS TeamReasoningEffort union).
TeamReasoningEffort = Literal["low", "medium", "high", "xhigh"]
_VALID_REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})

# --- legacy back-compat helpers (predate the full port) ---------------------

DEFAULT_WORKER_CLI = "codex"
DEFAULT_WORKER_MODEL = "o4-mini"


def resolve_worker_cli(explicit: str | None = None) -> str:
    """Resolve the CLI tool for a worker.

    Kept for back-compat with ``tests/unit/test_team.py``.
    """
    if explicit:
        return explicit.strip().lower()
    env_val = os.environ.get("OMX_TEAM_WORKER_CLI", "").strip().lower()
    return env_val or DEFAULT_WORKER_CLI


def resolve_worker_model(explicit: str | None = None) -> str:
    """Resolve the model for a worker (legacy helper)."""
    if explicit:
        return explicit
    return os.environ.get("OMX_TEAM_WORKER_MODEL", DEFAULT_WORKER_MODEL)


# --- parsed-args dataclass --------------------------------------------------


@dataclass(frozen=True)
class ParsedTeamWorkerLaunchArgs:
    """Result of :func:`parse_team_worker_launch_args`.

    Mirrors TS ``ParsedTeamWorkerLaunchArgs``.
    """

    passthrough: list[str] = field(default_factory=list)
    wants_bypass: bool = False
    reasoning_override: str | None = None
    model_override: str | None = None


# --- small internal helpers -------------------------------------------------


_REASONING_OVERRIDE_RE = re.compile(rf"^{re.escape(REASONING_KEY)}\s*=")


def _is_reasoning_override(value: str) -> bool:
    """True if ``value`` looks like ``model_reasoning_effort=...``."""
    return bool(_REASONING_OVERRIDE_RE.match(value.strip()))


def _is_valid_model_value(value: str) -> bool:
    """Mirror TS ``isValidModelValue``: non-empty and not a flag."""
    return value.strip() != "" and not value.startswith("-")


def _normalize_optional_model(model: str | None) -> str | None:
    """Strip + return ``model`` if non-empty, else ``None``."""
    if not isinstance(model, str):
        return None
    trimmed = model.strip()
    return trimmed or None


def _normalize_optional_reasoning(
    reasoning: str | None,
) -> TeamReasoningEffort | None:
    """Lowercase + validate a reasoning-effort string."""
    if not isinstance(reasoning, str):
        return None
    normalized = reasoning.strip().lower()
    if normalized in _VALID_REASONING_EFFORTS:
        return normalized  # type: ignore[return-value]
    return None


# --- shell-style arg splitting ---------------------------------------------


def split_worker_launch_args(raw: str | None) -> list[str]:
    """Split a whitespace-separated CLI flag string into tokens.

    Mirrors TS ``splitWorkerLaunchArgs`` (line 63), which does a naive
    ``raw.split(/\\s+/)`` — no shell quote handling. We intentionally
    match that behaviour rather than using :mod:`shlex`, because the TS
    contract callers do not produce quoted values.

    Empty / whitespace-only input returns an empty list.
    """
    if not raw or raw.strip() == "":
        return []
    return [s for s in raw.split() if s]


# --- argv parsing -----------------------------------------------------------


def parse_team_worker_launch_args(args: list[str]) -> ParsedTeamWorkerLaunchArgs:
    """Extract ``--model`` / ``-c model_reasoning_effort=...`` / bypass from argv.

    Mirrors TS ``parseTeamWorkerLaunchArgs``. Orphan ``--model`` with no
    valid following value (or ``--model=`` with an empty value) is
    silently dropped and never sent to passthrough.
    """
    passthrough: list[str] = []
    wants_bypass = False
    reasoning_override: str | None = None
    model_override: str | None = None

    i = 0
    n = len(args)
    while i < n:
        arg = args[i]

        if arg == CODEX_BYPASS_FLAG or arg == MADMAX_FLAG:
            wants_bypass = True
            i += 1
            continue

        if arg == MODEL_FLAG:
            maybe = args[i + 1] if i + 1 < n else None
            if isinstance(maybe, str) and _is_valid_model_value(maybe):
                model_override = maybe.strip()
                i += 2
            else:
                # Orphan --model is silently dropped.
                i += 1
            continue

        if arg.startswith(f"{MODEL_FLAG}="):
            inline = arg[len(MODEL_FLAG) + 1 :].strip()
            if _is_valid_model_value(inline):
                model_override = inline
            # --model= with empty/invalid value is silently dropped.
            i += 1
            continue

        if arg == CONFIG_FLAG:
            maybe = args[i + 1] if i + 1 < n else None
            if isinstance(maybe, str) and _is_reasoning_override(maybe):
                reasoning_override = maybe
                i += 2
                continue
            # Fall through: -c without a reasoning-effort value is
            # passthrough.

        passthrough.append(arg)
        i += 1

    return ParsedTeamWorkerLaunchArgs(
        passthrough=passthrough,
        wants_bypass=wants_bypass,
        reasoning_override=reasoning_override,
        model_override=model_override,
    )


def collect_inheritable_team_worker_args(codex_args: list[str]) -> list[str]:
    """Filter the parent codex argv down to the flags a team worker inherits.

    Mirrors TS ``collectInheritableTeamWorkerArgs``. Order is fixed:
    bypass, reasoning override, model override.
    """
    parsed = parse_team_worker_launch_args(codex_args)
    inherited: list[str] = []
    if parsed.wants_bypass:
        inherited.append(CODEX_BYPASS_FLAG)
    if parsed.reasoning_override:
        inherited.extend([CONFIG_FLAG, parsed.reasoning_override])
    if parsed.model_override:
        inherited.extend([MODEL_FLAG, parsed.model_override])
    return inherited


def normalize_team_worker_launch_args(
    args: list[str],
    preferred_model: str | None = None,
    preferred_reasoning: TeamReasoningEffort | str | None = None,
) -> list[str]:
    """Dedupe + canonicalize an argv list.

    Mirrors TS ``normalizeTeamWorkerLaunchArgs``. After this call there
    will be at most one ``--model <value>`` and at most one
    ``-c model_reasoning_effort="..."`` pair, appended in that order
    after any passthrough flags.

    Precedence:
      * Reasoning: explicit override in ``args`` beats ``preferred_reasoning``.
      * Model: ``preferred_model`` (if non-empty) wins over any model
        override already present in ``args`` — callers use this slot to
        force a final selection.
    """
    parsed = parse_team_worker_launch_args(args)
    normalized: list[str] = list(parsed.passthrough)

    if parsed.wants_bypass:
        normalized.append(CODEX_BYPASS_FLAG)

    selected_reasoning: str | None
    if parsed.reasoning_override is not None:
        selected_reasoning = parsed.reasoning_override
    else:
        norm_pref = _normalize_optional_reasoning(preferred_reasoning)
        selected_reasoning = (
            f'{REASONING_KEY}="{norm_pref}"' if norm_pref is not None else None
        )
    if selected_reasoning:
        normalized.extend([CONFIG_FLAG, selected_reasoning])

    selected_model = _normalize_optional_model(
        preferred_model
    ) or _normalize_optional_model(parsed.model_override)
    if selected_model:
        normalized.extend([MODEL_FLAG, selected_model])

    return normalized


# --- resolve options --------------------------------------------------------


@dataclass(frozen=True)
class ResolveTeamWorkerLaunchArgsOptions:
    """Options bag for :func:`resolve_team_worker_launch_args`.

    Mirrors TS ``ResolveTeamWorkerLaunchArgsOptions``.
    """

    existing_raw: str | None = None
    inherited_args: list[str] | None = None
    fallback_model: str | None = None
    preferred_reasoning: TeamReasoningEffort | str | None = None


def resolve_team_worker_launch_args(
    options: ResolveTeamWorkerLaunchArgsOptions,
) -> list[str]:
    """Combine env, inherited, and fallback into the final launch argv.

    Mirrors TS ``resolveTeamWorkerLaunchArgs``. Model precedence:

    1. ``--model`` already in ``existing_raw`` (env-supplied).
    2. ``--model`` in ``inherited_args`` (parent process).
    3. ``options.fallback_model``.
    """
    env_args = split_worker_launch_args(options.existing_raw)
    inherited_args = options.inherited_args or []
    all_args = env_args + inherited_args

    env_model = _normalize_optional_model(
        parse_team_worker_launch_args(env_args).model_override
    )
    inherited_model = _normalize_optional_model(
        parse_team_worker_launch_args(inherited_args).model_override
    )
    fallback_model = _normalize_optional_model(options.fallback_model)
    selected_model = env_model or inherited_model or fallback_model

    return normalize_team_worker_launch_args(
        all_args, selected_model, options.preferred_reasoning
    )


# --- agent-type → model / reasoning routing ---------------------------------


def resolve_agent_reasoning_effort(
    agent_type: str | None,
) -> TeamReasoningEffort | None:
    """Map an agent type to its declared reasoning effort.

    Mirrors TS ``resolveAgentReasoningEffort``. Returns ``None`` if the
    agent type is unknown or the declared value isn't a valid
    ``TeamReasoningEffort``.
    """
    if not isinstance(agent_type, str) or agent_type.strip() == "":
        return None
    agent = get_agent(agent_type)
    if agent is None:
        return None
    return _normalize_optional_reasoning(agent.reasoning_effort)


def is_low_complexity_agent_type(agent_type: str | None) -> bool:
    """True if ``agent_type`` routes to the low-complexity/spark lane.

    Mirrors TS ``isLowComplexityAgentType``. Any agent type ending in
    ``-low`` is treated as low-complexity in addition to the explicit
    set in :data:`LOW_COMPLEXITY_AGENT_TYPES`.
    """
    if not agent_type:
        return False
    normalized = agent_type.strip().lower()
    if normalized == "":
        return False
    if normalized.endswith("-low"):
        return True
    return normalized in LOW_COMPLEXITY_AGENT_TYPES


def resolve_team_low_complexity_default_model(
    codex_home_override: str | Path | None = None,
) -> str:
    """Effective default model for low-complexity / spark lane.

    Mirrors TS ``resolveTeamLowComplexityDefaultModel`` (which simply
    delegates to ``getSparkDefaultModel``).
    """
    return _get_spark_default_model(codex_home_override)


def resolve_agent_default_model(
    agent_type: str | None,
    codex_home_override: str | Path | None = None,
) -> str | None:
    """Map an agent type to its default model.

    Mirrors TS ``resolveAgentDefaultModel``:

    * Empty/unknown → ``None``.
    * Any type ending in ``-low`` → spark default.
    * ``executor`` → frontier default (parity special case).
    * Otherwise dispatch on the agent's ``model_class``:
      ``fast`` → spark, ``frontier`` → frontier, ``standard`` → standard.
    """
    if not isinstance(agent_type, str) or agent_type.strip() == "":
        return None
    normalized = agent_type.strip().lower()
    if normalized == "":
        return None
    if normalized.endswith("-low"):
        return resolve_team_low_complexity_default_model(codex_home_override)
    if normalized == "executor":
        return _get_main_default_model(codex_home_override)

    agent = get_agent(normalized)
    if agent is None:
        return None
    model_class = agent.model_class
    if model_class == "fast":
        return resolve_team_low_complexity_default_model(codex_home_override)
    if model_class == "frontier":
        return _get_main_default_model(codex_home_override)
    if model_class == "standard":
        return _get_standard_default_model(codex_home_override)
    return None


# --- env / config chains (private; would normally live in config/models) ---
#
# These mirror src/config/models.ts but are inlined here so this module
# can be ported in isolation per Phase 2.3 scope. When config/models.py
# is fully ported, these helpers should be replaced with imports from
# that module.


def _normalize_configured_value(value: object) -> str | None:
    """Mirror TS ``normalizeConfiguredValue``: trimmed non-empty string."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _resolved_codex_home(override: str | Path | None) -> Path:
    if override is not None:
        return Path(override)
    return codex_home()


def _read_omx_config_file(override: str | Path | None) -> dict | None:
    """Read ``$CODEX_HOME/.omx-config.json``; return ``None`` on any error.

    Hoisted to :func:`omx.config.models.read_omx_config_file`. Kept as a
    private alias so existing call sites in this module stay stable.
    """
    from omx.config.models import read_omx_config_file

    return read_omx_config_file(override)


def _read_codex_config_file(override: str | Path | None) -> dict | None:
    """Read ``$CODEX_HOME/config.toml``. Hoisted to ``config.models``."""
    from omx.config.models import read_codex_config_file

    return read_codex_config_file(override)


def _read_config_env_value(key: str, override: str | Path | None) -> str | None:
    config = _read_omx_config_file(override)
    if not config:
        return None
    env_block = config.get("env")
    if not isinstance(env_block, dict):
        return None
    return _normalize_configured_value(env_block.get(key))


def _read_models_block(override: str | Path | None) -> dict | None:
    config = _read_omx_config_file(override)
    if not config:
        return None
    models = config.get("models")
    if not isinstance(models, dict):
        return None
    return models


def _read_team_low_complexity_override(
    override: str | Path | None,
) -> str | None:
    models = _read_models_block(override)
    if not models:
        return None
    for key in TEAM_LOW_COMPLEXITY_MODEL_KEYS:
        value = _normalize_configured_value(models.get(key))
        if value:
            return value
    return None


def _get_env_configured_main_default_model(
    override: str | Path | None,
) -> str | None:
    return _normalize_configured_value(
        os.environ.get(OMX_DEFAULT_FRONTIER_MODEL_ENV)
    ) or _read_config_env_value(OMX_DEFAULT_FRONTIER_MODEL_ENV, override)


def _get_env_configured_standard_default_model(
    override: str | Path | None,
) -> str | None:
    return _normalize_configured_value(
        os.environ.get(OMX_DEFAULT_STANDARD_MODEL_ENV)
    ) or _read_config_env_value(OMX_DEFAULT_STANDARD_MODEL_ENV, override)


def _get_env_configured_spark_default_model(
    override: str | Path | None,
) -> str | None:
    return (
        _normalize_configured_value(os.environ.get(OMX_DEFAULT_SPARK_MODEL_ENV))
        or _normalize_configured_value(os.environ.get(OMX_SPARK_MODEL_ENV))
        or _read_config_env_value(OMX_DEFAULT_SPARK_MODEL_ENV, override)
        or _read_config_env_value(OMX_SPARK_MODEL_ENV, override)
    )


def _get_codex_config_root_model(override: str | Path | None) -> str | None:
    config = _read_codex_config_file(override)
    if not config:
        return None
    return _normalize_configured_value(config.get("model"))


def _get_main_default_model(override: str | Path | None) -> str:
    """Hoisted to :func:`omx.config.models.get_main_default_model`."""
    from omx.config.models import get_main_default_model

    return get_main_default_model(override)


def _get_standard_default_model(override: str | Path | None) -> str:
    """Hoisted to :func:`omx.config.models.get_standard_default_model`."""
    from omx.config.models import get_standard_default_model

    return get_standard_default_model(override)


def _get_spark_default_model(override: str | Path | None) -> str:
    """Hoisted to :func:`omx.config.models.get_spark_default_model`."""
    from omx.config.models import get_spark_default_model

    return get_spark_default_model(override)


__all__ = [
    # flag tokens
    "MADMAX_FLAG",
    "CODEX_BYPASS_FLAG",
    "MODEL_FLAG",
    "CONFIG_FLAG",
    "REASONING_KEY",
    # env var names
    "OMX_DEFAULT_FRONTIER_MODEL_ENV",
    "OMX_DEFAULT_STANDARD_MODEL_ENV",
    "OMX_DEFAULT_SPARK_MODEL_ENV",
    "OMX_SPARK_MODEL_ENV",
    # canonical defaults
    "DEFAULT_FRONTIER_MODEL",
    "DEFAULT_STANDARD_MODEL",
    "DEFAULT_SPARK_MODEL",
    "TEAM_LOW_COMPLEXITY_DEFAULT_MODEL",
    "TEAM_LOW_COMPLEXITY_MODEL_KEYS",
    "LOW_COMPLEXITY_AGENT_TYPES",
    # types
    "TeamReasoningEffort",
    "ParsedTeamWorkerLaunchArgs",
    "ResolveTeamWorkerLaunchArgsOptions",
    # functions
    "split_worker_launch_args",
    "parse_team_worker_launch_args",
    "collect_inheritable_team_worker_args",
    "normalize_team_worker_launch_args",
    "resolve_team_worker_launch_args",
    "resolve_agent_reasoning_effort",
    "resolve_agent_default_model",
    "is_low_complexity_agent_type",
    "resolve_team_low_complexity_default_model",
    # legacy back-compat
    "DEFAULT_WORKER_CLI",
    "DEFAULT_WORKER_MODEL",
    "resolve_worker_cli",
    "resolve_worker_model",
]
