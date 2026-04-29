"""Tests for the pipeline module."""

from __future__ import annotations

import unittest

from omx.pipeline.types import (
    PipelineConfig,
    PipelineModeStateExtension,
    PipelineStage,
    StageContext,
    StageResult,
)
from omx.pipeline.orchestrator import (
    create_autopilot_pipeline_config,
    run_pipeline,
    _validate_config,
)
from omx.pipeline.stages.ralplan import create_ralplan_stage, RalplanStage
from omx.pipeline.stages.team_exec import (
    create_team_exec_stage,
    build_team_instruction,
    TeamExecDescriptor,
    TeamExecStage,
)
from omx.pipeline.stages.ralph_verify import (
    create_ralph_verify_stage,
    build_ralph_instruction,
    RalphVerifyDescriptor,
    RalphVerifyStage,
)


class SimpleStage(PipelineStage):
    """Simple test stage."""

    def __init__(self, stage_name: str, result_status: str = "completed") -> None:
        self._name = stage_name
        self._result_status = result_status

    @property
    def name(self) -> str:
        return self._name

    def run(self, ctx: StageContext) -> StageResult:
        return StageResult(
            status=self._result_status,
            artifacts={"stage": self._name},
            duration_ms=1,
        )


class SkippableStage(SimpleStage):
    """Stage that can be skipped."""

    def can_skip(self, ctx: StageContext) -> bool:
        return True


class FailingStage(PipelineStage):
    """Stage that raises an exception."""

    @property
    def name(self) -> str:
        return "failing"

    def run(self, ctx: StageContext) -> StageResult:
        raise RuntimeError("Stage blew up")


class TestPipelineTypes(unittest.TestCase):
    """Tests for pipeline type dataclasses."""

    def test_stage_context(self) -> None:
        ctx = StageContext(task="test task", cwd="/tmp")
        self.assertEqual(ctx.task, "test task")
        self.assertEqual(ctx.artifacts, {})
        self.assertIsNone(ctx.previous_stage_result)

    def test_stage_result(self) -> None:
        result = StageResult(status="completed", duration_ms=100)
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.error)

    def test_pipeline_mode_state_extension(self) -> None:
        ext = PipelineModeStateExtension(pipeline_name="test")
        self.assertEqual(ext.pipeline_name, "test")
        self.assertEqual(ext.pipeline_max_ralph_iterations, 10)


