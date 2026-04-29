"""Idle worker nudge detection.

Port of src/team/idle-nudge.ts.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_NUDGE_DELAY_MS = 30000
DEFAULT_MAX_NUDGE_COUNT = 3


@dataclass
class NudgeConfig:
    """Configuration for idle worker nudge behavior.

    Attributes:
        delay_ms: Milliseconds of idle time before nudging.
        max_count: Maximum number of nudges to send.
        message: Text to inject into the worker pane.
    """

    delay_ms: int = DEFAULT_NUDGE_DELAY_MS
    max_count: int = DEFAULT_MAX_NUDGE_COUNT
    message: str = "Worker appears idle. Continue working on your assigned task."


def should_nudge(
    idle_duration_ms: float,
    nudge_count: int,
    config: NudgeConfig | None = None,
) -> bool:
    """Determine if an idle worker should receive a nudge prompt.

    Args:
        idle_duration_ms: How long the worker has been idle.
        nudge_count: Number of nudges already sent.
        config: Override nudge configuration (uses defaults if None).

    Returns:
        True if the worker should be nudged.
    """
    cfg = config or NudgeConfig()
    if nudge_count >= cfg.max_count:
        return False
    return idle_duration_ms >= cfg.delay_ms
