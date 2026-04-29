"""Document refresh enforcer.

Port of src/document-refresh/enforcer.ts.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

from omx.document_refresh.config import (
    DEFAULT_DOCUMENT_REFRESH_RULES,
    DocumentRefreshRule,
)

DOCUMENT_REFRESH_EXEMPTION_PREFIX = "Document-refresh: not-needed |"

RELEASE_COLLATERAL_GLOBS = [
    "CHANGELOG.md",
    "RELEASE_BODY.md",
    "docs/release-notes-*.md",
    "docs/release-body-*.md",
    "docs/qa/release-readiness-*.md",
]

TOOLING_ONLY_GLOBS = [
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "tsconfig.json",
    "tsconfig.*.json",
    "biome.json",
    ".github/workflows/**",
    ".gitignore",
]

FINAL_HANDOFF_MARKER_PATTERNS = [
    re.compile(
        r"\b(?:final handoff|handoff|merge-ready|launch-ready|ready to merge|ready for dev|shippable)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:task|work|implementation|feature|change|verification)\b[\s\S]{0,80}\b(?:complete|completed|done|finished)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:verification|tests?|build|lint)\b[\s\S]{0,120}\b(?:pass|passed|green|success|succeeded)\b",
        re.IGNORECASE,
    ),
]


@dataclass
class ChangedPathRecord:
    """A changed file record from git.

    Attributes:
        status: Git status code.
        path: File path.
        previous_path: Previous path for renames.
    """

    status: str = ""
    path: str = ""
    previous_path: str | None = None


@dataclass
class DocumentRefreshRuleWarning:
    """Warning from a specific rule.

    Attributes:
        rule_id: Rule identifier.
        description: Rule description.
        changed_paths: Changed source paths.
        refresh_targets: Expected refresh targets.
    """

    rule_id: str = ""
    description: str = ""
    changed_paths: list[str] = field(default_factory=list)
    refresh_targets: list[str] = field(default_factory=list)


@dataclass
class DocumentRefreshWarning:
    """Aggregated document refresh warning.

    Attributes:
        scope: Warning scope (commit or final-handoff).
        rules: Individual rule warnings.
        triggering_paths: Paths that triggered warnings.
        expected_targets: Expected refresh targets.
        message: Formatted warning message.
    """

    scope: str = ""  # "commit" | "final-handoff"
    rules: list[DocumentRefreshRuleWarning] = field(default_factory=list)
    triggering_paths: list[str] = field(default_factory=list)
    expected_targets: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class DocumentRefreshEvaluationInput:
    """Input for document refresh evaluation.

    Attributes:
        scope: Evaluation scope.
        changes: Changed path records.
        rules: Refresh rules to apply.
        exemption_text: Exemption text to check.
        local_fresh_targets: Locally fresh target paths.
    """

    scope: str = ""
    changes: list[ChangedPathRecord] = field(default_factory=list)
    rules: list[DocumentRefreshRule] | None = None
    exemption_text: str | None = None
    local_fresh_targets: list[str] = field(default_factory=list)


def _normalize_repo_path(path: str) -> str:
    """Normalize a repo path to forward slashes."""
    return path.replace("\\", "/").lstrip("./")


def _escape_regex(char: str) -> str:
    """Escape a single character for regex."""
    if re.match(r"[|\\{}()\[\]^$+?.]", char):
        return f"\\{char}"
    return char


def glob_to_regexp(glob: str) -> re.Pattern[str]:
    """Convert a glob pattern to a regex.

    Args:
        glob: Glob pattern string.

    Returns:
        Compiled regex pattern.
    """
    normalized = _normalize_repo_path(glob)
    source = "^"
    i = 0
    while i < len(normalized):
        char = normalized[i]
        next_char = normalized[i + 1] if i + 1 < len(normalized) else None
        if char == "*" and next_char == "*":
            i += 1
            if i + 1 < len(normalized) and normalized[i + 1] == "/":
                i += 1
                source += "(?:.*?/)?"
            else:
                source += ".*"
        elif char == "*":
            source += "[^/]*"
        elif char == "?":
            source += "[^/]"
        else:
            source += _escape_regex(char)
        i += 1
    source += "$"
    return re.compile(source)


def path_matches_glob(path: str, glob: str) -> bool:
    """Check if a path matches a glob pattern.

    Args:
        path: File path to check.
        glob: Glob pattern.

    Returns:
        True if the path matches.
    """
    return bool(glob_to_regexp(glob).match(_normalize_repo_path(path)))


def _path_matches_any(path: str, globs: list[str] | None) -> bool:
    """Check if a path matches any of the given globs."""
    return any(path_matches_glob(path, g) for g in (globs or []))


def _is_rename_only(record: ChangedPathRecord) -> bool:
    """Check if a record is a rename-only change."""
    return bool(re.match(r"^R100$", record.status.strip()))


def _is_trigger_only_excluded(record: ChangedPathRecord) -> bool:
    """Check if a record is excluded from triggering."""
    path = _normalize_repo_path(record.path)
    return _path_matches_any(path, TOOLING_ONLY_GLOBS) or _path_matches_any(
        path, RELEASE_COLLATERAL_GLOBS
    )


def _unique(values: list[str]) -> list[str]:
    """Deduplicate and sort normalized paths."""
    return sorted(set(_normalize_repo_path(v) for v in values if v))


def has_document_refresh_exemption(text: str | None) -> bool:
    """Check if text contains a document refresh exemption.

    Args:
        text: Text to check.

    Returns:
        True if an exemption is present.
    """
    if not text:
        return False
    return any(
        line.strip().startswith(DOCUMENT_REFRESH_EXEMPTION_PREFIX)
        for line in text.split("\n")
    )


def parse_git_name_status(text: str) -> list[ChangedPathRecord]:
    """Parse git diff --name-status output.

    Args:
        text: Raw git output.

    Returns:
        List of ChangedPathRecord instances.
    """
    records: list[ChangedPathRecord] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            continue
        parts = [p for p in line.split("\t") if p]
        if len(parts) < 2:
            continue
        status = parts[0]
        if re.match(r"^[RC]\d+$", status):
            if len(parts) >= 3:
                records.append(
                    ChangedPathRecord(
                        status=status,
                        previous_path=_normalize_repo_path(parts[1]),
                        path=_normalize_repo_path(parts[2]),
                    )
                )
        else:
            records.append(
                ChangedPathRecord(
                    status=status,
                    path=_normalize_repo_path(parts[1]),
                )
            )
    return records


def _changed_path_candidates(record: ChangedPathRecord) -> list[str]:
    """Get all candidate paths from a change record."""
    return _unique([p for p in [record.path, record.previous_path or ""] if p])


def _record_matches_rule_source(
    record: ChangedPathRecord, rule: DocumentRefreshRule
) -> bool:
    """Check if a change record matches a rule's source globs."""
    if _is_rename_only(record):
        return False
    if _is_trigger_only_excluded(record):
        return False
    candidates = _changed_path_candidates(record)
    matches_source = any(_path_matches_any(p, rule.source_globs) for p in candidates)
    all_ignored = (
        all(_path_matches_any(p, rule.ignored_globs) for p in candidates)
        if rule.ignored_globs
        else False
    )
    return matches_source and not all_ignored


