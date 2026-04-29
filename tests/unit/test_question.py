"""Tests for the question/interview UI system."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from omx.question.types import (
    AnswerKind,
    QuestionAnswer,
    QuestionInput,
    QuestionOption,
    QuestionRecord,
    QuestionRendererState,
    QuestionStatus,
    QuestionType,
    get_normalized_question_type,
    is_multi_answerable_question,
    normalize_question_input,
)
from omx.question.state import (
    create_question_record,
    get_question_record_path,
    get_question_state_dir,
    is_terminal_question_status,
    mark_question_answered,
    mark_question_prompting,
    mark_question_terminal_error,
    read_question_record,
)
from omx.question.policy import (
    evaluate_question_policy,
)
from omx.question.renderer import (
    QuestionRendererStrategy,
    format_question_answer_for_injection,
    resolve_question_renderer_strategy,
)
from omx.question.ui import (
    _build_answer,
    _get_option_labels,
    _parse_selection,
    _render_options,
    prompt_for_selections,
    run_question_ui,
)
from omx.question.deep_interview import (
    clear_deep_interview_question_obligation,
    create_deep_interview_question_obligation,
    is_pending_deep_interview_question_enforcement,
    satisfy_deep_interview_question_obligation,
)
from omx.question.client import (
    OmxQuestionError,
    _parse_question_stdout,
)


class TestQuestionTypes(unittest.TestCase):
    """Tests for question type definitions and normalisation."""

    def test_question_type_enum(self) -> None:
        self.assertEqual(QuestionType.SINGLE, "single-answerable")
        self.assertEqual(QuestionType.MULTI, "multi-answerable")

    def test_question_status_enum(self) -> None:
        self.assertEqual(QuestionStatus.PENDING, "pending")
        self.assertEqual(QuestionStatus.ANSWERED, "answered")

    def test_get_normalized_question_type_defaults_single(self) -> None:
        self.assertEqual(get_normalized_question_type(), QuestionType.SINGLE)

    def test_get_normalized_question_type_multi_select(self) -> None:
        self.assertEqual(
            get_normalized_question_type(multi_select=True), QuestionType.MULTI
        )

    def test_get_normalized_question_type_explicit(self) -> None:
        self.assertEqual(
            get_normalized_question_type(QuestionType.MULTI), QuestionType.MULTI
        )

    def test_is_multi_answerable_question(self) -> None:
        self.assertTrue(is_multi_answerable_question(multi_select=True))
        self.assertFalse(is_multi_answerable_question())

    def test_normalize_question_input_valid(self) -> None:
        raw = {
            "question": "Pick one",
            "options": ["A", "B"],
        }
        result = normalize_question_input(raw)
        self.assertEqual(result.question, "Pick one")
        self.assertEqual(len(result.options), 2)
        self.assertEqual(result.options[0].label, "A")
        self.assertTrue(result.allow_other)
        self.assertEqual(result.other_label, "Other")

    def test_normalize_question_input_empty_question(self) -> None:
        with self.assertRaises(ValueError):
            normalize_question_input({"question": ""})

    def test_normalize_question_input_not_dict(self) -> None:
        with self.assertRaises(ValueError):
            normalize_question_input("not a dict")

    def test_normalize_question_input_conflict_single_multi(self) -> None:
        with self.assertRaises(ValueError):
            normalize_question_input(
                {
                    "question": "X",
                    "options": ["A"],
                    "type": "single-answerable",
                    "multi_select": True,
                }
            )

    def test_normalize_option_object(self) -> None:
        raw = {
            "question": "Pick",
            "options": [{"label": "Foo", "value": "foo", "description": "A foo"}],
        }
        result = normalize_question_input(raw)
        self.assertEqual(result.options[0].label, "Foo")
        self.assertEqual(result.options[0].value, "foo")
        self.assertEqual(result.options[0].description, "A foo")

    def test_normalize_no_options_no_other(self) -> None:
        with self.assertRaises(ValueError):
            normalize_question_input(
                {
                    "question": "X",
                    "options": [],
                    "allow_other": False,
                }
            )

    def test_question_option_serialise(self) -> None:
        opt = QuestionOption(label="A", value="a", description="desc")
        d = opt.to_dict()
        self.assertEqual(d["label"], "A")
        restored = QuestionOption.from_dict(d)
        self.assertEqual(restored.label, "A")
        self.assertEqual(restored.description, "desc")

    def test_question_answer_serialise(self) -> None:
        ans = QuestionAnswer(
            kind=AnswerKind.OPTION,
            value="x",
            selected_labels=["X"],
            selected_values=["x"],
        )
        d = ans.to_dict()
        self.assertEqual(d["kind"], "option")
        restored = QuestionAnswer.from_dict(d)
        self.assertEqual(restored.kind, AnswerKind.OPTION)

    def test_question_record_serialise(self) -> None:
        rec = QuestionRecord(
            question_id="q-1",
            question="What?",
            options=[QuestionOption(label="A", value="a")],
            status=QuestionStatus.PENDING,
        )
        d = rec.to_dict()
        self.assertEqual(d["question_id"], "q-1")
        restored = QuestionRecord.from_dict(d)
        self.assertEqual(restored.question_id, "q-1")
        self.assertEqual(restored.status, QuestionStatus.PENDING)

    def test_renderer_state_serialise(self) -> None:
        rs = QuestionRendererState(
            renderer="tmux-pane", target="%1", launched_at="2025-01-01T00:00:00Z"
        )
        d = rs.to_dict()
        restored = QuestionRendererState.from_dict(d)
        self.assertEqual(restored.renderer, "tmux-pane")
        self.assertEqual(restored.target, "%1")


class TestQuestionState(unittest.TestCase):
    """Tests for question state persistence."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_and_read_question_record(self) -> None:
        qi = QuestionInput(question="Pick one", options=[QuestionOption("A", "a")])
        path, record = create_question_record(self.tmpdir, qi)
        self.assertTrue(path.exists())
        self.assertEqual(record.status, QuestionStatus.PENDING)

        loaded = read_question_record(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.question, "Pick one")

    def test_mark_question_answered(self) -> None:
        qi = QuestionInput(question="Test?")
        path, _ = create_question_record(self.tmpdir, qi)
        answer = QuestionAnswer(
            kind=AnswerKind.OPTION,
            value="yes",
            selected_labels=["Yes"],
            selected_values=["yes"],
        )
        updated = mark_question_answered(path, answer)
        self.assertEqual(updated.status, QuestionStatus.ANSWERED)
        self.assertIsNotNone(updated.answer)

    def test_mark_question_prompting(self) -> None:
        qi = QuestionInput(question="Test?")
        path, _ = create_question_record(self.tmpdir, qi)
        renderer = QuestionRendererState(
            renderer="inline-tty", target="inline-tty", launched_at="now"
        )
        updated = mark_question_prompting(path, renderer)
        self.assertEqual(updated.status, QuestionStatus.PROMPTING)
        self.assertIsNotNone(updated.renderer)

    def test_mark_question_terminal_error(self) -> None:
        qi = QuestionInput(question="Test?")
        path, _ = create_question_record(self.tmpdir, qi)
        updated = mark_question_terminal_error(
            path, QuestionStatus.ERROR, "test_code", "test msg"
        )
        self.assertEqual(updated.status, QuestionStatus.ERROR)
        self.assertEqual(updated.error["code"], "test_code")

    def test_is_terminal_question_status(self) -> None:
        self.assertTrue(is_terminal_question_status(QuestionStatus.ANSWERED))
        self.assertTrue(is_terminal_question_status(QuestionStatus.ABORTED))
        self.assertTrue(is_terminal_question_status(QuestionStatus.ERROR))
        self.assertFalse(is_terminal_question_status(QuestionStatus.PENDING))
        self.assertFalse(is_terminal_question_status(QuestionStatus.PROMPTING))

    def test_read_nonexistent_record(self) -> None:
        path = Path(self.tmpdir) / "nonexistent.json"
        self.assertIsNone(read_question_record(path))

    def test_get_question_state_dir(self) -> None:
        d = get_question_state_dir(self.tmpdir)
        self.assertTrue(str(d).endswith("questions"))

    def test_get_question_record_path(self) -> None:
        p = get_question_record_path(self.tmpdir, "q-123")
        self.assertTrue(str(p).endswith("q-123.json"))


