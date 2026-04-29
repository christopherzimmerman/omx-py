"""Hook event types and envelopes.

Port of src/hooks/extensibility/events.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

DERIVED_EVENTS = {"needs-input", "pre-tool-use", "post-tool-use"}


@dataclass
class HookEventEnvelope:
    """Envelope carrying a hook event with metadata for plugin dispatch.

    Attributes:
        schema_version: Envelope schema version.
        event: Event name (e.g. "needs-input", "pre-tool-use").
        timestamp: ISO timestamp of event creation.
        source: Origin type ("native" or "derived").
        context: Arbitrary context payload for the event.
        session_id: Associated session identifier.
        thread_id: Associated thread identifier.
        turn_id: Associated turn identifier.
        mode: Active workflow mode at event time.
        confidence: Confidence score for derived events (0.0-1.0).
        parser_reason: Reason string from the parser for derived events.
    """

    schema_version: str = "1"
    event: str = ""
    timestamp: str = ""
    source: str = "native"  # "native" or "derived"
    context: dict[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    mode: str | None = None
    confidence: float | None = None
    parser_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "schema_version": self.schema_version,
            "event": self.event,
            "timestamp": self.timestamp,
            "source": self.source,
            "context": self.context,
        }
        for f in (
            "session_id",
            "thread_id",
            "turn_id",
            "mode",
            "confidence",
            "parser_reason",
        ):
            v = getattr(self, f)
            if v is not None:
                d[f] = v
        return d


def build_hook_event(
    event: str,
    context: dict[str, Any] | None = None,
    *,
    session_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    mode: str | None = None,
) -> HookEventEnvelope:
    """Build a hook event envelope with auto-detected source type.

    Args:
        event: Event name.
        context: Optional context payload.
        session_id: Session identifier.
        thread_id: Thread identifier.
        turn_id: Turn identifier.
        mode: Active workflow mode.

    Returns:
        Populated HookEventEnvelope.
    """
    source = "derived" if event in DERIVED_EVENTS else "native"
    return HookEventEnvelope(
        event=event,
        timestamp=datetime.now(timezone.utc).isoformat(),
        source=source,
        context=context or {},
        session_id=session_id,
        thread_id=thread_id,
        turn_id=turn_id,
        mode=mode,
        confidence=0.5 if source == "derived" else None,
    )


def build_native_hook_event(
    event: str, context: dict[str, Any] | None = None, **kwargs: Any
) -> HookEventEnvelope:
    """Build a hook event envelope explicitly marked as native source."""
    env = build_hook_event(event, context, **kwargs)
    env.source = "native"
    env.confidence = None
    return env


def build_derived_hook_event(
    event: str,
    context: dict[str, Any] | None = None,
    confidence: float = 0.5,
    parser_reason: str | None = None,
    **kwargs: Any,
) -> HookEventEnvelope:
    """Build a hook event envelope marked as derived with confidence score."""
    env = build_hook_event(event, context, **kwargs)
    env.source = "derived"
    env.confidence = max(0.0, min(1.0, confidence))
    env.parser_reason = parser_reason
    return env
