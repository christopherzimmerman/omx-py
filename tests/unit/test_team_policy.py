"""Tests for omx.team.state.policy — normalize_team_policy / normalize_team_governance.

Mirrors the behavior of TS `normalizeTeamPolicy` / `normalizeTeamGovernance`
in `src/team/state.ts`.
"""

from __future__ import annotations

import unittest

from omx.team.state.policy import (
    DEFAULT_DISPATCH_ACK_TIMEOUT_MS,
    MAX_DISPATCH_ACK_TIMEOUT_MS,
    MIN_DISPATCH_ACK_TIMEOUT_MS,
    TeamDispatchMode,
    TeamDisplayMode,
    TeamGovernance,
    TeamPolicy,
    TeamWorkerLaunchMode,
    normalize_team_governance,
    normalize_team_policy,
)


class TestNormalizeTeamPolicyDefaults(unittest.TestCase):
    def test_none_input_returns_defaults(self):
        p = normalize_team_policy(None)
        self.assertEqual(p.display_mode, TeamDisplayMode.AUTO)
        self.assertEqual(p.worker_launch_mode, TeamWorkerLaunchMode.INTERACTIVE)
        self.assertEqual(p.dispatch_mode, TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK)
        self.assertEqual(p.dispatch_ack_timeout_ms, DEFAULT_DISPATCH_ACK_TIMEOUT_MS)

    def test_empty_dict_returns_defaults(self):
        p = normalize_team_policy({})
        self.assertEqual(p, TeamPolicy())

    def test_default_overrides_apply_when_raw_missing_those_fields(self):
        p = normalize_team_policy(
            None,
            defaults={
                "display_mode": "split_pane",
                "worker_launch_mode": "prompt",
            },
        )
        self.assertEqual(p.display_mode, TeamDisplayMode.SPLIT_PANE)
        self.assertEqual(p.worker_launch_mode, TeamWorkerLaunchMode.PROMPT)
        # dispatch_mode default is always hook_preferred_with_fallback
        self.assertEqual(p.dispatch_mode, TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK)

    def test_invalid_defaults_fall_back_to_canonical(self):
        p = normalize_team_policy(
            None,
            defaults={
                "display_mode": "not-a-real-mode",
                "worker_launch_mode": "bogus",
            },
        )
        self.assertEqual(p.display_mode, TeamDisplayMode.AUTO)
        self.assertEqual(p.worker_launch_mode, TeamWorkerLaunchMode.INTERACTIVE)


class TestNormalizeTeamPolicyDisplayMode(unittest.TestCase):
    def test_split_pane_accepted(self):
        p = normalize_team_policy({"display_mode": "split_pane"})
        self.assertEqual(p.display_mode, TeamDisplayMode.SPLIT_PANE)

    def test_auto_accepted_via_default_base(self):
        p = normalize_team_policy({"display_mode": "auto"})
        self.assertEqual(p.display_mode, TeamDisplayMode.AUTO)

    def test_unknown_display_mode_falls_back_to_default_base(self):
        # base default is auto, so unknown values fall to auto.
        p = normalize_team_policy({"display_mode": "weird"})
        self.assertEqual(p.display_mode, TeamDisplayMode.AUTO)

    def test_unknown_display_mode_falls_back_to_overridden_base(self):
        p = normalize_team_policy(
            {"display_mode": "weird"},
            defaults={
                "display_mode": "split_pane",
                "worker_launch_mode": "interactive",
            },
        )
        # raw had a non-"split_pane" value → uses base, which is split_pane.
        self.assertEqual(p.display_mode, TeamDisplayMode.SPLIT_PANE)


class TestNormalizeTeamPolicyWorkerLaunchMode(unittest.TestCase):
    def test_prompt_accepted(self):
        p = normalize_team_policy({"worker_launch_mode": "prompt"})
        self.assertEqual(p.worker_launch_mode, TeamWorkerLaunchMode.PROMPT)

    def test_interactive_accepted_via_default_base(self):
        p = normalize_team_policy({"worker_launch_mode": "interactive"})
        self.assertEqual(p.worker_launch_mode, TeamWorkerLaunchMode.INTERACTIVE)

    def test_unknown_worker_launch_mode_falls_back_to_base(self):
        p = normalize_team_policy({"worker_launch_mode": "bogus"})
        self.assertEqual(p.worker_launch_mode, TeamWorkerLaunchMode.INTERACTIVE)


class TestNormalizeTeamPolicyDispatchMode(unittest.TestCase):
    def test_transport_direct_accepted(self):
        p = normalize_team_policy({"dispatch_mode": "transport_direct"})
        self.assertEqual(p.dispatch_mode, TeamDispatchMode.TRANSPORT_DIRECT)

    def test_hook_preferred_accepted(self):
        p = normalize_team_policy({"dispatch_mode": "hook_preferred_with_fallback"})
        self.assertEqual(p.dispatch_mode, TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK)

    def test_unknown_dispatch_mode_collapses_to_hook_preferred(self):
        p = normalize_team_policy({"dispatch_mode": "bogus"})
        self.assertEqual(p.dispatch_mode, TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK)

    def test_missing_dispatch_mode_collapses_to_hook_preferred(self):
        p = normalize_team_policy({})
        self.assertEqual(p.dispatch_mode, TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK)


