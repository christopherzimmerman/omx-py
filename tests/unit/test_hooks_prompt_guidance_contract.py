"""Tests for omx.hooks.prompt_guidance_contract.

Covers the public surface: contract dataclass, the canonical contract tuples,
and that each contract carries non-empty, case-insensitive compiled regex
patterns.
"""

from __future__ import annotations

import re
import unittest

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

_ALL_CONTRACT_TUPLES: tuple[tuple[str, tuple[GuidanceSurfaceContract, ...]], ...] = (
    ("ROOT_TEMPLATE_CONTRACTS", ROOT_TEMPLATE_CONTRACTS),
    ("CORE_ROLE_CONTRACTS", CORE_ROLE_CONTRACTS),
    ("SCENARIO_ROLE_CONTRACTS", SCENARIO_ROLE_CONTRACTS),
    ("WAVE_TWO_CONTRACTS", WAVE_TWO_CONTRACTS),
    ("CATALOG_CONTRACTS", CATALOG_CONTRACTS),
    ("LEGACY_PROMPT_CONTRACTS", LEGACY_PROMPT_CONTRACTS),
    ("SPECIALIZED_PROMPT_CONTRACTS", SPECIALIZED_PROMPT_CONTRACTS),
    ("SKILL_CONTRACTS", SKILL_CONTRACTS),
)


class TestGuidanceSurfaceContractDataclass(unittest.TestCase):
    def test_is_frozen(self) -> None:
        c = GuidanceSurfaceContract(id="x", path="x.md")
        with self.assertRaises(Exception):
            c.id = "y"  # type: ignore[misc]

    def test_default_patterns_is_empty_tuple(self) -> None:
        c = GuidanceSurfaceContract(id="x", path="x.md")
        self.assertEqual(c.required_patterns, ())

    def test_carries_patterns(self) -> None:
        pat = re.compile(r"hello", re.IGNORECASE)
        c = GuidanceSurfaceContract(id="x", path="x.md", required_patterns=(pat,))
        self.assertEqual(len(c.required_patterns), 1)
        self.assertIs(c.required_patterns[0], pat)


class TestRootTemplateContracts(unittest.TestCase):
    def test_has_single_agents_template_entry(self) -> None:
        self.assertEqual(len(ROOT_TEMPLATE_CONTRACTS), 1)
        c = ROOT_TEMPLATE_CONTRACTS[0]
        self.assertEqual(c.id, "agents-template")
        self.assertEqual(c.path, "templates/AGENTS.md")

    def test_pattern_count_matches_ts(self) -> None:
        # TS source has 29 patterns in ROOT_TEMPLATE_PATTERNS.
        self.assertEqual(len(ROOT_TEMPLATE_CONTRACTS[0].required_patterns), 29)


class TestCoreRoleContracts(unittest.TestCase):
    def test_three_roles(self) -> None:
        ids = sorted(c.id for c in CORE_ROLE_CONTRACTS)
        self.assertEqual(ids, ["executor", "planner", "verifier"])

    def test_paths_match_prompt_dir(self) -> None:
        for c in CORE_ROLE_CONTRACTS:
            self.assertEqual(c.path, f"prompts/{c.id}.md")


class TestScenarioRoleContracts(unittest.TestCase):
    def test_three_scenarios(self) -> None:
        ids = sorted(c.id for c in SCENARIO_ROLE_CONTRACTS)
        self.assertEqual(
            ids, ["executor-scenarios", "planner-scenarios", "verifier-scenarios"]
        )

    def test_each_scenario_has_continue_pattern(self) -> None:
        for c in SCENARIO_ROLE_CONTRACTS:
            joined = " || ".join(p.pattern for p in c.required_patterns)
            self.assertIn("continue", joined)


class TestWaveTwoContracts(unittest.TestCase):
    def test_expected_names(self) -> None:
        expected = {
            "architect",
            "critic",
            "debugger",
            "test-engineer",
            "code-reviewer",
            "quality-reviewer",
            "security-reviewer",
            "researcher",
            "explore",
        }
        self.assertEqual({c.id for c in WAVE_TWO_CONTRACTS}, expected)

    def test_all_share_wave_two_patterns(self) -> None:
        first_patterns = WAVE_TWO_CONTRACTS[0].required_patterns
        for c in WAVE_TWO_CONTRACTS[1:]:
            self.assertEqual(c.required_patterns, first_patterns)


