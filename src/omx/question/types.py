"""Question types and input normalisation.

Port of src/question/types.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class QuestionType(StrEnum):
    """Whether a question accepts a single or multiple answers."""

    SINGLE = "single-answerable"
    MULTI = "multi-answerable"


class QuestionStatus(StrEnum):
    """Lifecycle status of a question record."""

    PENDING = "pending"
    PROMPTING = "prompting"
    ANSWERED = "answered"
    ABORTED = "aborted"
    ERROR = "error"


class AnswerKind(StrEnum):
    """Kind of answer provided."""

    OPTION = "option"
    OTHER = "other"
    MULTI = "multi"


class QuestionRendererKind(StrEnum):
    """Renderer transport used to display a question."""

    TMUX_PANE = "tmux-pane"
    TMUX_SESSION = "tmux-session"
    INLINE_TTY = "inline-tty"
    WINDOWS_CONSOLE = "windows-console"


@dataclass
class QuestionOption:
    """A single selectable option in a question.

    Attributes:
        label: Display text for the option.
        value: Machine-readable value for the option.
        description: Optional longer description.
    """

    label: str
    value: str
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        d: dict[str, Any] = {"label": self.label, "value": self.value}
        if self.description is not None:
            d["description"] = self.description
        return d

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> QuestionOption:
        """Deserialise from dict."""
        return QuestionOption(
            label=str(raw.get("label", "")),
            value=str(raw.get("value", "")),
            description=raw.get("description"),
        )


@dataclass
class QuestionInput:
    """Normalised question input ready for rendering.

    Attributes:
        question: The question text.
        options: Available answer options.
        allow_other: Whether free-text "other" is allowed.
        other_label: Label for the free-text option.
        multi_select: Whether multiple selections are allowed.
        header: Optional header text.
        question_type: Single or multi answerable.
        source: Origin identifier (e.g. 'deep-interview').
        session_id: Session scope.
    """

    question: str
    options: list[QuestionOption] = field(default_factory=list)
    allow_other: bool = True
    other_label: str = "Other"
    multi_select: bool = False
    header: str | None = None
    question_type: QuestionType = QuestionType.SINGLE
    source: str | None = None
    session_id: str | None = None


@dataclass
class QuestionAnswer:
    """A user's answer to a question.

    Attributes:
        kind: The answer kind (option/other/multi).
        value: The answer value(s).
        selected_labels: Human-readable labels that were selected.
        selected_values: Machine values that were selected.
        other_text: Free-text response if kind is 'other'.
    """

    kind: AnswerKind
    value: str | list[str]
    selected_labels: list[str] = field(default_factory=list)
    selected_values: list[str] = field(default_factory=list)
    other_text: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        d: dict[str, Any] = {
            "kind": str(self.kind),
            "value": self.value,
            "selected_labels": self.selected_labels,
            "selected_values": self.selected_values,
        }
        if self.other_text is not None:
            d["other_text"] = self.other_text
        return d

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> QuestionAnswer:
        """Deserialise from dict."""
        return QuestionAnswer(
            kind=AnswerKind(raw.get("kind", "option")),
            value=raw.get("value", ""),
            selected_labels=raw.get("selected_labels", []),
            selected_values=raw.get("selected_values", []),
            other_text=raw.get("other_text"),
        )


@dataclass
class QuestionRendererState:
    """State of the renderer that displayed a question.

    Attributes:
        renderer: Renderer transport kind.
        target: Target identifier (pane ID, session name, etc.).
        launched_at: ISO timestamp of renderer launch.
        return_target: Pane to inject answer text into.
        return_transport: Transport method for return injection.
        pid: Process ID of renderer (Windows console).
    """

    renderer: str
    target: str
    launched_at: str
    return_target: str | None = None
    return_transport: str | None = None
    pid: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        d: dict[str, Any] = {
            "renderer": self.renderer,
            "target": self.target,
            "launched_at": self.launched_at,
        }
        if self.return_target is not None:
            d["return_target"] = self.return_target
        if self.return_transport is not None:
            d["return_transport"] = self.return_transport
        if self.pid is not None:
            d["pid"] = self.pid
        return d

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> QuestionRendererState:
        """Deserialise from dict."""
        return QuestionRendererState(
            renderer=str(raw.get("renderer", "")),
            target=str(raw.get("target", "")),
            launched_at=str(raw.get("launched_at", "")),
            return_target=raw.get("return_target"),
            return_transport=raw.get("return_transport"),
            pid=raw.get("pid"),
        )


@dataclass
class QuestionRecord:
    """Persisted question record.

    Attributes:
        kind: Schema identifier.
        question_id: Unique question identifier.
        created_at: ISO creation timestamp.
        updated_at: ISO last-update timestamp.
        status: Current lifecycle status.
        question: The question text.
        options: Available answer options.
        allow_other: Whether free-text "other" is allowed.
        other_label: Label for the free-text option.
        multi_select: Whether multiple selections are allowed.
        session_id: Session scope.
        header: Optional header text.
        question_type: Single or multi answerable.
        source: Origin identifier.
        renderer: Renderer state.
        answer: The user's answer (if answered).
        error: Error details (if errored/aborted).
    """

    kind: str = "omx.question/v1"
    question_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    status: QuestionStatus = QuestionStatus.PENDING
    question: str = ""
    options: list[QuestionOption] = field(default_factory=list)
    allow_other: bool = True
    other_label: str = "Other"
    multi_select: bool = False
    session_id: str | None = None
    header: str | None = None
    question_type: QuestionType | None = None
    source: str | None = None
    renderer: QuestionRendererState | None = None
    answer: QuestionAnswer | None = None
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to JSON-safe dict."""
        d: dict[str, Any] = {
            "kind": self.kind,
            "question_id": self.question_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": str(self.status),
            "question": self.question,
            "options": [o.to_dict() for o in self.options],
            "allow_other": self.allow_other,
            "other_label": self.other_label,
            "multi_select": self.multi_select,
        }
        if self.session_id is not None:
            d["session_id"] = self.session_id
        if self.header is not None:
            d["header"] = self.header
        if self.question_type is not None:
            d["type"] = str(self.question_type)
        if self.source is not None:
            d["source"] = self.source
        if self.renderer is not None:
            d["renderer"] = self.renderer.to_dict()
        if self.answer is not None:
            d["answer"] = self.answer.to_dict()
        if self.error is not None:
            d["error"] = self.error
        return d

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> QuestionRecord:
        """Deserialise from dict."""
        renderer_raw = raw.get("renderer")
        answer_raw = raw.get("answer")
        options_raw = raw.get("options", [])
        q_type = raw.get("type")
        return QuestionRecord(
            kind=raw.get("kind", "omx.question/v1"),
            question_id=raw.get("question_id", ""),
            created_at=raw.get("created_at", ""),
            updated_at=raw.get("updated_at", ""),
            status=QuestionStatus(raw.get("status", "pending")),
            question=raw.get("question", ""),
            options=[QuestionOption.from_dict(o) for o in options_raw],
            allow_other=raw.get("allow_other", True),
            other_label=raw.get("other_label", "Other"),
            multi_select=raw.get("multi_select", False),
            session_id=raw.get("session_id"),
            header=raw.get("header"),
            question_type=QuestionType(q_type) if q_type else None,
            source=raw.get("source"),
            renderer=QuestionRendererState.from_dict(renderer_raw)
            if renderer_raw
            else None,
            answer=QuestionAnswer.from_dict(answer_raw) if answer_raw else None,
            error=raw.get("error"),
        )