class TestNormalizeTeamPolicyDispatchAckTimeout(unittest.TestCase):
    def test_default_when_missing(self):
        p = normalize_team_policy({})
        self.assertEqual(p.dispatch_ack_timeout_ms, DEFAULT_DISPATCH_ACK_TIMEOUT_MS)

    def test_below_min_clamps_up(self):
        p = normalize_team_policy({"dispatch_ack_timeout_ms": 1})
        self.assertEqual(p.dispatch_ack_timeout_ms, MIN_DISPATCH_ACK_TIMEOUT_MS)

    def test_above_max_clamps_down(self):
        p = normalize_team_policy({"dispatch_ack_timeout_ms": 99_999})
        self.assertEqual(p.dispatch_ack_timeout_ms, MAX_DISPATCH_ACK_TIMEOUT_MS)

    def test_in_range_is_preserved(self):
        p = normalize_team_policy({"dispatch_ack_timeout_ms": 1500})
        self.assertEqual(p.dispatch_ack_timeout_ms, 1500)

    def test_floor_applied_to_float(self):
        p = normalize_team_policy({"dispatch_ack_timeout_ms": 1500.9})
        self.assertEqual(p.dispatch_ack_timeout_ms, 1500)

    def test_string_numeric_coerced(self):
        p = normalize_team_policy({"dispatch_ack_timeout_ms": "3000"})
        self.assertEqual(p.dispatch_ack_timeout_ms, 3000)

    def test_non_numeric_string_falls_back_to_default(self):
        p = normalize_team_policy({"dispatch_ack_timeout_ms": "not-a-number"})
        self.assertEqual(p.dispatch_ack_timeout_ms, DEFAULT_DISPATCH_ACK_TIMEOUT_MS)

    def test_none_value_falls_back_to_default(self):
        p = normalize_team_policy({"dispatch_ack_timeout_ms": None})
        self.assertEqual(p.dispatch_ack_timeout_ms, DEFAULT_DISPATCH_ACK_TIMEOUT_MS)

    def test_dict_value_falls_back_to_default(self):
        p = normalize_team_policy({"dispatch_ack_timeout_ms": {"bad": "shape"}})
        self.assertEqual(p.dispatch_ack_timeout_ms, DEFAULT_DISPATCH_ACK_TIMEOUT_MS)


class TestNormalizeTeamPolicyMerge(unittest.TestCase):
    def test_partial_input_merges_with_defaults(self):
        p = normalize_team_policy({"display_mode": "split_pane"})
        self.assertEqual(p.display_mode, TeamDisplayMode.SPLIT_PANE)
        # other fields use canonical defaults
        self.assertEqual(p.worker_launch_mode, TeamWorkerLaunchMode.INTERACTIVE)
        self.assertEqual(p.dispatch_mode, TeamDispatchMode.HOOK_PREFERRED_WITH_FALLBACK)
        self.assertEqual(p.dispatch_ack_timeout_ms, DEFAULT_DISPATCH_ACK_TIMEOUT_MS)

    def test_unknown_fields_ignored(self):
        p = normalize_team_policy(
            {
                "display_mode": "split_pane",
                "totally_made_up": "garbage",
                "another_unknown": 42,
            }
        )
        self.assertEqual(p.display_mode, TeamDisplayMode.SPLIT_PANE)
        # The unknown keys must not appear on the dataclass.
        self.assertFalse(hasattr(p, "totally_made_up"))

    def test_fully_specified_round_trip(self):
        p = normalize_team_policy(
            {
                "display_mode": "split_pane",
                "worker_launch_mode": "prompt",
                "dispatch_mode": "transport_direct",
                "dispatch_ack_timeout_ms": 5000,
            }
        )
        self.assertEqual(
            p,
            TeamPolicy(
                display_mode=TeamDisplayMode.SPLIT_PANE,
                worker_launch_mode=TeamWorkerLaunchMode.PROMPT,
                dispatch_mode=TeamDispatchMode.TRANSPORT_DIRECT,
                dispatch_ack_timeout_ms=5000,
            ),
        )

    def test_to_dict_serializes_enum_values(self):
        p = normalize_team_policy(
            {
                "display_mode": "split_pane",
                "worker_launch_mode": "prompt",
                "dispatch_mode": "transport_direct",
                "dispatch_ack_timeout_ms": 2500,
            }
        )
        self.assertEqual(
            p.to_dict(),
            {
                "display_mode": "split_pane",
                "worker_launch_mode": "prompt",
                "dispatch_mode": "transport_direct",
                "dispatch_ack_timeout_ms": 2500,
            },
        )