class TestQuestionPolicy(unittest.TestCase):
    """Tests for question policy evaluation."""

    def test_allowed_default(self) -> None:
        decision = evaluate_question_policy("/tmp", env={})
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.fallback_allowed)

    def test_worker_blocked(self) -> None:
        decision = evaluate_question_policy("/tmp", env={"OMX_TEAM_WORKER": "true"})
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "worker_blocked")

    def test_team_blocked(self) -> None:
        decision = evaluate_question_policy(
            "/tmp",
            env={},
            active_teams=[{"teamName": "core", "phase": "exec"}],
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "team_blocked")

    def test_execution_mode_blocked(self) -> None:
        decision = evaluate_question_policy(
            "/tmp",
            env={},
            active_modes=["autopilot"],
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "active_execution_mode_blocked")


class TestQuestionRenderer(unittest.TestCase):
    """Tests for question renderer strategy and formatting."""

    def test_resolve_test_noop(self) -> None:
        strategy = resolve_question_renderer_strategy(
            env={"OMX_QUESTION_TEST_RENDERER": "noop"}
        )
        self.assertEqual(strategy, QuestionRendererStrategy.TEST_NOOP)

    def test_resolve_inside_tmux(self) -> None:
        strategy = resolve_question_renderer_strategy(
            env={"TMUX": "/tmp/tmux-1000/default,12345,0"}
        )
        self.assertEqual(strategy, QuestionRendererStrategy.INSIDE_TMUX)

    def test_resolve_unsupported(self) -> None:
        strategy = resolve_question_renderer_strategy(env={})
        self.assertEqual(strategy, QuestionRendererStrategy.UNSUPPORTED)

    def test_format_answer_option(self) -> None:
        ans = QuestionAnswer(
            kind=AnswerKind.OPTION,
            value="yes",
            selected_labels=["Yes"],
            selected_values=["yes"],
        )
        text = format_question_answer_for_injection(ans)
        self.assertIn("[omx question answered]", text)
        self.assertIn("yes", text)

    def test_format_answer_multi(self) -> None:
        ans = QuestionAnswer(
            kind=AnswerKind.MULTI,
            value=["a", "b"],
            selected_labels=["A", "B"],
            selected_values=["a", "b"],
        )
        text = format_question_answer_for_injection(ans)
        self.assertIn("a, b", text)

    def test_format_answer_other(self) -> None:
        ans = QuestionAnswer(
            kind=AnswerKind.OTHER,
            value="custom",
            selected_labels=["Other"],
            selected_values=["custom"],
            other_text="my custom text",
        )
        text = format_question_answer_for_injection(ans)
        self.assertIn("my custom text", text)


