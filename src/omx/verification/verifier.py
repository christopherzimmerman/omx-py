"""Verification gate logic.

Port of src/verification/verifier.ts. Evidence-backed verification
of task completion with sizing-based verification depth.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class VerificationConfidence(StrEnum):
    """Confidence level in verification."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class VerificationEvidence:
    """A single piece of verification evidence.

    Attributes:
        type: Evidence type.
        passed: Whether this check passed.
        command: Command that was run.
        output: Command output.
        details: Additional details.
    """

    type: str  # "test" | "typecheck" | "lint" | "build" | "manual" | "runtime"
    passed: bool
    command: str | None = None
    output: str | None = None
    details: str | None = None


@dataclass
class VerificationResult:
    """Full verification result.

    Attributes:
        passed: Overall pass/fail.
        evidence: List of evidence items.
        summary: Human-readable summary.
        confidence: Confidence level.
    """

    passed: bool
    evidence: list[VerificationEvidence] = field(default_factory=list)
    summary: str = ""
    confidence: VerificationConfidence = VerificationConfidence.MEDIUM


def has_structured_verification_evidence(summary: str | None) -> bool:
    """Check for structured verification evidence in a task completion summary.

    Args:
        summary: Task completion summary text.

    Returns:
        True if structured verification evidence is detected.
    """
    if not isinstance(summary, str):
        return False
    text = summary.strip()
    if not text:
        return False

    has_section = bool(
        re.search(r"verification(?:\s+evidence)?\s*:", text, re.IGNORECASE)
        or re.search(r"##\s*verification", text, re.IGNORECASE)
    )
    if not has_section:
        return False

    has_signal = bool(
        re.search(r"\b(pass|passed|fail|failed)\b", text, re.IGNORECASE)
        or re.search(r"`[^`]+`", text)
        or re.search(r"\b(command|test|build|typecheck|lint)\b", text, re.IGNORECASE)
    )
    return has_signal


def get_verification_instructions(
    task_size: str,
    task_description: str,
) -> str:
    """Generate verification instructions for a given task size.

    Args:
        task_size: One of "small", "standard", or "large".
        task_description: Description of the task being verified.

    Returns:
        Verification instruction text.
    """
    base = f"""
## Verification Protocol

Verify the following task is complete: {task_description}

### Required Evidence:
"""
    match task_size:
        case "small":
            return (
                base
                + """
1. Run type checker on modified files (if TypeScript/typed language)
2. Run tests related to the change
3. Confirm the change works as described

Report: PASS/FAIL with evidence for each check.
"""
            )
        case "standard":
            return (
                base
                + """
1. Run full type check (tsc --noEmit or equivalent)
2. Run test suite (focus on changed areas)
3. Run linter on modified files
4. Verify the feature/fix works end-to-end
5. Check for regressions in related functionality

Report: PASS/FAIL with command output for each check.
"""
            )
        case "large":
            return (
                base
                + """
1. Run full type check across the project
2. Run complete test suite
3. Run linter across modified files
4. Security review of changes (OWASP top 10)
5. Performance impact assessment
6. API compatibility check (if applicable)
7. End-to-end verification of all affected features
8. Regression testing of adjacent functionality

Report: PASS/FAIL with detailed evidence for each check.
Include confidence level (high/medium/low) with justification.
"""
            )
        case _:
            return base


def determine_task_size(file_count: int, line_changes: int) -> str:
    """Determine task size from file count and line changes.

    Args:
        file_count: Number of files changed.
        line_changes: Number of lines changed.

    Returns:
        Task size string: "small", "standard", or "large".
    """
    if file_count <= 3 and line_changes < 100:
        return "small"
    if file_count <= 15 and line_changes < 500:
        return "standard"
    return "large"


def get_fix_loop_instructions(max_retries: int = 3) -> str:
    """Generate fix-verify loop instructions.

    Args:
        max_retries: Maximum number of fix-verify iterations.

    Returns:
        Fix-loop instruction text.
    """
    return f"""
## Fix-Verify Loop

If verification fails:
1. Identify the root cause of each failure
2. Fix the issue (prefer minimal changes)
3. Re-run verification
4. Repeat up to {max_retries} times
5. If still failing after {max_retries} attempts, escalate with:
   - What was attempted
   - What failed and why
   - Recommended next steps
"""
