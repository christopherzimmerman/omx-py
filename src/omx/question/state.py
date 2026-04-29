"""Question state persistence.

Port of src/question/state.ts. Persists question records to
.omx/state/questions/<question-id>.json.
"""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from omx.question.types import (
    QuestionAnswer,
    QuestionInput,
    QuestionRecord,
    QuestionRendererState,
    QuestionStatus,
    get_normalized_question_type,
)
from omx.state.paths import get_state_dir

QUESTION_NAMESPACE = "questions"
DEFAULT_POLL_INTERVAL_S = 0.1


def _build_question_id(now: datetime | None = None) -> str:
    """Generate a unique question identifier.

    Args:
        now: Optional timestamp override.

    Returns:
        A unique question ID string.
    """
    now = now or datetime.now(timezone.utc)
    iso = now.isoformat().replace(":", "-").replace(".", "-")
    rand = f"{random.getrandbits(32):08x}"
    return f"question-{iso}-{rand}"


def get_question_state_dir(cwd: str, session_id: str | None = None) -> Path:
    """Get the directory for question state files.

    Args:
        cwd: Working directory.
        session_id: Optional session scope.

    Returns:
        Path to the question state directory.
    """
    return get_state_dir(cwd, session_id) / QUESTION_NAMESPACE


def get_question_record_path(
    cwd: str, question_id: str, session_id: str | None = None
) -> Path:
    """Get the path to a specific question record file.

    Args:
        cwd: Working directory.
        question_id: Question identifier.
        session_id: Optional session scope.

    Returns:
        Path to the question record JSON file.
    """
    return get_question_state_dir(cwd, session_id) / f"{question_id}.json"


def write_question_record(record_path: Path, record: QuestionRecord) -> None:
    """Write a question record to disk atomically.

    Args:
        record_path: Path to write the record to.
        record: The question record to persist.
    """
    record_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = record_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")
    tmp.replace(record_path)


def read_question_record(record_path: Path) -> QuestionRecord | None:
    """Read a question record from disk.

    Args:
        record_path: Path to the record file.

    Returns:
        The QuestionRecord, or None if not found.
    """
    if not record_path.exists():
        return None
    try:
        raw = json.loads(record_path.read_text(encoding="utf-8"))
        return QuestionRecord.from_dict(raw)
    except (json.JSONDecodeError, OSError):
        return None


def create_question_record(
    cwd: str,
    question_input: QuestionInput,
    session_id: str | None = None,
    now: datetime | None = None,
) -> tuple[Path, QuestionRecord]:
    """Create and persist a new question record.

    Args:
        cwd: Working directory.
        question_input: The normalised question input.
        session_id: Optional session scope.
        now: Optional timestamp override.

    Returns:
        Tuple of (record_path, record).
    """
    now = now or datetime.now(timezone.utc)
    question_id = _build_question_id(now)
    now_iso = now.isoformat()
    q_type = get_normalized_question_type(
        question_input.question_type, question_input.multi_select
    )

    record = QuestionRecord(
        kind="omx.question/v1",
        question_id=question_id,
        created_at=now_iso,
        updated_at=now_iso,
        status=QuestionStatus.PENDING,
        question=question_input.question,
        options=list(question_input.options),
        allow_other=question_input.allow_other,
        other_label=question_input.other_label,
        multi_select=question_input.multi_select,
        session_id=session_id or question_input.session_id,
        header=question_input.header,
        question_type=q_type,
        source=question_input.source,
    )
    record_path = get_question_record_path(cwd, question_id, session_id)
    write_question_record(record_path, record)
    return record_path, record


def update_question_record(
    record_path: Path,
    updater: Callable[[QuestionRecord], QuestionRecord],
) -> QuestionRecord:
    """Read, update, and re-persist a question record.

    Args:
        record_path: Path to the record file.
        updater: Function that transforms the record.

    Returns:
        The updated QuestionRecord.

    Raises:
        FileNotFoundError: If the record does not exist.
    """
    current = read_question_record(record_path)
    if current is None:
        raise FileNotFoundError(f"Question record not found: {record_path}")
    updated = updater(current)
    write_question_record(record_path, updated)
    return updated


