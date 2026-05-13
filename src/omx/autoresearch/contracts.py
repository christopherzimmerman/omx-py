"""Autoresearch contracts and types.

Port of ``src/autoresearch/contracts.ts``.

Sync-only: TS ``loadAutoresearchMissionContract`` was ``async``; we expose a
synchronous equivalent backed by ``subprocess.run`` for the embedded
``git rev-parse --show-toplevel`` call.

Exports
-------
* :class:`AutoresearchKeepPolicy` — string literal type for the keep policy.
* :class:`AutoresearchEvaluatorContract` — parsed evaluator block.
* :class:`ParsedSandboxContract` — full parsed sandbox frontmatter + body.
* :class:`AutoresearchEvaluatorResult` — evaluator stdout decoded to a record.
* :class:`AutoresearchMissionContract` — mission directory descriptor.
* :func:`slugify_mission_name` — TS parity slugifier.
* :func:`parse_sandbox_contract` — sandbox.md frontmatter parser.
* :func:`parse_evaluator_result` — evaluator JSON parser.
* :func:`load_autoresearch_mission_contract` — full mission loader.

The legacy ``ResearchMission`` / ``ResearchCandidate`` dataclasses are kept for
the lightweight :func:`omx.autoresearch.runtime.run_research_loop` helper that
predates the full lifecycle port.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

__all__ = [
    "AutoresearchKeepPolicy",
    "AutoresearchEvaluatorContract",
    "ParsedSandboxContract",
    "AutoresearchEvaluatorResult",
    "AutoresearchMissionContract",
    "ResearchMission",
    "ResearchCandidate",
    "AutoresearchContractError",
    "slugify_mission_name",
    "parse_sandbox_contract",
    "parse_evaluator_result",
    "load_autoresearch_mission_contract",
    "MISSION_DIR_GIT_ERROR",
    "SANDBOX_FRONTMATTER_ERROR",
    "EVALUATOR_BLOCK_ERROR",
    "EVALUATOR_COMMAND_ERROR",
    "EVALUATOR_FORMAT_REQUIRED_ERROR",
    "EVALUATOR_FORMAT_JSON_ERROR",
]


AutoresearchKeepPolicy = Literal["score_improvement", "pass_only"]


# --- TS parity error strings -------------------------------------------------

MISSION_DIR_GIT_ERROR = "mission-dir must be inside a git repository."
SANDBOX_FRONTMATTER_ERROR = (
    "sandbox.md must start with YAML frontmatter containing "
    "evaluator.command and evaluator.format=json."
)
EVALUATOR_BLOCK_ERROR = "sandbox.md frontmatter must define an evaluator block."
EVALUATOR_COMMAND_ERROR = "sandbox.md frontmatter evaluator.command is required."
EVALUATOR_FORMAT_REQUIRED_ERROR = (
    "sandbox.md frontmatter evaluator.format is required and "
    "must be json in autoresearch v1."
)
EVALUATOR_FORMAT_JSON_ERROR = (
    "sandbox.md frontmatter evaluator.format must be json in autoresearch v1."
)


class AutoresearchContractError(ValueError):
    """Raised when a mission/sandbox contract is invalid."""


def _contract_error(message: str) -> AutoresearchContractError:
    return AutoresearchContractError(message)


# --- Dataclasses -------------------------------------------------------------


@dataclass
class AutoresearchEvaluatorContract:
    """Parsed evaluator block from ``sandbox.md`` frontmatter.

    Attributes:
        command: Shell command to run as the evaluator.
        format: Always ``"json"`` in autoresearch v1.
        keep_policy: Optional keep policy override.
    """

    command: str
    format: Literal["json"] = "json"
    keep_policy: AutoresearchKeepPolicy | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"command": self.command, "format": self.format}
        if self.keep_policy is not None:
            d["keep_policy"] = self.keep_policy
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutoresearchEvaluatorContract:
        keep_policy = d.get("keep_policy")
        return cls(
            command=str(d["command"]),
            format="json",
            keep_policy=keep_policy
            if keep_policy in ("score_improvement", "pass_only")
            else None,
        )


@dataclass
class ParsedSandboxContract:
    """Parsed sandbox contract — frontmatter dict + evaluator + body."""

    frontmatter: dict[str, Any]
    evaluator: AutoresearchEvaluatorContract
    body: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "frontmatter": self.frontmatter,
            "evaluator": self.evaluator.to_dict(),
            "body": self.body,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ParsedSandboxContract:
        return cls(
            frontmatter=dict(d.get("frontmatter", {})),
            evaluator=AutoresearchEvaluatorContract.from_dict(d["evaluator"]),
            body=str(d.get("body", "")),
        )


@dataclass
class AutoresearchEvaluatorResult:
    """Decoded evaluator JSON output.

    Attributes:
        pass_: ``True`` when the evaluator declared the candidate passing.
            Stored under the trailing-underscore name because ``pass`` is a
            Python reserved word; serialization uses the wire key ``pass``.
        score: Optional numeric score.
    """

    pass_: bool
    score: float | int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"pass": self.pass_}
        if self.score is not None:
            d["score"] = self.score
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutoresearchEvaluatorResult:
        score = d.get("score")
        return cls(
            pass_=bool(d["pass"]),
            score=score
            if isinstance(score, (int, float)) and not isinstance(score, bool)
            else None,
        )


@dataclass
class AutoresearchMissionContract:
    """Descriptor for a loaded mission directory.

    Mirrors the TS ``AutoresearchMissionContract`` interface verbatim.
    """

    missionDir: str
    repoRoot: str
    missionFile: str
    sandboxFile: str
    missionRelativeDir: str
    missionContent: str
    sandboxContent: str
    sandbox: ParsedSandboxContract
    missionSlug: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "missionDir": self.missionDir,
            "repoRoot": self.repoRoot,
            "missionFile": self.missionFile,
            "sandboxFile": self.sandboxFile,
            "missionRelativeDir": self.missionRelativeDir,
            "missionContent": self.missionContent,
            "sandboxContent": self.sandboxContent,
            "sandbox": self.sandbox.to_dict(),
            "missionSlug": self.missionSlug,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AutoresearchMissionContract:
        return cls(
            missionDir=d["missionDir"],
            repoRoot=d["repoRoot"],
            missionFile=d["missionFile"],
            sandboxFile=d["sandboxFile"],
            missionRelativeDir=d["missionRelativeDir"],
            missionContent=d["missionContent"],
            sandboxContent=d["sandboxContent"],
            sandbox=ParsedSandboxContract.from_dict(d["sandbox"]),
            missionSlug=d["missionSlug"],
        )


# --- Legacy lightweight types (kept for backward compatibility) -------------


@dataclass
class ResearchMission:
    """Lightweight mission definition for :func:`run_research_loop`.

    Predates the full TS-parity ``AutoresearchMissionContract``. Retained
    because callers of the simple loop API depend on it.
    """

    task: str
    max_iterations: int = 10
    evaluation_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "max_iterations": self.max_iterations,
            "evaluation_criteria": self.evaluation_criteria,
            "constraints": self.constraints,
        }


@dataclass
class ResearchCandidate:
    """Lightweight candidate output for :func:`run_research_loop`."""

    iteration: int
    content: str
    score: float = 0.0
    feedback: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "content": self.content,
            "score": self.score,
            "feedback": self.feedback,
        }


# --- git helper --------------------------------------------------------------


def _read_git(repo_path: str, args: list[str]) -> str:
    """Run ``git <args>`` in ``repo_path`` and return trimmed stdout.

    Raises :class:`AutoresearchContractError` with the captured stderr text on
    non-zero exit, matching TS ``readGit`` semantics.
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError) as error:
        raise _contract_error(str(error) or MISSION_DIR_GIT_ERROR) from error

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise _contract_error(stderr or MISSION_DIR_GIT_ERROR)
    return (result.stdout or "").strip()