def _has_rule_refresh(
    changes: list[ChangedPathRecord],
    local_fresh_targets: list[str],
    rule: DocumentRefreshRule,
) -> bool:
    """Check if a rule's refresh targets are satisfied."""
    for record in changes:
        if _is_rename_only(record):
            continue
        if any(
            _path_matches_any(p, rule.refresh_targets)
            for p in _changed_path_candidates(record)
        ):
            return True
    return any(_path_matches_any(p, rule.refresh_targets) for p in local_fresh_targets)


def evaluate_document_refresh(
    input_data: DocumentRefreshEvaluationInput,
) -> DocumentRefreshWarning | None:
    """Evaluate whether document refresh is needed.

    Args:
        input_data: Evaluation input.

    Returns:
        DocumentRefreshWarning if refresh is needed, else None.
    """
    if has_document_refresh_exemption(input_data.exemption_text):
        return None

    changes = [r for r in input_data.changes if r.path.strip()]
    if not changes:
        return None

    rules = input_data.rules or DEFAULT_DOCUMENT_REFRESH_RULES
    local_fresh = (
        _unique(input_data.local_fresh_targets)
        if input_data.scope == "final-handoff"
        else []
    )
    warnings: list[DocumentRefreshRuleWarning] = []

    for rule in rules:
        triggering = []
        for record in changes:
            if _record_matches_rule_source(record, rule):
                triggering.extend(_changed_path_candidates(record))
        changed_paths = _unique(
            [p for p in triggering if _path_matches_any(p, rule.source_globs)]
        )
        if not changed_paths:
            continue
        if _has_rule_refresh(changes, local_fresh, rule):
            continue
        warnings.append(
            DocumentRefreshRuleWarning(
                rule_id=rule.id,
                description=rule.description,
                changed_paths=changed_paths,
                refresh_targets=list(rule.refresh_targets),
            )
        )

    if not warnings:
        return None

    triggering_paths = _unique([p for w in warnings for p in w.changed_paths])
    expected_targets = _unique([t for w in warnings for t in w.refresh_targets])
    return DocumentRefreshWarning(
        scope=input_data.scope,
        rules=warnings,
        triggering_paths=triggering_paths,
        expected_targets=expected_targets,
        message=format_document_refresh_warning(
            DocumentRefreshWarning(
                scope=input_data.scope,
                rules=warnings,
                triggering_paths=triggering_paths,
                expected_targets=expected_targets,
                message="",
            )
        ),
    )


