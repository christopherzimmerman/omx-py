"""Tests for omx.hooks.triage_heuristic.

Covers each of the 11 rules in `triage_prompt` plus the public dataclass.
"""

from __future__ import annotations

import unittest

from omx.hooks.triage_heuristic import (
    ANCHORED_EDIT_WORD_LIMIT,
    HEAVY_WORD_THRESHOLD,
    SHORT_QUESTION_WORD_LIMIT,
    TriageDecision,
    triage_prompt,
)


class TestTriageDecisionDataclass(unittest.TestCase):
    def test_is_frozen(self) -> None:
        d = TriageDecision(lane="PASS", reason="x")
        with self.assertRaises(Exception):
            d.lane = "HEAVY"  # type: ignore[misc]

    def test_default_destination_is_none(self) -> None:
        d = TriageDecision(lane="PASS", reason="x")
        self.assertIsNone(d.destination)


class TestRule1EmptyAndTrivial(unittest.TestCase):
    def test_empty_string(self) -> None:
        d = triage_prompt("")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "empty_input")

    def test_whitespace_only(self) -> None:
        d = triage_prompt("   \n\t  ")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "empty_input")

    def test_trivial_hi(self) -> None:
        d = triage_prompt("hi")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "trivial_acknowledgement")

    def test_trivial_thanks_with_period(self) -> None:
        d = triage_prompt("thanks.")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "trivial_acknowledgement")

    def test_trivial_sounds_good(self) -> None:
        d = triage_prompt("sounds good")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "trivial_acknowledgement")


class TestRule2OptOut(unittest.TestCase):
    def test_just_chat(self) -> None:
        d = triage_prompt("just chat with me about python decorators")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "explicit_opt_out")

    def test_plain_answer(self) -> None:
        d = triage_prompt("give me a plain answer to my question")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "explicit_opt_out")

    def test_no_workflow(self) -> None:
        d = triage_prompt("no workflow please, just respond")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "explicit_opt_out")

    def test_dont_route(self) -> None:
        d = triage_prompt("don't route this, talk directly")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "explicit_opt_out")


class TestRule3QuestionExplanation(unittest.TestCase):
    def test_explain_starter(self) -> None:
        d = triage_prompt("explain how the auth middleware works")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "explore")
        self.assertEqual(d.reason, "question_or_explanation")

    def test_what_starter(self) -> None:
        d = triage_prompt("what does this function do")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "explore")

    def test_tell_me_about(self) -> None:
        d = triage_prompt("tell me about the dispatcher")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "explore")

    def test_short_question_with_q_mark(self) -> None:
        d = triage_prompt("does this support utf-8?")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "explore")

    def test_external_lookup_question_routes_to_researcher_not_explore(self) -> None:
        # A "?"-ending prompt that also has external research signals + lookup
        # verb + external override (npm) should NOT take the explore path.
        d = triage_prompt("can you check the npm release notes for next?")
        self.assertNotEqual(d.destination, "explore")


class TestRule4AnchoredEdit(unittest.TestCase):
    def test_src_path_anchor(self) -> None:
        d = triage_prompt("fix the bug in src/foo/bar.ts")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "executor")
        self.assertEqual(d.reason, "anchored_edit")

    def test_line_number_anchor(self) -> None:
        d = triage_prompt("update the comment at line 42")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "executor")

    def test_fix_typo_in(self) -> None:
        d = triage_prompt("fix typo in the README header")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "executor")

    def test_rename_in(self) -> None:
        d = triage_prompt("rename foo to bar in main")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "executor")


class TestRule5LocalReferenceLookup(unittest.TestCase):
    def test_find_in_repo(self) -> None:
        d = triage_prompt("find every caller of foo in this repo")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "explore")
        self.assertEqual(d.reason, "local_reference_lookup")

    def test_search_codebase(self) -> None:
        d = triage_prompt("search the codebase for TODO comments")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "explore")


class TestRule6ImplementationResearchGoal(unittest.TestCase):
    def test_implement_using_external_docs(self) -> None:
        d = triage_prompt(
            "implement a new caching layer using the official Redis api docs"
        )
        self.assertEqual(d.lane, "HEAVY")
        self.assertEqual(d.destination, "autopilot")
        self.assertEqual(d.reason, "implementation_research_goal")

    def test_refactor_after_research(self) -> None:
        d = triage_prompt(
            "refactor the auth flow after you research the latest oauth changelog"
        )
        self.assertEqual(d.lane, "HEAVY")
        self.assertEqual(d.destination, "autopilot")