def _ensure_path_inside(parent_path: str, child_path: str) -> None:
    try:
        rel = os.path.relpath(child_path, parent_path)
    except ValueError as error:  # e.g. different drives on Windows
        raise _contract_error(MISSION_DIR_GIT_ERROR) from error
    if rel == "" or rel == ".":
        return
    # Inside parent if relative path does not start with `..`
    if not rel.startswith(".."):
        return
    raise _contract_error(MISSION_DIR_GIT_ERROR)


# --- slugifier ---------------------------------------------------------------


def slugify_mission_name(value: str) -> str:
    """Slugify a mission directory name. Port of TS ``slugifyMissionName``.

    Algorithm:
      1. Lowercase.
      2. Replace non-alphanumeric runs with ``-``.
      3. Collapse repeated ``-``.
      4. Strip leading/trailing ``-``.
      5. Truncate to 48 chars; fall back to ``"mission"`` when empty.
    """
    s = value.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s)
    s = re.sub(r"^-|-$", "", s)
    s = s[:48]
    return s or "mission"


# --- Frontmatter parser ------------------------------------------------------


_FRONTMATTER_RE = re.compile(r"^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$")
_SECTION_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*$")
_NESTED_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.+)\s*$")


def _extract_frontmatter(content: str) -> tuple[str, str]:
    match = _FRONTMATTER_RE.match(content)
    if not match:
        raise _contract_error(SANDBOX_FRONTMATTER_ERROR)
    return match.group(1) or "", (match.group(2) or "").strip()


