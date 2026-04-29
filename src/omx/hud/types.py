"""HUD type definitions for oh-my-codex.

Port of src/hud/types.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


@dataclass
class RalphStateForHud:
    """Ralph loop state for HUD display.

    Attributes:
        active: Whether ralph is active.
        iteration: Current iteration number.
        max_iterations: Maximum iterations.
    """

    active: bool = False
    iteration: int | None = None
    max_iterations: int | None = None


@dataclass
class UltraworkStateForHud:
    """Ultrawork state for HUD display.

    Attributes:
        active: Whether ultrawork is active.
        reinforcement_count: Reinforcement count.
    """

    active: bool = False
    reinforcement_count: int | None = None


@dataclass
class AutopilotStateForHud:
    """Autopilot state for HUD display.

    Attributes:
        active: Whether autopilot is active.
        current_phase: Current phase string.
    """

    active: bool = False
    current_phase: str | None = None


@dataclass
class RalplanStateForHud:
    """Ralplan state for HUD display.

    Attributes:
        active: Whether ralplan is active.
        current_phase: Current phase string.
        iteration: Current iteration.
        planning_complete: Whether planning is complete.
    """

    active: bool = False
    current_phase: str | None = None
    iteration: int | None = None
    planning_complete: bool | None = None


@dataclass
class DeepInterviewStateForHud:
    """Deep-interview state for HUD display.

    Attributes:
        active: Whether deep-interview is active.
        current_phase: Current phase string.
        input_lock_active: Whether input lock is active.
    """

    active: bool = False
    current_phase: str | None = None
    input_lock_active: bool | None = None


@dataclass
class AutoresearchStateForHud:
    """Autoresearch state for HUD display.

    Attributes:
        active: Whether autoresearch is active.
        current_phase: Current phase string.
    """

    active: bool = False
    current_phase: str | None = None


@dataclass
class UltraqaStateForHud:
    """Ultraqa state for HUD display.

    Attributes:
        active: Whether ultraqa is active.
        current_phase: Current phase string.
    """

    active: bool = False
    current_phase: str | None = None


@dataclass
class TeamStateForHud:
    """Team state for HUD display.

    Attributes:
        active: Whether team mode is active.
        current_phase: Current phase string.
        agent_count: Number of agents.
        team_name: Team name.
    """

    active: bool = False
    current_phase: str | None = None
    agent_count: int | None = None
    team_name: str | None = None


@dataclass
class HudMetrics:
    """Metrics tracked by notify hook.

    Attributes:
        total_turns: Total conversation turns.
        session_turns: Turns in current session.
        last_activity: ISO timestamp of last activity.
        session_input_tokens: Input tokens in session.
        session_output_tokens: Output tokens in session.
        session_total_tokens: Total tokens in session.
        five_hour_limit_pct: Five-hour usage percentage.
        weekly_limit_pct: Weekly usage percentage.
    """

    total_turns: int = 0
    session_turns: int = 0
    last_activity: str = ""
    session_input_tokens: int | None = None
    session_output_tokens: int | None = None
    session_total_tokens: int | None = None
    five_hour_limit_pct: float | None = None
    weekly_limit_pct: float | None = None


@dataclass
class HudNotifyState:
    """HUD notify state written by notify hook.

    Attributes:
        last_turn_at: ISO timestamp of last turn.
        turn_count: Number of turns.
        last_agent_output: Last agent output text.
    """

    last_turn_at: str = ""
    turn_count: int = 0
    last_agent_output: str | None = None


@dataclass
class SessionStateForHud:
    """Session state for HUD display.

    Attributes:
        session_id: Session identifier.
        started_at: ISO timestamp of session start.
    """

    session_id: str = ""
    started_at: str = ""


@dataclass
class HudRenderContext:
    """All data needed to render one HUD frame.

    Attributes:
        version: OMX version string or None.
        git_branch: Current git branch or None.
        ralph: Ralph state.
        ultrawork: Ultrawork state.
        autopilot: Autopilot state.
        ralplan: Ralplan state.
        deep_interview: Deep interview state.
        autoresearch: Autoresearch state.
        ultraqa: Ultraqa state.
        team: Team state.
        metrics: HUD metrics.
        hud_notify: Notify state.
        session: Session state.
        runtime_snapshot: Runtime snapshot data.
    """

    version: str | None = None
    git_branch: str | None = None
    ralph: RalphStateForHud | None = None
    ultrawork: UltraworkStateForHud | None = None
    autopilot: AutopilotStateForHud | None = None
    ralplan: RalplanStateForHud | None = None
    deep_interview: DeepInterviewStateForHud | None = None
    autoresearch: AutoresearchStateForHud | None = None
    ultraqa: UltraqaStateForHud | None = None
    team: TeamStateForHud | None = None
    metrics: HudMetrics | None = None
    hud_notify: HudNotifyState | None = None
    session: SessionStateForHud | None = None
    runtime_snapshot: dict[str, Any] | None = None


class HudPreset(StrEnum):
    """HUD preset names."""

    MINIMAL = "minimal"
    FOCUSED = "focused"
    FULL = "full"


class HudGitDisplay(StrEnum):
    """HUD git display modes."""

    BRANCH = "branch"
    REPO_BRANCH = "repo-branch"


@dataclass
class HudGitConfig:
    """HUD git display configuration.

    Attributes:
        display: Display mode.
        remote_name: Remote name.
        repo_label: Repository label.
    """

    display: str | None = None
    remote_name: str | None = None
    repo_label: str | None = None


@dataclass
class HudConfig:
    """HUD configuration stored in .omx/hud-config.json.

    Attributes:
        preset: HUD preset.
        git: Git display configuration.
    """

    preset: str | None = None
    git: HudGitConfig | None = None


@dataclass
class ResolvedHudGitConfig:
    """Resolved HUD git display configuration.

    Attributes:
        display: Display mode.
        remote_name: Remote name.
        repo_label: Repository label.
    """

    display: str = "repo-branch"
    remote_name: str | None = None
    repo_label: str | None = None


@dataclass
class ResolvedHudConfig:
    """Resolved HUD configuration with defaults applied.

    Attributes:
        preset: HUD preset.
        git: Git display configuration.
    """

    preset: str = "focused"
    git: ResolvedHudGitConfig = field(default_factory=lambda: ResolvedHudGitConfig())


DEFAULT_HUD_CONFIG = ResolvedHudConfig(
    preset="focused",
    git=ResolvedHudGitConfig(display="repo-branch"),
)


@dataclass
class HudFlags:
    """CLI flags for omx hud.

    Attributes:
        watch: Whether to run in watch mode.
        json: Whether to output JSON.
        tmux: Whether to open in tmux pane.
        preset: Optional preset override.
    """

    watch: bool = False
    json: bool = False
    tmux: bool = False
    preset: str | None = None
