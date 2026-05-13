"""Tests for ``omx.team.role_router``.

Covers both layers:

* Layer 1: prompt loading utilities (``load_role_prompt``, ``is_known_role``,
  ``list_available_roles``).
* Layer 2: heuristic role routing (``route_task_to_role`` / ``RoleRouterResult``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omx.team.role_router import (
    RoleRouterResult,
    infer_role_from_task_legacy,
    is_known_role,
    list_available_roles,
    load_role_prompt,
    route_task_to_role,
    route_task_to_role_legacy,
)


# ─── Layer 1: prompt loading ────────────────────────────────────────────────


@pytest.fixture
def prompts_dir(tmp_path: Path) -> Path:
    """Create a fake prompts directory with a handful of role files."""
    (tmp_path / "executor.md").write_text("You are the executor.\n", encoding="utf-8")
    (tmp_path / "test-engineer.md").write_text(
        "  You are the test engineer.  \n", encoding="utf-8"
    )
    (tmp_path / "blank.md").write_text("   \n\n", encoding="utf-8")
    # Unrelated file — must be ignored by listing.
    (tmp_path / "notes.txt").write_text("not a role", encoding="utf-8")
    # Sub-directory — must be ignored by listing.
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "ignored.md").write_text("ignored", encoding="utf-8")
    return tmp_path


def test_load_role_prompt_returns_trimmed_content(prompts_dir: Path) -> None:
    assert load_role_prompt("executor", prompts_dir) == "You are the executor."
    assert (
        load_role_prompt("test-engineer", prompts_dir) == "You are the test engineer."
    )


def test_load_role_prompt_missing_file_returns_none(prompts_dir: Path) -> None:
    assert load_role_prompt("nonexistent-role", prompts_dir) is None


def test_load_role_prompt_blank_content_returns_none(prompts_dir: Path) -> None:
    """A prompt file that is only whitespace must read as ``None``."""
    assert load_role_prompt("blank", prompts_dir) is None


def test_load_role_prompt_rejects_invalid_role_names(prompts_dir: Path) -> None:
    # Plant a malicious file to prove the regex blocks it without reading.
    (prompts_dir / "../evil.md").parent  # noqa: B018 - path construction only
    assert load_role_prompt("../evil", prompts_dir) is None
    assert load_role_prompt("UPPER", prompts_dir) is None
    assert load_role_prompt("with_underscore", prompts_dir) is None
    assert load_role_prompt("", prompts_dir) is None
    assert load_role_prompt("9-leading-digit", prompts_dir) is None


def test_load_role_prompt_accepts_string_dir(prompts_dir: Path) -> None:
    """``prompts_dir`` may be passed as ``str`` as well as ``Path``."""
    assert load_role_prompt("executor", str(prompts_dir)) == "You are the executor."


def test_is_known_role_returns_true_for_present_role(prompts_dir: Path) -> None:
    assert is_known_role("executor", prompts_dir) is True
    assert is_known_role("test-engineer", prompts_dir) is True


def test_is_known_role_returns_false_for_missing_or_invalid(prompts_dir: Path) -> None:
    assert is_known_role("nonexistent", prompts_dir) is False
    assert is_known_role("UPPER", prompts_dir) is False
    assert is_known_role("../evil", prompts_dir) is False


def test_list_available_roles_returns_sorted_md_files(prompts_dir: Path) -> None:
    roles = list_available_roles(prompts_dir)
    assert roles == ["blank", "executor", "test-engineer"]


def test_list_available_roles_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert list_available_roles(tmp_path / "does-not-exist") == []


def test_list_available_roles_ignores_subdirectories(prompts_dir: Path) -> None:
    # The fixture's subdir contains ``ignored.md`` — it must not appear.
    assert "ignored" not in list_available_roles(prompts_dir)


# ─── Layer 2: heuristic role routing ────────────────────────────────────────


def test_route_returns_role_router_result_dataclass() -> None:
    result = route_task_to_role("Fix the build", "")
    assert isinstance(result, RoleRouterResult)
    assert result.role == "build-fixer"
    assert result.confidence == "high"
    assert "build" in result.reason


def test_route_build_fix_intent_takes_priority() -> None:
    result = route_task_to_role("Fix TypeScript compilation errors in src/", "")
    assert result.role == "build-fixer"
    assert result.confidence == "high"


def test_route_debug_intent() -> None:
    result = route_task_to_role(
        "Investigate root cause of regression in checkout flow", ""
    )
    assert result.role == "debugger"
    assert result.confidence == "high"


def test_route_local_exploration_intent() -> None:
    result = route_task_to_role(
        "Find all files that reference the legacy session repo", ""
    )
    assert result.role == "explore"
    assert result.confidence == "high"


def test_route_implementation_heavy_dependency_uses_fallback() -> None:
    """Dependency words in implementation-heavy replacement work → fallback medium."""
    result = route_task_to_role(
        "Migrate the npm package and wire the new client adapter into our handlers",
        "",
        None,
        "executor",
    )
    assert result.role == "executor"
    assert result.confidence == "medium"
    assert "dependency" in result.reason


def test_route_dependency_evaluation_intent() -> None:
    result = route_task_to_role(
        "Evaluate candidate npm packages and compare license and maintenance trade-offs",
        "",
    )
    assert result.role == "dependency-expert"
    assert result.confidence == "high"


def test_route_research_intent_official_docs() -> None:
    result = route_task_to_role(
        "Research official docs for the new API to verify version compatibility", ""
    )
    assert result.role == "researcher"
    assert result.confidence == "high"


def test_route_docs_intent() -> None:
    result = route_task_to_role("Update README with new migration guide", "")
    assert result.role == "writer"
    assert result.confidence == "high"


def test_route_design_intent() -> None:
    result = route_task_to_role("Design responsive layout for the settings page", "")
    assert result.role == "designer"
    assert result.confidence == "high"


def test_route_cleanup_intent() -> None:
    result = route_task_to_role(
        "Refactor and simplify the checkout module to reduce complexity", ""
    )
    assert result.role == "code-simplifier"
    assert result.confidence == "high"


def test_route_review_intent_quality() -> None:
    result = route_task_to_role("Review the new pricing module for quality issues", "")
    assert result.role == "quality-reviewer"
    assert result.confidence == "high"


def test_route_review_intent_security_domain() -> None:
    result = route_task_to_role(
        "Audit the authentication module for XSS and injection vulnerabilities", ""
    )
    assert result.role == "security-reviewer"
    assert result.confidence == "high"
    assert "security" in result.reason


def test_route_verification_intent_test_engineer() -> None:
    result = route_task_to_role(
        "Add unit tests and integration tests for the parser", ""
    )
    assert result.role == "test-engineer"
    assert result.confidence == "high"


def test_route_implementation_with_security_uses_fallback_medium() -> None:
    result = route_task_to_role(
        "Implement OAuth authentication support",
        "",
        None,
        "executor",
    )
    assert result.role == "executor"
    assert result.confidence == "medium"
    assert "security" in result.reason or "auth" in result.reason


def test_route_keyword_score_high_when_two_matches() -> None:
    """Multiple keywords from the same category → high confidence keyword score."""
    # "tailwind" + "component" are both in the designer category and avoid
    # triggering the higher-priority design intent regex.
    result = route_task_to_role("Polish the tailwind component visuals", "")
    assert result.role == "designer"
    assert result.confidence == "high"
    assert "2 keywords" in result.reason


def test_route_keyword_score_medium_when_single_match() -> None:
    result = route_task_to_role("Run mocha", "")
    assert result.role == "test-engineer"
    assert result.confidence == "medium"
    assert 'matched keyword "mocha"' in result.reason


def test_route_no_match_uses_fallback_low() -> None:
    result = route_task_to_role("xyz abc 123", "", None, "executor")
    assert result.role == "executor"
    assert result.confidence == "low"
    assert "no keyword match" in result.reason


def test_route_phase_context_low_confidence_for_team_verify() -> None:
    result = route_task_to_role("xyz abc 123", "", "team-verify", "executor")
    assert result.role == "executor"  # fallback per plan
    assert result.confidence == "low"
    assert "team-verify" in result.reason
    assert "verifier" in result.reason


def test_route_phase_context_unknown_phase_uses_plain_fallback() -> None:
    result = route_task_to_role("xyz abc 123", "", "team-exec", "executor")
    assert result.role == "executor"
    assert result.confidence == "low"
    # No phase suggestion when phase isn't in PHASE_CONTEXT_LABELS.
    assert "team-exec" not in result.reason


def test_route_combines_subject_and_description() -> None:
    """``task_subject`` and ``task_description`` are concatenated before matching."""
    result = route_task_to_role(
        "Refresh notes", "Update the changelog and README", None, "executor"
    )
    assert result.role == "writer"
    assert result.confidence == "high"


def test_route_korean_debug_keyword_triggers_debugger() -> None:
    """Korean lane intent regex parity check."""
    # 디버그 = "debug"
    result = route_task_to_role("디버그 후 원인 파악", "")
    assert result.role == "debugger"
    assert result.confidence == "high"


def test_route_default_fallback_is_executor() -> None:
    """Default ``fallback_role`` parameter is ``executor``."""
    result = route_task_to_role("xyz abc 123")
    assert result.role == "executor"
    assert result.confidence == "low"


def test_role_router_result_is_immutable() -> None:
    """``RoleRouterResult`` is a frozen dataclass."""
    result = route_task_to_role("Fix the build", "")
    with pytest.raises(Exception):  # FrozenInstanceError
        result.role = "executor"  # type: ignore[misc]


# ─── Legacy helpers (preserved API) ─────────────────────────────────────────


def test_infer_role_from_task_legacy_keyword_match() -> None:
    assert infer_role_from_task_legacy("Add unit tests") == "verifier"
    assert infer_role_from_task_legacy("Debug crash") == "debugger"
    assert infer_role_from_task_legacy("Deploy to prod") == "devops"


def test_infer_role_from_task_legacy_file_extension_fallback() -> None:
    assert infer_role_from_task_legacy("Misc work", ["src/style.css"]) == "designer"
    assert infer_role_from_task_legacy("Misc work", ["src/query.sql"]) == "data-analyst"
    assert infer_role_from_task_legacy("Misc work", []) == "executor"


def test_route_task_to_role_legacy_restricts_to_available_roles() -> None:
    task = {"description": "Add tests"}
    # ``verifier`` not in roster → falls back to ``executor`` when present.
    assert route_task_to_role_legacy(task, ["executor", "designer"]) == "executor"
    # ``executor`` not available either → first available wins.
    assert route_task_to_role_legacy(task, ["designer", "writer"]) == "designer"
    # Inferred role IS in roster → returned directly.
    assert route_task_to_role_legacy(task, ["verifier", "executor"]) == "verifier"
