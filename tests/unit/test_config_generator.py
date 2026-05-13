"""Tests for ``omx.config.generator`` strip/merge/repair functions.

Mirrors the TS test suite in
``src/config/__tests__/generator-idempotent.test.ts``. Each public function
gets:

- A no-op-on-empty case.
- An idempotency check (running twice == running once).
- A preservation check for user-owned content.
"""

from __future__ import annotations

import re
import tempfile
import tomllib
import unittest
from pathlib import Path

from omx.config.generator import (
    MergeOptions,
    StripResult,
    build_merged_config,
    deep_merge_dicts,
    get_root_model_name,
    has_legacy_omx_team_run_table,
    merge_config,
    repair_config_if_needed,
    strip_existing_omx_blocks,
    strip_existing_shared_mcp_registry_block,
    strip_omx_env_settings,
    strip_omx_feature_flags,
    strip_omx_seeded_behavioral_defaults,
    strip_omx_top_level_keys,
    upsert_codex_hooks_feature_flag,
)
from omx.config.mcp_registry import UnifiedMcpRegistryServer


def _count(text: str, pattern: str, flags: int = re.MULTILINE) -> int:
    return len(re.findall(pattern, text, flags))


# ---------------------------------------------------------------------------
# has_legacy_omx_team_run_table / get_root_model_name
# ---------------------------------------------------------------------------


class TestPredicates(unittest.TestCase):
    def test_has_legacy_team_run_table_empty(self) -> None:
        self.assertFalse(has_legacy_omx_team_run_table(""))

    def test_has_legacy_team_run_table_bare(self) -> None:
        self.assertTrue(has_legacy_omx_team_run_table("[mcp_servers.omx_team_run]\n"))

    def test_has_legacy_team_run_table_quoted(self) -> None:
        self.assertTrue(has_legacy_omx_team_run_table('[mcp_servers."omx_team_run"]\n'))

    def test_has_legacy_team_run_table_other_sections(self) -> None:
        self.assertFalse(
            has_legacy_omx_team_run_table(
                "[mcp_servers.omx_state]\n[mcp_servers.user]\n"
            )
        )

    def test_get_root_model_name_present(self) -> None:
        self.assertEqual(get_root_model_name('model = "o3"\n'), "o3")

    def test_get_root_model_name_missing(self) -> None:
        self.assertIsNone(get_root_model_name(""))

    def test_get_root_model_name_inside_table_ignored(self) -> None:
        cfg = '[features]\nmodel = "o3"\n'
        self.assertIsNone(get_root_model_name(cfg))


# ---------------------------------------------------------------------------
# strip_omx_top_level_keys
# ---------------------------------------------------------------------------


class TestStripOmxTopLevelKeys(unittest.TestCase):
    def test_empty_is_noop(self) -> None:
        self.assertEqual(strip_omx_top_level_keys(""), "")

    def test_removes_managed_keys(self) -> None:
        cfg = "\n".join(
            [
                "# oh-my-codex top-level settings (must be before any [table])",
                'notify = ["node", "/x.js"]',
                'model_reasoning_effort = "medium"',
                'developer_instructions = "foo"',
                'model = "o3"',
                "",
                "[features]",
                "x = true",
                "",
            ]
        )
        out = strip_omx_top_level_keys(cfg)
        self.assertNotIn("notify =", out)
        self.assertNotIn("model_reasoning_effort", out)
        self.assertNotIn("developer_instructions", out)
        # Non-OMX root keys preserved
        self.assertIn('model = "o3"', out)
        # User section preserved
        self.assertIn("[features]", out)
        self.assertIn("x = true", out)
        # Header comment removed
        self.assertNotIn("oh-my-codex top-level settings", out)

    def test_idempotent(self) -> None:
        cfg = "\n".join(
            [
                'notify = ["node", "/x.js"]',
                'model = "o3"',
                "",
                "[features]",
                "",
            ]
        )
        once = strip_omx_top_level_keys(cfg)
        twice = strip_omx_top_level_keys(once)
        self.assertEqual(once, twice)

    def test_preserves_multiline_developer_instructions_inside_other_keys(
        self,
    ) -> None:
        # When developer_instructions is multiline, the entry-split treats it
        # as a single root entry and removes the whole thing.
        cfg = "\n".join(
            [
                'developer_instructions = """line1',
                "line2",
                'line3"""',
                'model = "o3"',
                "",
            ]
        )
        out = strip_omx_top_level_keys(cfg)
        self.assertNotIn("line2", out)
        self.assertNotIn("line3", out)
        self.assertIn('model = "o3"', out)


