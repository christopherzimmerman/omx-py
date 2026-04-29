"""Team execution stage adapter for pipeline orchestrator.

Port of src/pipeline/stages/team-exec.ts.
Wraps team mode (tmux-based Codex CLI workers) into a PipelineStage.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from omx.pipeline.types import PipelineStage, StageContext, StageResult


@dataclass
class TeamExecDescriptor:
    """Descriptor for a team execution run.

    Attributes:
        task: Task description.
        worker_count: Number of Codex CLI workers.
        agent_type: Agent type/role for workers.
        available_agent_types: Available agent types.
        staffing_plan: Staffing plan descriptor.
        use_worktrees: Whether to use git worktrees.
        cwd: Working directory.
        extra_env: Additional environment variables.
    """

    task: str = ""
    worker_count: int = 2
    agent_type: str = "executor"
    available_agent_types: list[str] = field(default_factory=list)
    staffing_plan: dict[str, Any] = field(default_factory=dict)
    use_worktrees: bool = False
    cwd: str = ""
    extra_env: dict[str, str] | None = None


def build_team_instruction(descriptor: TeamExecDescriptor) -> str:
    """Build the omx team CLI instruction from a descriptor.

    Args:
        descriptor: Team execution descriptor.

    Returns:
        Shell command string for team execution.
    """
    task_json = json.dumps(descriptor.task)
    launch_command = (
        f"omx team {descriptor.worker_count}:{descriptor.agent_type} {task_json}"
    )
    staffing_summary = descriptor.staffing_plan.get("staffingSummary", "")
    verification_summary = descriptor.staffing_plan.get("verificationPlan", {}).get(
        "summary", ""
    )
    return f"{launch_command} # staffing={staffing_summary} # verify={verification_summary}"


class TeamExecStage(PipelineStage):
    """Team execution pipeline stage.

    Delegates to the existing omx team infrastructure.
    """

    def __init__(
        self,
        worker_count: int = 2,
        agent_type: str = "executor",
        use_worktrees: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._worker_count = worker_count
        self._agent_type = agent_type
        self._use_worktrees = use_worktrees
        self._extra_env = extra_env

    @property
    def name(self) -> str:
        return "team-exec"

    def run(self, ctx: StageContext) -> StageResult:
        """Execute the team execution stage.

        Args:
            ctx: Stage context.

        Returns:
            StageResult with team execution artifacts.
        """
        start_time = time.time()
        try:
            ralplan_artifacts = ctx.artifacts.get("ralplan")
            if isinstance(ralplan_artifacts, dict):
                plan_context = (
                    f"Plan from RALPLAN stage:\n{json.dumps(ralplan_artifacts, indent=2, default=str)}"
                    f"\n\nTask: {ctx.task}"
                )
            else:
                plan_context = ctx.task

            descriptor = TeamExecDescriptor(
                task=plan_context,
                worker_count=self._worker_count,
                agent_type=self._agent_type,
                use_worktrees=self._use_worktrees,
                cwd=ctx.cwd,
                extra_env=self._extra_env,
            )

            return StageResult(
                status="completed",
                artifacts={
                    "teamDescriptor": descriptor,
                    "workerCount": self._worker_count,
                    "agentType": self._agent_type,
                    "stage": "team-exec",
                    "instruction": build_team_instruction(descriptor),
                },
                duration_ms=int((time.time() - start_time) * 1000),
            )
        except Exception as err:
            return StageResult(
                status="failed",
                artifacts={},
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"Team execution stage failed: {err}",
            )


def create_team_exec_stage(
    worker_count: int = 2,
    agent_type: str = "executor",
    use_worktrees: bool = False,
    extra_env: dict[str, str] | None = None,
) -> TeamExecStage:
    """Create a team-exec pipeline stage.

    Args:
        worker_count: Number of Codex CLI workers.
        agent_type: Agent type for workers.
        use_worktrees: Whether to use git worktrees.
        extra_env: Additional environment variables.

    Returns:
        A configured TeamExecStage instance.
    """
    return TeamExecStage(
        worker_count=worker_count,
        agent_type=agent_type,
        use_worktrees=use_worktrees,
        extra_env=extra_env,
    )
