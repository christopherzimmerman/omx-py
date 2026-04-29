"""Pipeline orchestrator for oh-my-codex.

Configurable pipeline that sequences: RALPLAN -> teams -> ralph verification.
Mirrors OMC pipeline design.
"""

from omx.pipeline.types import (
    PipelineConfig,
    PipelineModeStateExtension,
    PipelineResult,
    PipelineStage,
    StageContext,
    StageResult,
)
from omx.pipeline.orchestrator import (
    create_autopilot_pipeline_config,
    run_pipeline,
)
from omx.pipeline.stages.ralplan import create_ralplan_stage
from omx.pipeline.stages.team_exec import create_team_exec_stage, build_team_instruction
from omx.pipeline.stages.ralph_verify import (
    create_ralph_verify_stage,
    build_ralph_instruction,
)

__all__ = [
    "PipelineConfig",
    "PipelineModeStateExtension",
    "PipelineResult",
    "PipelineStage",
    "StageContext",
    "StageResult",
    "create_autopilot_pipeline_config",
    "run_pipeline",
    "create_ralplan_stage",
    "create_team_exec_stage",
    "build_team_instruction",
    "create_ralph_verify_stage",
    "build_ralph_instruction",
]