def _strip_surrounding_quotes(raw: str) -> str:
    # Mirror TS: `value.replace(/^['"]|['"]$/g, '')`
    out = raw
    if out and out[0] in ("'", '"'):
        out = out[1:]
    if out and out[-1] in ("'", '"'):
        out = out[:-1]
    return out


def _parse_simple_yaml_frontmatter(frontmatter: str) -> dict[str, Any]:
    """Subset YAML parser matching TS ``parseSimpleYamlFrontmatter``.

    Only supports flat key/value pairs and one-level nested sections — exactly
    what the autoresearch sandbox contract allows.
    """
    result: dict[str, Any] = {}
    current_section: str | None = None

    for raw_line in re.split(r"\r?\n", frontmatter):
        # Normalize tabs to two spaces to match TS behavior so the
        # "indented?" check on `line.startsWith(' ')` is stable.
        line = raw_line.replace("\t", "  ")
        trimmed = line.strip()
        if not trimmed or trimmed.startswith("#"):
            continue

        section_match = _SECTION_RE.match(trimmed)
        if section_match:
            current_section = section_match.group(1)
            result[current_section] = {}
            continue

        nested_match = _NESTED_RE.match(trimmed)
        if not nested_match:
            raise _contract_error(f"Unsupported sandbox.md frontmatter line: {trimmed}")

        key = nested_match.group(1)
        raw_value = nested_match.group(2)
        value = _strip_surrounding_quotes(raw_value)

        if line.startswith(" ") or line.startswith("\t"):
            if not current_section:
                raise _contract_error(
                    f"Nested sandbox.md frontmatter key requires a parent section: {trimmed}"
                )
            section = result.get(current_section)
            if not isinstance(section, dict):
                raise _contract_error(
                    f"Invalid sandbox.md frontmatter section: {current_section}"
                )
            section[key] = value
            continue

        result[key] = value
        current_section = None

    return result


def _parse_keep_policy(raw: Any) -> AutoresearchKeepPolicy | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise _contract_error(
            "sandbox.md frontmatter evaluator.keep_policy must be a string when provided."
        )
    normalized = raw.strip().lower()
    if not normalized:
        return None
    if normalized == "pass_only":
        return "pass_only"
    if normalized == "score_improvement":
        return "score_improvement"
    raise _contract_error(
        "sandbox.md frontmatter evaluator.keep_policy must be one of: "
        "score_improvement, pass_only."
    )


