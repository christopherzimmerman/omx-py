"""Run loop — iterates steps until a terminal outcome.

Port of src/runtime/run-loop.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from omx.runtime.run_outcome import classify_run_outcome, is_terminal_run_outcome


@dataclass
class RunLoopIteration:
    """Result of a single step function invocation.

    Attributes:
        outcome: Raw outcome string from the step.
        state: Updated state to carry forward.
    """

    outcome: str
    state: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedIteration:
    """A normalized iteration record with classified outcome.

    Attributes:
        outcome: Canonical outcome string.
        terminal: Whether this outcome halts the loop.
        state: State at the end of this iteration.
        iteration: 1-based iteration number.
    """

    outcome: str
    terminal: bool
    state: dict[str, Any]
    iteration: int


@dataclass
class RunLoopResult:
    """Final result of a completed run loop.

    Attributes:
        iteration_count: Total iterations executed.
        terminal_outcome: The outcome that terminated the loop.
        history: Complete list of normalized iterations.
    """

    iteration_count: int
    terminal_outcome: str
    history: list[NormalizedIteration]


StepFunction = Callable[[dict[str, Any]], RunLoopIteration]
OnIterationCallback = Callable[[NormalizedIteration], None]


def run_until_terminal(
    step: StepFunction,
    *,
    initial_state: dict[str, Any] | None = None,
    max_iterations: int = 100,
    on_iteration: OnIterationCallback | None = None,
) -> RunLoopResult:
    """Run a step function until a terminal outcome is reached.

    Args:
        step: Function that takes current state and returns a RunLoopIteration
        initial_state: Starting state dict
        max_iterations: Safety limit
        on_iteration: Optional callback after each iteration

    Returns:
        RunLoopResult with final outcome and history
    """
    state = dict(initial_state or {})
    history: list[NormalizedIteration] = []

    for i in range(1, max_iterations + 1):
        iteration_result = step(state)
        outcome = classify_run_outcome(iteration_result.outcome)
        terminal = is_terminal_run_outcome(outcome)

        normalized = NormalizedIteration(
            outcome=outcome,
            terminal=terminal,
            state=iteration_result.state,
            iteration=i,
        )
        history.append(normalized)

        if on_iteration:
            on_iteration(normalized)

        if terminal:
            return RunLoopResult(
                iteration_count=i,
                terminal_outcome=outcome,
                history=history,
            )

        # Carry state forward
        state = iteration_result.state

    raise RuntimeError(
        f"Run loop exceeded max_iterations ({max_iterations}) without reaching terminal state"
    )
