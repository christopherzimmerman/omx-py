"""``omx question`` — blocking question UI entrypoint.

Port of ``src/cli/question.ts``. Sync, stdlib-only.

Two modes:

* ``omx question --input '<json>' [--json]`` — create a question record
  from the input schema and run the inline-TTY UI to get an answer.
* ``omx question --ui --state-path <path>`` — render an existing record
  (used internally when a renderer is launched out-of-process).

The Python port does not yet implement out-of-process renderer
launching, so the input-mode path runs the UI inline and returns the
answer JSON.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUESTION_HELP = """\
omx question - OMX-owned blocking user question entrypoint

Usage:
  omx question --input '<json>' [--json]
  omx question --ui --state-path <absolute-or-relative-record-path>

Options:
  --help, -h           Show this help message
  --input <json>       JSON object with question/options schema
  --input=<json>       Same as --input
  --json               Emit compact JSON on stdout
  --ui                 Render an existing state record (internal)
  --state-path <path>  Question record path used by --ui mode\
"""


@dataclass
class ParsedQuestionArgs:
    help: bool = False
    json: bool = False
    ui: bool = False
    input: str | None = None
    state_path: str | None = None


def parse_question_args(args: list[str]) -> ParsedQuestionArgs:
    """Parse ``omx question`` arguments."""
    parsed = ParsedQuestionArgs()
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--help", "-h", "help"):
            parsed.help = True
            i += 1
            continue
        if arg == "--json":
            parsed.json = True
            i += 1
            continue
        if arg == "--ui":
            parsed.ui = True
            i += 1
            continue
        if arg == "--input":
            if i + 1 >= len(args):
                raise ValueError("Missing JSON value after --input")
            parsed.input = args[i + 1]
            i += 2
            continue
        if arg.startswith("--input="):
            parsed.input = arg[len("--input=") :]
            i += 1
            continue
        if arg == "--state-path":
            if i + 1 >= len(args):
                raise ValueError("Missing path value after --state-path")
            parsed.state_path = args[i + 1]
            i += 2
            continue
        if arg.startswith("--state-path="):
            parsed.state_path = arg[len("--state-path=") :]
            i += 1
            continue
        raise ValueError(f"Unknown question argument: {arg}")
    return parsed


def _print_json(payload: Any, compact: bool) -> None:
    if compact:
        print(json.dumps(payload, separators=(",", ":")))
    else:
        print(json.dumps(payload, indent=2))


def handle_question(args: list[str]) -> None:
    """Top-level handler for ``omx question``."""
    try:
        parsed = parse_question_args(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if parsed.help or not args:
        print(QUESTION_HELP)
        return

    if parsed.ui:
        if not parsed.state_path:
            print("Error: --ui requires --state-path", file=sys.stderr)
            sys.exit(1)
        from omx.question.ui import run_question_ui

        try:
            run_question_ui(parsed.state_path)
        except FileNotFoundError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        return

    if not parsed.input:
        print("Error: omx question requires --input in normal mode", file=sys.stderr)
        sys.exit(1)

    try:
        raw_input_obj = json.loads(parsed.input)
    except json.JSONDecodeError as exc:
        print(f"Error: --input must be valid JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    from omx.question.policy import evaluate_question_policy
    from omx.question.state import (
        create_question_record,
        read_question_record,
    )
    from omx.question.types import normalize_question_input

    try:
        question_input = normalize_question_input(raw_input_obj)
    except Exception as exc:  # noqa: BLE001
        print(f"Error: invalid question input: {exc}", file=sys.stderr)
        sys.exit(1)

    cwd = os.getcwd()
    policy = evaluate_question_policy(
        cwd=cwd, explicit_session_id=question_input.session_id
    )
    if not policy.allowed:
        _print_json(
            {
                "ok": False,
                "error": {"code": policy.code, "message": policy.message},
            },
            parsed.json,
        )
        sys.exit(1)

    record_path, record = create_question_record(cwd, question_input, policy.session_id)

    # Run the UI inline (we don't have out-of-process renderer launching).
    from omx.question.ui import run_question_ui

    try:
        run_question_ui(str(record_path))
    except Exception as exc:  # noqa: BLE001
        _print_json(
            {
                "ok": False,
                "question_id": record.question_id,
                "session_id": record.session_id,
                "error": {
                    "code": "question_runtime_failed",
                    "message": str(exc),
                },
            },
            parsed.json,
        )
        sys.exit(1)

    # Re-read the (now-updated) record to extract the answer.
    final_record = read_question_record(Path(record_path))
    if final_record is None:
        _print_json(
            {
                "ok": False,
                "error": {
                    "code": "question_record_missing",
                    "message": "question record disappeared after UI finished",
                },
            },
            parsed.json,
        )
        sys.exit(1)

    if final_record.status != "answered" or final_record.answer is None:
        _print_json(
            {
                "ok": False,
                "question_id": final_record.question_id,
                "error": final_record.error
                or {
                    "code": "question_not_answered",
                    "message": f"Question ended with status {final_record.status}.",
                },
            },
            parsed.json,
        )
        sys.exit(1)

    answer_payload: dict[str, Any]
    answer = final_record.answer
    if hasattr(answer, "to_dict"):
        answer_payload = answer.to_dict()  # type: ignore[assignment]
    elif isinstance(answer, dict):
        answer_payload = answer
    else:
        answer_payload = {"raw": str(answer)}

    _print_json(
        {
            "ok": True,
            "question_id": final_record.question_id,
            "session_id": final_record.session_id,
            "prompt": {
                "header": final_record.header,
                "question": final_record.question,
                "options": [
                    o.to_dict() if hasattr(o, "to_dict") else o
                    for o in (final_record.options or [])
                ],
                "allow_other": final_record.allow_other,
                "other_label": final_record.other_label,
            },
            "answer": answer_payload,
        },
        parsed.json,
    )