class TestCatalogContracts(unittest.TestCase):
    def test_expected_names(self) -> None:
        expected = {
            "analyst",
            "api-reviewer",
            "build-fixer",
            "dependency-expert",
            "designer",
            "git-master",
            "information-architect",
            "performance-reviewer",
            "product-analyst",
            "product-manager",
            "qa-tester",
            "quality-strategist",
            "style-reviewer",
            "ux-researcher",
            "vision",
            "writer",
        }
        self.assertEqual({c.id for c in CATALOG_CONTRACTS}, expected)


class TestLegacyAndSpecializedContracts(unittest.TestCase):
    def test_legacy_has_code_simplifier(self) -> None:
        self.assertEqual([c.id for c in LEGACY_PROMPT_CONTRACTS], ["code-simplifier"])

    def test_specialized_has_sisyphus_lite(self) -> None:
        self.assertEqual(
            [c.id for c in SPECIALIZED_PROMPT_CONTRACTS], ["sisyphus-lite"]
        )


class TestSkillContracts(unittest.TestCase):
    def test_expected_names(self) -> None:
        expected = {
            "analyze",
            "autopilot",
            "build-fix",
            "code-review",
            "plan",
            "ralph",
            "ralplan",
            "security-review",
            "team",
            "ultraqa",
            "ultrawork",
        }
        self.assertEqual({c.id for c in SKILL_CONTRACTS}, expected)

    def test_paths_under_skills_dir(self) -> None:
        for c in SKILL_CONTRACTS:
            self.assertEqual(c.path, f"skills/{c.id}/SKILL.md")

    def test_ultrawork_has_extra_patterns(self) -> None:
        # ULTRAWORK_SKILL_PATTERNS extends SKILL_PATTERNS with 6 extras (9 total).
        ultrawork = next(c for c in SKILL_CONTRACTS if c.id == "ultrawork")
        standard = next(c for c in SKILL_CONTRACTS if c.id == "ralph")
        self.assertGreater(
            len(ultrawork.required_patterns), len(standard.required_patterns)
        )
        self.assertEqual(len(ultrawork.required_patterns), 9)
        self.assertEqual(len(standard.required_patterns), 3)


class TestAllContractsWellFormed(unittest.TestCase):
    def test_every_pattern_is_case_insensitive_compiled_regex(self) -> None:
        for tup_name, tup in _ALL_CONTRACT_TUPLES:
            for c in tup:
                self.assertIsInstance(c, GuidanceSurfaceContract, tup_name)
                self.assertGreater(
                    len(c.required_patterns),
                    0,
                    msg=f"{tup_name}/{c.id} has empty patterns",
                )
                for p in c.required_patterns:
                    self.assertIsInstance(p, re.Pattern)
                    self.assertTrue(
                        p.flags & re.IGNORECASE,
                        msg=f"{tup_name}/{c.id} pattern not IGNORECASE: {p.pattern!r}",
                    )

    def test_every_id_is_unique_within_its_tuple(self) -> None:
        for tup_name, tup in _ALL_CONTRACT_TUPLES:
            ids = [c.id for c in tup]
            self.assertEqual(
                len(ids), len(set(ids)), msg=f"{tup_name} has duplicate ids"
            )

    def test_every_path_is_relative_forward_slash(self) -> None:
        for _tup_name, tup in _ALL_CONTRACT_TUPLES:
            for c in tup:
                self.assertFalse(c.path.startswith("/"))
                self.assertNotIn("\\", c.path)


class TestLegacyAlias(unittest.TestCase):
    """`prompt_guidance` is kept as a re-export shim for legacy imports."""

    def test_alias_module_re_exports_same_objects(self) -> None:
        from omx.hooks import prompt_guidance as legacy

        self.assertIs(legacy.GuidanceSurfaceContract, GuidanceSurfaceContract)
        self.assertIs(legacy.ROOT_TEMPLATE_CONTRACTS, ROOT_TEMPLATE_CONTRACTS)
        self.assertIs(legacy.SKILL_CONTRACTS, SKILL_CONTRACTS)


if __name__ == "__main__":
    unittest.main()
