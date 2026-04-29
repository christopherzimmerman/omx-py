"""Native agent config generators for Codex CLI.

Port of src/agents/native-config.ts. Writes standalone TOML files
for agent role definitions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from omx.agents.roles import AGENT_BY_NAME

EXACT_GPT_5_4_MINI_MODEL = "gpt-5.4-mini"

POSTURE_OVERLAYS: dict[str, str] = {
    "frontier-orchestrator": "\n".join(
        [
            "<posture_overlay>",
            "",
            "You are operating in the frontier-orchestrator posture.",
            "- Prioritize intent classification before implementation.",
            "- Default to delegation and orchestration when specialists exist.",
            "- Treat the first decision as a routing problem: research vs planning vs implementation vs verification.",
            "- Challenge flawed user assumptions concisely before execution when the design is likely to cause avoidable problems.",
            "- Preserve explicit executor handoff boundaries: do not absorb deep implementation work when a specialized executor is more appropriate.",
            "",
            "</posture_overlay>",
        ]
    ),
    "deep-worker": "\n".join(
        [
            "<posture_overlay>",
            "",
            "You are operating in the deep-worker posture.",
            "- Once the task is clearly implementation-oriented, bias toward direct execution and end-to-end completion.",
            "- Explore first, then implement minimal changes that match existing patterns.",
            "- Keep verification strict: diagnostics, tests, and build evidence are mandatory before claiming completion.",
            "- Escalate only after materially different approaches fail or when architecture tradeoffs exceed local implementation scope.",
            "",
            "</posture_overlay>",
        ]
    ),
    "fast-lane": "\n".join(
        [
            "<posture_overlay>",
            "",
            "You are operating in the fast-lane posture.",
            "- Optimize for fast triage, search, lightweight synthesis, and narrow routing decisions.",
            "- Do not start deep implementation unless the task is tightly bounded and obvious.",
            "- If the task expands beyond quick classification or lightweight execution, escalate to a frontier-orchestrator or deep-worker role.",
            "- Keep responses quality-first, scope-aware, and conservative under ambiguity; avoid empty verbosity and reflexive tool escalation.",
            "",
            "</posture_overlay>",
        ]
    ),
}

MODEL_CLASS_OVERLAYS: dict[str, str] = {
    "frontier": "\n".join(
        [
            "<model_class_guidance>",
            "",
            "This role is tuned for frontier-class models.",
            "- Use the model's steerability for coordination, tradeoff reasoning, and precise delegation.",
            "- Favor clean routing decisions over impulsive implementation.",
            "",
            "</model_class_guidance>",
        ]
    ),
    "standard": "\n".join(
        [
            "<model_class_guidance>",
            "",
            "This role is tuned for standard-capability models.",
            "- Balance autonomy with clear boundaries.",
            "- Prefer explicit verification and narrow scope control over speculative reasoning.",
            "",
            "</model_class_guidance>",
        ]
    ),
    "fast": "\n".join(
        [
            "<model_class_guidance>",
            "",
            "This role is tuned for fast/low-latency models.",
            "- Prefer quick search, synthesis, and routing over prolonged reasoning.",
            "- Escalate rather than bluff when deeper work is required.",
            "",
            "</model_class_guidance>",
        ]
    ),
}

EXACT_MINI_MODEL_OVERLAY = "\n".join(
    [
        "<exact_model_guidance>",
        "",
        f"This role is executing under the exact {EXACT_GPT_5_4_MINI_MODEL} model.",
        "- Use a strict execution order: inspect -> plan -> act -> verify.",
        "- Treat completion criteria as explicit: only report done after the requested work is implemented and fresh verification passes.",
        "- If requirements are ambiguous or a blocker appears, state the blocker plainly and stop guessing until the missing decision is resolved.",
        "- Do not bluff, pad, or invent results; report missing evidence and incomplete work honestly.",
        "",
        "</exact_model_guidance>",
    ]
)


@dataclass
class GeneratedNativeAgentConfig:
    """Configuration for a standalone native agent TOML file.

    Attributes:
        name: Agent name.
        description: Human-readable description.
        developer_instructions: Optional developer instructions block.
        model: Optional model identifier.
        reasoning_effort: Optional reasoning effort level.
    """

    name: str
    description: str
    developer_instructions: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None  # "low", "medium", "high", "xhigh"


@dataclass
class RoleInstructionMetadata:
    """Metadata for composing role instructions.

    Attributes:
        name: Agent role name.
        posture: Behavioral posture.
        model_class: Model tier.
        routing_role: Routing role.
    """

    name: str
    posture: str
    model_class: str
    routing_role: str


def _is_exact_mini_model(resolved_model: str | None) -> bool:
    return (resolved_model or "").strip() == EXACT_GPT_5_4_MINI_MODEL


def strip_frontmatter(content: str) -> str:
    """Strip YAML frontmatter (between --- markers) from markdown content.

    Args:
        content: Markdown content possibly containing YAML frontmatter.

    Returns:
        Content with frontmatter removed and leading/trailing whitespace stripped.
    """
    match = re.match(r"^---\r?\n[\s\S]*?\r?\n---\r?\n?", content)
    if match:
        return content[match.end() :].strip()
    return content.strip()


def compose_role_instructions(
    prompt_content: str,
    metadata: RoleInstructionMetadata | None,
    resolved_model: str | None = None,
) -> str:
    """Compose full role instructions from prompt content and metadata.

    Args:
        prompt_content: Raw markdown prompt content.
        metadata: Optional role metadata for overlays.
        resolved_model: Optional resolved model name for model-specific overlays.

    Returns:
        Composed instruction string.
    """
    instructions = strip_frontmatter(prompt_content)
    parts = [instructions]

    if metadata:
        parts.extend(
            [
                "",
                POSTURE_OVERLAYS.get(metadata.posture, ""),
                "",
                MODEL_CLASS_OVERLAYS.get(metadata.model_class, ""),
            ]
        )

    if _is_exact_mini_model(resolved_model):
        parts.extend(["", EXACT_MINI_MODEL_OVERLAY])

    meta_lines: list[str] = []
    if metadata:
        meta_lines.extend(
            [
                "## OMX Agent Metadata",
                f"- role: {metadata.name}",
                f"- posture: {metadata.posture}",
                f"- model_class: {metadata.model_class}",
                f"- routing_role: {metadata.routing_role}",
            ]
        )
    if resolved_model:
        if not meta_lines:
            meta_lines.append("## OMX Agent Metadata")
        meta_lines.append(f"- resolved_model: {resolved_model}")
    if meta_lines:
        parts.extend(["", *meta_lines])

    return "\n".join(parts)


def compose_role_instructions_for_role(
    role_name: str,
    prompt_content: str,
    resolved_model: str | None = None,
) -> str:
    """Compose role instructions for a named role.

    Args:
        role_name: Agent role name.
        prompt_content: Raw markdown prompt content.
        resolved_model: Optional resolved model name.

    Returns:
        Composed instruction string.
    """
    agent = AGENT_BY_NAME.get(role_name)
    meta = (
        RoleInstructionMetadata(
            name=agent.name,
            posture=agent.posture,
            model_class=agent.model_class,
            routing_role=agent.routing_role,
        )
        if agent
        else None
    )
    return compose_role_instructions(prompt_content, meta, resolved_model)


def _escape_toml_multiline(s: str) -> str:
    """Escape content for TOML triple-quoted strings."""
    return re.sub(r'"{3,}', lambda m: "\\".join(m.group(0)), s)


def _escape_toml_basic_string(s: str) -> str:
    """Escape content for TOML basic strings."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def generate_standalone_agent_toml(config: GeneratedNativeAgentConfig) -> str:
    """Generate TOML content for a standalone agent config.

    Args:
        config: Agent configuration.

    Returns:
        TOML file content string.
    """
    lines = [
        f"# oh-my-codex agent: {config.name}",
        f'name = "{_escape_toml_basic_string(config.name)}"',
        f'description = "{_escape_toml_basic_string(config.description)}"',
    ]
    if config.model:
        lines.append(f'model = "{_escape_toml_basic_string(config.model)}"')
    if config.reasoning_effort:
        lines.append(f'model_reasoning_effort = "{config.reasoning_effort}"')
    if config.developer_instructions and config.developer_instructions.strip():
        escaped = _escape_toml_multiline(config.developer_instructions)
        lines.extend(['developer_instructions = """', escaped, '"""'])
    lines.append("")
    return "\n".join(lines)