# ---------------------------------------------------------------------------
# strip_omx_seeded_behavioral_defaults
# ---------------------------------------------------------------------------


class TestStripOmxSeededBehavioralDefaults(unittest.TestCase):
    def test_empty_is_noop(self) -> None:
        self.assertEqual(strip_omx_seeded_behavioral_defaults(""), "")

    def test_removes_unchanged_seeded_block(self) -> None:
        cfg = "\n".join(
            [
                'model = "gpt-5.5"',
                "# oh-my-codex seeded behavioral defaults (uninstall removes unchanged defaults)",
                "model_context_window = 250000",
                "model_auto_compact_token_limit = 200000",
                "# End oh-my-codex seeded behavioral defaults",
                "",
                "[features]",
                "x = true",
            ]
        )
        out = strip_omx_seeded_behavioral_defaults(cfg)
        self.assertNotIn("model_context_window", out)
        self.assertNotIn("model_auto_compact_token_limit", out)
        self.assertNotIn("seeded behavioral defaults", out)
        # User content preserved
        self.assertIn('model = "gpt-5.5"', out)
        self.assertIn("[features]", out)

    def test_preserves_user_modified_block_contents(self) -> None:
        cfg = "\n".join(
            [
                'model = "gpt-5.5"',
                "# oh-my-codex seeded behavioral defaults (uninstall removes unchanged defaults)",
                "model_context_window = 640000",
                "model_auto_compact_token_limit = 200000",
                "# End oh-my-codex seeded behavioral defaults",
            ]
        )
        out = strip_omx_seeded_behavioral_defaults(cfg)
        # User changed the context window — the body must survive.
        self.assertIn("model_context_window = 640000", out)
        self.assertIn("model_auto_compact_token_limit = 200000", out)
        # Markers are still removed
        self.assertNotIn("seeded behavioral defaults", out)

    def test_idempotent(self) -> None:
        cfg = "\n".join(
            [
                "# oh-my-codex seeded behavioral defaults (uninstall removes unchanged defaults)",
                "model_context_window = 250000",
                "model_auto_compact_token_limit = 200000",
                "# End oh-my-codex seeded behavioral defaults",
            ]
        )
        once = strip_omx_seeded_behavioral_defaults(cfg)
        twice = strip_omx_seeded_behavioral_defaults(once)
        self.assertEqual(once, twice)

    def test_ignores_markers_after_first_table(self) -> None:
        # Markers below the first [table] header are NOT touched.
        cfg = "\n".join(
            [
                'model = "gpt-5.5"',
                "[features]",
                "# oh-my-codex seeded behavioral defaults (uninstall removes unchanged defaults)",
                "model_context_window = 250000",
                "model_auto_compact_token_limit = 200000",
                "# End oh-my-codex seeded behavioral defaults",
            ]
        )
        out = strip_omx_seeded_behavioral_defaults(cfg)
        self.assertEqual(out, cfg)


# ---------------------------------------------------------------------------
# strip_omx_feature_flags
# ---------------------------------------------------------------------------


class TestStripOmxFeatureFlags(unittest.TestCase):
    def test_empty_is_noop(self) -> None:
        self.assertEqual(strip_omx_feature_flags(""), "")

    def test_strips_managed_flags(self) -> None:
        cfg = "\n".join(
            [
                "[features]",
                "multi_agent = true",
                "child_agents_md = true",
                "codex_hooks = true",
                "user_feature = true",
                "collab = true",
                "",
            ]
        )
        out = strip_omx_feature_flags(cfg)
        self.assertNotIn("multi_agent", out)
        self.assertNotIn("child_agents_md", out)
        self.assertNotIn("codex_hooks", out)
        self.assertNotIn("collab", out)
        self.assertIn("user_feature = true", out)

    def test_drops_empty_section_header(self) -> None:
        cfg = "\n".join(
            [
                "[features]",
                "multi_agent = true",
                "child_agents_md = true",
                "codex_hooks = true",
                "",
            ]
        )
        out = strip_omx_feature_flags(cfg)
        self.assertNotIn("[features]", out)

    def test_idempotent(self) -> None:
        cfg = "\n".join(
            [
                "[features]",
                "multi_agent = true",
                "user_feature = true",
                "",
            ]
        )
        once = strip_omx_feature_flags(cfg)
        twice = strip_omx_feature_flags(once)
        self.assertEqual(once, twice)


# ---------------------------------------------------------------------------
# strip_omx_env_settings
# ---------------------------------------------------------------------------


