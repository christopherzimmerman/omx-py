"""Pipeline Orchestrator for oh-my-codex.

Port of src/pipeline/orchestrator.ts.
Sequences configurable stages (RALPLAN -> teams -> ralph verification).
"""

from __future__ import annotations

import os
import time
from typing import Any

from omx.pipeline.types import (
    PipelineConfig,
    PipelineResult,
    PipelineStage,
    StageContext,
    StageResult,
)

MODE_NAME = "autopilot"


def _validate_config(config: PipelineConfig) -> None:
    """Validate pipeline configuration.

    Args:
        config: Pipeline configuration to validate.

    Raises:
        ValueError: If the config is invalid.
    """
    if not config.name or not config.name.strip():
        raise ValueError("Pipeline config requires a non-empty name")
    if not config.task or not config.task.strip():
        raise ValueError("Pipeline config requires a non-empty task")
    if not config.stages:
        raise ValueError("Pipeline config requires at least one stage")

    names: set[str] = set()
    for stage in config.stages:
        if not stage.name or not stage.name.strip():
            raise ValueError("Every pipeline stage must have a non-empty name")
        if stage.name in names:
            raise ValueError(f"Duplicate stage name: {stage.name}")
        names.add(stage.name)

    if config.max_ralph_iterations is not None:
        if (
            not isinstance(config.max_ralph_iterations, int)
            or config.max_ralph_iterations <= 0
        ):
            raise ValueError("max_ralph_iterations must be a positive integer")

    if config.worker_count is not None:
        if not isinstance(config.worker_count, int) or config.worker_count <= 0:
            raise ValueError("workerCount must be a positive integer")


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    """Run a configured pipeline to completion.

    Executes stages sequentially, passing accumulated artifacts between them.

    Args:
        config: Pipeline configuration.

    Returns:
        PipelineResult with overall status and per-stage results.

    Raises:
        ValueError: If the config is invalid.
    """
    _validate_config(config)

    cwd = config.cwd or os.getcwd()
    start_time = time.time()

    stage_results: dict[str, StageResult] = {}
    artifacts: dict[str, Any] = {}
    previous_result: StageResult | None = None
    last_stage_name: str | None = None

    for i, stage in enumerate(config.stages):
        ctx = StageContext(
            task=config.task,
            artifacts=dict(artifacts),
            previous_stage_result=previous_result,
            cwd=cwd,
            session_id=config.session_id,
        )

        if last_stage_name and config.on_stage_transition:
            config.on_stage_transition(last_stage_name, stage.name)

        if stage.can_skip(ctx):
            skipped_result = StageResult(status="skipped", artifacts={}, duration_ms=0)
            stage_results[stage.name] = skipped_result
            last_stage_name = stage.name
            previous_result = skipped_result
            continue

        try:
            result = stage.run(ctx)
        except Exception as err:
            elapsed_ms = int((time.time() - start_time) * 1000)
            result = StageResult(
                status="failed",
                artifacts={},
                duration_ms=elapsed_ms,
                error=f"Stage {stage.name} threw: {err}",
            )

        stage_results[stage.name] = result

        if result.artifacts:
            artifacts[stage.name] = result.artifacts

        if result.status == "failed":
            duration_ms = int((time.time() - start_time) * 1000)
            return PipelineResult(
                status="failed",
                stage_results=stage_results,
                duration_ms=duration_ms,
                artifacts=artifacts,
                error=result.error,
                failed_stage=stage.name,
            )

        last_stage_name = stage.name
        previous_result = result

    duration_ms = int((time.time() - start_time) * 1000)
    return PipelineResult(
        status="completed",
        stage_results=stage_results,
        duration_ms=duration_ms,
        artifacts=artifacts,
    )


def create_autopilot_pipeline_config(
    task: str,
    *,
    stages: list[PipelineStage],
    cwd: str | None = None,
    session_id: str | None = None,
    max_ralph_iterations: int | None = None,
    worker_count: int | None = None,
    agent_type: str | None = None,
    on_stage_transition: Any = None,
) -> PipelineConfig:
    """Create the default autopilot pipeline configuration.

    Sequences: RALPLAN -> team-exec -> ralph-verify.

    Args:
        task: Task description.
        stages: Ordered list of pipeline stages.
        cwd: Working directory.
        session_id: Optional session ID.
        max_ralph_iterations: Max ralph iterations (default 10).
        worker_count: Worker count (default 2).
        agent_type: Agent type (default 'executor').
        on_stage_transition: Stage transition callback.

    Returns:
        A PipelineConfig ready for execution.
    """
    return PipelineConfig(
        name="autopilot",
        task=task,
        stages=stages,
        cwd=cwd,
        session_id=session_id,
        max_ralph_iterations=max_ralph_iterations or 10,
        worker_count=worker_count or 2,
        agent_type=agent_type or "executor",
        on_stage_transition=on_stage_transition,
    )