class TestQuestionUI(unittest.TestCase):
    """Tests for the interactive question UI."""

    def _make_record(self, **kwargs) -> QuestionRecord:
        defaults = {
            "question_id": "q-1",
            "question": "Pick?",
            "options": [
                QuestionOption("A", "a"),
                QuestionOption("B", "b"),
            ],
            "allow_other": True,
            "other_label": "Other",
            "multi_select": False,
        }
        defaults.update(kwargs)
        return QuestionRecord(**defaults)

    def test_get_option_labels(self) -> None:
        rec = self._make_record()
        labels = _get_option_labels(rec)
        self.assertEqual(len(labels), 3)  # 2 options + other
        self.assertIn("1. A", labels[0])
        self.assertIn("3. Other", labels[2])

    def test_get_option_labels_no_other(self) -> None:
        rec = self._make_record(allow_other=False)
        labels = _get_option_labels(rec)
        self.assertEqual(len(labels), 2)

    def test_render_options(self) -> None:
        rec = self._make_record()
        lines = _render_options(rec)
        self.assertTrue(all(line.startswith("  [ ]") for line in lines))

    def test_parse_selection_valid(self) -> None:
        self.assertEqual(_parse_selection("1", 3, False), [1])
        self.assertEqual(_parse_selection("2", 3, False), [2])
        self.assertIsNone(_parse_selection("", 3, False))
        self.assertIsNone(_parse_selection("0", 3, False))
        self.assertIsNone(_parse_selection("4", 3, False))

    def test_parse_selection_multi(self) -> None:
        self.assertEqual(_parse_selection("1,2", 3, True), [1, 2])
        self.assertEqual(_parse_selection("1,1,2", 3, True), [1, 2])

    def test_build_answer_single(self) -> None:
        rec = self._make_record()
        ans = _build_answer(rec, [1])
        self.assertEqual(ans.kind, AnswerKind.OPTION)
        self.assertEqual(ans.value, "a")

    def test_build_answer_other(self) -> None:
        rec = self._make_record()
        ans = _build_answer(rec, [3], other_text="custom")
        self.assertEqual(ans.kind, AnswerKind.OTHER)
        self.assertEqual(ans.other_text, "custom")

    def test_build_answer_multi(self) -> None:
        rec = self._make_record(multi_select=True, question_type=QuestionType.MULTI)
        ans = _build_answer(rec, [1, 2])
        self.assertEqual(ans.kind, AnswerKind.MULTI)
        self.assertIsInstance(ans.value, list)

    def test_prompt_for_selections(self) -> None:
        rec = self._make_record()
        inputs = iter(["1"])
        result = prompt_for_selections(
            rec,
            input_fn=lambda _: next(inputs),
            output=open(os.devnull, "w"),
        )
        self.assertEqual(result, [1])

    def test_prompt_retries_on_invalid(self) -> None:
        rec = self._make_record()
        inputs = iter(["bad", "0", "1"])
        result = prompt_for_selections(
            rec,
            input_fn=lambda _: next(inputs),
            output=open(os.devnull, "w"),
        )
        self.assertEqual(result, [1])

    def test_run_question_ui_full(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            qi = QuestionInput(
                question="Test?",
                options=[QuestionOption("Yes", "yes"), QuestionOption("No", "no")],
            )
            path, _ = create_question_record(tmpdir, qi)
            inputs = iter(["1"])
            run_question_ui(
                str(path),
                input_fn=lambda _: next(inputs),
                output=open(os.devnull, "w"),
            )
            final = read_question_record(path)
            self.assertEqual(final.status, QuestionStatus.ANSWERED)
            self.assertEqual(final.answer.value, "yes")
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


class TestDeepInterview(unittest.TestCase):
    """Tests for the deep interview workflow."""

    def test_create_obligation(self) -> None:
        obl = create_deep_interview_question_obligation()
        self.assertTrue(obl.obligation_id.startswith("deep-interview-question-"))
        self.assertEqual(obl.status, "pending")

    def test_is_pending_enforcement(self) -> None:
        obl = create_deep_interview_question_obligation()
        self.assertTrue(is_pending_deep_interview_question_enforcement(obl.to_dict()))
        self.assertFalse(is_pending_deep_interview_question_enforcement(None))
        self.assertFalse(is_pending_deep_interview_question_enforcement({}))

    def test_satisfy_obligation(self) -> None:
        obl = create_deep_interview_question_obligation()
        satisfied = satisfy_deep_interview_question_obligation(obl, "q-123")
        self.assertEqual(satisfied.status, "satisfied")
        self.assertEqual(satisfied.question_id, "q-123")
        self.assertIsNotNone(satisfied.satisfied_at)

    def test_clear_obligation_pending(self) -> None:
        obl = create_deep_interview_question_obligation()
        cleared = clear_deep_interview_question_obligation(obl, "abort")
        self.assertEqual(cleared.status, "cleared")
        self.assertEqual(cleared.clear_reason, "abort")

    def test_clear_obligation_none(self) -> None:
        self.assertIsNone(clear_deep_interview_question_obligation(None, "error"))

    def test_clear_obligation_already_satisfied(self) -> None:
        obl = create_deep_interview_question_obligation()
        obl.status = "satisfied"
        result = clear_deep_interview_question_obligation(obl, "abort")
        self.assertEqual(result.status, "satisfied")


class TestQuestionClient(unittest.TestCase):
    """Tests for question client parsing."""

    def test_parse_stdout_empty(self) -> None:
        with self.assertRaises(OmxQuestionError) as ctx:
            _parse_question_stdout("", "", 1)
        self.assertEqual(ctx.exception.code, "question_no_stdout")

    def test_parse_stdout_invalid_json(self) -> None:
        with self.assertRaises(OmxQuestionError) as ctx:
            _parse_question_stdout("not json", "", 1)
        self.assertEqual(ctx.exception.code, "question_invalid_stdout")

    def test_parse_stdout_valid(self) -> None:
        payload = {"ok": True, "question_id": "q-1"}
        result = _parse_question_stdout(json.dumps(payload), "", 0)
        self.assertTrue(result["ok"])
        self.assertEqual(result["question_id"], "q-1")


if __name__ == "__main__":
    unittest.main()
