"""Interactive question UI using stdlib input().

Port of src/question/ui.ts. Provides number-based selection prompts
for single and multi-answer questions, with free-text "other" support.
"""

from __future__ import annotations

import sys
from typing import Callable, TextIO

from omx.question.state import (
    mark_question_answered,
    mark_question_terminal_error,
    read_question_record,
)
from omx.question.types import (
    AnswerKind,
    QuestionAnswer,
    QuestionRecord,
    QuestionStatus,
    is_multi_answerable_question,
)


def _get_option_labels(record: QuestionRecord) -> list[str]:
    """Build numbered option labels for display.

    Args:
        record: The question record.

    Returns:
        List of numbered label strings.
    """
    labels = [f"{i + 1}. {opt.label}" for i, opt in enumerate(record.options)]
    if record.allow_other:
        labels.append(f"{len(record.options) + 1}. {record.other_label}")
    return labels


def _render_options(record: QuestionRecord) -> list[str]:
    """Render option lines with checkbox indicators.

    Args:
        record: The question record.

    Returns:
        List of formatted option lines.
    """
    return [f"  [ ] {label}" for label in _get_option_labels(record)]


def _parse_selection(
    raw: str, option_count: int, multi_select: bool
) -> list[int] | None:
    """Parse user selection input into option indices.

    Args:
        raw: Raw input string.
        option_count: Total number of options (including 'other').
        multi_select: Whether multiple selections are allowed.

    Returns:
        List of 1-based selection indices, or None if invalid.
    """
    trimmed = raw.strip()
    if not trimmed:
        return None
    parts = trimmed.split(",") if multi_select else [trimmed]
    values: list[int] = []
    for part in parts:
        try:
            val = int(part.strip())
            if val != val:  # NaN check not needed in Python but kept for parity
                continue
            values.append(val)
        except ValueError:
            continue
    if not values:
        return None
    if not multi_select and len(values) != 1:
        return None
    if any(v < 1 or v > option_count for v in values):
        return None
    return list(dict.fromkeys(values))  # deduplicate preserving order


def _build_answer(
    record: QuestionRecord,
    selections: list[int],
    other_text: str | None = None,
) -> QuestionAnswer:
    """Build a QuestionAnswer from user selections.

    Args:
        record: The question record.
        selections: 1-based selection indices.
        other_text: Free-text if 'other' was selected.

    Returns:
        Constructed QuestionAnswer.

    Raises:
        ValueError: If the answer state is invalid.
    """
    option_count = len(record.options)
    other_index = option_count + 1

    selected_options = [record.options[v - 1] for v in selections if v <= option_count]
    selected_labels = [opt.label for opt in selected_options]
    selected_values = [opt.value for opt in selected_options]
    includes_other = record.allow_other and other_index in selections

    multi = is_multi_answerable_question(record.question_type, record.multi_select)

    if multi:
        values = (
            [*selected_values, other_text]
            if includes_other and other_text
            else list(selected_values)
        )
        labels = (
            [*selected_labels, record.other_label]
            if includes_other and other_text
            else list(selected_labels)
        )
        return QuestionAnswer(
            kind=AnswerKind.MULTI,
            value=values,
            selected_labels=labels,
            selected_values=values,
            other_text=other_text if includes_other else None,
        )

    if includes_other:
        if not other_text:
            raise ValueError("Other response text is required.")
        return QuestionAnswer(
            kind=AnswerKind.OTHER,
            value=other_text,
            selected_labels=[record.other_label],
            selected_values=[other_text],
            other_text=other_text,
        )

    if not selected_options:
        raise ValueError("No option selected.")
    selected = selected_options[0]
    return QuestionAnswer(
        kind=AnswerKind.OPTION,
        value=selected.value,
        selected_labels=[selected.label],
        selected_values=[selected.value],
    )


def prompt_for_selections(
    record: QuestionRecord,
    *,
    input_fn: Callable[[str], str] | None = None,
    output: TextIO | None = None,
) -> list[int]:
    """Prompt the user to select options by number.

    Args:
        record: The question record.
        input_fn: Callable for reading user input (defaults to builtin input).
        output: Output stream for display (defaults to sys.stdout).

    Returns:
        List of 1-based selected indices.
    """
    read_input = input_fn or input
    out = output or sys.stdout

    out.write("\n")
    if record.header:
        out.write(f"{record.header}\n")
    out.write(f"{record.question}\n\n")
    for line in _render_options(record):
        out.write(f"{line}\n")
    out.write("\n")

    option_count = len(record.options) + (1 if record.allow_other else 0)
    multi = is_multi_answerable_question(record.question_type, record.multi_select)
    prompt = (
        "Choose one or more options by number (comma-separated): "
        if multi
        else "Choose an option by number: "
    )

    selections: list[int] | None = None
    while selections is None:
        raw = read_input(prompt)
        selections = _parse_selection(raw, option_count, multi)
        if selections is None:
            out.write("Invalid selection. Please try again.\n")

    return selections


def prompt_for_other_text(
    label: str,
    *,
    input_fn: Callable[[str], str] | None = None,
    output: TextIO | None = None,
) -> str:
    """Prompt the user for free-text input.

    Args:
        label: The prompt label.
        input_fn: Callable for reading user input.
        output: Output stream for display.

    Returns:
        Non-empty trimmed text from the user.
    """
    read_input = input_fn or input
    out = output or sys.stdout

    while True:
        candidate = read_input(f"{label}: ").strip()
        if candidate:
            return candidate
        out.write("Please enter a response.\n")


def run_question_ui(
    record_path: str,
    *,
    input_fn: Callable[[str], str] | None = None,
    output: TextIO | None = None,
) -> None:
    """Run the full interactive question UI flow.

    Reads the question record, prompts the user for selections,
    handles 'other' text input, builds the answer, and persists it.

    Args:
        record_path: Path to the question record JSON file.
        input_fn: Callable for reading user input.
        output: Output stream for display.

    Raises:
        FileNotFoundError: If the question record does not exist.
    """
    from pathlib import Path

    path = Path(record_path)
    record = read_question_record(path)
    if record is None:
        raise FileNotFoundError(f"Question record not found: {record_path}")

    try:
        selections = prompt_for_selections(record, input_fn=input_fn, output=output)

        other_text: str | None = None
        if record.allow_other and (len(record.options) + 1) in selections:
            other_text = prompt_for_other_text(
                record.other_label, input_fn=input_fn, output=output
            )

        answer = _build_answer(record, selections, other_text)
        mark_question_answered(path, answer)

    except Exception as exc:
        mark_question_terminal_error(
            path,
            QuestionStatus.ERROR,
            "question_ui_failed",
            str(exc),
        )
        raise
