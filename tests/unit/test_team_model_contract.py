"""Tests for omx.team.model_contract — full TS parity coverage.

Mirrors the behaviour of ``src/team/model-contract.ts`` plus the env /
config resolution chain in ``src/config/models.ts`` that the
contract delegates to.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from omx.team.model_contract import (
    CODEX_BYPASS_FLAG,
    CONFIG_FLAG,
    DEFAULT_FRONTIER_MODEL,
    DEFAULT_SPARK_MODEL,
    DEFAULT_STANDARD_MODEL,
    LOW_COMPLEXITY_AGENT_TYPES,
    MADMAX_FLAG,
    MODEL_FLAG,
    OMX_DEFAULT_FRONTIER_MODEL_ENV,
    OMX_DEFAULT_SPARK_MODEL_ENV,
    OMX_DEFAULT_STANDARD_MODEL_ENV,
    OMX_SPARK_MODEL_ENV,
    REASONING_KEY,
    TEAM_LOW_COMPLEXITY_DEFAULT_MODEL,
    ParsedTeamWorkerLaunchArgs,
    ResolveTeamWorkerLaunchArgsOptions,
    collect_inheritable_team_worker_args,
    is_low_complexity_agent_type,
    normalize_team_worker_launch_args,
    parse_team_worker_launch_args,
    resolve_agent_default_model,
    resolve_agent_reasoning_effort,
    resolve_team_low_complexity_default_model,
    resolve_team_worker_launch_args,
    resolve_worker_cli,
    resolve_worker_model,
    split_worker_launch_args,
)

# Env-var keys that affect model resolution. We scrub them in every
# test so the host environment cannot leak through.
_MODEL_ENV_KEYS = (
    OMX_DEFAULT_FRONTIER_MODEL_ENV,
    OMX_DEFAULT_STANDARD_MODEL_ENV,
    OMX_DEFAULT_SPARK_MODEL_ENV,
    OMX_SPARK_MODEL_ENV,
)


def _clean_env() -> dict[str, str]:
    """Return a copy of os.environ with model env vars removed."""
    return {k: v for k, v in os.environ.items() if k not in _MODEL_ENV_KEYS}


# ---------------------------------------------------------------------------
# split_worker_launch_args
# ---------------------------------------------------------------------------


class TestSplitWorkerLaunchArgs(unittest.TestCase):
    def test_none_returns_empty(self):
        self.assertEqual(split_worker_launch_args(None), [])

    def test_empty_string_returns_empty(self):
        self.assertEqual(split_worker_launch_args(""), [])

    def test_whitespace_only_returns_empty(self):
        self.assertEqual(split_worker_launch_args("   \t  \n "), [])

    def test_single_flag(self):
        self.assertEqual(split_worker_launch_args("--model"), ["--model"])

    def test_flag_with_value(self):
        self.assertEqual(
            split_worker_launch_args("--model gpt-5"), ["--model", "gpt-5"]
        )

    def test_multiple_whitespace_collapsed(self):
        self.assertEqual(
            split_worker_launch_args("  --model   gpt-5\t-c x=y "),
            ["--model", "gpt-5", "-c", "x=y"],
        )


# ---------------------------------------------------------------------------
# parse_team_worker_launch_args
# ---------------------------------------------------------------------------


class TestParseTeamWorkerLaunchArgs(unittest.TestCase):
    def test_empty(self):
        parsed = parse_team_worker_launch_args([])
        self.assertEqual(parsed, ParsedTeamWorkerLaunchArgs())

    def test_passthrough_only(self):
        parsed = parse_team_worker_launch_args(["--foo", "bar", "--baz"])
        self.assertEqual(parsed.passthrough, ["--foo", "bar", "--baz"])
        self.assertFalse(parsed.wants_bypass)
        self.assertIsNone(parsed.model_override)
        self.assertIsNone(parsed.reasoning_override)

    def test_madmax_sets_wants_bypass(self):
        parsed = parse_team_worker_launch_args([MADMAX_FLAG])
        self.assertTrue(parsed.wants_bypass)
        self.assertEqual(parsed.passthrough, [])

    def test_codex_bypass_sets_wants_bypass(self):
        parsed = parse_team_worker_launch_args([CODEX_BYPASS_FLAG])
        self.assertTrue(parsed.wants_bypass)
        self.assertEqual(parsed.passthrough, [])

    def test_model_with_value(self):
        parsed = parse_team_worker_launch_args(["--model", "gpt-5"])
        self.assertEqual(parsed.model_override, "gpt-5")
        self.assertEqual(parsed.passthrough, [])

    def test_model_value_trimmed(self):
        parsed = parse_team_worker_launch_args(["--model", "  gpt-5  "])
        self.assertEqual(parsed.model_override, "gpt-5")

    def test_orphan_model_dropped(self):
        # --model with no following value at all
        parsed = parse_team_worker_launch_args(["--model"])
        self.assertIsNone(parsed.model_override)
        self.assertEqual(parsed.passthrough, [])

    def test_orphan_model_followed_by_flag_dropped(self):
        parsed = parse_team_worker_launch_args(["--model", "--other"])
        self.assertIsNone(parsed.model_override)
        # --model is dropped; --other still passes through.
        self.assertEqual(parsed.passthrough, ["--other"])

    def test_inline_model_equals(self):
        parsed = parse_team_worker_launch_args(["--model=gpt-5"])
        self.assertEqual(parsed.model_override, "gpt-5")
        self.assertEqual(parsed.passthrough, [])

    def test_inline_model_equals_empty_dropped(self):
        parsed = parse_team_worker_launch_args(["--model="])
        self.assertIsNone(parsed.model_override)
        self.assertEqual(parsed.passthrough, [])

    def test_reasoning_override_via_dash_c(self):
        parsed = parse_team_worker_launch_args(["-c", 'model_reasoning_effort="high"'])
        self.assertEqual(parsed.reasoning_override, 'model_reasoning_effort="high"')
        self.assertEqual(parsed.passthrough, [])

    def test_dash_c_unrelated_passes_through(self):
        # -c with a non-reasoning config value is passthrough; the value
        # token gets consumed as a separate arg by the outer loop.
        parsed = parse_team_worker_launch_args(["-c", "other_key=value"])
        self.assertIsNone(parsed.reasoning_override)
        self.assertEqual(parsed.passthrough, ["-c", "other_key=value"])

    def test_dash_c_at_end_passes_through(self):
        parsed = parse_team_worker_launch_args(["-c"])
        self.assertIsNone(parsed.reasoning_override)
        self.assertEqual(parsed.passthrough, ["-c"])

    def test_mixed_args(self):
        parsed = parse_team_worker_launch_args(
            [
                "--foo",
                MADMAX_FLAG,
                "--model",
                "gpt-5",
                "-c",
                'model_reasoning_effort="medium"',
                "--bar",
            ]
        )
        self.assertTrue(parsed.wants_bypass)
        self.assertEqual(parsed.model_override, "gpt-5")
        self.assertEqual(parsed.reasoning_override, 'model_reasoning_effort="medium"')
        self.assertEqual(parsed.passthrough, ["--foo", "--bar"])


# ---------------------------------------------------------------------------
# collect_inheritable_team_worker_args
# ---------------------------------------------------------------------------


class TestCollectInheritableTeamWorkerArgs(unittest.TestCase):
    def test_nothing_inheritable(self):
        self.assertEqual(collect_inheritable_team_worker_args(["--foo", "bar"]), [])

    def test_inherits_in_canonical_order(self):
        out = collect_inheritable_team_worker_args(
            [
                "--model",
                "gpt-5",
                MADMAX_FLAG,
                "-c",
                'model_reasoning_effort="high"',
                "--noise",
            ]
        )
        # Order is fixed: bypass, reasoning, model.
        self.assertEqual(
            out,
            [
                CODEX_BYPASS_FLAG,
                CONFIG_FLAG,
                'model_reasoning_effort="high"',
                MODEL_FLAG,
                "gpt-5",
            ],
        )

    def test_drops_passthrough(self):
        out = collect_inheritable_team_worker_args(["--noise", "--other", "value"])
        self.assertEqual(out, [])


# ---------------------------------------------------------------------------
# normalize_team_worker_launch_args
# ---------------------------------------------------------------------------


class TestNormalizeTeamWorkerLaunchArgs(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(normalize_team_worker_launch_args([]), [])

    def test_passthrough_preserved(self):
        self.assertEqual(
            normalize_team_worker_launch_args(["--foo", "bar"]),
            ["--foo", "bar"],
        )

    def test_canonical_model_dedup_keeps_last_via_preferred(self):
        # Two --model flags in input; preferred_model wins.
        out = normalize_team_worker_launch_args(
            ["--model", "first", "--model", "second"],
            preferred_model="winner",
        )
        # Only ONE --model entry, with the preferred value.
        self.assertEqual(out.count(MODEL_FLAG), 1)
        idx = out.index(MODEL_FLAG)
        self.assertEqual(out[idx + 1], "winner")

    def test_canonical_model_dedup_without_preferred(self):
        # When no preferred_model is given, the last parsed override wins.
        out = normalize_team_worker_launch_args(
            ["--model", "first", "--model", "second"]
        )
        self.assertEqual(out.count(MODEL_FLAG), 1)
        idx = out.index(MODEL_FLAG)
        self.assertEqual(out[idx + 1], "second")

    def test_preferred_reasoning_used_when_no_explicit_override(self):
        out = normalize_team_worker_launch_args(["--foo"], preferred_reasoning="high")
        self.assertIn(CONFIG_FLAG, out)
        idx = out.index(CONFIG_FLAG)
        self.assertEqual(out[idx + 1], f'{REASONING_KEY}="high"')

    def test_explicit_reasoning_override_wins_over_preferred(self):
        out = normalize_team_worker_launch_args(
            ["-c", f'{REASONING_KEY}="low"'], preferred_reasoning="high"
        )
        idx = out.index(CONFIG_FLAG)
        # Original explicit override is preserved verbatim.
        self.assertEqual(out[idx + 1], f'{REASONING_KEY}="low"')

    def test_invalid_preferred_reasoning_dropped(self):
        out = normalize_team_worker_launch_args(["--foo"], preferred_reasoning="bogus")
        self.assertNotIn(CONFIG_FLAG, out)

    def test_bypass_emitted_once(self):
        out = normalize_team_worker_launch_args([MADMAX_FLAG, MADMAX_FLAG])
        self.assertEqual(out.count(CODEX_BYPASS_FLAG), 1)

    def test_emitted_order(self):
        # Order: passthrough, bypass, reasoning, model.
        out = normalize_team_worker_launch_args(
            [
                "--foo",
                MADMAX_FLAG,
                "--model",
                "gpt-5",
                "-c",
                f'{REASONING_KEY}="medium"',
            ],
            preferred_model="override",
        )
        self.assertEqual(
            out,
            [
                "--foo",
                CODEX_BYPASS_FLAG,
                CONFIG_FLAG,
                f'{REASONING_KEY}="medium"',
                MODEL_FLAG,
                "override",
            ],
        )


# ---------------------------------------------------------------------------
# resolve_team_worker_launch_args (env + inherited + fallback)
# ---------------------------------------------------------------------------


class TestResolveTeamWorkerLaunchArgs(unittest.TestCase):
    def test_all_empty(self):
        out = resolve_team_worker_launch_args(ResolveTeamWorkerLaunchArgsOptions())
        self.assertEqual(out, [])

    def test_env_model_wins_over_inherited_and_fallback(self):
        out = resolve_team_worker_launch_args(
            ResolveTeamWorkerLaunchArgsOptions(
                existing_raw="--model env-model",
                inherited_args=["--model", "inherited-model"],
                fallback_model="fallback-model",
            )
        )
        idx = out.index(MODEL_FLAG)
        self.assertEqual(out[idx + 1], "env-model")

    def test_inherited_model_wins_over_fallback(self):
        out = resolve_team_worker_launch_args(
            ResolveTeamWorkerLaunchArgsOptions(
                existing_raw="",  # no env model
                inherited_args=["--model", "inherited-model"],
                fallback_model="fallback-model",
            )
        )
        idx = out.index(MODEL_FLAG)
        self.assertEqual(out[idx + 1], "inherited-model")

    def test_fallback_used_when_nothing_else(self):
        out = resolve_team_worker_launch_args(
            ResolveTeamWorkerLaunchArgsOptions(fallback_model="fallback-model")
        )
        idx = out.index(MODEL_FLAG)
        self.assertEqual(out[idx + 1], "fallback-model")

    def test_no_model_anywhere_means_no_flag(self):
        out = resolve_team_worker_launch_args(
            ResolveTeamWorkerLaunchArgsOptions(existing_raw="--foo bar")
        )
        self.assertNotIn(MODEL_FLAG, out)

    def test_preferred_reasoning_applied(self):
        out = resolve_team_worker_launch_args(
            ResolveTeamWorkerLaunchArgsOptions(
                fallback_model="m", preferred_reasoning="high"
            )
        )
        idx = out.index(CONFIG_FLAG)
        self.assertEqual(out[idx + 1], f'{REASONING_KEY}="high"')

    def test_env_bypass_inherited(self):
        out = resolve_team_worker_launch_args(
            ResolveTeamWorkerLaunchArgsOptions(existing_raw=MADMAX_FLAG)
        )
        self.assertIn(CODEX_BYPASS_FLAG, out)


# ---------------------------------------------------------------------------
# is_low_complexity_agent_type
# ---------------------------------------------------------------------------


class TestIsLowComplexityAgentType(unittest.TestCase):
    def test_none(self):
        self.assertFalse(is_low_complexity_agent_type(None))

    def test_empty(self):
        self.assertFalse(is_low_complexity_agent_type(""))
        self.assertFalse(is_low_complexity_agent_type("   "))

    def test_explicit_set(self):
        for name in LOW_COMPLEXITY_AGENT_TYPES:
            with self.subTest(name=name):
                self.assertTrue(is_low_complexity_agent_type(name))

    def test_case_insensitive(self):
        self.assertTrue(is_low_complexity_agent_type("EXPLORE"))
        self.assertTrue(is_low_complexity_agent_type("Style-Reviewer"))

    def test_dash_low_suffix(self):
        self.assertTrue(is_low_complexity_agent_type("executor-low"))
        self.assertTrue(is_low_complexity_agent_type("architect-low"))

    def test_unrelated_returns_false(self):
        self.assertFalse(is_low_complexity_agent_type("architect"))
        self.assertFalse(is_low_complexity_agent_type("executor"))


# ---------------------------------------------------------------------------
# resolve_agent_reasoning_effort
# ---------------------------------------------------------------------------


class TestResolveAgentReasoningEffort(unittest.TestCase):
    def test_none_input(self):
        self.assertIsNone(resolve_agent_reasoning_effort(None))

    def test_empty_input(self):
        self.assertIsNone(resolve_agent_reasoning_effort(""))
        self.assertIsNone(resolve_agent_reasoning_effort("   "))

    def test_unknown_agent(self):
        self.assertIsNone(resolve_agent_reasoning_effort("does-not-exist-agent"))

    def test_known_agent_explore_low(self):
        self.assertEqual(resolve_agent_reasoning_effort("explore"), "low")

    def test_known_agent_executor_medium(self):
        self.assertEqual(resolve_agent_reasoning_effort("executor"), "medium")

    def test_known_agent_architect_high(self):
        self.assertEqual(resolve_agent_reasoning_effort("architect"), "high")


# ---------------------------------------------------------------------------
# Model env-var precedence (frontier / standard / spark chains)
# ---------------------------------------------------------------------------


class TestModelEnvVarPrecedence(unittest.TestCase):
    """Cover OMX_DEFAULT_* env vars + legacy OMX_SPARK_MODEL alias."""

    def test_frontier_env_wins(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_FRONTIER_MODEL_ENV: "env-frontier"},
                clear=True,
            ),
        ):
            self.assertEqual(
                resolve_agent_default_model("executor", codex_home_override=tmp),
                "env-frontier",
            )

    def test_frontier_falls_back_to_default(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(os.environ, _clean_env(), clear=True),
        ):
            self.assertEqual(
                resolve_agent_default_model("executor", codex_home_override=tmp),
                DEFAULT_FRONTIER_MODEL,
            )

    def test_standard_env_used_for_standard_agent(self):
        # debugger has model_class='standard'.
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_STANDARD_MODEL_ENV: "env-standard"},
                clear=True,
            ),
        ):
            self.assertEqual(
                resolve_agent_default_model("debugger", codex_home_override=tmp),
                "env-standard",
            )

    def test_standard_falls_back_to_frontier(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_FRONTIER_MODEL_ENV: "env-frontier"},
                clear=True,
            ),
        ):
            # No standard override → inherits frontier.
            self.assertEqual(
                resolve_agent_default_model("debugger", codex_home_override=tmp),
                "env-frontier",
            )

    def test_spark_env_wins(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_SPARK_MODEL_ENV: "env-spark"},
                clear=True,
            ),
        ):
            self.assertEqual(
                resolve_team_low_complexity_default_model(codex_home_override=tmp),
                "env-spark",
            )

    def test_legacy_spark_alias_honored(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_SPARK_MODEL_ENV: "legacy-spark"},
                clear=True,
            ),
        ):
            self.assertEqual(
                resolve_team_low_complexity_default_model(codex_home_override=tmp),
                "legacy-spark",
            )

    def test_default_spark_alias_wins_over_legacy(self):
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
                resolve_team_low_complexity_default_model(codex_home_override=tmp),
                "modern-spark",
            )

    def test_spark_falls_back_to_default(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(os.environ, _clean_env(), clear=True),
        ):
            self.assertEqual(
                resolve_team_low_complexity_default_model(codex_home_override=tmp),
                DEFAULT_SPARK_MODEL,
            )


# ---------------------------------------------------------------------------
# resolve_agent_default_model — agent type → model-class routing
# ---------------------------------------------------------------------------


class TestResolveAgentDefaultModel(unittest.TestCase):
    def test_none_input(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(
                resolve_agent_default_model(None, codex_home_override=tmp)
            )

    def test_empty_input(self):
        with TemporaryDirectory() as tmp:
            self.assertIsNone(resolve_agent_default_model("", codex_home_override=tmp))
            self.assertIsNone(
                resolve_agent_default_model("   ", codex_home_override=tmp)
            )

    def test_unknown_agent_returns_none(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(os.environ, _clean_env(), clear=True),
        ):
            self.assertIsNone(
                resolve_agent_default_model(
                    "totally-unknown-agent", codex_home_override=tmp
                )
            )

    def test_executor_is_frontier_special_case(self):
        # executor's model_class is 'standard' but the TS contract forces
        # it to the frontier default.
        with (
            TemporaryDirectory() as tmp,
            patch.dict(os.environ, _clean_env(), clear=True),
        ):
            self.assertEqual(
                resolve_agent_default_model("executor", codex_home_override=tmp),
                DEFAULT_FRONTIER_MODEL,
            )

    def test_dash_low_suffix_routes_to_spark(self):
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_SPARK_MODEL_ENV: "spark-x"},
                clear=True,
            ),
        ):
            self.assertEqual(
                resolve_agent_default_model("architect-low", codex_home_override=tmp),
                "spark-x",
            )

    def test_fast_model_class_routes_to_spark(self):
        # 'explore' has model_class='fast'.
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_SPARK_MODEL_ENV: "spark-x"},
                clear=True,
            ),
        ):
            self.assertEqual(
                resolve_agent_default_model("explore", codex_home_override=tmp),
                "spark-x",
            )

    def test_frontier_model_class_routes_to_frontier(self):
        # 'architect' has model_class='frontier'.
        with (
            TemporaryDirectory() as tmp,
            patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_FRONTIER_MODEL_ENV: "frontier-x"},
                clear=True,
            ),
        ):
            self.assertEqual(
                resolve_agent_default_model("architect", codex_home_override=tmp),
                "frontier-x",
            )


# ---------------------------------------------------------------------------
# Config file (.omx-config.json + config.toml) integration
# ---------------------------------------------------------------------------


class TestConfigFileIntegration(unittest.TestCase):
    def test_omx_config_env_block_provides_frontier(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / ".omx-config.json").write_text(
                json.dumps({"env": {OMX_DEFAULT_FRONTIER_MODEL_ENV: "file-frontier"}})
            )
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(
                    resolve_agent_default_model("executor", codex_home_override=tmp),
                    "file-frontier",
                )

    def test_env_overrides_omx_config_file(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / ".omx-config.json").write_text(
                json.dumps({"env": {OMX_DEFAULT_FRONTIER_MODEL_ENV: "file-frontier"}})
            )
            with patch.dict(
                os.environ,
                {**_clean_env(), OMX_DEFAULT_FRONTIER_MODEL_ENV: "env-wins"},
                clear=True,
            ):
                self.assertEqual(
                    resolve_agent_default_model("executor", codex_home_override=tmp),
                    "env-wins",
                )

    def test_codex_config_toml_root_model_used_for_frontier_fallback(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "config.toml").write_text('model = "toml-frontier"\n')
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(
                    resolve_agent_default_model("executor", codex_home_override=tmp),
                    "toml-frontier",
                )

    def test_models_block_team_low_complexity_override(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / ".omx-config.json").write_text(
                json.dumps({"models": {"team_low_complexity": "file-spark"}})
            )
            with patch.dict(os.environ, _clean_env(), clear=True):
                self.assertEqual(
                    resolve_team_low_complexity_default_model(codex_home_override=tmp),
                    "file-spark",
                )

    def test_malformed_omx_config_file_is_ignored(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / ".omx-config.json").write_text("not valid json {{{")
            with patch.dict(os.environ, _clean_env(), clear=True):
                # Falls all the way through to the canonical default.
                self.assertEqual(
                    resolve_team_low_complexity_default_model(codex_home_override=tmp),
                    DEFAULT_SPARK_MODEL,
                )

    def test_malformed_codex_config_toml_is_ignored(self):
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "config.toml").write_text("model = unterminated [[[")
            with patch.dict(os.environ, _clean_env(), clear=True):
                # No env, no valid TOML → canonical frontier default.
                self.assertEqual(
                    resolve_agent_default_model("executor", codex_home_override=tmp),
                    DEFAULT_FRONTIER_MODEL,
                )


# ---------------------------------------------------------------------------
# Canonical constants
# ---------------------------------------------------------------------------


class TestCanonicalConstants(unittest.TestCase):
    def test_team_low_complexity_default_aliases_spark(self):
        self.assertEqual(TEAM_LOW_COMPLEXITY_DEFAULT_MODEL, DEFAULT_SPARK_MODEL)

    def test_default_models_are_strings(self):
        self.assertIsInstance(DEFAULT_FRONTIER_MODEL, str)
        self.assertIsInstance(DEFAULT_STANDARD_MODEL, str)
        self.assertIsInstance(DEFAULT_SPARK_MODEL, str)
        for v in (
            DEFAULT_FRONTIER_MODEL,
            DEFAULT_STANDARD_MODEL,
            DEFAULT_SPARK_MODEL,
        ):
            self.assertTrue(v)


# ---------------------------------------------------------------------------
# Legacy back-compat helpers preserved
# ---------------------------------------------------------------------------


class TestLegacyBackCompat(unittest.TestCase):
    def test_resolve_worker_cli_default(self):
        with patch.dict(os.environ, {"OMX_TEAM_WORKER_CLI": ""}, clear=False):
            self.assertEqual(resolve_worker_cli(), "codex")

    def test_resolve_worker_cli_explicit(self):
        self.assertEqual(resolve_worker_cli("Claude"), "claude")

    def test_resolve_worker_cli_env(self):
        with patch.dict(os.environ, {"OMX_TEAM_WORKER_CLI": "Claude"}):
            self.assertEqual(resolve_worker_cli(), "claude")

    def test_resolve_worker_model_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OMX_TEAM_WORKER_MODEL", None)
            self.assertEqual(resolve_worker_model(), "o4-mini")

    def test_resolve_worker_model_explicit(self):
        self.assertEqual(resolve_worker_model("gpt-5"), "gpt-5")


if __name__ == "__main__":
    unittest.main()
