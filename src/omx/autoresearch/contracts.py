"""Autoresearch contracts and types.

Port of src/autoresearch/contracts.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResearchMission:
    """Defines a research task with constraints and evaluation criteria.

    Attributes:
        task: Description of the research objective.
        max_iterations: Maximum generate-evaluate cycles.
        evaluation_criteria: Criteria for scoring candidates.
        constraints: Boundaries the research must respect.
    """

    task: str
    max_iterations: int = 10
    evaluation_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "max_iterations": self.max_iterations,
            "evaluation_criteria": self.evaluation_criteria,
            "constraints": self.constraints,
        }


@dataclass
class ResearchCandidate:
    """A single research iteration output with evaluation score.

    Attributes:
        iteration: 1-based iteration number.
        content: Generated research content.
        score: Evaluation score (0.0-1.0, 1.0 = satisfactory).
        feedback: Evaluator feedback for the next iteration.
    """

    iteration: int
    content: str
    score: float = 0.0
    feedback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "content": self.content,
            "score": self.score,
            "feedback": self.feedback,
        }
