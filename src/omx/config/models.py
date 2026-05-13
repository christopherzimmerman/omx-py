"""Model configuration.

Port of :file:`src/config/models.ts`.

Reads per-mode model overrides and default-env overrides from
``$CODEX_HOME/.omx-config.json`` and falls back to the Codex
``$CODEX_HOME/config.toml`` root ``model`` key. Mirrors the TS
resolution chain exactly, plus the legacy ``OMX_SPARK_MODEL`` alias for
the spark/low-complexity lane.

Config format::

    {
      "env": {
        "OMX_DEFAULT_FRONTIER_MODEL": "your-frontier-model",
        "OMX_DEFAULT_STANDARD_MODEL": "your-standard-model",
        "OMX_DEFAULT_SPARK_MODEL": "your-spark-model"
      },
      "models": {
        "default": "o4-mini",
        "team": "gpt-4.1"
      }
    }

Resolution order:

* **Frontier / main:** ``OMX_DEFAULT_FRONTIER_MODEL`` (env) →
  ``$CODEX_HOME/.omx-config.json`` ``env`` block →
  ``$CODEX_HOME/config.toml`` root ``model`` → :data:`DEFAULT_FRONTIER_MODEL`.
* **Standard:** ``OMX_DEFAULT_STANDARD_MODEL`` (env/config) → frontier chain.
* **Spark / low-complexity:** ``OMX_DEFAULT_SPARK_MODEL`` →
  ``OMX_SPARK_MODEL`` (legacy alias) → ``env`` block for either of those →
  ``models.team_low_complexity`` (or its key aliases) →
  :data:`DEFAULT_SPARK_MODEL`.
* **Mode-specific:** ``models.<mode>`` → ``models.default`` → frontier chain.

TOML parsing uses the stdlib :mod:`tomllib` (Python 3.11+) per the
"stdlib only" locked decision. No asyncio.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any

from omx.utils.paths import codex_config_path, codex_home

# ---------------------------------------------------------------------------
# Env-var names (canonical + legacy alias)
# ---------------------------------------------------------------------------

OMX_DEFAULT_FRONTIER_MODEL_ENV = "OMX_DEFAULT_FRONTIER_MODEL"
OMX_DEFAULT_STANDARD_MODEL_ENV = "OMX_DEFAULT_STANDARD_MODEL"
OMX_DEFAULT_SPARK_MODEL_ENV = "OMX_DEFAULT_SPARK_MODEL"
OMX_SPARK_MODEL_ENV = "OMX_SPARK_MODEL"  # legacy alias for spark default

# ---------------------------------------------------------------------------
# Canonical model defaults (mirror config/models.ts line 86-88)
# ---------------------------------------------------------------------------

DEFAULT_FRONTIER_MODEL = "gpt-5.5"
DEFAULT_STANDARD_MODEL = "gpt-5.4-mini"
DEFAULT_SPARK_MODEL = "gpt-5.3-codex-spark"

#: TS has no exported ``DEFAULT_SETUP_MODEL`` — it is a local in
#: ``cli/setup.ts`` and ``config/generator.ts`` that aliases
#: :data:`DEFAULT_FRONTIER_MODEL`. Exposed here as the canonical Python
#: source for setup/generator callers that want to share the constant
#: instead of redeclaring it.
DEFAULT_SETUP_MODEL = DEFAULT_FRONTIER_MODEL

#: Keys searched (in order) inside the ``models`` block of
#: ``.omx-config.json`` for a low-complexity team-worker override.
#: Mirrors TS ``TEAM_LOW_COMPLEXITY_MODEL_KEYS``.
TEAM_LOW_COMPLEXITY_MODEL_KEYS: tuple[str, ...] = (
    "team_low_complexity",
    "team-low-complexity",
    "teamLowComplexity",
)


# ---------------------------------------------------------------------------
# Type aliases (mirror TS ``ModelsConfig`` / ``OmxConfigEnv``)
# ---------------------------------------------------------------------------
#
# TS uses index-signature interfaces. The closest stdlib Python analogue
# is a plain ``dict[str, str | None]``. We expose them as module-level
# aliases for documentation and ``isinstance``-style annotations.

ModelsConfig = dict[str, str | None]
OmxConfigEnv = dict[str, str | None]


# ---------------------------------------------------------------------------
# Small internal helpers
# ---------------------------------------------------------------------------


def _normalize_configured_value(value: Any) -> str | None:
    """Mirror TS ``normalizeConfiguredValue``: trimmed non-empty string."""
    if not isinstance(value, str):
        return None
    trimmed = value.strip()
    return trimmed or None


def _resolved_codex_home(override: str | Path | None) -> Path:
    if override is not None:
        return Path(override)
    return codex_home()


# ---------------------------------------------------------------------------
# Raw config file readers (public so other modules can reuse them)
# ---------------------------------------------------------------------------


def read_omx_config_file(
    codex_home_override: str | Path | None = None,
) -> dict[str, Any] | None:
    """Read ``$CODEX_HOME/.omx-config.json``.

    Returns ``None`` if the file is missing, unreadable, or not a JSON
    object. Mirrors TS ``readOmxConfigFile``.
    """
    path = _resolved_codex_home(codex_home_override) / ".omx-config.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def read_codex_config_file(
    codex_home_override: str | Path | None = None,
) -> dict[str, Any] | None:
    """Read ``$CODEX_HOME/config.toml`` via stdlib :mod:`tomllib`.

    Returns ``None`` if the file is missing, unparseable, or not a
    table at the root. Mirrors TS ``readCodexConfigFile``.
    """
    if codex_home_override is not None:
        path = Path(codex_home_override) / "config.toml"
    else:
        path = codex_config_path()
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _read_models_block(
    codex_home_override: str | Path | None,
) -> dict[str, Any] | None:
    config = read_omx_config_file(codex_home_override)
    if not config:
        return None
    models = config.get("models")
    if not isinstance(models, dict):
        return None
    return models


def _read_config_env_value(
    key: str, codex_home_override: str | Path | None
) -> str | None:
    """Read a single key from the ``env`` block of ``.omx-config.json``."""
    config = read_omx_config_file(codex_home_override)
    if not config:
        return None
    env_block = config.get("env")
    if not isinstance(env_block, dict):
        return None
    return _normalize_configured_value(env_block.get(key))


def _read_team_low_complexity_override(
    codex_home_override: str | Path | None,
) -> str | None:
    """Read the team-low-complexity override from the ``models`` block."""
    models = _read_models_block(codex_home_override)
    if not models:
        return None
    for key in TEAM_LOW_COMPLEXITY_MODEL_KEYS:
        value = _normalize_configured_value(models.get(key))
        if value:
            return value
    return None


# ---------------------------------------------------------------------------
# Public env-override readers
# ---------------------------------------------------------------------------


def read_configured_env_overrides(
    codex_home_override: str | Path | None = None,
) -> dict[str, str]:
    """Return the ``env`` block of ``.omx-config.json`` as a dict.

    Mirrors TS ``readConfiguredEnvOverrides``. Only non-empty trimmed
    string values are returned. Missing config / non-object ``env`` →
    empty dict.
    """
    config = read_omx_config_file(codex_home_override)
    if not config:
        return {}
    env_block = config.get("env")
    if not isinstance(env_block, dict):
        return {}
    resolved: dict[str, str] = {}
    for key, value in env_block.items():
        normalized = _normalize_configured_value(value)
        if normalized is not None:
            resolved[key] = normalized
    return resolved


def read_active_provider_env_overrides(
    env: dict[str, str] | None = None,
    codex_home_override: str | Path | None = None,
) -> dict[str, str]:
    """Return the active Codex provider's ``env_key`` -> value mapping.

    Reads ``model_provider`` + ``model_providers.<name>.env_key`` from
    ``$CODEX_HOME/config.toml`` and looks the resulting key up in
    ``env`` (defaulting to :data:`os.environ`). Mirrors TS
    ``readActiveProviderEnvOverrides``.

    Returns an empty dict if any step in the chain is missing.
    """
    source_env: dict[str, str]
    if env is None:
        source_env = dict(os.environ)
    else:
        source_env = env

    config = read_codex_config_file(codex_home_override)
    if not config:
        return {}

    active_provider = _normalize_configured_value(config.get("model_provider"))
    if not active_provider:
        return {}

    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        return {}

    provider_config = providers.get(active_provider)
    if not isinstance(provider_config, dict):
        return {}

    env_key = _normalize_configured_value(provider_config.get("env_key"))
    if not env_key:
        return {}

    env_value = _normalize_configured_value(source_env.get(env_key))
    if env_value is None:
        return {}
    return {env_key: env_value}


# ---------------------------------------------------------------------------
# Env-configured default getters (process env + .omx-config.json env block)
# ---------------------------------------------------------------------------


def get_env_configured_main_default_model(
    env: dict[str, str] | None = None,
    codex_home_override: str | Path | None = None,
) -> str | None:
    """Resolve ``OMX_DEFAULT_FRONTIER_MODEL`` from env or config.

    Mirrors TS ``getEnvConfiguredMainDefaultModel``.
    """
    source_env = os.environ if env is None else env
    return _normalize_configured_value(
        source_env.get(OMX_DEFAULT_FRONTIER_MODEL_ENV)
    ) or _read_config_env_value(OMX_DEFAULT_FRONTIER_MODEL_ENV, codex_home_override)


def get_env_configured_standard_default_model(
    env: dict[str, str] | None = None,
    codex_home_override: str | Path | None = None,
) -> str | None:
    """Resolve ``OMX_DEFAULT_STANDARD_MODEL`` from env or config.

    Mirrors TS ``getEnvConfiguredStandardDefaultModel``.
    """
    source_env = os.environ if env is None else env
    return _normalize_configured_value(
        source_env.get(OMX_DEFAULT_STANDARD_MODEL_ENV)
    ) or _read_config_env_value(OMX_DEFAULT_STANDARD_MODEL_ENV, codex_home_override)


def get_env_configured_spark_default_model(
    env: dict[str, str] | None = None,
    codex_home_override: str | Path | None = None,
) -> str | None:
    """Resolve the spark default from env/config, honouring legacy alias.

    Mirrors TS ``getEnvConfiguredSparkDefaultModel``. Precedence:
    process env ``OMX_DEFAULT_SPARK_MODEL`` → process env
    ``OMX_SPARK_MODEL`` (legacy) → config ``env``
    ``OMX_DEFAULT_SPARK_MODEL`` → config ``env`` ``OMX_SPARK_MODEL``.
    """
    source_env = os.environ if env is None else env
    return (
        _normalize_configured_value(source_env.get(OMX_DEFAULT_SPARK_MODEL_ENV))
        or _normalize_configured_value(source_env.get(OMX_SPARK_MODEL_ENV))
        or _read_config_env_value(OMX_DEFAULT_SPARK_MODEL_ENV, codex_home_override)
        or _read_config_env_value(OMX_SPARK_MODEL_ENV, codex_home_override)
    )


def _get_codex_config_root_model(
    codex_home_override: str | Path | None,
) -> str | None:
    """Read the root ``model`` key from ``$CODEX_HOME/config.toml``."""
    config = read_codex_config_file(codex_home_override)
    if not config:
        return None
    return _normalize_configured_value(config.get("model"))


# ---------------------------------------------------------------------------
# Public model resolvers
# ---------------------------------------------------------------------------


def get_main_default_model(
    codex_home_override: str | Path | None = None,
) -> str:
    """Return the envvar-backed main/default model.

    Resolution: ``OMX_DEFAULT_FRONTIER_MODEL`` → ``config.toml`` root
    ``model`` → :data:`DEFAULT_FRONTIER_MODEL`. Mirrors TS
    ``getMainDefaultModel``.
    """
    return (
        get_env_configured_main_default_model(None, codex_home_override)
        or _get_codex_config_root_model(codex_home_override)
        or DEFAULT_FRONTIER_MODEL
    )


def get_standard_default_model(
    codex_home_override: str | Path | None = None,
) -> str:
    """Return the envvar-backed standard/default subagent model.

    Standard-role subagents inherit the configured main/default model
    unless an explicit standard-lane override is configured. Mirrors TS
    ``getStandardDefaultModel``.

    Resolution: ``OMX_DEFAULT_STANDARD_MODEL`` →
    ``OMX_DEFAULT_FRONTIER_MODEL`` → ``config.toml`` root ``model`` →
    :data:`DEFAULT_FRONTIER_MODEL`.
    """
    return get_env_configured_standard_default_model(
        None, codex_home_override
    ) or get_main_default_model(codex_home_override)


def get_spark_default_model(
    codex_home_override: str | Path | None = None,
) -> str:
    """Return the envvar-backed spark/low-complexity default model.

    Resolution: ``OMX_DEFAULT_SPARK_MODEL`` → ``OMX_SPARK_MODEL`` (legacy)
    → ``models.team_low_complexity`` override (or its aliases) →
    :data:`DEFAULT_SPARK_MODEL`. Mirrors TS ``getSparkDefaultModel``.
    """
    return (
        get_env_configured_spark_default_model(None, codex_home_override)
        or _read_team_low_complexity_override(codex_home_override)
        or DEFAULT_SPARK_MODEL
    )


def get_model_for_mode(
    mode: str,
    codex_home_override: str | Path | None = None,
) -> str:
    """Return the configured model for a specific mode.

    Resolution: ``models.<mode>`` → ``models.default`` →
    :func:`get_main_default_model`. Mirrors TS ``getModelForMode``.
    """
    models = _read_models_block(codex_home_override)
    if models is not None:
        mode_value = _normalize_configured_value(models.get(mode))
        if mode_value:
            return mode_value
        default_value = _normalize_configured_value(models.get("default"))
        if default_value:
            return default_value
    return get_main_default_model(codex_home_override)


def get_team_low_complexity_model(
    codex_home_override: str | Path | None = None,
) -> str:
    """Return the low-complexity team-worker model.

    Resolution: explicit ``models.team_low_complexity`` override (and
    aliases) → :func:`get_spark_default_model`. Mirrors TS
    ``getTeamLowComplexityModel``.
    """
    return _read_team_low_complexity_override(
        codex_home_override
    ) or get_spark_default_model(codex_home_override)


# ---------------------------------------------------------------------------
# Legacy back-compat helpers (predate the full port).
# ---------------------------------------------------------------------------
# These are not TS exports; they preserve the original 26-LOC stub's
# alias-based resolver API so any internal caller continues to work.

DEFAULT_MODEL = "o4-mini"
REASONING_MODEL = "o3"
FAST_MODEL = "o4-mini"

MODEL_ALIASES: dict[str, str] = {
    "fast": FAST_MODEL,
    "reasoning": REASONING_MODEL,
    "default": DEFAULT_MODEL,
}


def resolve_model(label: str) -> str:
    """Resolve a model alias to a concrete model name.

    Args:
        label: Model alias (``"fast"``, ``"reasoning"``, ``"default"``)
            or literal name.

    Returns:
        Concrete model identifier (passes through if not a known alias).
    """
    return MODEL_ALIASES.get(label.lower().strip(), label)


__all__ = [
    # env var names
    "OMX_DEFAULT_FRONTIER_MODEL_ENV",
    "OMX_DEFAULT_STANDARD_MODEL_ENV",
    "OMX_DEFAULT_SPARK_MODEL_ENV",
    "OMX_SPARK_MODEL_ENV",
    # canonical defaults
    "DEFAULT_FRONTIER_MODEL",
    "DEFAULT_STANDARD_MODEL",
    "DEFAULT_SPARK_MODEL",
    "DEFAULT_SETUP_MODEL",
    "TEAM_LOW_COMPLEXITY_MODEL_KEYS",
    # type aliases
    "ModelsConfig",
    "OmxConfigEnv",
    # raw readers
    "read_omx_config_file",
    "read_codex_config_file",
    # public env-override readers
    "read_configured_env_overrides",
    "read_active_provider_env_overrides",
    # env-configured getters
    "get_env_configured_main_default_model",
    "get_env_configured_standard_default_model",
    "get_env_configured_spark_default_model",
    # public resolvers
    "get_main_default_model",
    "get_standard_default_model",
    "get_spark_default_model",
    "get_model_for_mode",
    "get_team_low_complexity_model",
    # legacy back-compat
    "DEFAULT_MODEL",
    "REASONING_MODEL",
    "FAST_MODEL",
    "MODEL_ALIASES",
    "resolve_model",
]
