"""Autoresearch runtime — research loop executor.

Port of src/autoresearch/runtime.ts.
"""

from __future__ import annotations

from typing import Callable

from omx.autoresearch.contracts import ResearchCandidate, ResearchMission


def run_research_loop(
    mission: ResearchMission,
    generate: Callable[[ResearchMission, list[ResearchCandidate]], ResearchCandidate],
    evaluate: Callable[[ResearchCandidate, ResearchMission], float],
    *,
    on_iteration: Callable[[int, ResearchCandidate], None] | None = None,
) -> list[ResearchCandidate]:
    """Run the generate-evaluate research loop until completion.

    Terminates when max_iterations is reached or a candidate scores >= 1.0.

    Args:
        mission: The research mission specification.
        generate: Function that produces a candidate given history.
        evaluate: Function that scores a candidate against the mission.
        on_iteration: Optional callback after each iteration.

    Returns:
        List of all generated candidates in order.
    """
    candidates: list[ResearchCandidate] = []

    for i in range(1, mission.max_iterations + 1):
        candidate = generate(mission, candidates)
        candidate.iteration = i
        candidate.score = evaluate(candidate, mission)
        candidates.append(candidate)

        if on_iteration:
            on_iteration(i, candidate)

        if candidate.score >= 1.0:
            break

    return candidates