def is_final_handoff_document_refresh_candidate(text: str | None) -> bool:
    """Check if text indicates a final handoff that should trigger refresh check.

    Args:
        text: Text to analyze.

    Returns:
        True if the text looks like a final handoff.
    """
    message = (text or "").strip()
    if not message:
        return False
    if has_document_refresh_exemption(message):
        return True
    return any(p.search(message) for p in FINAL_HANDOFF_MARKER_PATTERNS)


def format_document_refresh_warning(warning: DocumentRefreshWarning) -> str:
    """Format a document refresh warning as a human-readable message.

    Args:
        warning: Warning to format.

    Returns:
        Formatted warning string.
    """
    seam = (
        "Bash git commit uses the staged diff only"
        if warning.scope == "commit"
        else "final handoff uses staged + unstaged changes and fresh local .omx planning/spec files"
    )
    rule_lines = "\n".join(f"- {r.rule_id}: {r.description}" for r in warning.rules)
    path_lines = "\n".join(f"- {p}" for p in warning.triggering_paths[:8])
    target_lines = "\n".join(f"- {t}" for t in warning.expected_targets[:10])
    return "\n".join(
        [
            "Document-refresh warning: mapped code or test-contract changes may need a planning-spec/product-doc refresh.",
            f"Scope: {seam}. This warning is agent-only and does not add CI/pre-commit hard blocking.",
            "Triggered rule(s):",
            rule_lines,
            "Changed path(s):",
            path_lines,
            "Expected refresh target(s):",
            target_lines,
            f"If no refresh is needed, acknowledge explicitly with: {DOCUMENT_REFRESH_EXEMPTION_PREFIX} <reason>",
        ]
    )


def read_staged_git_changes(cwd: str) -> list[ChangedPathRecord] | None:
    """Read staged git changes.

    Args:
        cwd: Working directory.

    Returns:
        List of ChangedPathRecord or None on error.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-status"],
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
        )
        return parse_git_name_status(result.stdout)
    except (OSError, subprocess.SubprocessError):
        return None


def read_staged_and_unstaged_git_changes(cwd: str) -> list[ChangedPathRecord] | None:
    """Read both staged and unstaged git changes.

    Args:
        cwd: Working directory.

    Returns:
        Combined list of ChangedPathRecord or None on error.
    """
    staged = read_staged_git_changes(cwd)
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status"],
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
        )
        unstaged = parse_git_name_status(result.stdout)
    except (OSError, subprocess.SubprocessError):
        return None
    if staged is None:
        return None
    return [*staged, *unstaged]