# ── Helper functions ────────────────────────────────────────────────────────


def _safe_string(value: Any) -> str:
    """Coerce to string, returning '' for non-strings."""
    return value if isinstance(value, str) else ""


def _normalize_option(raw: Any, index: int) -> QuestionOption:
    """Normalise a raw option value into a QuestionOption.

    Args:
        raw: String or dict describing the option.
        index: Position index for error messages.

    Returns:
        Normalised QuestionOption.

    Raises:
        ValueError: If the option is invalid.
    """
    if isinstance(raw, str):
        label = raw.strip()
        if not label:
            raise ValueError(f"options[{index}] must be a non-empty string")
        return QuestionOption(label=label, value=label)

    if not isinstance(raw, dict):
        raise ValueError(f"options[{index}] must be a string or object")

    label = _safe_string(raw.get("label", "")).strip()
    value = _safe_string(raw.get("value", "")).strip() or label
    description = _safe_string(raw.get("description", "")).strip() or None

    if not label:
        raise ValueError(f"options[{index}].label must be a non-empty string")
    if not value:
        raise ValueError(f"options[{index}].value must be a non-empty string")

    return QuestionOption(label=label, value=value, description=description)


def _parse_question_type(raw: Any) -> QuestionType | None:
    """Parse a question type from raw input.

    Args:
        raw: Raw type value.

    Returns:
        Parsed QuestionType or None.

    Raises:
        ValueError: If the type is invalid.
    """
    normalised = _safe_string(raw).strip().lower()
    if not normalised:
        return None
    if normalised in ("multi-answerable", "multi-select"):
        return QuestionType.MULTI
    if normalised in ("single-answerable", "single-select"):
        return QuestionType.SINGLE
    raise ValueError("type must be one of: single-answerable, multi-answerable")