class TestStripOmxEnvSettings(unittest.TestCase):
    def test_empty_is_noop(self) -> None:
        self.assertEqual(strip_omx_env_settings(""), "")

    def test_no_env_section_is_noop(self) -> None:
        cfg = "[features]\nx = true\n"
        self.assertEqual(strip_omx_env_settings(cfg), cfg)

    def test_removes_explore_routing_key(self) -> None:
        cfg = "\n".join(
            [
                "[env]",
                'USE_OMX_EXPLORE_CMD = "1"',
                'USER_KEY = "x"',
                "",
            ]
        )
        out = strip_omx_env_settings(cfg)
        self.assertNotIn("USE_OMX_EXPLORE_CMD", out)
        self.assertIn('USER_KEY = "x"', out)
        self.assertIn("[env]", out)

    def test_drops_empty_env_section(self) -> None:
        cfg = "\n".join(
            [
                "[env]",
                'USE_OMX_EXPLORE_CMD = "1"',
                "",
            ]
        )
        out = strip_omx_env_settings(cfg)
        self.assertNotIn("[env]", out)

    def test_idempotent(self) -> None:
        cfg = "\n".join(
            [
                "[env]",
                'USE_OMX_EXPLORE_CMD = "1"',
                'USER_KEY = "x"',
                "",
            ]
        )
        once = strip_omx_env_settings(cfg)
        twice = strip_omx_env_settings(once)
        self.assertEqual(once, twice)


# ---------------------------------------------------------------------------
# upsert_codex_hooks_feature_flag
# ---------------------------------------------------------------------------


class TestUpsertCodexHooksFeatureFlag(unittest.TestCase):
    def test_empty_input_adds_section(self) -> None:
        out = upsert_codex_hooks_feature_flag("")
        self.assertIn("[features]", out)
        self.assertIn("codex_hooks = true", out)

    def test_adds_to_existing_section_without_disturbing_siblings(self) -> None:
        cfg = "\n".join(
            [
                "[features]",
                "multi_agent = true",
                "user_feature = true",
                "",
            ]
        )
        out = upsert_codex_hooks_feature_flag(cfg)
        self.assertIn("multi_agent = true", out)
        self.assertIn("user_feature = true", out)
        self.assertIn("codex_hooks = true", out)
        # Section only added once.
        self.assertEqual(_count(out, r"^codex_hooks = true$"), 1)
        self.assertEqual(_count(out, r"^\[features\]$"), 1)

    def test_idempotent_when_already_present(self) -> None:
        cfg = "\n".join(
            [
                "[features]",
                "codex_hooks = true",
                "",
            ]
        )
        once = upsert_codex_hooks_feature_flag(cfg)
        twice = upsert_codex_hooks_feature_flag(once)
        self.assertEqual(once, twice)
        self.assertEqual(_count(twice, r"^codex_hooks = true$"), 1)

    def test_appends_section_when_missing(self) -> None:
        cfg = '[env]\nUSER_KEY = "x"\n'
        out = upsert_codex_hooks_feature_flag(cfg)
        self.assertIn("[env]", out)
        self.assertIn('USER_KEY = "x"', out)
        self.assertIn("[features]", out)
        self.assertIn("codex_hooks = true", out)


# ---------------------------------------------------------------------------
# strip_existing_omx_blocks
# ---------------------------------------------------------------------------


class TestStripExistingOmxBlocks(unittest.TestCase):
    def test_empty_returns_zero(self) -> None:
        result = strip_existing_omx_blocks("")
        self.assertIsInstance(result, StripResult)
        self.assertEqual(result.cleaned, "")
        self.assertEqual(result.removed, 0)

    def test_strips_single_block(self) -> None:
        cfg = "\n".join(
            [
                "[user.before]",
                'name = "kept"',
                "",
                "# ============================================================",
                "# oh-my-codex (OMX) Configuration",
                "# ============================================================",
                "",
                "[mcp_servers.omx_state]",
                'command = "node"',
                "",
                "# End oh-my-codex",
                "",
                "[user.after]",
                'name = "kept-after"',
                "",
            ]
        )
        result = strip_existing_omx_blocks(cfg)
        self.assertEqual(result.removed, 1)
        self.assertNotIn("[mcp_servers.omx_state]", result.cleaned)
        self.assertNotIn("OMX) Configuration", result.cleaned)
        self.assertIn("[user.before]", result.cleaned)
        self.assertIn("[user.after]", result.cleaned)

    def test_strips_multiple_blocks(self) -> None:
        block = "\n".join(
            [
                "# ============================================================",
                "# oh-my-codex (OMX) Configuration",
                "# ============================================================",
                "[mcp_servers.omx_state]",
                "# End oh-my-codex",
            ]
        )
        cfg = block + "\n\n" + block + "\n"
        result = strip_existing_omx_blocks(cfg)
        self.assertEqual(result.removed, 2)
        self.assertNotIn("OMX) Configuration", result.cleaned)

    def test_idempotent(self) -> None:
        cfg = "\n".join(
            [
                "# ============================================================",
                "# oh-my-codex (OMX) Configuration",
                "# ============================================================",
                "[mcp_servers.omx_state]",
                "# End oh-my-codex",
                "",
            ]
        )
        once = strip_existing_omx_blocks(cfg)
        twice = strip_existing_omx_blocks(once.cleaned)
        self.assertEqual(once.cleaned, twice.cleaned)
        self.assertEqual(twice.removed, 0)


