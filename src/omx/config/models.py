"""Model selection defaults."""

from __future__ import annotations

DEFAULT_MODEL = "o4-mini"
REASONING_MODEL = "o3"
FAST_MODEL = "o4-mini"

MODEL_ALIASES: dict[str, str] = {
    "fast": FAST_MODEL,
    "reasoning": REASONING_MODEL,
    "default": DEFAULT_MODEL,
}


def resolve_model(label: str) -> str:
    """Resolve a model alias to a concrete model name.

    Args:
        label: Model alias ("fast", "reasoning", "default") or literal name.

    Returns:
        Concrete model identifier (passes through if not a known alias).
    """
    return MODEL_ALIASES.get(label.lower().strip(), label)