def mark_question_prompting(
    record_path: Path,
    renderer: QuestionRendererState,
) -> QuestionRecord:
    """Mark a question as actively prompting with a renderer.

    Args:
        record_path: Path to the record file.
        renderer: The renderer state to attach.

    Returns:
        The updated QuestionRecord.
    """

    def _updater(record: QuestionRecord) -> QuestionRecord:
        if not is_terminal_question_status(record.status):
            record.status = QuestionStatus.PROMPTING
        record.updated_at = datetime.now(timezone.utc).isoformat()
        record.renderer = renderer
        return record

    return update_question_record(record_path, _updater)


def mark_question_answered(
    record_path: Path,
    answer: QuestionAnswer,
) -> QuestionRecord:
    """Mark a question as answered.

    Args:
        record_path: Path to the record file.
        answer: The user's answer.

    Returns:
        The updated QuestionRecord.
    """

    def _updater(record: QuestionRecord) -> QuestionRecord:
        record.status = QuestionStatus.ANSWERED
        record.updated_at = datetime.now(timezone.utc).isoformat()
        record.answer = answer
        record.error = None
        return record

    return update_question_record(record_path, _updater)


def mark_question_terminal_error(
    record_path: Path,
    status: QuestionStatus,
    code: str,
    message: str,
) -> QuestionRecord:
    """Mark a question with a terminal error or abort status.

    Args:
        record_path: Path to the record file.
        status: Terminal status (ABORTED or ERROR).
        code: Error code.
        message: Error message.

    Returns:
        The updated QuestionRecord.
    """

    def _updater(record: QuestionRecord) -> QuestionRecord:
        record.status = status
        record.updated_at = datetime.now(timezone.utc).isoformat()
        record.error = {
            "code": code,
            "message": message,
            "at": datetime.now(timezone.utc).isoformat(),
        }
        return record

    return update_question_record(record_path, _updater)


def is_terminal_question_status(status: QuestionStatus) -> bool:
    """Check whether a question status is terminal (no further transitions).

    Args:
        status: The status to check.

    Returns:
        True if the status is terminal.
    """
    return status in (
        QuestionStatus.ANSWERED,
        QuestionStatus.ABORTED,
        QuestionStatus.ERROR,
    )


def wait_for_question_terminal_state(
    record_path: Path,
    *,
    poll_interval_s: float | None = None,
    timeout_s: float | None = None,
    renderer_alive: Callable[[QuestionRecord], bool] | None = None,
    renderer_death_message: Callable[[QuestionRecord], str] | None = None,
) -> QuestionRecord:
    """Poll a question record until it reaches a terminal state.

    Args:
        record_path: Path to the record file.
        poll_interval_s: Seconds between polls (default 0.1).
        timeout_s: Maximum seconds to wait (None for indefinite).
        renderer_alive: Callback to check renderer liveness.
        renderer_death_message: Callback for renderer-died error message.

    Returns:
        The terminal QuestionRecord.

    Raises:
        FileNotFoundError: If the record disappears.
        TimeoutError: If the timeout expires.
        RuntimeError: If the renderer dies.
    """
    interval = max(
        0.01,
        poll_interval_s if poll_interval_s is not None else DEFAULT_POLL_INTERVAL_S,
    )
    started = time.monotonic()

    while True:
        record = read_question_record(record_path)
        if record is None:
            raise FileNotFoundError(
                f"Question record not found while waiting: {record_path}"
            )
        if is_terminal_question_status(record.status):
            return record

        if renderer_alive is not None and not renderer_alive(record):
            renderer_name = record.renderer.renderer if record.renderer else "unknown"
            msg = (
                renderer_death_message(record)
                if renderer_death_message
                else f"Question renderer {renderer_name} exited before answering."
            )
            raise RuntimeError(msg)

        if timeout_s is not None and timeout_s >= 0:
            elapsed = time.monotonic() - started
            if elapsed > timeout_s:
                raise TimeoutError(
                    f"Timed out waiting for question answer after {timeout_s}s"
                )

        time.sleep(interval)
