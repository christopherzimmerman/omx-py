"""Template interpolation engine.

Lightweight {{variable}} interpolation with {{#if var}}...{{/if}} conditionals.
No external dependencies. Produces output matching formatter.py functions.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import PurePosixPath

from omx.notifications.formatter import parse_tmux_tail
from omx.notifications.types import FullNotificationPayload

# Set of known template variables for validation
KNOWN_VARIABLES: set[str] = {
    # Raw payload fields
    "event",
    "sessionId",
    "message",
    "timestamp",
    "tmuxSession",
    "projectPath",
    "projectName",
    "modesUsed",
    "contextSummary",
    "durationMs",
    "agentsSpawned",
    "agentsCompleted",
    "reason",
    "activeMode",
    "iteration",
    "maxIterations",
    "question",
    "incompleteTasks",
    "agentName",
    "agentType",
    "tmuxTail",
    "tmuxPaneId",
    # Reply context
    "replyChannel",
    "replyTarget",
    "replyThread",
    # Computed variables
    "duration",
    "time",
    "modesDisplay",
    "iterationDisplay",
    "agentDisplay",
    "projectDisplay",
    "footer",
    "tmuxTailBlock",
    "reasonDisplay",
}


def _format_duration(ms: int | None) -> str:
    """Format duration from milliseconds to human-readable string."""
    if not ms:
        return "unknown"
    seconds = ms // 1000
    minutes = seconds // 60
    hours = minutes // 60

    if hours > 0:
        return f"{hours}h {minutes % 60}m {seconds % 60}s"
    if minutes > 0:
        return f"{minutes}m {seconds % 60}s"
    return f"{seconds}s"


def _get_project_display(payload: FullNotificationPayload) -> str:
    """Get project display name from payload."""
    if payload.project_name:
        return payload.project_name
    if payload.project_path:
        return (
            PurePosixPath(payload.project_path).name
            or payload.project_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        )
    return "unknown"


def _build_footer_text(payload: FullNotificationPayload) -> str:
    """Build common footer with tmux and project info (markdown)."""
    parts: list[str] = []
    if payload.tmux_session:
        parts.append(f"**tmux:** `{payload.tmux_session}`")
    parts.append(f"**project:** `{_get_project_display(payload)}`")
    return " | ".join(parts)


def _build_tmux_tail_block(payload: FullNotificationPayload) -> str:
    """Build tmux tail block with code fence, or empty string."""
    if not payload.tmux_tail:
        return ""
    parsed = parse_tmux_tail(payload.tmux_tail)
    if not parsed:
        return ""
    return f"\n\n**Recent output:**\n```\n{parsed}\n```"


def compute_template_variables(
    payload: FullNotificationPayload,
) -> dict[str, str]:
    """Build the full variable map from a notification payload.

    Includes raw payload fields (string-converted) and computed variables.

    Args:
        payload: The notification payload.

    Returns:
        Dict mapping variable names to string values.
    """
    v: dict[str, str] = {}

    # Raw payload fields (None -> "")
    v["event"] = payload.event or ""
    v["sessionId"] = payload.session_id or ""
    v["message"] = payload.message or ""
    v["timestamp"] = payload.timestamp or ""
    v["tmuxSession"] = payload.tmux_session or ""
    v["projectPath"] = payload.project_path or ""
    v["projectName"] = payload.project_name or ""
    v["modesUsed"] = ", ".join(payload.modes_used) if payload.modes_used else ""
    v["contextSummary"] = payload.context_summary or ""
    v["durationMs"] = (
        str(payload.duration_ms) if payload.duration_ms is not None else ""
    )
    v["agentsSpawned"] = (
        str(payload.agents_spawned) if payload.agents_spawned is not None else ""
    )
    v["agentsCompleted"] = (
        str(payload.agents_completed) if payload.agents_completed is not None else ""
    )
    v["reason"] = payload.reason or ""
    v["activeMode"] = payload.active_mode or ""
    v["iteration"] = str(payload.iteration) if payload.iteration is not None else ""
    v["maxIterations"] = (
        str(payload.max_iterations) if payload.max_iterations is not None else ""
    )
    v["question"] = payload.question or ""
    v["incompleteTasks"] = (
        str(payload.incomplete_tasks) if payload.incomplete_tasks is not None else ""
    )
    v["agentName"] = payload.agent_name or ""
    v["agentType"] = payload.agent_type or ""
    v["tmuxTail"] = payload.tmux_tail or ""
    v["tmuxPaneId"] = payload.tmux_pane_id or ""

    # Computed variables
    v["duration"] = _format_duration(payload.duration_ms)
    if payload.timestamp:
        try:
            dt = datetime.fromisoformat(payload.timestamp.replace("Z", "+00:00"))
            v["time"] = dt.strftime("%H:%M:%S")
        except Exception:
            v["time"] = ""
    else:
        v["time"] = ""
    v["modesDisplay"] = ", ".join(payload.modes_used) if payload.modes_used else ""
    v["iterationDisplay"] = (
        f"{payload.iteration}/{payload.max_iterations}"
        if payload.iteration is not None and payload.max_iterations is not None
        else ""
    )
    v["agentDisplay"] = (
        f"{payload.agents_completed or 0}/{payload.agents_spawned} completed"
        if payload.agents_spawned is not None
        else ""
    )
    v["projectDisplay"] = _get_project_display(payload)
    v["footer"] = _build_footer_text(payload)
    v["tmuxTailBlock"] = _build_tmux_tail_block(payload)
    v["reasonDisplay"] = payload.reason or "unknown"

    return v


def _process_conditionals(template: str, variables: dict[str, str]) -> str:
    """Process {{#if var}}...{{/if}} conditionals."""

    def _replace(m: re.Match) -> str:
        var_name = m.group(1)
        content = m.group(2)
        value = variables.get(var_name, "")
        return content if value else ""

    return re.sub(
        r"\{\{#if\s+(\w+)\}\}([\s\S]*?)\{\{/if\}\}",
        _replace,
        template,
    )


def _replace_variables(template: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} placeholders with values."""

    def _replace(m: re.Match) -> str:
        return variables.get(m.group(1), "")

    return re.sub(r"\{\{(\w+)\}\}", _replace, template)


def _post_process(text: str) -> str:
    """Post-process interpolated text (trim trailing whitespace)."""
    return text.rstrip()


def interpolate_template(
    template: str,
    payload: FullNotificationPayload,
) -> str:
    """Interpolate a template string with payload values.

    1. Process {{#if var}}...{{/if}} conditionals
    2. Replace {{variable}} placeholders
    3. Post-process to normalize

    Args:
        template: Template string with placeholders.
        payload: The notification payload.

    Returns:
        Interpolated string.
    """
    variables = compute_template_variables(payload)
    result = _process_conditionals(template, variables)
    result = _replace_variables(result, variables)
    result = _post_process(result)
    return result


def validate_template(template: str) -> tuple[bool, list[str]]:
    """Validate a template string for unknown variables.

    Args:
        template: Template string to validate.

    Returns:
        Tuple of (valid, unknown_vars).
    """
    unknown_vars: list[str] = []

    for m in re.finditer(r"\{\{#if\s+(\w+)\}\}", template):
        if m.group(1) not in KNOWN_VARIABLES and m.group(1) not in unknown_vars:
            unknown_vars.append(m.group(1))

    for m in re.finditer(r"\{\{(?!#if\s|/if)(\w+)\}\}", template):
        if m.group(1) not in KNOWN_VARIABLES and m.group(1) not in unknown_vars:
            unknown_vars.append(m.group(1))

    return (len(unknown_vars) == 0, unknown_vars)


# Default templates that produce output identical to formatter.py functions
DEFAULT_TEMPLATES: dict[str, str] = {
    "session-start": (
        "# Session Started\n\n"
        "**Session:** `{{sessionId}}`\n"
        "**Project:** `{{projectDisplay}}`\n"
        "**Time:** {{time}}"
        "{{#if tmuxSession}}\n**tmux:** `{{tmuxSession}}`{{/if}}"
    ),
    "session-stop": (
        "# Session Continuing\n"
        "{{#if activeMode}}\n**Mode:** {{activeMode}}{{/if}}"
        "{{#if iterationDisplay}}\n**Iteration:** {{iterationDisplay}}{{/if}}"
        "{{#if incompleteTasks}}\n**Incomplete tasks:** {{incompleteTasks}}{{/if}}"
        "\n\n{{footer}}"
    ),
    "session-end": (
        "# Session Ended\n\n"
        "**Session:** `{{sessionId}}`\n"
        "**Duration:** {{duration}}\n"
        "**Reason:** {{reasonDisplay}}"
        "{{#if agentDisplay}}\n**Agents:** {{agentDisplay}}{{/if}}"
        "{{#if modesDisplay}}\n**Modes:** {{modesDisplay}}{{/if}}"
        "{{#if contextSummary}}\n\n**Summary:** {{contextSummary}}{{/if}}"
        "{{tmuxTailBlock}}"
        "\n\n{{footer}}"
    ),
    "session-idle": (
        "# Session Idle\n\n"
        "Codex has finished and is waiting for input.\n"
        "{{#if reason}}\n**Reason:** {{reason}}{{/if}}"
        "{{#if modesDisplay}}\n**Modes:** {{modesDisplay}}{{/if}}"
        "{{tmuxTailBlock}}"
        "\n\n{{footer}}"
    ),
    "ask-user-question": (
        "# Input Needed\n"
        "{{#if question}}\n**Question:** {{question}}\n{{/if}}"
        "\nCodex is waiting for your response.\n\n{{footer}}"
    ),
}


def get_default_template(event: str) -> str:
    """Get the default template for an event type.

    Args:
        event: The notification event name.

    Returns:
        Default template string.
    """
    return DEFAULT_TEMPLATES.get(event, "Event: {{event}}")
