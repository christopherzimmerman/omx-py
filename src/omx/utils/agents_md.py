"""AGENTS.md marker detection and manipulation.

Port of src/utils/agents-md.ts.
"""

from __future__ import annotations

from omx.utils.agents_model_table import OMX_MODELS_END_MARKER, OMX_MODELS_START_MARKER

OMX_GENERATED_AGENTS_MARKER = "<!-- omx:generated:agents-md -->"
_AUTONOMY_DIRECTIVE_END_MARKER = "<!-- END AUTONOMY DIRECTIVE -->"


def is_omx_generated_agents_md(content: str) -> bool:
    """Check whether content contains the OMX generated-agents marker.

    Args:
        content: AGENTS.md file content.

    Returns:
        True if the marker is present.
    """
    return OMX_GENERATED_AGENTS_MARKER in content


def has_omx_managed_agents_sections(content: str) -> bool:
    """Check for any OMX-managed section (generated marker or model table).

    Args:
        content: AGENTS.md file content.

    Returns:
        True if any OMX-managed section is present.
    """
    return is_omx_generated_agents_md(content) or (
        OMX_MODELS_START_MARKER in content and OMX_MODELS_END_MARKER in content
    )


def add_generated_agents_marker(content: str) -> str:
    """Insert the OMX generated marker into AGENTS.md content.

    Inserts after the autonomy directive end marker if present, otherwise
    after the first newline.

    Args:
        content: AGENTS.md file content.

    Returns:
        Content with the marker inserted (idempotent).
    """
    if OMX_GENERATED_AGENTS_MARKER in content:
        return content

    idx = content.find(_AUTONOMY_DIRECTIVE_END_MARKER)
    if idx >= 0:
        insert_at = idx + len(_AUTONOMY_DIRECTIVE_END_MARKER)
        has_newline = insert_at < len(content) and content[insert_at] == "\n"
        insertion_point = insert_at + 1 if has_newline else insert_at
        return (
            content[:insertion_point]
            + f"{OMX_GENERATED_AGENTS_MARKER}\n"
            + content[insertion_point:]
        )

    first_nl = content.find("\n")
    if first_nl == -1:
        return f"{content}\n{OMX_GENERATED_AGENTS_MARKER}\n"

    return (
        content[: first_nl + 1]
        + f"{OMX_GENERATED_AGENTS_MARKER}\n"
        + content[first_nl + 1 :]
    )