# ---------------------------------------------------------------------------
# strip_existing_shared_mcp_registry_block
# ---------------------------------------------------------------------------


class TestStripExistingSharedMcpRegistryBlock(unittest.TestCase):
    def test_empty_returns_zero(self) -> None:
        result = strip_existing_shared_mcp_registry_block("")
        self.assertEqual(result.cleaned, "")
        self.assertEqual(result.removed, 0)

    def test_strips_registry_block(self) -> None:
        cfg = "\n".join(
            [
                "[user.before]",
                'name = "kept"',
                "",
                "# ============================================================",
                "# oh-my-codex (OMX) Shared MCP Registry Sync",
                "# ============================================================",
                "",
                "[mcp_servers.shared]",
                'command = "x"',
                "# End oh-my-codex shared MCP registry sync",
                "",
            ]
        )
        result = strip_existing_shared_mcp_registry_block(cfg)
        self.assertEqual(result.removed, 1)
        self.assertNotIn("Shared MCP Registry Sync", result.cleaned)
        self.assertNotIn("[mcp_servers.shared]", result.cleaned)
        self.assertIn("[user.before]", result.cleaned)

    def test_idempotent(self) -> None:
        cfg = (
            "# oh-my-codex (OMX) Shared MCP Registry Sync\n"
            "[mcp_servers.shared]\n"
            "# End oh-my-codex shared MCP registry sync\n"
        )
        once = strip_existing_shared_mcp_registry_block(cfg)
        twice = strip_existing_shared_mcp_registry_block(once.cleaned)
        self.assertEqual(once.cleaned, twice.cleaned)


# ---------------------------------------------------------------------------
# build_merged_config
# ---------------------------------------------------------------------------


