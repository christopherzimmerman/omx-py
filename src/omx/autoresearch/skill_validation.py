"""Autoresearch skill instruction validation.

Port of src/autoresearch/skill-validation.ts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any


class AutoresearchValidationMode(StrEnum):
    """Validation mode for autoresearch completion."""

    MISSION_VALIDATOR_SCRIPT = "mission-validator-script"
    PROMPT_ARCHITECT_ARTIFACT = "prompt-architect-artifact"


@dataclass
class AutoresearchCompletionStatus:
    """Completion status of an autoresearch run.

    Attributes:
        complete: Whether the research is complete.
        reason: Reason string for the status.
        validation_mode: Active validation mode.
        artifact_path: Path to the completion artifact.
        output_artifact_path: Path to the output artifact (architect mode).
    """

    complete: bool
    reason: str
    validation_mode: AutoresearchValidationMode | None = None
    artifact_path: str | None = None
    output_artifact_path: str | None = None


def _safe_string(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _safe_object(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _safe_boolean(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _lookup_string(raw: dict[str, Any] | None, *keys: str) -> str:
    if not raw:
        return ""
    for key in keys:
        direct = _safe_string(raw.get(key))
        if direct:
            return direct
    nested = _safe_object(raw.get("state"))
    if nested:
        for key in keys:
            val = _safe_string(nested.get(key))
            if val:
                return val
    return ""


def _lookup_boolean(raw: dict[str, Any] | None, *keys: str) -> bool | None:
    if not raw:
        return None
    for key in keys:
        direct = _safe_boolean(raw.get(key))
        if direct is not None:
            return direct
    nested = _safe_object(raw.get("state"))
    if nested:
        for key in keys:
            val = _safe_boolean(nested.get(key))
            if val is not None:
                return val
    return None


def normalize_autoresearch_validation_mode(
    value: Any,
) -> AutoresearchValidationMode | None:
    """Normalize a raw value into an ``AutoresearchValidationMode``.

    Args:
        value: Raw string value.

    Returns:
        Normalized mode or ``None``.
    """
    normalized = _safe_string(value).lower()
    if normalized == "mission-validator-script":
        return AutoresearchValidationMode.MISSION_VALIDATOR_SCRIPT
    if normalized == "prompt-architect-artifact":
        return AutoresearchValidationMode.PROMPT_ARCHITECT_ARTIFACT
    return None


def _resolve_maybe_relative_path(cwd: str, raw_path: str) -> str:
    if not raw_path:
        return raw_path
    return raw_path if raw_path.startswith("/") else str(Path(cwd) / raw_path)


def _derive_default_artifact_path(
    cwd: str, raw_state: dict[str, Any] | None
) -> str | None:
    slug = _lookup_string(raw_state, "slug", "mission_slug", "missionSlug")
    if not slug:
        return None
    return str(
        Path(cwd) / ".omx" / "specs" / f"autoresearch-{slug}" / "completion.json"
    )


def _resolve_artifact_path(cwd: str, raw_state: dict[str, Any] | None) -> str | None:
    explicit = _lookup_string(
        raw_state,
        "completion_artifact_path",
        "completionArtifactPath",
        "validator_artifact_path",
        "validatorArtifactPath",
    )
    if explicit:
        return _resolve_maybe_relative_path(cwd, explicit)
    return _derive_default_artifact_path(cwd, raw_state)


def _read_json_if_exists(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _is_passing_status(value: Any) -> bool:
    normalized = _safe_string(value).lower()
    return normalized in {
        "pass",
        "passed",
        "complete",
        "completed",
        "success",
        "succeeded",
        "approved",
    }


def _has_architect_approval(artifact: dict[str, Any] | None) -> bool:
    if not artifact:
        return False
    direct = _lookup_boolean(
        artifact, "architect_approved", "architectApproved", "approved"
    )
    if direct is True:
        return True
    arch_review = _safe_object(artifact.get("architect_review")) or _safe_object(
        artifact.get("architectReview")
    )
    arch_validation = _safe_object(
        artifact.get("architect_validation")
    ) or _safe_object(artifact.get("architectValidation"))
    return _is_passing_status((arch_review or {}).get("verdict")) or _is_passing_status(
        (arch_validation or {}).get("verdict")
    )


def _resolve_output_artifact_path(
    cwd: str,
    raw_state: dict[str, Any] | None,
    artifact: dict[str, Any] | None,
) -> str | None:
    explicit = _lookup_string(
        raw_state, "output_artifact_path", "outputArtifactPath"
    ) or _lookup_string(artifact, "output_artifact_path", "outputArtifactPath")
    if not explicit:
        return None
    return _resolve_maybe_relative_path(cwd, explicit)


def assess_autoresearch_completion_state(
    raw_state: dict[str, Any] | None,
    cwd: str,
) -> AutoresearchCompletionStatus:
    """Assess autoresearch completion status from raw mode state.

    Args:
        raw_state: Raw mode state dict.
        cwd: Working directory.

    Returns:
        Completion status assessment.
    """
    validation_mode = normalize_autoresearch_validation_mode(
        _lookup_string(raw_state, "validation_mode", "validationMode")
    )
    if not raw_state:
        return AutoresearchCompletionStatus(
            complete=False,
            reason="missing_mode_state",
        )
    if not validation_mode:
        return AutoresearchCompletionStatus(
            complete=False,
            reason="missing_validation_mode",
            artifact_path=_resolve_artifact_path(cwd, raw_state),
        )

    artifact_path = _resolve_artifact_path(cwd, raw_state)
    artifact = _read_json_if_exists(artifact_path)
    if not artifact_path:
        return AutoresearchCompletionStatus(
            complete=False,
            reason="missing_completion_artifact_path",
            validation_mode=validation_mode,
        )
    if not artifact:
        return AutoresearchCompletionStatus(
            complete=False,
            reason="missing_or_invalid_completion_artifact",
            validation_mode=validation_mode,
            artifact_path=artifact_path,
        )

    if validation_mode == AutoresearchValidationMode.MISSION_VALIDATOR_SCRIPT:
        validator_cmd = _lookup_string(
            raw_state, "mission_validator_command", "missionValidatorCommand"
        ) or _lookup_string(_safe_object(raw_state.get("mission_validator")), "command")
        if not validator_cmd:
            return AutoresearchCompletionStatus(
                complete=False,
                reason="missing_mission_validator_command",
                validation_mode=validation_mode,
                artifact_path=artifact_path,
            )
        if _lookup_boolean(
            artifact, "passed", "complete", "completed", "valid"
        ) is True or _is_passing_status(artifact.get("status")):
            return AutoresearchCompletionStatus(
                complete=True,
                reason="validator_passed",
                validation_mode=validation_mode,
                artifact_path=artifact_path,
            )
        return AutoresearchCompletionStatus(
            complete=False,
            reason="validator_not_passed",
            validation_mode=validation_mode,
            artifact_path=artifact_path,
        )

    # prompt-architect-artifact mode
    validator_prompt = _lookup_string(
        raw_state, "validator_prompt", "validatorPrompt"
    ) or _lookup_string(artifact, "validator_prompt", "validatorPrompt")
    if not validator_prompt:
        return AutoresearchCompletionStatus(
            complete=False,
            reason="missing_validator_prompt",
            validation_mode=validation_mode,
            artifact_path=artifact_path,
        )
    output_artifact_path = _resolve_output_artifact_path(cwd, raw_state, artifact)
    if not output_artifact_path or not Path(output_artifact_path).exists():
        return AutoresearchCompletionStatus(
            complete=False,
            reason="missing_output_artifact",
            validation_mode=validation_mode,
            artifact_path=artifact_path,
            output_artifact_path=output_artifact_path,
        )
    if not _has_architect_approval(artifact):
        return AutoresearchCompletionStatus(
            complete=False,
            reason="missing_architect_approval",
            validation_mode=validation_mode,
            artifact_path=artifact_path,
            output_artifact_path=output_artifact_path,
        )
    return AutoresearchCompletionStatus(
        complete=True,
        reason="architect_approved",
        validation_mode=validation_mode,
        artifact_path=artifact_path,
        output_artifact_path=output_artifact_path,
    )
