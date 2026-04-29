"""Tests for small modules (Batch 8)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omx.planning.artifacts import (
    PlanningArtifacts,
    is_planning_complete,
    read_planning_artifacts,
    select_latest_planning_artifacts,
)
from omx.session_history.search import (
    SessionSearchOptions,
    parse_since_spec,
)
from omx.subagents.tracker import (
    SUBAGENT_TRACKING_SCHEMA_VERSION,
    RecordSubagentTurnInput,
    create_subagent_tracking_state,
    normalize_subagent_tracking_state,
    record_subagent_turn,
    summarize_subagent_session,
    read_subagent_tracking_state,
    write_subagent_tracking_state,
)
from omx.document_refresh.config import (
    DEFAULT_DOCUMENT_REFRESH_RULES,
)
from omx.document_refresh.enforcer import (
    ChangedPathRecord,
    DocumentRefreshEvaluationInput,
    glob_to_regexp,
    has_document_refresh_exemption,
    is_final_handoff_document_refresh_candidate,
    parse_git_name_status,
    path_matches_glob,
    evaluate_document_refresh,
    DOCUMENT_REFRESH_EXEMPTION_PREFIX,
)
from omx.visual.constants import (
    VISUAL_NEXT_ACTIONS_LIMIT,
    VISUAL_VERDICT_STATUSES,
    VisualVerdictStatus,
)
from omx.visual.verdict import (
    parse_visual_verdict,
    build_visual_loop_feedback,
)


class TestPlanningArtifacts(unittest.TestCase):
    """Tests for planning artifacts."""

    def test_read_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            artifacts = read_planning_artifacts(tmpdir)
            self.assertEqual(artifacts.prd_paths, [])
            self.assertEqual(artifacts.test_spec_paths, [])

    def test_is_planning_complete_no(self) -> None:
        artifacts = PlanningArtifacts()
        self.assertFalse(is_planning_complete(artifacts))

    def test_is_planning_complete_yes(self) -> None:
        artifacts = PlanningArtifacts(
            prd_paths=["/a/prd-test.md"],
            test_spec_paths=["/a/test-spec-test.md"],
        )
        self.assertTrue(is_planning_complete(artifacts))

    def test_read_with_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            plans_dir = Path(tmpdir) / ".omx" / "plans"
            plans_dir.mkdir(parents=True)
            (plans_dir / "prd-feature-x.md").write_text("PRD content")
            (plans_dir / "test-spec-feature-x.md").write_text("Test spec")
            artifacts = read_planning_artifacts(tmpdir)
            self.assertEqual(len(artifacts.prd_paths), 1)
            self.assertEqual(len(artifacts.test_spec_paths), 1)

    def test_select_latest_slug_matching(self) -> None:
        artifacts = PlanningArtifacts(
            prd_paths=["/a/prd-alpha.md", "/a/prd-beta.md"],
            test_spec_paths=["/a/test-spec-alpha.md", "/a/test-spec-beta.md"],
        )
        selection = select_latest_planning_artifacts(artifacts)
        self.assertEqual(selection.prd_path, "/a/prd-beta.md")
        self.assertEqual(selection.test_spec_paths, ["/a/test-spec-beta.md"])


class TestSessionHistorySearch(unittest.TestCase):
    """Tests for session history search."""

    def test_parse_since_duration(self) -> None:
        now = 1000000000
        cutoff = parse_since_spec("7d", now)
        self.assertIsNotNone(cutoff)
        self.assertEqual(cutoff, now - 7 * 86_400_000)

    def test_parse_since_hours(self) -> None:
        now = 1000000000
        cutoff = parse_since_spec("24h", now)
        self.assertEqual(cutoff, now - 24 * 3_600_000)

    def test_parse_since_none(self) -> None:
        self.assertIsNone(parse_since_spec(None))
        self.assertIsNone(parse_since_spec(""))

    def test_parse_since_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_since_spec("not-a-date")

    def test_search_empty_query(self) -> None:
        from omx.session_history.search import search_session_history

        with self.assertRaises(ValueError):
            search_session_history(SessionSearchOptions(query=""))

    def test_search_no_results(self) -> None:
        from omx.session_history.search import search_session_history

        with tempfile.TemporaryDirectory() as tmpdir:
            report = search_session_history(
                SessionSearchOptions(
                    query="nonexistent",
                    codex_home_dir=tmpdir,
                )
            )
            self.assertEqual(report.results, [])
            self.assertEqual(report.searched_files, 0)


class TestSubagentTracker(unittest.TestCase):
    """Tests for subagent tracker."""

    def test_create_state(self) -> None:
        state = create_subagent_tracking_state()
        self.assertEqual(state.schema_version, SUBAGENT_TRACKING_SCHEMA_VERSION)
        self.assertEqual(state.sessions, {})

    def test_normalize_empty(self) -> None:
        state = normalize_subagent_tracking_state(None)
        self.assertEqual(state.sessions, {})

    def test_normalize_invalid(self) -> None:
        state = normalize_subagent_tracking_state("not a dict")
        self.assertEqual(state.sessions, {})

    def test_record_turn_new_session(self) -> None:
        state = create_subagent_tracking_state()
        result = record_subagent_turn(
            state,
            RecordSubagentTurnInput(
                session_id="sess1",
                thread_id="thread1",
                timestamp="2025-01-01T00:00:00Z",
            ),
        )
        self.assertIn("sess1", result.sessions)
        session = result.sessions["sess1"]
        self.assertEqual(session.leader_thread_id, "thread1")
        self.assertIn("thread1", session.threads)
        self.assertEqual(session.threads["thread1"].kind, "leader")
        self.assertEqual(session.threads["thread1"].turn_count, 1)

    def test_record_turn_subagent(self) -> None:
        state = create_subagent_tracking_state()
        state = record_subagent_turn(
            state,
            RecordSubagentTurnInput(
                session_id="sess1",
                thread_id="leader1",
                timestamp="2025-01-01T00:00:00Z",
            ),
        )
        state = record_subagent_turn(
            state,
            RecordSubagentTurnInput(
                session_id="sess1",
                thread_id="sub1",
                timestamp="2025-01-01T00:01:00Z",
            ),
        )
        session = state.sessions["sess1"]
        self.assertEqual(session.threads["leader1"].kind, "leader")
        self.assertEqual(session.threads["sub1"].kind, "subagent")

    def test_read_write_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            state = create_subagent_tracking_state()
            state = record_subagent_turn(
                state,
                RecordSubagentTurnInput(
                    session_id="s1",
                    thread_id="t1",
                    timestamp="2025-01-01T00:00:00Z",
                ),
            )
            path = write_subagent_tracking_state(tmpdir, state)
            self.assertTrue(Path(path).exists())

            loaded = read_subagent_tracking_state(tmpdir)
            self.assertIn("s1", loaded.sessions)

    def test_summarize_session(self) -> None:
        state = create_subagent_tracking_state()
        state = record_subagent_turn(
            state,
            RecordSubagentTurnInput(
                session_id="s1",
                thread_id="leader",
                timestamp="2025-01-01T00:00:00Z",
            ),
        )
        state = record_subagent_turn(
            state,
            RecordSubagentTurnInput(
                session_id="s1",
                thread_id="sub1",
                timestamp="2025-01-01T00:01:00Z",
            ),
        )
        summary = summarize_subagent_session(
            state,
            "s1",
            now="2025-01-01T00:01:30Z",
        )
        self.assertIsNotNone(summary)
        self.assertEqual(summary.session_id, "s1")
        self.assertEqual(summary.leader_thread_id, "leader")
        self.assertIn("sub1", summary.all_subagent_thread_ids)

    def test_summarize_missing_session(self) -> None:
        state = create_subagent_tracking_state()
        self.assertIsNone(summarize_subagent_session(state, "nonexistent"))


class TestDocumentRefreshConfig(unittest.TestCase):
    """Tests for document refresh configuration."""

    def test_default_rules_exist(self) -> None:
        self.assertTrue(len(DEFAULT_DOCUMENT_REFRESH_RULES) > 0)

    def test_rule_structure(self) -> None:
        rule = DEFAULT_DOCUMENT_REFRESH_RULES[0]
        self.assertTrue(len(rule.id) > 0)
        self.assertTrue(len(rule.source_globs) > 0)
        self.assertTrue(len(rule.refresh_targets) > 0)


class TestDocumentRefreshEnforcer(unittest.TestCase):
    """Tests for document refresh enforcer."""

    def test_glob_to_regexp_wildcard(self) -> None:
        pattern = glob_to_regexp("src/*.ts")
        self.assertTrue(pattern.match("src/file.ts"))
        self.assertFalse(pattern.match("src/sub/file.ts"))

    def test_glob_to_regexp_double_star(self) -> None:
        pattern = glob_to_regexp("src/**/*.ts")
        self.assertTrue(pattern.match("src/sub/file.ts"))
        self.assertTrue(pattern.match("src/file.ts"))

    def test_path_matches_glob(self) -> None:
        self.assertTrue(path_matches_glob("src/foo.ts", "src/*.ts"))
        self.assertFalse(path_matches_glob("other/foo.ts", "src/*.ts"))

    def test_has_exemption(self) -> None:
        self.assertTrue(
            has_document_refresh_exemption(
                f"{DOCUMENT_REFRESH_EXEMPTION_PREFIX} no doc changes needed"
            )
        )
        self.assertFalse(has_document_refresh_exemption("just a normal message"))
        self.assertFalse(has_document_refresh_exemption(None))

    def test_parse_git_name_status(self) -> None:
        output = "M\tsrc/foo.ts\nA\tsrc/bar.ts\nR100\told.ts\tnew.ts\n"
        records = parse_git_name_status(output)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0].status, "M")
        self.assertEqual(records[0].path, "src/foo.ts")
        self.assertEqual(records[2].status, "R100")
        self.assertEqual(records[2].previous_path, "old.ts")

    def test_is_final_handoff(self) -> None:
        self.assertTrue(is_final_handoff_document_refresh_candidate("task complete"))
        self.assertTrue(is_final_handoff_document_refresh_candidate("ready to merge"))
        self.assertFalse(is_final_handoff_document_refresh_candidate("just doing work"))
        self.assertFalse(is_final_handoff_document_refresh_candidate(None))

    def test_evaluate_no_changes(self) -> None:
        result = evaluate_document_refresh(
            DocumentRefreshEvaluationInput(
                scope="commit",
                changes=[],
            )
        )
        self.assertIsNone(result)

    def test_evaluate_with_exemption(self) -> None:
        result = evaluate_document_refresh(
            DocumentRefreshEvaluationInput(
                scope="commit",
                changes=[ChangedPathRecord(status="M", path="src/foo.ts")],
                exemption_text=f"{DOCUMENT_REFRESH_EXEMPTION_PREFIX} not needed",
            )
        )
        self.assertIsNone(result)

    def test_evaluate_trigger(self) -> None:
        result = evaluate_document_refresh(
            DocumentRefreshEvaluationInput(
                scope="commit",
                changes=[
                    ChangedPathRecord(
                        status="M", path="src/scripts/codex-native-hook.ts"
                    )
                ],
            )
        )
        # Should trigger native-hook-behavior rule
        self.assertIsNotNone(result)
        self.assertEqual(result.scope, "commit")
        self.assertTrue(len(result.rules) > 0)


class TestVisualConstants(unittest.TestCase):
    """Tests for visual constants."""

    def test_next_actions_limit(self) -> None:
        self.assertEqual(VISUAL_NEXT_ACTIONS_LIMIT, 5)

    def test_verdict_statuses(self) -> None:
        self.assertEqual(VISUAL_VERDICT_STATUSES, ("pass", "revise", "fail"))

    def test_verdict_status_enum(self) -> None:
        self.assertEqual(VisualVerdictStatus.PASS, "pass")
        self.assertEqual(VisualVerdictStatus.FAIL, "fail")


class TestVisualVerdict(unittest.TestCase):
    """Tests for visual verdict parsing."""

    def _valid_input(self) -> dict:
        return {
            "score": 85,
            "verdict": "pass",
            "category_match": True,
            "differences": ["color mismatch"],
            "suggestions": ["adjust brightness"],
            "reasoning": "Close enough.",
        }

    def test_parse_valid(self) -> None:
        verdict = parse_visual_verdict(self._valid_input())
        self.assertEqual(verdict.score, 85)
        self.assertEqual(verdict.verdict, "pass")
        self.assertTrue(verdict.category_match)
        self.assertEqual(len(verdict.differences), 1)
        self.assertEqual(verdict.reasoning, "Close enough.")

    def test_parse_invalid_score(self) -> None:
        data = self._valid_input()
        data["score"] = 150
        with self.assertRaises(ValueError):
            parse_visual_verdict(data)

    def test_parse_invalid_verdict(self) -> None:
        data = self._valid_input()
        data["verdict"] = "unknown"
        with self.assertRaises(ValueError):
            parse_visual_verdict(data)

    def test_parse_not_object(self) -> None:
        with self.assertRaises(ValueError):
            parse_visual_verdict("not an object")

    def test_parse_missing_reasoning(self) -> None:
        data = self._valid_input()
        data["reasoning"] = ""
        with self.assertRaises(ValueError):
            parse_visual_verdict(data)

    def test_build_loop_feedback_passes(self) -> None:
        data = self._valid_input()
        data["score"] = 95
        feedback = build_visual_loop_feedback(data, threshold=90)
        self.assertTrue(feedback.passes_threshold)
        self.assertEqual(feedback.threshold, 90)

    def test_build_loop_feedback_fails(self) -> None:
        data = self._valid_input()
        data["score"] = 50
        feedback = build_visual_loop_feedback(data, threshold=90)
        self.assertFalse(feedback.passes_threshold)

    def test_loop_feedback_next_actions(self) -> None:
        data = self._valid_input()
        feedback = build_visual_loop_feedback(data)
        # Should include suggestions + fix: differences
        self.assertTrue(len(feedback.next_actions) > 0)
        self.assertTrue(len(feedback.next_actions) <= VISUAL_NEXT_ACTIONS_LIMIT)


class TestWorkflowTransitionReconcile(unittest.TestCase):
    """Tests for workflow transition reconcile."""

    def test_import(self) -> None:
        from omx.state.workflow_transition_reconcile import (
            ReconciledWorkflowTransition,
        )

        self.assertIsNotNone(ReconciledWorkflowTransition)

    def test_reconcile_no_active_modes(self) -> None:
        from omx.state.workflow_transition_reconcile import (
            reconcile_workflow_transition,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            result = reconcile_workflow_transition(
                tmpdir,
                "ralph",
                current_modes=[],
            )
            self.assertTrue(result.decision.allowed)
            self.assertEqual(result.auto_completed_modes, [])


if __name__ == "__main__":
    unittest.main()