class TestBuildMergedConfig(unittest.TestCase):
    def test_empty_input_produces_full_omx_config(self) -> None:
        out = build_merged_config("", "/tmp/omx")
        # Top-level OMX keys
        self.assertEqual(_count(out, r"^notify\s*="), 1)
        self.assertEqual(_count(out, r"^model_reasoning_effort\s*="), 1)
        self.assertEqual(_count(out, r"^developer_instructions\s*="), 1)
        # Features
        self.assertEqual(_count(out, r"^\[features\]$"), 1)
        self.assertEqual(_count(out, r"^multi_agent = true$"), 1)
        self.assertEqual(_count(out, r"^child_agents_md = true$"), 1)
        self.assertEqual(_count(out, r"^codex_hooks = true$"), 1)
        # Env
        self.assertEqual(_count(out, r"^\[env\]$"), 1)
        self.assertEqual(_count(out, r'^USE_OMX_EXPLORE_CMD = "1"$'), 1)
        # Agents
        self.assertEqual(_count(out, r"^\[agents\]$"), 1)
        self.assertEqual(_count(out, r"^max_threads = 6$"), 1)
        self.assertEqual(_count(out, r"^max_depth = 2$"), 1)
        # Tui present
        self.assertEqual(_count(out, r"^\[tui\]$"), 1)
        # OMX config block
        self.assertEqual(_count(out, r"oh-my-codex \(OMX\) Configuration"), 1)
        self.assertEqual(_count(out, r"^# End oh-my-codex$"), 1)
        # First-party MCP servers
        self.assertEqual(_count(out, r"^\[mcp_servers\.omx_state\]$"), 1)
        self.assertEqual(_count(out, r"^\[mcp_servers\.omx_memory\]$"), 1)
        self.assertEqual(_count(out, r"^\[mcp_servers\.omx_code_intel\]$"), 1)
        self.assertEqual(_count(out, r"^\[mcp_servers\.omx_trace\]$"), 1)
        self.assertEqual(_count(out, r"^\[mcp_servers\.omx_wiki\]$"), 1)
        # Legacy team_run not emitted
        self.assertEqual(_count(out, r"^\[mcp_servers\.omx_team_run\]$"), 0)
        self.assertNotIn("team-server.js", out)

    def test_idempotent_double_run(self) -> None:
        first = build_merged_config("", "/tmp/omx")
        second = build_merged_config(first, "/tmp/omx")
        self.assertEqual(_count(second, r"oh-my-codex \(OMX\) Configuration"), 1)
        self.assertEqual(_count(second, r"^\[tui\]$"), 1)
        self.assertEqual(_count(second, r"^\[features\]$"), 1)
        self.assertEqual(_count(second, r"^codex_hooks = true$"), 1)
        self.assertEqual(_count(second, r"^notify\s*="), 1)

    def test_idempotent_triple_run(self) -> None:
        first = build_merged_config("", "/tmp/omx")
        second = build_merged_config(first, "/tmp/omx")
        third = build_merged_config(second, "/tmp/omx")
        # Marker counts stay stable across runs (TS parity — exact-equality is
        # not promised because rerun adds a single blank line drift before the
        # body, mirroring TS ``buildMergedConfig`` behavior).
        for run in (first, second, third):
            self.assertEqual(_count(run, r"oh-my-codex \(OMX\) Configuration"), 1)
            self.assertEqual(_count(run, r"^# End oh-my-codex$"), 1)
            self.assertEqual(_count(run, r"^\[mcp_servers\.omx_state\]$"), 1)
            self.assertEqual(_count(run, r"^\[tui\]$"), 1)
            self.assertEqual(_count(run, r"^\[features\]$"), 1)
            self.assertEqual(_count(run, r"^codex_hooks = true$"), 1)
            self.assertEqual(_count(run, r"^notify\s*="), 1)
            self.assertEqual(_count(run, r"^model_reasoning_effort\s*="), 1)
            self.assertEqual(_count(run, r"^developer_instructions\s*="), 1)
            self.assertEqual(_count(run, r"^\[env\]$"), 1)
            self.assertEqual(_count(run, r'^USE_OMX_EXPLORE_CMD = "1"$'), 1)
            self.assertEqual(_count(run, r"^\[agents\]$"), 1)

    def test_include_tui_false_skips_tui_table(self) -> None:
        out = build_merged_config("", "/tmp/omx", MergeOptions(include_tui=False))
        self.assertEqual(_count(out, r"^\[tui\]$"), 0)
        # But the rest of the OMX block is still present.
        self.assertIn("[mcp_servers.omx_state]", out)
        self.assertIn("[env]", out)
        self.assertIn('USE_OMX_EXPLORE_CMD = "1"', out)

    def test_preserves_user_root_keys(self) -> None:
        existing = 'approval_policy = "on-failure"\n'
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn('approval_policy = "on-failure"', out)

    def test_preserves_user_sections(self) -> None:
        existing = "\n".join(
            [
                "[user.custom]",
                'name = "kept"',
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn("[user.custom]", out)
        self.assertIn('name = "kept"', out)

    def test_strips_orphaned_legacy_omx_mcp_sections(self) -> None:
        existing = "\n".join(
            [
                'model = "o3"',
                "",
                "[mcp_servers.omx_state]",
                'command = "node"',
                'args = ["/old/path/state-server.js"]',
                "",
                "[user.custom]",
                'name = "kept"',
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        # Old path is gone, only the new one survives
        self.assertNotIn("/old/path/state-server.js", out)
        self.assertEqual(_count(out, r"^\[mcp_servers\.omx_state\]$"), 1)
        self.assertIn("[user.custom]", out)

    def test_strips_orphaned_legacy_agent_sections(self) -> None:
        existing = "\n".join(
            [
                "[agents.executor]",
                'description = "old"',
                'config_file = "/old.toml"',
                "",
                "[agents.explore]",
                'description = "old"',
                "",
                "[user.custom]",
                'name = "kept"',
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertNotIn("[agents.executor]", out)
        self.assertNotIn("[agents.explore]", out)
        # The user section survives
        self.assertIn("[user.custom]", out)
        # Global agents seeded
        self.assertIn("[agents]", out)
        self.assertIn("max_threads = 6", out)

    def test_preserves_user_agent_sections(self) -> None:
        existing = "\n".join(
            [
                '[agents."my-custom-bot"]',
                'description = "My custom agent"',
                "",
                "[agents.myreviewer]",
                'description = "Company reviewer"',
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn('[agents."my-custom-bot"]', out)
        self.assertIn('description = "My custom agent"', out)
        self.assertIn("[agents.myreviewer]", out)
        self.assertIn('description = "Company reviewer"', out)

    def test_merges_user_tui_section(self) -> None:
        existing = "\n".join(
            [
                "[tui]",
                'theme = "night"',
                'status_line = ["git-branch"]',
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertEqual(_count(out, r"^\[tui\]$"), 1)
        self.assertIn('theme = "night"', out)
        self.assertIn(
            'status_line = ["model-with-reasoning", "git-branch", '
            '"context-remaining", "total-input-tokens", "total-output-tokens", '
            '"five-hour-limit", "weekly-limit"]',
            out,
        )

    def test_seeds_model_when_missing(self) -> None:
        existing = 'approval_policy = "on-failure"\n'
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn('model = "gpt-5.5"', out)
        self.assertIn("model_context_window = 250000", out)
        self.assertIn("model_auto_compact_token_limit = 200000", out)

    def test_preserves_existing_non_default_model(self) -> None:
        existing = 'model = "o3"\n'
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn('model = "o3"', out)
        # Behavioral defaults only seeded for the default model
        self.assertNotIn("model_context_window = 250000", out)
        self.assertNotIn("model_auto_compact_token_limit = 200000", out)

    def test_model_override_replaces_existing_model(self) -> None:
        out = build_merged_config(
            'model = "gpt-5.3-codex"\n',
            "/tmp/omx",
            MergeOptions(model_override="gpt-5.5"),
        )
        self.assertIn('model = "gpt-5.5"', out)
        self.assertNotIn('"gpt-5.3-codex"', out)
        self.assertIn("model_context_window = 250000", out)
        self.assertIn("model_auto_compact_token_limit = 200000", out)

    def test_independent_seeded_defaults_are_not_duplicated(self) -> None:
        existing = "\n".join(
            [
                'model = "gpt-5.5"',
                "model_context_window = 640000",
                "",
            ]
        )
        once = build_merged_config(existing, "/tmp/omx")
        twice = build_merged_config(once, "/tmp/omx")
        self.assertEqual(_count(twice, r"^model_context_window = 640000$"), 1)
        self.assertEqual(_count(twice, r"^model_auto_compact_token_limit = 200000$"), 1)
        self.assertNotIn("model_context_window = 250000", twice)

    def test_seeds_only_missing_auto_compact(self) -> None:
        existing = "\n".join(
            [
                'model = "gpt-5.5"',
                "model_context_window = 640000",
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn("model_context_window = 640000", out)
        self.assertIn("model_auto_compact_token_limit = 200000", out)

    def test_seeds_only_missing_context_window(self) -> None:
        existing = "\n".join(
            [
                'model = "gpt-5.5"',
                "model_auto_compact_token_limit = 150000",
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn("model_context_window = 250000", out)
        self.assertIn("model_auto_compact_token_limit = 150000", out)

    def test_preserves_existing_env_keys(self) -> None:
        existing = "\n".join(
            [
                "[env]",
                'FOO = "bar"',
                'USE_OMX_EXPLORE_CMD = "0"',
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn('FOO = "bar"', out)
        self.assertIn('USE_OMX_EXPLORE_CMD = "0"', out)
        self.assertEqual(_count(out, r"^\[env\]$"), 1)

    def test_replaces_orphaned_managed_notify(self) -> None:
        existing = "\n".join(
            [
                "[shell_environment_policy]",
                'inherit = "all"',
                "",
                'notify = ["node", "/tmp/legacy-notify-hook.js"]',
                "",
                '    "node",',
                '    "/tmp/legacy-notify-hook.js",',
                "]",
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertEqual(_count(out, r"^notify\s*="), 1)
        self.assertNotIn("legacy-notify-hook.js", out)

    def test_strips_legacy_team_run_table(self) -> None:
        existing = "\n".join(
            [
                "[user.before]",
                'name = "kept-before"',
                "",
                "# ============================================================",
                "# oh-my-codex (OMX) Configuration",
                "# ============================================================",
                "",
                "[mcp_servers.omx_team_run]",
                'command = "node"',
                'args = ["/tmp/team-server.js"]',
                "",
                "# End oh-my-codex",
                "",
                "[user.after]",
                'name = "kept-after"',
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertNotIn("[mcp_servers.omx_team_run]", out)
        self.assertNotIn("team-server.js", out)
        self.assertIn("[user.before]", out)
        self.assertIn("[user.after]", out)

    def test_repairs_duplicate_tui_sections(self) -> None:
        existing = "\n".join(
            [
                "[mcp_servers.figma]",
                'url = "https://mcp.figma.com/mcp"',
                "",
                "[tui]",
                'status_line = ["git-branch"]',
                "",
                "# End oh-my-codex",
                "",
                "[tui]",
                'status_line = ["model-with-reasoning", "git-branch"]',
                "",
                "# End oh-my-codex",
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertEqual(_count(out, r"^\[tui\]$"), 1)
        self.assertIn("[mcp_servers.figma]", out)

    def test_shared_mcp_registry_block_emitted(self) -> None:
        opts = MergeOptions(
            shared_mcp_servers=[
                UnifiedMcpRegistryServer(
                    name="eslint",
                    command="npx",
                    args=["@eslint/mcp@latest"],
                    enabled=True,
                    startup_timeout_sec=12,
                ),
            ],
            shared_mcp_registry_source="/tmp/.omx/mcp-registry.json",
        )
        first = build_merged_config("", "/tmp/omx", opts)
        second = build_merged_config(first, "/tmp/omx", opts)
        self.assertEqual(
            _count(second, r"oh-my-codex \(OMX\) Shared MCP Registry Sync"),
            1,
        )
        self.assertEqual(_count(second, r"^\[mcp_servers\.eslint\]$"), 1)
        self.assertIn("# Source: /tmp/.omx/mcp-registry.json", second)

    def test_shared_mcp_skips_existing_user_entries(self) -> None:
        existing = "\n".join(
            [
                "[mcp_servers.existing_server]",
                'command = "custom"',
                'args = ["serve"]',
                "",
            ]
        )
        opts = MergeOptions(
            shared_mcp_servers=[
                UnifiedMcpRegistryServer(
                    name="existing_server",
                    command="existing-server",
                    args=["mcp"],
                ),
                UnifiedMcpRegistryServer(
                    name="eslint",
                    command="npx",
                    args=["@eslint/mcp@latest"],
                ),
            ],
            shared_mcp_registry_source="/tmp/.omx/mcp-registry.json",
        )
        out = build_merged_config(existing, "/tmp/omx", opts)
        self.assertEqual(_count(out, r"^\[mcp_servers\.existing_server\]$"), 1)
        self.assertIn('command = "custom"', out)
        # New server still added
        self.assertEqual(_count(out, r"^\[mcp_servers\.eslint\]$"), 1)

    def test_adds_launcher_startup_timeout(self) -> None:
        existing = "\n".join(
            [
                "[mcp_servers.filesystem]",
                'command = "npx"',
                'args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]',
                "",
            ]
        )
        first = build_merged_config(existing, "/tmp/omx")
        second = build_merged_config(first, "/tmp/omx")
        self.assertIn("[mcp_servers.filesystem]", first)
        self.assertIn("startup_timeout_sec = 15", first)
        # Idempotent — not duplicated
        self.assertEqual(
            _count(second, r"^startup_timeout_sec = 15$"),
            _count(first, r"^startup_timeout_sec = 15$"),
        )

    def test_preserves_explicit_launcher_timeouts(self) -> None:
        existing = "\n".join(
            [
                "[mcp_servers.fetch]",
                'command = "uvx"',
                'args = ["mcp-server-fetch"]',
                "startup_timeout_sec = 22",
                "",
                "[mcp_servers.custom]",
                'command = "custom-mcp"',
                'args = ["serve"]',
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn("startup_timeout_sec = 22", out)
        # custom (non-launcher) gets no automatic timeout
        custom_match = re.search(r"\[mcp_servers\.custom\][\s\S]*?(?=\n\[|\Z)", out)
        self.assertIsNotNone(custom_match)
        self.assertNotIn("startup_timeout_sec = 15", custom_match.group(0))

    def test_npm_exec_recognized_as_launcher(self) -> None:
        existing = "\n".join(
            [
                "[mcp_servers.seq]",
                'command = "npm"',
                'args = ["exec", "@modelcontextprotocol/server-sequential-thinking"]',
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertIn("[mcp_servers.seq]", out)
        self.assertIn("startup_timeout_sec = 15", out)

    def test_removes_multiline_developer_instructions(self) -> None:
        existing = "\n".join(
            [
                'model = "gpt-5.5"',
                'developer_instructions = """Custom instructions survive as valid TOML.',
                "This line used to be orphaned by setup.",
                'This closing line used to break parsing."""',
                "",
                "[features]",
                "web_search = true",
                "",
            ]
        )
        out = build_merged_config(existing, "/tmp/omx")
        self.assertNotIn("This line used to be orphaned", out)
        self.assertNotIn("This closing line used to break parsing", out)
        self.assertEqual(_count(out, r"^developer_instructions\s*="), 1)
        # Output is parseable TOML
        tomllib.loads(out)


# ---------------------------------------------------------------------------
# merge_config (file-level)
# ---------------------------------------------------------------------------


class TestMergeConfig(unittest.TestCase):
    def test_creates_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            merge_config(path, tmpdir)
            self.assertTrue(path.exists())
            content = path.read_text(encoding="utf-8")
            self.assertIn("oh-my-codex (OMX) Configuration", content)
            self.assertIn("multi_agent = true", content)
            self.assertIn("codex_hooks = true", content)
            self.assertIn("[tui]", content)

    def test_idempotent_double_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            merge_config(path, tmpdir)
            first = path.read_text(encoding="utf-8")
            merge_config(path, tmpdir)
            second = path.read_text(encoding="utf-8")
            # Marker counts must remain singleton (TS parity)
            for content in (first, second):
                self.assertEqual(
                    _count(content, r"oh-my-codex \(OMX\) Configuration"), 1
                )
                self.assertEqual(_count(content, r"^\[tui\]$"), 1)
                self.assertEqual(_count(content, r"^\[features\]$"), 1)
                self.assertEqual(_count(content, r"^codex_hooks = true$"), 1)

    def test_preserves_user_content_added_between_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            merge_config(path, tmpdir)
            content = path.read_text(encoding="utf-8")
            content += '\n[user.prefs]\ntheme = "dark"\n'
            path.write_text(content, encoding="utf-8")
            merge_config(path, tmpdir)
            result = path.read_text(encoding="utf-8")
            self.assertIn("[user.prefs]", result)
            self.assertIn('theme = "dark"', result)


# ---------------------------------------------------------------------------
# repair_config_if_needed
# ---------------------------------------------------------------------------


class TestRepairConfigIfNeeded(unittest.TestCase):
    def test_missing_file_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "missing.toml"
            self.assertFalse(repair_config_if_needed(path, tmpdir))

    def test_clean_config_returns_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            merge_config(path, tmpdir)
            self.assertFalse(repair_config_if_needed(path, tmpdir))

    def test_repairs_duplicate_tui(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            merge_config(path, tmpdir)
            content = path.read_text(encoding="utf-8")
            content += '\n[tui]\nstatus_line = ["git-branch"]\n'
            path.write_text(content, encoding="utf-8")
            self.assertEqual(_count(content, r"^\[tui\]$"), 2)
            self.assertTrue(repair_config_if_needed(path, tmpdir))
            repaired = path.read_text(encoding="utf-8")
            self.assertEqual(_count(repaired, r"^\[tui\]$"), 1)

    def test_repairs_legacy_team_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            content = "\n".join(
                [
                    "[user.before]",
                    'name = "kept-before"',
                    "",
                    "[mcp_servers.omx_team_run]",
                    'command = "node"',
                    'args = ["/tmp/team-server.js"]',
                    "",
                    "[user.after]",
                    'name = "kept-after"',
                    "",
                ]
            )
            path.write_text(content, encoding="utf-8")
            self.assertTrue(repair_config_if_needed(path, tmpdir))
            repaired = path.read_text(encoding="utf-8")
            self.assertNotIn("[mcp_servers.omx_team_run]", repaired)
            self.assertNotIn("team-server.js", repaired)
            self.assertIn("[user.before]", repaired)
            self.assertIn("[user.after]", repaired)

    def test_repairs_missing_launcher_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            content = "\n".join(
                [
                    "[mcp_servers.filesystem]",
                    'command = "npx"',
                    'args = ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]',
                    "",
                ]
            )
            path.write_text(content, encoding="utf-8")
            self.assertTrue(repair_config_if_needed(path, tmpdir))
            repaired = path.read_text(encoding="utf-8")
            self.assertIn("startup_timeout_sec = 15", repaired)


# ---------------------------------------------------------------------------
# deep_merge_dicts (preserved Python-side helper)
# ---------------------------------------------------------------------------


class TestDeepMergeDicts(unittest.TestCase):
    def test_empty_inputs(self) -> None:
        self.assertEqual(deep_merge_dicts({}, {}), {})

    def test_overlay_overrides_base(self) -> None:
        self.assertEqual(
            deep_merge_dicts({"a": 1, "b": 2}, {"b": 99, "c": 3}),
            {"a": 1, "b": 99, "c": 3},
        )

    def test_nested_merge(self) -> None:
        base = {"nested": {"x": 1, "y": 2}}
        overlay = {"nested": {"y": 3, "z": 4}}
        self.assertEqual(
            deep_merge_dicts(base, overlay),
            {"nested": {"x": 1, "y": 3, "z": 4}},
        )

    def test_does_not_mutate_inputs(self) -> None:
        base = {"a": {"x": 1}}
        overlay = {"a": {"y": 2}}
        deep_merge_dicts(base, overlay)
        self.assertEqual(base, {"a": {"x": 1}})
        self.assertEqual(overlay, {"a": {"y": 2}})


if __name__ == "__main__":
    unittest.main()