class TestNormalizeTeamGovernanceDefaults(unittest.TestCase):
    def test_none_input_returns_defaults(self):
        g = normalize_team_governance(None)
        self.assertEqual(
            g,
            TeamGovernance(
                delegation_only=False,
                plan_approval_required=False,
                nested_teams_allowed=False,
                one_team_per_leader_session=True,
                cleanup_requires_all_workers_inactive=True,
            ),
        )

    def test_empty_dict_returns_defaults(self):
        g = normalize_team_governance({})
        self.assertEqual(g, TeamGovernance())

    def test_default_dataclass_matches_ts_defaults(self):
        g = TeamGovernance()
        self.assertFalse(g.delegation_only)
        self.assertFalse(g.plan_approval_required)
        self.assertFalse(g.nested_teams_allowed)
        self.assertTrue(g.one_team_per_leader_session)
        self.assertTrue(g.cleanup_requires_all_workers_inactive)


class TestNormalizeTeamGovernanceFlags(unittest.TestCase):
    def test_explicit_true_flips_false_default(self):
        g = normalize_team_governance(
            {
                "delegation_only": True,
                "plan_approval_required": True,
                "nested_teams_allowed": True,
            }
        )
        self.assertTrue(g.delegation_only)
        self.assertTrue(g.plan_approval_required)
        self.assertTrue(g.nested_teams_allowed)

    def test_explicit_false_flips_true_default(self):
        g = normalize_team_governance(
            {
                "one_team_per_leader_session": False,
                "cleanup_requires_all_workers_inactive": False,
            }
        )
        self.assertFalse(g.one_team_per_leader_session)
        self.assertFalse(g.cleanup_requires_all_workers_inactive)

    def test_truthy_non_bool_does_not_flip_false_default(self):
        # TS uses `=== true`, so only the literal True flips it.
        g = normalize_team_governance(
            {
                "delegation_only": 1,  # truthy but not True
                "plan_approval_required": "yes",
                "nested_teams_allowed": [1],
            }
        )
        self.assertFalse(g.delegation_only)
        self.assertFalse(g.plan_approval_required)
        self.assertFalse(g.nested_teams_allowed)

    def test_falsy_non_bool_does_not_flip_true_default(self):
        # TS uses `!== false`, so only the literal False flips it.
        g = normalize_team_governance(
            {
                "one_team_per_leader_session": 0,  # falsy but not False
                "cleanup_requires_all_workers_inactive": "",
            }
        )
        self.assertTrue(g.one_team_per_leader_session)
        self.assertTrue(g.cleanup_requires_all_workers_inactive)

    def test_unknown_fields_ignored(self):
        g = normalize_team_governance(
            {
                "delegation_only": True,
                "bogus_field": "garbage",
                "another_unknown": 99,
            }
        )
        self.assertTrue(g.delegation_only)
        self.assertFalse(hasattr(g, "bogus_field"))


class TestNormalizeTeamGovernanceLegacyPolicy(unittest.TestCase):
    def test_legacy_policy_used_when_governance_is_none(self):
        g = normalize_team_governance(
            None,
            legacy_policy={
                "delegation_only": True,
                "plan_approval_required": True,
            },
        )
        self.assertTrue(g.delegation_only)
        self.assertTrue(g.plan_approval_required)
        # Untouched fields use the canonical defaults.
        self.assertTrue(g.one_team_per_leader_session)
        self.assertTrue(g.cleanup_requires_all_workers_inactive)

    def test_governance_takes_precedence_over_legacy_policy(self):
        g = normalize_team_governance(
            {"delegation_only": False},
            legacy_policy={"delegation_only": True},
        )
        # governance wins (its delegation_only is False / missing-equivalent)
        self.assertFalse(g.delegation_only)

    def test_both_none_returns_defaults(self):
        g = normalize_team_governance(None, legacy_policy=None)
        self.assertEqual(g, TeamGovernance())


class TestTeamPolicyDictRoundTrip(unittest.TestCase):
    def test_to_dict_from_dict_round_trip(self):
        original = TeamPolicy(
            display_mode=TeamDisplayMode.SPLIT_PANE,
            worker_launch_mode=TeamWorkerLaunchMode.PROMPT,
            dispatch_mode=TeamDispatchMode.TRANSPORT_DIRECT,
            dispatch_ack_timeout_ms=4000,
        )
        restored = TeamPolicy.from_dict(original.to_dict())
        self.assertEqual(restored, original)


class TestTeamGovernanceDictRoundTrip(unittest.TestCase):
    def test_to_dict_from_dict_round_trip(self):
        original = TeamGovernance(
            delegation_only=True,
            plan_approval_required=True,
            nested_teams_allowed=True,
            one_team_per_leader_session=False,
            cleanup_requires_all_workers_inactive=False,
        )
        restored = TeamGovernance.from_dict(original.to_dict())
        self.assertEqual(restored, original)


if __name__ == "__main__":
    unittest.main()
