"""Agent role definitions.

Port of src/agents/definitions.ts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefinition:
    """Immutable definition of a named agent role.

    Attributes:
        name: Unique agent name (e.g. "executor", "architect").
        description: Human-readable description of the role.
        reasoning_effort: Reasoning intensity ("low", "medium", "high").
        posture: Behavioral posture ("frontier-orchestrator", "deep-worker", "fast-lane").
        model_class: Model tier ("frontier", "standard", "fast").
        routing_role: Role in routing ("leader", "specialist", "executor").
        tools: Tool access level ("read-only", "analysis", "execution", "data").
        category: Functional category ("build", "review", "domain", "product", "coordination").
    """

    name: str
    description: str
    reasoning_effort: str  # "low", "medium", "high"
    posture: str  # "frontier-orchestrator", "deep-worker", "fast-lane"
    model_class: str  # "frontier", "standard", "fast"
    routing_role: str  # "leader", "specialist", "executor"
    tools: str  # "read-only", "analysis", "execution", "data"
    category: str  # "build", "review", "domain", "product", "coordination"


AGENT_DEFINITIONS: list[AgentDefinition] = [
    AgentDefinition(
        "explore",
        "Codebase exploration",
        "low",
        "fast-lane",
        "fast",
        "specialist",
        "read-only",
        "build",
    ),
    AgentDefinition(
        "analyst",
        "Requirements clarification",
        "medium",
        "frontier-orchestrator",
        "frontier",
        "leader",
        "analysis",
        "build",
    ),
    AgentDefinition(
        "planner",
        "Task planning",
        "medium",
        "frontier-orchestrator",
        "frontier",
        "leader",
        "analysis",
        "build",
    ),
    AgentDefinition(
        "architect",
        "System design",
        "high",
        "frontier-orchestrator",
        "frontier",
        "leader",
        "read-only",
        "build",
    ),
    AgentDefinition(
        "debugger",
        "Bug investigation",
        "high",
        "deep-worker",
        "standard",
        "executor",
        "analysis",
        "build",
    ),
    AgentDefinition(
        "executor",
        "Code implementation",
        "medium",
        "deep-worker",
        "standard",
        "executor",
        "execution",
        "build",
    ),
    AgentDefinition(
        "team-executor",
        "Team worker implementation",
        "medium",
        "deep-worker",
        "frontier",
        "executor",
        "execution",
        "build",
    ),
    AgentDefinition(
        "verifier",
        "Verification and testing",
        "high",
        "frontier-orchestrator",
        "standard",
        "leader",
        "analysis",
        "build",
    ),
    AgentDefinition(
        "style-reviewer",
        "Code style review",
        "low",
        "fast-lane",
        "fast",
        "specialist",
        "read-only",
        "review",
    ),
    AgentDefinition(
        "quality-reviewer",
        "Code quality review",
        "medium",
        "deep-worker",
        "standard",
        "specialist",
        "analysis",
        "review",
    ),
    AgentDefinition(
        "security-reviewer",
        "Security audit",
        "high",
        "deep-worker",
        "standard",
        "specialist",
        "analysis",
        "review",
    ),
    AgentDefinition(
        "researcher",
        "Research and docs lookup",
        "medium",
        "deep-worker",
        "standard",
        "specialist",
        "read-only",
        "domain",
    ),
    AgentDefinition(
        "data-analyst",
        "Data analysis",
        "medium",
        "deep-worker",
        "standard",
        "specialist",
        "data",
        "domain",
    ),
    AgentDefinition(
        "devops",
        "Infrastructure and CI/CD",
        "medium",
        "deep-worker",
        "standard",
        "executor",
        "execution",
        "domain",
    ),
    AgentDefinition(
        "designer",
        "UI/UX design guidance",
        "medium",
        "deep-worker",
        "standard",
        "specialist",
        "read-only",
        "product",
    ),
    AgentDefinition(
        "product-manager",
        "Product requirements",
        "medium",
        "frontier-orchestrator",
        "frontier",
        "leader",
        "analysis",
        "product",
    ),
    AgentDefinition(
        "team-orchestrator",
        "Multi-agent coordination",
        "high",
        "frontier-orchestrator",
        "frontier",
        "leader",
        "execution",
        "coordination",
    ),
]

AGENT_BY_NAME: dict[str, AgentDefinition] = {a.name: a for a in AGENT_DEFINITIONS}


def get_agent(name: str) -> AgentDefinition | None:
    """Look up an agent definition by name."""
    return AGENT_BY_NAME.get(name)


def list_agent_names() -> list[str]:
    """Return all registered agent names in definition order."""
    return [a.name for a in AGENT_DEFINITIONS]