def get_normalized_question_type(
    question_type: QuestionType | None = None,
    multi_select: bool = False,
) -> QuestionType:
    """Get the canonical question type from type and multi_select fields.

    Args:
        question_type: Explicit question type, if any.
        multi_select: Whether multi-select is enabled.

    Returns:
        The canonical QuestionType.
    """
    if question_type is not None:
        return question_type
    return QuestionType.MULTI if multi_select else QuestionType.SINGLE


def is_multi_answerable_question(
    question_type: QuestionType | None = None,
    multi_select: bool = False,
) -> bool:
    """Check whether a question accepts multiple answers.

    Args:
        question_type: Explicit question type, if any.
        multi_select: Whether multi-select is enabled.

    Returns:
        True if the question is multi-answerable.
    """
    return (
        get_normalized_question_type(question_type, multi_select) == QuestionType.MULTI
    )


def normalize_question_input(raw: Any) -> QuestionInput:
    """Normalise and validate raw question input.

    Args:
        raw: Raw JSON-decoded question input.

    Returns:
        Normalised QuestionInput.

    Raises:
        ValueError: If the input is invalid.
    """
    if not isinstance(raw, dict):
        raise ValueError("question input must be a JSON object")

    question = _safe_string(raw.get("question", "")).strip()
    header = _safe_string(raw.get("header", "")).strip() or None
    source = _safe_string(raw.get("source", "")).strip() or None
    session_id = _safe_string(raw.get("session_id", "")).strip() or None
    other_label = _safe_string(raw.get("other_label", "")).strip() or "Other"
    allow_other = raw.get("allow_other", True) is not False
    raw_multi_select = raw.get("multi_select")
    parsed_type = _parse_question_type(raw.get("type"))
    raw_options = raw.get("options", [])

    if not question:
        raise ValueError("question must be a non-empty string")

    if not isinstance(raw_options, list):
        raw_options = []

    if len(raw_options) == 0 and not allow_other:
        raise ValueError("options must be a non-empty array unless allow_other is true")

    if parsed_type == QuestionType.SINGLE and raw_multi_select is True:
        raise ValueError("type=single-answerable conflicts with multi_select=true")
    if parsed_type == QuestionType.MULTI and raw_multi_select is False:
        raise ValueError("type=multi-answerable conflicts with multi_select=false")

    options = [_normalize_option(opt, i) for i, opt in enumerate(raw_options)]
    q_type = get_normalized_question_type(parsed_type, raw_multi_select is True)
    multi_select = q_type == QuestionType.MULTI

    return QuestionInput(
        question=question,
        options=options,
        allow_other=allow_other,
        other_label=other_label,
        multi_select=multi_select,
        header=header,
        question_type=q_type,
        source=source,
        session_id=session_id,
    )
