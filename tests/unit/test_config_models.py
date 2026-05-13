"""Tests for omx.config.models — full TS parity coverage.

Mirrors the behaviour of ``src/config/models.ts``: env-var precedence,
``.omx-config.json`` env/models blocks, ``$CODEX_HOME/config.toml`` root
model fallback, mode-specific lookups, active-provider env passthrough,
and the legacy ``OMX_SPARK_MODEL`` alias.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from omx.config.models import (
    DEFAULT_FRONTIER_MODEL,
    DEFAULT_MODEL,
    DEFAULT_SETUP_MODEL,
    DEFAULT_SPARK_MODEL,
    DEFAULT_STANDARD_MODEL,
    FAST_MODEL,
    MODEL_ALIASES,
    OMX_DEFAULT_FRONTIER_MODEL_ENV,
    OMX_DEFAULT_SPARK_MODEL_ENV,
    OMX_DEFAULT_STANDARD_MODEL_ENV,
    OMX_SPARK_MODEL_ENV,
    REASONING_MODEL,
    TEAM_LOW_COMPLEXITY_MODEL_KEYS,
    get_env_configured_main_default_model,
    get_env_configured_spark_default_model,
    get_env_configured_standard_default_model,
    get_main_default_model,
    get_model_for_mode,
    get_spark_default_model,
    get_standard_default_model,
    get_team_low_complexity_model,
    read_active_provider_env_overrides,
    read_codex_config_file,
    read_configured_env_overrides,
    read_omx_config_file,
    resolve_model,
)

# Env-var keys that affect model resolution. We scrub them in every
# integration test so the host environment cannot leak through.
_MODEL_ENV_KEYS = (
    OMX_DEFAULT_FRONTIER_MODEL_ENV,
    OMX_DEFAULT_STANDARD_MODEL_ENV,
    OMX_DEFAULT_SPARK_MODEL_ENV,
    OMX_SPARK_MODEL_ENV,
)


def _clean_env() -> dict[str, str]:
    """Return a copy of os.environ with model env vars removed."""
    return {k: v for k, v in os.environ.items() if k not in _MODEL_ENV_KEYS}


def _write_omx_config(tmp: str, payload: dict) -> None:
    (Path(tmp) / ".omx-config.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_codex_toml(tmp: str, content: str) -> None:
    (Path(tmp) / "config.toml").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants(unittest.TestCase):
    def test_default_frontier_model(self):
        self.assertEqual(DEFAULT_FRONTIER_MODEL, "gpt-5.5")

    def test_default_standard_model(self):
        self.assertEqual(DEFAULT_STANDARD_MODEL, "gpt-5.4-mini")

    def test_default_spark_model(self):
        self.assertEqual(DEFAULT_SPARK_MODEL, "gpt-5.3-codex-spark")

    def test_default_setup_model_aliases_frontier(self):
        # TS keeps DEFAULT_SETUP_MODEL = DEFAULT_FRONTIER_MODEL local to
        # setup/generator; the Python module exposes it as a public alias.
        self.assertEqual(DEFAULT_SETUP_MODEL, DEFAULT_FRONTIER_MODEL)

    def test_env_var_names(self):
        self.assertEqual(OMX_DEFAULT_FRONTIER_MODEL_ENV, "OMX_DEFAULT_FRONTIER_MODEL")
        self.assertEqual(OMX_DEFAULT_STANDARD_MODEL_ENV, "OMX_DEFAULT_STANDARD_MODEL")
        self.assertEqual(OMX_DEFAULT_SPARK_MODEL_ENV, "OMX_DEFAULT_SPARK_MODEL")
        self.assertEqual(OMX_SPARK_MODEL_ENV, "OMX_SPARK_MODEL")

    def test_team_low_complexity_model_keys(self):
        self.assertEqual(
            TEAM_LOW_COMPLEXITY_MODEL_KEYS,
            ("team_low_complexity", "team-low-complexity", "teamLowComplexity"),
        )


# ---------------------------------------------------------------------------
# Legacy back-compat surface
# ---------------------------------------------------------------------------


class TestLegacyAliasResolver(unittest.TestCase):
    def test_resolve_model_known_aliases(self):
        self.assertEqual(resolve_model("fast"), FAST_MODEL)
        self.assertEqual(resolve_model("reasoning"), REASONING_MODEL)
        self.assertEqual(resolve_model("default"), DEFAULT_MODEL)

    def test_resolve_model_passthrough(self):
        self.assertEqual(resolve_model("gpt-4o"), "gpt-4o")

    def test_resolve_model_case_and_whitespace(self):
        self.assertEqual(resolve_model("  FAST  "), FAST_MODEL)

    def test_model_aliases_table(self):
        self.assertEqual(
            MODEL_ALIASES,
            {
                "fast": FAST_MODEL,
                "reasoning": REASONING_MODEL,
                "default": DEFAULT_MODEL,
            },
        )


# ---------------------------------------------------------------------------
# read_omx_config_file / read_codex_config_file
# ---------------------------------------------------------------------------


class TestReadOmxConfigFile(unittest.TestCase):
    def test_missing_returns_none(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(read_omx_config_file(tmp))

    def test_valid_json_object(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"env": {"X": "y"}})
            cfg = read_omx_config_file(tmp)
            self.assertEqual(cfg, {"env": {"X": "y"}})

    def test_malformed_json_returns_none(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / ".omx-config.json").write_text("not valid {")
            self.assertIsNone(read_omx_config_file(tmp))

    def test_top_level_array_returns_none(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / ".omx-config.json").write_text("[1, 2, 3]")
            self.assertIsNone(read_omx_config_file(tmp))


class TestReadCodexConfigFile(unittest.TestCase):
    def test_missing_returns_none(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(read_codex_config_file(tmp))

    def test_valid_toml(self):
        with TemporaryDirectory() as tmp:
            _write_codex_toml(tmp, 'model = "abc"\n')
            cfg = read_codex_config_file(tmp)
            self.assertIsNotNone(cfg)
            self.assertEqual(cfg["model"], "abc")

    def test_malformed_toml_returns_none(self):
        with TemporaryDirectory() as tmp:
            _write_codex_toml(tmp, "this is = not = valid = toml [")
            self.assertIsNone(read_codex_config_file(tmp))


# ---------------------------------------------------------------------------
# read_configured_env_overrides
# ---------------------------------------------------------------------------


class TestReadConfiguredEnvOverrides(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(read_configured_env_overrides(tmp), {})

    def test_no_env_block_returns_empty(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"models": {"default": "m"}})
            self.assertEqual(read_configured_env_overrides(tmp), {})

    def test_env_block_returned(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(
                tmp,
                {
                    "env": {
                        "OMX_DEFAULT_FRONTIER_MODEL": "frontier-from-file",
                        "OMX_DEFAULT_STANDARD_MODEL": "standard-from-file",
                    }
                },
            )
            out = read_configured_env_overrides(tmp)
            self.assertEqual(
                out,
                {
                    "OMX_DEFAULT_FRONTIER_MODEL": "frontier-from-file",
                    "OMX_DEFAULT_STANDARD_MODEL": "standard-from-file",
                },
            )

    def test_empty_string_values_dropped(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(
                tmp,
                {
                    "env": {
                        "OMX_DEFAULT_FRONTIER_MODEL": "  ",
                        "OMX_DEFAULT_STANDARD_MODEL": "kept",
                        "OMX_OTHER": "",
                    }
                },
            )
            out = read_configured_env_overrides(tmp)
            self.assertEqual(out, {"OMX_DEFAULT_STANDARD_MODEL": "kept"})

    def test_non_string_values_dropped(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(
                tmp,
                {"env": {"OMX_DEFAULT_FRONTIER_MODEL": 123, "X": True, "Y": "kept"}},
            )
            self.assertEqual(read_configured_env_overrides(tmp), {"Y": "kept"})


# ---------------------------------------------------------------------------
# read_active_provider_env_overrides
# ---------------------------------------------------------------------------


class TestReadActiveProviderEnvOverrides(unittest.TestCase):
    def test_no_codex_config_returns_empty(self):
        with TemporaryDirectory() as tmp:
            self.assertEqual(
                read_active_provider_env_overrides(env={}, codex_home_override=tmp),
                {},
            )

    def test_no_active_provider_returns_empty(self):
        with TemporaryDirectory() as tmp:
            _write_codex_toml(tmp, 'model = "abc"\n')
            self.assertEqual(
                read_active_provider_env_overrides(env={}, codex_home_override=tmp),
                {},
            )

    def test_active_provider_with_env_key_present(self):
        with TemporaryDirectory() as tmp:
            _write_codex_toml(
                tmp,
                'model_provider = "openai"\n'
                "[model_providers.openai]\n"
                'env_key = "OPENAI_API_KEY"\n',
            )
            out = read_active_provider_env_overrides(
                env={"OPENAI_API_KEY": "sk-test"},
                codex_home_override=tmp,
            )
            self.assertEqual(out, {"OPENAI_API_KEY": "sk-test"})

    def test_active_provider_with_env_key_missing(self):
        with TemporaryDirectory() as tmp:
            _write_codex_toml(
                tmp,
                'model_provider = "openai"\n'
                "[model_providers.openai]\n"
                'env_key = "OPENAI_API_KEY"\n',
            )
            self.assertEqual(
                read_active_provider_env_overrides(env={}, codex_home_override=tmp),
                {},
            )

    def test_active_provider_missing_provider_table(self):
        with TemporaryDirectory() as tmp:
            _write_codex_toml(tmp, 'model_provider = "openai"\n')
            self.assertEqual(
                read_active_provider_env_overrides(
                    env={"OPENAI_API_KEY": "sk-x"},
                    codex_home_override=tmp,
                ),
                {},
            )

    def test_active_provider_missing_env_key_field(self):
        with TemporaryDirectory() as tmp:
            _write_codex_toml(
                tmp,
                'model_provider = "openai"\n'
                "[model_providers.openai]\n"
                'other_key = "value"\n',
            )
            self.assertEqual(
                read_active_provider_env_overrides(
                    env={"OPENAI_API_KEY": "sk-x"},
                    codex_home_override=tmp,
                ),
                {},
            )

    def test_falls_back_to_os_environ(self):
        with TemporaryDirectory() as tmp:
            _write_codex_toml(
                tmp,
                'model_provider = "openai"\n'
                "[model_providers.openai]\n"
                'env_key = "OPENAI_API_KEY"\n',
            )
            with patch.dict(os.environ, {"OPENAI_API_KEY": "from-os-env"}, clear=False):
                out = read_active_provider_env_overrides(codex_home_override=tmp)
            self.assertEqual(out, {"OPENAI_API_KEY": "from-os-env"})


# ---------------------------------------------------------------------------
# Env-configured getters
# ---------------------------------------------------------------------------


class TestEnvConfiguredGetters(unittest.TestCase):
    def test_frontier_env_wins_over_config(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(
                tmp,
                {"env": {OMX_DEFAULT_FRONTIER_MODEL_ENV: "from-file"}},
            )
            with patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_FRONTIER_MODEL_ENV: "from-env"},
                clear=True,
            ):
                self.assertEqual(
                    get_env_configured_main_default_model(codex_home_override=tmp),
                    "from-env",
                )

    def test_frontier_config_used_when_env_missing(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(
                tmp,
                {"env": {OMX_DEFAULT_FRONTIER_MODEL_ENV: "from-file"}},
            )
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(
                    get_env_configured_main_default_model(codex_home_override=tmp),
                    "from-file",
                )

    def test_standard_env_chain(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_STANDARD_MODEL_ENV: "std"},
                clear=True,
            ),
        ):
            self.assertEqual(
                get_env_configured_standard_default_model(codex_home_override=tmp),
                "std",
            )

    def test_spark_env_legacy_alias_chain(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_SPARK_MODEL_ENV: "legacy-spark"},
                clear=True,
            ),
        ):
            self.assertEqual(
                get_env_configured_spark_default_model(codex_home_override=tmp),
                "legacy-spark",
            )

    def test_spark_env_modern_beats_legacy(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {
                    **_clean_env(),
                    OMX_DEFAULT_SPARK_MODEL_ENV: "modern-spark",
                    OMX_SPARK_MODEL_ENV: "legacy-spark",
                },
                clear=True,
            ),
        ):
            self.assertEqual(
                get_env_configured_spark_default_model(codex_home_override=tmp),
                "modern-spark",
            )

    def test_explicit_env_param_isolates_from_os_environ(self):
        # When env is explicitly passed in, os.environ must not leak.
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_FRONTIER_MODEL_ENV: "from-os"},
                clear=True,
            ),
        ):
            self.assertIsNone(
                get_env_configured_main_default_model(env={}, codex_home_override=tmp)
            )


# ---------------------------------------------------------------------------
# get_main_default_model / get_standard_default_model / get_spark_default_model
# ---------------------------------------------------------------------------


class TestGetMainDefaultModel(unittest.TestCase):
    def test_env_wins(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_FRONTIER_MODEL_ENV: "env-front"},
                clear=True,
            ),
        ):
            self.assertEqual(get_main_default_model(tmp), "env-front")

    def test_toml_root_model_fallback(self):
        with TemporaryDirectory() as tmp:
            _write_codex_toml(tmp, 'model = "toml-front"\n')
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(get_main_default_model(tmp), "toml-front")

    def test_canonical_default_when_nothing_set(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(os.environ, _clean_env(), clear=True),
        ):
            self.assertEqual(get_main_default_model(tmp), DEFAULT_FRONTIER_MODEL)


class TestGetStandardDefaultModel(unittest.TestCase):
    def test_standard_env_wins(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {
                    **_clean_env(),
                    OMX_DEFAULT_STANDARD_MODEL_ENV: "env-std",
                    OMX_DEFAULT_FRONTIER_MODEL_ENV: "env-front",
                },
                clear=True,
            ),
        ):
            self.assertEqual(get_standard_default_model(tmp), "env-std")

    def test_falls_back_to_frontier_chain(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_FRONTIER_MODEL_ENV: "env-front"},
                clear=True,
            ),
        ):
            self.assertEqual(get_standard_default_model(tmp), "env-front")

    def test_falls_back_to_canonical(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(os.environ, _clean_env(), clear=True),
        ):
            self.assertEqual(get_standard_default_model(tmp), DEFAULT_FRONTIER_MODEL)


class TestGetSparkDefaultModel(unittest.TestCase):
    def test_env_wins(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_SPARK_MODEL_ENV: "env-spark"},
                clear=True,
            ),
        ):
            self.assertEqual(get_spark_default_model(tmp), "env-spark")

    def test_legacy_alias_honored(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_SPARK_MODEL_ENV: "legacy"},
                clear=True,
            ),
        ):
            self.assertEqual(get_spark_default_model(tmp), "legacy")

    def test_models_block_team_low_complexity_used(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"models": {"team_low_complexity": "block-spark"}})
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(get_spark_default_model(tmp), "block-spark")

    def test_kebab_low_complexity_key_used(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"models": {"team-low-complexity": "kebab-spark"}})
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(get_spark_default_model(tmp), "kebab-spark")

    def test_camel_low_complexity_key_used(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"models": {"teamLowComplexity": "camel-spark"}})
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(get_spark_default_model(tmp), "camel-spark")

    def test_canonical_default_when_nothing_set(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(os.environ, _clean_env(), clear=True),
        ):
            self.assertEqual(get_spark_default_model(tmp), DEFAULT_SPARK_MODEL)

    def test_env_beats_models_block(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"models": {"team_low_complexity": "block"}})
            with patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_SPARK_MODEL_ENV: "env-wins"},
                clear=True,
            ):
                self.assertEqual(get_spark_default_model(tmp), "env-wins")


# ---------------------------------------------------------------------------
# get_team_low_complexity_model
# ---------------------------------------------------------------------------


class TestGetTeamLowComplexityModel(unittest.TestCase):
    def test_explicit_override_wins_over_env(self):
        # TS: explicit low-complexity key beats OMX_DEFAULT_SPARK_MODEL.
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"models": {"team_low_complexity": "explicit"}})
            with patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_SPARK_MODEL_ENV: "env"},
                clear=True,
            ):
                self.assertEqual(get_team_low_complexity_model(tmp), "explicit")

    def test_falls_through_to_spark_default(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_SPARK_MODEL_ENV: "env-spark"},
                clear=True,
            ),
        ):
            self.assertEqual(get_team_low_complexity_model(tmp), "env-spark")

    def test_falls_through_to_canonical(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(os.environ, _clean_env(), clear=True),
        ):
            self.assertEqual(get_team_low_complexity_model(tmp), DEFAULT_SPARK_MODEL)


# ---------------------------------------------------------------------------
# get_model_for_mode
# ---------------------------------------------------------------------------


class TestGetModelForMode(unittest.TestCase):
    def test_mode_specific_value(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(
                tmp,
                {"models": {"team": "team-model", "default": "default-model"}},
            )
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(get_model_for_mode("team", tmp), "team-model")

    def test_default_key_used_when_mode_missing(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"models": {"default": "default-model"}})
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(get_model_for_mode("other-mode", tmp), "default-model")

    def test_falls_through_to_main_default(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_FRONTIER_MODEL_ENV: "env-front"},
                clear=True,
            ),
        ):
            self.assertEqual(get_model_for_mode("anything", tmp), "env-front")

    def test_no_models_block_uses_canonical(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(os.environ, _clean_env(), clear=True),
        ):
            self.assertEqual(
                get_model_for_mode("anything", tmp), DEFAULT_FRONTIER_MODEL
            )

    def test_empty_string_mode_value_falls_through(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(
                tmp,
                {"models": {"team": "   ", "default": "default-model"}},
            )
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(get_model_for_mode("team", tmp), "default-model")


# ---------------------------------------------------------------------------
# Edge cases — malformed / missing pieces
# ---------------------------------------------------------------------------


class TestMalformedConfigHandling(unittest.TestCase):
    def test_malformed_omx_config_ignored(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / ".omx-config.json").write_text("not valid json")
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(get_main_default_model(tmp), DEFAULT_FRONTIER_MODEL)
                self.assertEqual(read_configured_env_overrides(tmp), {})

    def test_env_block_must_be_dict(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"env": "not a dict"})
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(read_configured_env_overrides(tmp), {})
                self.assertEqual(get_main_default_model(tmp), DEFAULT_FRONTIER_MODEL)

    def test_models_block_must_be_dict(self):
        with TemporaryDirectory() as tmp:
            _write_omx_config(tmp, {"models": ["not", "a", "dict"]})
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(
                    get_model_for_mode("team", tmp), DEFAULT_FRONTIER_MODEL
                )
                self.assertEqual(get_spark_default_model(tmp), DEFAULT_SPARK_MODEL)


if __name__ == "__main__":
    unittest.main()