class TestRule7ExternalReferenceResearch(unittest.TestCase):
    def test_look_up_official_docs(self) -> None:
        d = triage_prompt("look up the official docs for the new API version")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "researcher")
        self.assertEqual(d.reason, "external_reference_research")

    def test_check_changelog(self) -> None:
        d = triage_prompt("check the changelog for breaking changes")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "researcher")

    def test_research_tech_subject_and_need(self) -> None:
        d = triage_prompt("research how the fetch api lifecycle behavior works")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "researcher")


class TestRule8StructuralRedesign(unittest.TestCase):
    def test_redesign_auth(self) -> None:
        d = triage_prompt("redesign the authentication flow end to end")
        self.assertEqual(d.lane, "HEAVY")
        self.assertEqual(d.destination, "autopilot")
        self.assertEqual(d.reason, "structural_redesign_goal")

    def test_redesign_deployment_pipeline(self) -> None:
        d = triage_prompt("redesign the deployment pipeline for staging")
        self.assertEqual(d.lane, "HEAVY")
        self.assertEqual(d.destination, "autopilot")


class TestRule9VisualStyling(unittest.TestCase):
    def test_make_the_button(self) -> None:
        d = triage_prompt("make the button blue")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "designer")
        self.assertEqual(d.reason, "visual_styling_prompt")

    def test_change_the_color(self) -> None:
        d = triage_prompt("change the color of the header")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "designer")

    def test_redesign_with_visual_terms_is_designer(self) -> None:
        d = triage_prompt("redesign the header layout component")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "designer")


class TestRule10LongImperative(unittest.TestCase):
    def test_implement_long(self) -> None:
        d = triage_prompt(
            "implement a new caching layer with proper invalidation logic"
        )
        self.assertEqual(d.lane, "HEAVY")
        self.assertEqual(d.destination, "autopilot")
        self.assertEqual(d.reason, "long_imperative_goal")

    def test_build_long(self) -> None:
        d = triage_prompt("build a complete onboarding flow for new users today")
        self.assertEqual(d.lane, "HEAVY")
        self.assertEqual(d.destination, "autopilot")

    def test_refactor_long(self) -> None:
        d = triage_prompt(
            "refactor the entire team runtime to remove the legacy adapters now"
        )
        self.assertEqual(d.lane, "HEAVY")


class TestRule11FallbackPass(unittest.TestCase):
    def test_ambiguous_short_prompt(self) -> None:
        d = triage_prompt("foo bar baz")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "ambiguous_short_prompt")

    def test_short_imperative_does_not_trip_heavy(self) -> None:
        # Five words or fewer with imperative verb should NOT be HEAVY (rule 10
        # requires > HEAVY_WORD_THRESHOLD), and lacking anchors lands on PASS.
        d = triage_prompt("add new feature now")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "ambiguous_short_prompt")


class TestThresholdConstants(unittest.TestCase):
    def test_thresholds_match_ts(self) -> None:
        self.assertEqual(HEAVY_WORD_THRESHOLD, 5)
        self.assertEqual(SHORT_QUESTION_WORD_LIMIT, 10)
        self.assertEqual(ANCHORED_EDIT_WORD_LIMIT, 15)


class TestRulePrecedence(unittest.TestCase):
    """Rule order matters; earlier rules short-circuit later ones."""

    def test_opt_out_beats_explore_starter(self) -> None:
        d = triage_prompt("explain how this works but just chat about it")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "explicit_opt_out")

    def test_trivial_beats_everything(self) -> None:
        d = triage_prompt("ok")
        self.assertEqual(d.lane, "PASS")
        self.assertEqual(d.reason, "trivial_acknowledgement")

    def test_question_beats_imperative(self) -> None:
        # "explain " starter wins even though "implement" appears in body.
        d = triage_prompt("explain how to implement a caching layer")
        self.assertEqual(d.lane, "LIGHT")
        self.assertEqual(d.destination, "explore")


if __name__ == "__main__":
    unittest.main()
