"""RALPLAN stage adapter for pipeline orchestrator.

Port of src/pipeline/stages/ralplan.ts.
Wraps consensus planning workflow into a PipelineStage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from omx.pipeline.types import PipelineStage, StageContext, StageResult


@dataclass
class RalplanStageOptions:
    """Options for the RALPLAN pipeline stage.

    Attributes:
        executor: Optional executor callable for live planning.
        max_iterations: Maximum planning iterations.
    """

    executor: Callable[..., Any] | None = None
    max_iterations: int | None = None


class RalplanStage(PipelineStage):
    """RALPLAN pipeline stage.

    Performs consensus planning by coordinating planner, architect,
    and critic agents. Outputs a plan file that downstream stages consume.
    """

    def __init__(self, options: RalplanStageOptions | None = None) -> None:
        self._options = options or RalplanStageOptions()

    @property
    def name(self) -> str:
        return "ralplan"

    def can_skip(self, ctx: StageContext) -> bool:
        """Skip if planning is already complete."""
        try:
            from omx.planning.artifacts import (
                is_planning_complete,
                read_planning_artifacts,
            )

            return is_planning_complete(read_planning_artifacts(ctx.cwd))
        except ImportError:
            return False

    def run(self, ctx: StageContext) -> StageResult:
        """Execute the RALPLAN stage.

        Args:
            ctx: Stage context.

        Returns:
            StageResult with planning artifacts.
        """
        start_time = time.time()
        try:
            try:
                from omx.planning.artifacts import (
                    read_planning_artifacts,
                    is_planning_complete,
                )

                planning = read_planning_artifacts(ctx.cwd)
                return StageResult(
                    status="completed",
                    artifacts={
                        "plansDir": planning.plans_dir,
                        "specsDir": planning.specs_dir,
                        "task": ctx.task,
                        "prdPaths": planning.prd_paths,
                        "testSpecPaths": planning.test_spec_paths,
                        "deepInterviewSpecPaths": planning.deep_interview_spec_paths,
                        "planningComplete": is_planning_complete(planning),
                        "stage": "ralplan",
                        "instruction": f"Run RALPLAN consensus planning for: {ctx.task}",
                    },
                    duration_ms=int((time.time() - start_time) * 1000),
                )
            except ImportError:
                return StageResult(
                    status="completed",
                    artifacts={
                        "task": ctx.task,
                        "stage": "ralplan",
                        "instruction": f"Run RALPLAN consensus planning for: {ctx.task}",
                    },
                    duration_ms=int((time.time() - start_time) * 1000),
                )
        except Exception as err:
            return StageResult(
                status="failed",
                artifacts={},
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"RALPLAN stage failed: {err}",
            )


def create_ralplan_stage(
    executor: Any = None,
    max_iterations: int | None = None,
) -> RalplanStage:
    """Create a RALPLAN pipeline stage.

    Args:
        executor: Optional executor for live planning.
        max_iterations: Maximum planning iterations.

    Returns:
        A configured RalplanStage instance.
    """
    return RalplanStage(
        RalplanStageOptions(executor=executor, max_iterations=max_iterations)
    )