def parse_sandbox_contract(content: str) -> ParsedSandboxContract:
    """Parse ``sandbox.md`` contents into a :class:`ParsedSandboxContract`."""
    frontmatter, body = _extract_frontmatter(content)
    parsed_frontmatter = _parse_simple_yaml_frontmatter(frontmatter)
    evaluator_raw = parsed_frontmatter.get("evaluator")

    if not isinstance(evaluator_raw, dict):
        raise _contract_error(EVALUATOR_BLOCK_ERROR)

    cmd_raw = evaluator_raw.get("command")
    fmt_raw = evaluator_raw.get("format")
    command = cmd_raw.strip() if isinstance(cmd_raw, str) else ""
    fmt = fmt_raw.strip().lower() if isinstance(fmt_raw, str) else ""
    keep_policy = _parse_keep_policy(evaluator_raw.get("keep_policy"))

    if not command:
        raise _contract_error(EVALUATOR_COMMAND_ERROR)
    if not fmt:
        raise _contract_error(EVALUATOR_FORMAT_REQUIRED_ERROR)
    if fmt != "json":
        raise _contract_error(EVALUATOR_FORMAT_JSON_ERROR)

    return ParsedSandboxContract(
        frontmatter=parsed_frontmatter,
        evaluator=AutoresearchEvaluatorContract(
            command=command,
            format="json",
            keep_policy=keep_policy,
        ),
        body=body,
    )


def parse_evaluator_result(raw: str) -> AutoresearchEvaluatorResult:
    """Parse evaluator JSON stdout. TS parity with ``parseEvaluatorResult``."""
    import json

    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise _contract_error(
            "Evaluator output must be valid JSON with required boolean pass "
            "and optional numeric score."
        ) from error

    if not isinstance(parsed, dict):
        raise _contract_error("Evaluator output must be a JSON object.")

    pass_raw = parsed.get("pass")
    if not isinstance(pass_raw, bool):
        raise _contract_error("Evaluator output must include boolean pass.")

    score_raw = parsed.get("score")
    if score_raw is not None and not isinstance(score_raw, (int, float)):
        raise _contract_error("Evaluator output score must be numeric when provided.")
    if isinstance(score_raw, bool):
        # JSON booleans are technically `int` in Python; reject explicitly.
        raise _contract_error("Evaluator output score must be numeric when provided.")

    return AutoresearchEvaluatorResult(
        pass_=pass_raw,
        score=score_raw if isinstance(score_raw, (int, float)) else None,
    )


def load_autoresearch_mission_contract(
    mission_dir_arg: str,
) -> AutoresearchMissionContract:
    """Load and validate a mission directory.

    Synchronous port of TS ``loadAutoresearchMissionContract``.
    """
    mission_dir = os.path.abspath(mission_dir_arg)
    if not os.path.exists(mission_dir):
        raise _contract_error(f"mission-dir does not exist: {mission_dir}")

    repo_root = _read_git(mission_dir, ["rev-parse", "--show-toplevel"])
    _ensure_path_inside(repo_root, mission_dir)

    mission_file = os.path.join(mission_dir, "mission.md")
    sandbox_file = os.path.join(mission_dir, "sandbox.md")
    if not os.path.exists(mission_file):
        raise _contract_error(
            f"mission.md is required inside mission-dir: {mission_file}"
        )
    if not os.path.exists(sandbox_file):
        raise _contract_error(
            f"sandbox.md is required inside mission-dir: {sandbox_file}"
        )

    mission_content = Path(mission_file).read_text(encoding="utf-8")
    sandbox_content = Path(sandbox_file).read_text(encoding="utf-8")
    sandbox = parse_sandbox_contract(sandbox_content)

    rel = os.path.relpath(mission_dir, repo_root)
    if not rel or rel == ".":
        mission_relative_dir = os.path.basename(mission_dir)
    else:
        # TS uses POSIX-style separators for the slug source; normalize.
        mission_relative_dir = rel.replace(os.sep, "/")
    mission_slug = slugify_mission_name(mission_relative_dir)

    return AutoresearchMissionContract(
        missionDir=mission_dir,
        repoRoot=repo_root,
        missionFile=mission_file,
        sandboxFile=sandbox_file,
        missionRelativeDir=mission_relative_dir,
        missionContent=mission_content,
        sandboxContent=sandbox_content,
        sandbox=sandbox,
        missionSlug=mission_slug,
    )
