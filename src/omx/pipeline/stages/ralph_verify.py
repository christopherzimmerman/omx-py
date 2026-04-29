"""Ralph verification stage adapter for pipeline orchestrator.

Port of src/pipeline/stages/ralph-verify.ts.
Wraps the ralph persistence loop into a PipelineStage.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from omx.pipeline.types import PipelineStage, StageContext, StageResult


@dataclass
class RalphVerifyDescriptor:
    """Descriptor for a ralph verification run.

    Attributes:
        task: Task description.
        max_iterations: Maximum iterations.
        cwd: Working directory.
        session_id: Optional session ID.
        available_agent_types: Available agent types.
        staffing_plan: Staffing plan descriptor.
        execution_artifacts: Artifacts from execution stage.
    """

    task: str = ""
    max_iterations: int = 10
    cwd: str = ""
    session_id: str | None = None
    available_agent_types: list[str] = field(default_factory=list)
    staffing_plan: dict[str, Any] = field(default_factory=dict)
    execution_artifacts: dict[str, Any] = field(default_factory=dict)


def build_ralph_instruction(descriptor: RalphVerifyDescriptor) -> str:
    """Build the ralph CLI instruction from a descriptor.

    Args:
        descriptor: Ralph verification descriptor.

    Returns:
        Shell command string for ralph verification.
    """
    staffing_summary = descriptor.staffing_plan.get("staffingSummary", "")
    verification_summary = descriptor.staffing_plan.get("verificationPlan", {}).get(
        "summary", ""
    )
    shell_command = descriptor.staffing_plan.get("launchHints", {}).get(
        "shellCommand", "omx ralph"
    )
    return (
        f"{shell_command} # max_iterations={descriptor.max_iterations} "
        f"# staffing={staffing_summary} # verify={verification_summary}"
    )


class RalphVerifyStage(PipelineStage):
    """Ralph verification pipeline stage.

    Wraps the ralph persistence loop for the verification phase.
    """

    def __init__(self, max_iterations: int = 10) -> None:
        self._max_iterations = max_iterations

    @property
    def name(self) -> str:
        return "ralph-verify"

    def run(self, ctx: StageContext) -> StageResult:
        """Execute the ralph verification stage.

        Args:
            ctx: Stage context.

        Returns:
            StageResult with verification artifacts.
        """
        start_time = time.time()
        try:
            team_artifacts = ctx.artifacts.get("team-exec", {})
            descriptor = RalphVerifyDescriptor(
                task=ctx.task,
                max_iterations=self._max_iterations,
                cwd=ctx.cwd,
                session_id=ctx.session_id,
                execution_artifacts=team_artifacts
                if isinstance(team_artifacts, dict)
                else {},
            )

            return StageResult(
                status="completed",
                artifacts={
                    "verifyDescriptor": descriptor,
                    "maxIterations": self._max_iterations,
                    "stage": "ralph-verify",
                    "instruction": build_ralph_instruction(descriptor),
                },
                duration_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as err:
            return StageResult(
                status="failed",
                artifacts={},
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"Ralph verification stage failed: {err}",
            )


def create_ralph_verify_stage(max_iterations: int = 10) -> RalphVerifyStage:
    """Create a ralph-verify pipeline stage.

    Args:
        max_iterations: Maximum number of verification iterations.

    Returns:
        A configured RalphVerifyStage instance.
    """
    return RalphVerifyStage(max_iterations=max_iterations)
