"""Explore route defaults and sparkshell guidance.

Port of src/hooks/explore-routing.ts.
"""

from __future__ import annotations

import os
import re

OMX_EXPLORE_CMD_ENV = "USE_OMX_EXPLORE_CMD"

_DISABLED_VALUES = {"0", "false", "no", "off"}

_SIMPLE_EXPLORATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(where|find|locate|search|grep|ripgrep)\b", re.IGNORECASE),
    re.compile(
        r"\b(file|files|path|paths|symbol|symbols|usage|usages|reference|references)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(pattern|patterns|match|matches|matching)\b", re.IGNORECASE),
    re.compile(r"\bhow does\b", re.IGNORECASE),
    re.compile(
        r"\bwhich\b.*\b(contain|contains|define|defines|use|uses)\b", re.IGNORECASE
    ),
    re.compile(
        r"\b(read[- ]only|explor(e|ation)|inspect|lookup|look up|map)\b", re.IGNORECASE
    ),
]

_NON_EXPLORATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(implement|write|edit|modify|change|refactor|fix|patch|add|remove|delete)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(build|create)\b.*\b(feature|system|workflow|integration|module)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(migrate|rewrite|overhaul|redesign)\b", re.IGNORECASE),
    re.compile(r"\b(test|lint|typecheck|compile|deploy)\b", re.IGNORECASE),
]


def is_explore_command_routing_enabled(env: dict[str, str] | None = None) -> bool:
    """Check whether explore command routing is enabled.

    Args:
        env: Environment variables dict (defaults to ``os.environ``).

    Returns:
        True if routing is enabled (default-on).
    """
    environ = env if env is not None else os.environ
    raw = environ.get(OMX_EXPLORE_CMD_ENV)
    if not isinstance(raw, str):
        return True
    return raw.strip().lower() not in _DISABLED_VALUES


def is_simple_exploration_prompt(text: str) -> bool:
    """Check if a prompt looks like a simple exploration request.

    Args:
        text: User prompt text.

    Returns:
        True if the prompt matches exploration patterns without implementation signals.
    """
    trimmed = text.strip()
    if not trimmed:
        return False
    if any(p.search(trimmed) for p in _NON_EXPLORATION_PATTERNS):
        return False
    return any(p.search(trimmed) for p in _SIMPLE_EXPLORATION_PATTERNS)


def build_explore_routing_guidance(env: dict[str, str] | None = None) -> str:
    """Build the explore routing guidance text.

    Args:
        env: Environment variables dict (defaults to ``os.environ``).

    Returns:
        Guidance string (empty if routing is disabled).
    """
    if not is_explore_command_routing_enabled(env):
        return ""
    return "\n".join(
        [
            f"**Explore Command Preference:** enabled via `{OMX_EXPLORE_CMD_ENV}` (default-on; opt out with `0`, `false`, `no`, or `off`)",
            "- Advisory steering only: agents SHOULD treat `omx explore` as the default first stop for direct inspection and SHOULD reserve `omx sparkshell` for qualifying read-only shell-native tasks.",
            "- For simple file/symbol lookups, use `omx explore` FIRST before attempting full code analysis.",
            "- When the user asks for a simple read-only exploration task (file/symbol/pattern/relationship lookup), strongly prefer `omx explore` as the default surface.",
            '- Explore examples: `omx explore --prompt "which files define TeamPolicy"`, `omx explore --prompt "find usages of buildExploreRoutingGuidance"`.',
            '- SparkShell examples: use `omx sparkshell -- rg -n "TeamPolicy" src`, `omx sparkshell -- npm test`, or `omx sparkshell --tmux-pane %12` for noisy verification, bounded shell output, or tmux-pane summaries.',
            "- Keep `omx explore` prompts narrow and concrete; prefer a single lookup goal or a small related cluster, using `--prompt` for quick asks and `--prompt-file` for longer reusable briefs.",
            "- Treat `omx explore` as a shell-only allowlisted read-only path; keep edits, tests, diagnostics, MCP/web needs, and complex shell composition on the richer normal path.",
            "- Keep implementation, refactor, test, or ambiguous broad requests on the normal Codex path.",
            "- If `omx explore` is unavailable, stalls, or fails, retry with a narrower prompt or gracefully fall back to the normal path.",
        ]
    )