class TestPipelineOrchestrator(unittest.TestCase):
    """Tests for pipeline orchestrator."""

    def test_validate_empty_name(self) -> None:
        config = PipelineConfig(name="", task="test", stages=[SimpleStage("a")])
        with self.assertRaises(ValueError):
            _validate_config(config)

    def test_validate_empty_task(self) -> None:
        config = PipelineConfig(name="test", task="", stages=[SimpleStage("a")])
        with self.assertRaises(ValueError):
            _validate_config(config)

    def test_validate_no_stages(self) -> None:
        config = PipelineConfig(name="test", task="task", stages=[])
        with self.assertRaises(ValueError):
            _validate_config(config)

    def test_validate_duplicate_stage_names(self) -> None:
        config = PipelineConfig(
            name="test",
            task="task",
            stages=[SimpleStage("a"), SimpleStage("a")],
        )
        with self.assertRaises(ValueError):
            _validate_config(config)

    def test_validate_invalid_ralph_iterations(self) -> None:
        config = PipelineConfig(
            name="test",
            task="task",
            stages=[SimpleStage("a")],
            max_ralph_iterations=-1,
        )
        with self.assertRaises(ValueError):
            _validate_config(config)

    def test_run_single_stage(self) -> None:
        config = PipelineConfig(
            name="test",
            task="do stuff",
            stages=[SimpleStage("alpha")],
        )
        result = run_pipeline(config)
        self.assertEqual(result.status, "completed")
        self.assertIn("alpha", result.stage_results)
        self.assertEqual(result.stage_results["alpha"].status, "completed")

    def test_run_multi_stage(self) -> None:
        config = PipelineConfig(
            name="test",
            task="do stuff",
            stages=[SimpleStage("a"), SimpleStage("b"), SimpleStage("c")],
        )
        result = run_pipeline(config)
        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.stage_results), 3)

    def test_run_with_skip(self) -> None:
        config = PipelineConfig(
            name="test",
            task="do stuff",
            stages=[SkippableStage("skipped"), SimpleStage("run")],
        )
        result = run_pipeline(config)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.stage_results["skipped"].status, "skipped")
        self.assertEqual(result.stage_results["run"].status, "completed")

    def test_run_with_failure(self) -> None:
        config = PipelineConfig(
            name="test",
            task="do stuff",
            stages=[
                SimpleStage("ok"),
                SimpleStage("fail", "failed"),
                SimpleStage("never"),
            ],
        )
        result = run_pipeline(config)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failed_stage, "fail")
        self.assertNotIn("never", result.stage_results)

    def test_run_with_exception(self) -> None:
        config = PipelineConfig(
            name="test",
            task="do stuff",
            stages=[FailingStage()],
        )
        result = run_pipeline(config)
        self.assertEqual(result.status, "failed")
        self.assertIn("Stage blew up", result.error or "")

    def test_stage_transition_callback(self) -> None:
        transitions: list[tuple[str, str]] = []
        config = PipelineConfig(
            name="test",
            task="do stuff",
            stages=[SimpleStage("a"), SimpleStage("b")],
            on_stage_transition=lambda f, t: transitions.append((f, t)),
        )
        run_pipeline(config)
        self.assertEqual(transitions, [("a", "b")])

    def test_create_autopilot_config(self) -> None:
        config = create_autopilot_pipeline_config(
            "build feature",
            stages=[SimpleStage("s")],
        )
        self.assertEqual(config.name, "autopilot")
        self.assertEqual(config.task, "build feature")
        self.assertEqual(config.max_ralph_iterations, 10)
        self.assertEqual(config.worker_count, 2)


class TestRalplanStage(unittest.TestCase):
    """Tests for the RALPLAN stage."""

    def test_create_ralplan_stage(self) -> None:
        stage = create_ralplan_stage()
        self.assertIsInstance(stage, RalplanStage)
        self.assertEqual(stage.name, "ralplan")

    def test_run_without_planning(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            stage = create_ralplan_stage()
            ctx = StageContext(task="test task", cwd=tmpdir)
            result = stage.run(ctx)
            self.assertEqual(result.status, "completed")
            self.assertEqual(result.artifacts.get("stage"), "ralplan")


class TestTeamExecStage(unittest.TestCase):
    """Tests for the team-exec stage."""

    def test_create_stage(self) -> None:
        stage = create_team_exec_stage(worker_count=4, agent_type="reviewer")
        self.assertIsInstance(stage, TeamExecStage)
        self.assertEqual(stage.name, "team-exec")

    def test_run(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            stage = create_team_exec_stage()
            ctx = StageContext(task="build it", cwd=tmpdir)
            result = stage.run(ctx)
            self.assertEqual(result.status, "completed")
            self.assertIn("team-exec", result.artifacts.get("stage", ""))

    def test_build_instruction(self) -> None:
        desc = TeamExecDescriptor(
            task="do thing", worker_count=3, agent_type="executor"
        )
        instruction = build_team_instruction(desc)
        self.assertIn("omx team 3:executor", instruction)


class TestRalphVerifyStage(unittest.TestCase):
    """Tests for the ralph-verify stage."""

    def test_create_stage(self) -> None:
        stage = create_ralph_verify_stage(max_iterations=5)
        self.assertIsInstance(stage, RalphVerifyStage)
        self.assertEqual(stage.name, "ralph-verify")

    def test_run(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            stage = create_ralph_verify_stage()
            ctx = StageContext(task="verify", cwd=tmpdir)
            result = stage.run(ctx)
            self.assertEqual(result.status, "completed")

    def test_build_instruction(self) -> None:
        desc = RalphVerifyDescriptor(max_iterations=5)
        instruction = build_ralph_instruction(desc)
        self.assertIn("max_iterations=5", instruction)


if __name__ == "__main__":
    unittest.main()
