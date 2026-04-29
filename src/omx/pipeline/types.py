"""Pipeline stage interfaces for oh-my-codex.

Port of src/pipeline/types.ts.
Shared stage contracts that align with OMC pipeline design.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class StageContext:
    """Context passed into each pipeline stage.

    Attributes:
        task: Original task description provided by the user.
        artifacts: Accumulated artifacts from prior stages keyed by stage name.
        previous_stage_result: Result of the immediately preceding stage.
        cwd: Working directory for the pipeline run.
        session_id: Optional session id for scoped state.
    """

    task: str
    artifacts: dict[str, Any] = field(default_factory=dict)
    previous_stage_result: "StageResult | None" = None
    cwd: str = ""
    session_id: str | None = None


@dataclass
class StageResult:
    """Result returned by each pipeline stage after execution.

    Attributes:
        status: Stage completion status.
        artifacts: Artifacts produced by this stage.
        duration_ms: Wall-clock duration in milliseconds.
        error: Human-readable error description when status is 'failed'.
    """

    status: str  # "completed" | "failed" | "skipped"
    artifacts: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None


class PipelineStage(ABC):
    """A single stage in the pipeline.

    Implementations wrap concrete execution backends behind this
    uniform interface.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique name for this stage."""
        ...

    @abstractmethod
    def run(self, ctx: StageContext) -> StageResult:
        """Execute the stage.

        Args:
            ctx: Stage context with task and accumulated artifacts.

        Returns:
            A StageResult describing the outcome.
        """
        ...

    def can_skip(self, ctx: StageContext) -> bool:
        """Optional predicate — return True to skip this stage.

        Args:
            ctx: Stage context.

        Returns:
            True if this stage should be skipped.
        """
        return False


@dataclass
class PipelineConfig:
    """Configuration for a pipeline run.

    Attributes:
        name: Human-readable pipeline name.
        task: The task description driving the pipeline.
        stages: Ordered list of stages to execute.
        cwd: Working directory.
        session_id: Optional session id.
        max_ralph_iterations: Maximum ralph verification iterations.
        worker_count: Number of team workers.
        agent_type: Agent type for team workers.
        on_stage_transition: Callback fired on stage transitions.
    """

    name: str
    task: str
    stages: list[PipelineStage] = field(default_factory=list)
    cwd: str | None = None
    session_id: str | None = None
    max_ralph_iterations: int | None = None
    worker_count: int | None = None
    agent_type: str | None = None
    on_stage_transition: Callable[[str, str], None] | None = None


@dataclass
class PipelineResult:
    """Final result of a complete pipeline run.

    Attributes:
        status: Overall pipeline status.
        stage_results: Per-stage results keyed by stage name.
        duration_ms: Total wall-clock duration in milliseconds.
        artifacts: Merged artifact map from all stages.
        error: Error from the failing stage.
        failed_stage: Name of the stage that failed.
    """

    status: str  # "completed" | "failed" | "cancelled"
    stage_results: dict[str, StageResult] = field(default_factory=dict)
    duration_ms: int = 0
    artifacts: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    failed_stage: str | None = None


@dataclass
class PipelineModeStateExtension:
    """Extended ModeState fields for pipeline mode.

    Attributes:
        pipeline_name: Pipeline config name.
        pipeline_stages: Names of stages in execution order.
        pipeline_stage_index: Index of the currently executing stage.
        pipeline_stage_results: Per-stage results collected so far.
        pipeline_max_ralph_iterations: Ralph iteration ceiling.
        pipeline_worker_count: Worker count for team execution.
        pipeline_agent_type: Agent type for team workers.
    """

    pipeline_name: str = ""
    pipeline_stages: list[str] = field(default_factory=list)
    pipeline_stage_index: int = 0
    pipeline_stage_results: dict[str, StageResult] = field(default_factory=dict)
    pipeline_max_ralph_iterations: int = 10
    pipeline_worker_count: int = 2
    pipeline_agent_type: str = "executor"
