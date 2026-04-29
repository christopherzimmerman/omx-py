"""Question CLI client.

Port of src/question/client.ts. Runs ``omx question`` as a subprocess
and parses the JSON result from stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable



@dataclass
class OmxQuestionSuccessPayload:
    """Successful question result payload.

    Attributes:
        ok: Always True for success.
        question_id: The question identifier.
        session_id: Session scope.
        prompt: The question input that was prompted.
        answer: The user's answer.
    """

    ok: bool = True
    question_id: str = ""
    session_id: str | None = None
    prompt: dict[str, Any] = field(default_factory=dict)
    answer: dict[str, Any] = field(default_factory=dict)


@dataclass
class OmxQuestionErrorPayload:
    """Error question result payload.

    Attributes:
        ok: Always False for errors.
        question_id: Optional question identifier.
        session_id: Session scope.
        error: Error details with code and message.
    """

    ok: bool = False
    question_id: str | None = None
    session_id: str | None = None
    error: dict[str, str] = field(default_factory=dict)


class OmxQuestionError(Exception):
    """Error raised when the ``omx question`` subprocess fails.

    Attributes:
        code: Machine-readable error code.
        payload: Optional error payload from stdout.
        stdout: Raw stdout content.
        stderr: Raw stderr content.
        exit_code: Process exit code.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        payload: OmxQuestionErrorPayload | None = None,
        stdout: str = "",
        stderr: str = "",
        exit_code: int | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.payload = payload
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code


@dataclass
class ProcessResult:
    """Result of running a subprocess.

    Attributes:
        code: Exit code.
        stdout: Standard output.
        stderr: Standard error.
    """

    code: int | None = None
    stdout: str = ""
    stderr: str = ""


ProcessRunner = Callable[[list[str], str], ProcessResult]


def default_process_runner(args: list[str], cwd: str) -> ProcessResult:
    """Run a subprocess with default settings.

    Args:
        args: Command and arguments.
        cwd: Working directory.

    Returns:
        ProcessResult with exit code, stdout, stderr.
    """
    try:
        result = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        return ProcessResult(
            code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
        )
    except Exception as exc:
        return ProcessResult(code=None, stdout="", stderr=str(exc))


def _parse_question_stdout(
    stdout: str, stderr: str, exit_code: int | None
) -> dict[str, Any]:
    """Parse the JSON payload from ``omx question`` stdout.

    Args:
        stdout: Raw stdout content.
        stderr: Raw stderr content.
        exit_code: Process exit code.

    Returns:
        Parsed JSON payload dict.

    Raises:
        OmxQuestionError: If stdout is empty or invalid JSON.
    """
    trimmed = stdout.strip()
    if not trimmed:
        raise OmxQuestionError(
            "question_no_stdout",
            "omx question did not emit a JSON response on stdout.",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        )
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError as exc:
        raise OmxQuestionError(
            "question_invalid_stdout",
            f"omx question emitted invalid JSON on stdout: {exc}",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
        ) from exc


def run_omx_question(
    question_input: dict[str, Any],
    *,
    cwd: str | None = None,
    runner: ProcessRunner | None = None,
) -> OmxQuestionSuccessPayload:
    """Run ``omx question`` subprocess and return the answer.

    Args:
        question_input: Question input dict (must contain 'question' key).
        cwd: Working directory (defaults to current directory).
        runner: Optional custom process runner.

    Returns:
        OmxQuestionSuccessPayload with the answer.

    Raises:
        OmxQuestionError: If the subprocess fails or returns an error.
    """
    import os

    effective_cwd = cwd or os.getcwd()
    effective_runner = runner or default_process_runner

    args = [
        sys.executable,
        "-m",
        "omx",
        "question",
        "--json",
        "--input",
        json.dumps(question_input),
    ]

    result = effective_runner(args, effective_cwd)
    payload = _parse_question_stdout(result.stdout, result.stderr, result.code)

    if not payload.get("ok", False):
        error_info = payload.get("error", {})
        raise OmxQuestionError(
            error_info.get("code", "unknown"),
            error_info.get("message", "Unknown error"),
            payload=OmxQuestionErrorPayload(
                ok=False,
                question_id=payload.get("question_id"),
                session_id=payload.get("session_id"),
                error=error_info,
            ),
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.code,
        )

    if result.code is not None and result.code != 0:
        raise OmxQuestionError(
            "question_nonzero_exit",
            f"omx question returned an answer but exited with code {result.code}.",
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.code,
        )

    return OmxQuestionSuccessPayload(
        ok=True,
        question_id=payload.get("question_id", ""),
        session_id=payload.get("session_id"),
        prompt=payload.get("prompt", {}),
        answer=payload.get("answer", {}),
    )
