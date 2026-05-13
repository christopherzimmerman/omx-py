"""Tests for omx.autoresearch.contracts.

Covers the slugifier, sandbox frontmatter parser, evaluator-result parser, and
the mission contract loader (with git subprocess monkeypatched).
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from omx.autoresearch import contracts
from omx.autoresearch.contracts import (
    AutoresearchContractError,
    AutoresearchEvaluatorContract,
    AutoresearchEvaluatorResult,
    AutoresearchMissionContract,
    EVALUATOR_BLOCK_ERROR,
    EVALUATOR_COMMAND_ERROR,
    EVALUATOR_FORMAT_JSON_ERROR,
    MISSION_DIR_GIT_ERROR,
    ParsedSandboxContract,
    ResearchCandidate,
    ResearchMission,
    SANDBOX_FRONTMATTER_ERROR,
    load_autoresearch_mission_contract,
    parse_evaluator_result,
    parse_sandbox_contract,
    slugify_mission_name,
)


# --- slugify -----------------------------------------------------------------


class TestSlugifyMissionName(unittest.TestCase):
    def test_basic_alphanumeric(self) -> None:
        self.assertEqual(slugify_mission_name("Hello World"), "hello-world")

    def test_collapses_repeated_separators(self) -> None:
        self.assertEqual(slugify_mission_name("a__b___c"), "a-b-c")

    def test_strips_leading_and_trailing_separators(self) -> None:
        self.assertEqual(slugify_mission_name("---foo---"), "foo")

    def test_handles_unicode_by_replacement(self) -> None:
        # Non-ASCII chars are not a-z0-9 → replaced with separators
        self.assertEqual(slugify_mission_name("café-au-lait"), "caf-au-lait")

    def test_truncates_to_48_chars(self) -> None:
        slug = slugify_mission_name("a" * 80)
        self.assertEqual(len(slug), 48)
        self.assertEqual(slug, "a" * 48)

    def test_empty_falls_back_to_mission(self) -> None:
        self.assertEqual(slugify_mission_name(""), "mission")
        self.assertEqual(slugify_mission_name("///---"), "mission")

    def test_already_slug(self) -> None:
        self.assertEqual(slugify_mission_name("already-slug-99"), "already-slug-99")

    def test_directory_path(self) -> None:
        # TS uses POSIX-relative dir; the slugifier collapses `/` to `-`
        self.assertEqual(
            slugify_mission_name("research/missions/my-experiment-001"),
            "research-missions-my-experiment-001",
        )


# --- parse_sandbox_contract --------------------------------------------------


class TestParseSandboxContract(unittest.TestCase):
    def _build(
        self,
        *,
        evaluator_command: str = "node ./scripts/eval.js",
        evaluator_format: str | None = "json",
        keep_policy: str | None = None,
        body: str = "Sandbox body text.",
        include_evaluator: bool = True,
        extra_top: str | None = None,
    ) -> str:
        lines = ["---"]
        if extra_top:
            lines.append(extra_top)
        if include_evaluator:
            lines.append("evaluator:")
            if evaluator_command is not None:
                lines.append(f"  command: {evaluator_command}")
            if evaluator_format is not None:
                lines.append(f"  format: {evaluator_format}")
            if keep_policy is not None:
                lines.append(f"  keep_policy: {keep_policy}")
        lines.append("---")
        lines.append(body)
        return "\n".join(lines)

    def test_happy_path_default_policy(self) -> None:
        text = self._build()
        parsed = parse_sandbox_contract(text)
        self.assertIsInstance(parsed, ParsedSandboxContract)
        self.assertEqual(parsed.evaluator.command, "node ./scripts/eval.js")
        self.assertEqual(parsed.evaluator.format, "json")
        self.assertIsNone(parsed.evaluator.keep_policy)
        self.assertEqual(parsed.body, "Sandbox body text.")

    def test_pass_only_keep_policy(self) -> None:
        parsed = parse_sandbox_contract(self._build(keep_policy="pass_only"))
        self.assertEqual(parsed.evaluator.keep_policy, "pass_only")

    def test_score_improvement_keep_policy(self) -> None:
        parsed = parse_sandbox_contract(self._build(keep_policy="score_improvement"))
        self.assertEqual(parsed.evaluator.keep_policy, "score_improvement")

    def test_invalid_keep_policy(self) -> None:
        with self.assertRaises(AutoresearchContractError) as cm:
            parse_sandbox_contract(self._build(keep_policy="something-else"))
        self.assertIn("keep_policy", str(cm.exception))

    def test_quoted_values(self) -> None:
        text = "---\nevaluator:\n  command: \"node ./scripts/eval.js\"\n  format: 'json'\n---\nbody"
        parsed = parse_sandbox_contract(text)
        self.assertEqual(parsed.evaluator.command, "node ./scripts/eval.js")
        self.assertEqual(parsed.evaluator.format, "json")

    def test_missing_frontmatter(self) -> None:
        with self.assertRaises(AutoresearchContractError) as cm:
            parse_sandbox_contract("just body text, no frontmatter")
        self.assertEqual(str(cm.exception), SANDBOX_FRONTMATTER_ERROR)

    def test_missing_evaluator_block(self) -> None:
        text = "---\nfoo: bar\n---\nbody"
        with self.assertRaises(AutoresearchContractError) as cm:
            parse_sandbox_contract(text)
        self.assertEqual(str(cm.exception), EVALUATOR_BLOCK_ERROR)

    def test_missing_command(self) -> None:
        with self.assertRaises(AutoresearchContractError) as cm:
            parse_sandbox_contract(self._build(evaluator_command=""))
        self.assertEqual(str(cm.exception), EVALUATOR_COMMAND_ERROR)

    def test_missing_format(self) -> None:
        with self.assertRaises(AutoresearchContractError) as cm:
            parse_sandbox_contract(self._build(evaluator_format=None))
        # The TS contract differentiates required vs. wrong-value; ours mirrors.
        self.assertIn("format", str(cm.exception))

    def test_non_json_format(self) -> None:
        with self.assertRaises(AutoresearchContractError) as cm:
            parse_sandbox_contract(self._build(evaluator_format="yaml"))
        self.assertEqual(str(cm.exception), EVALUATOR_FORMAT_JSON_ERROR)

    def test_skips_comments_and_blanks(self) -> None:
        text = (
            "---\n"
            "# a top-level comment\n"
            "\n"
            "evaluator:\n"
            "  command: bash run.sh\n"
            "  format: json\n"
            "---\n"
            "body"
        )
        parsed = parse_sandbox_contract(text)
        self.assertEqual(parsed.evaluator.command, "bash run.sh")

    def test_unsupported_line_raises(self) -> None:
        with self.assertRaises(AutoresearchContractError):
            parse_sandbox_contract("---\n!!nope\n---\nbody")

    def test_keep_policy_non_string_raises(self) -> None:
        # Reach inside via parse_simple_yaml — provide a manually crafted dict
        with self.assertRaises(AutoresearchContractError) as cm:
            contracts._parse_keep_policy(123)
        self.assertIn("must be a string", str(cm.exception))

    def test_keep_policy_whitespace_only(self) -> None:
        self.assertIsNone(contracts._parse_keep_policy("   "))

    def test_keep_policy_none(self) -> None:
        self.assertIsNone(contracts._parse_keep_policy(None))

    def test_nested_section_required_for_indented_key(self) -> None:
        # Tab-indented key without preceding section header
        text = "---\n\tkey: value\n---\nbody"
        with self.assertRaises(AutoresearchContractError):
            parse_sandbox_contract(text)


# --- parse_evaluator_result -------------------------------------------------


class TestParseEvaluatorResult(unittest.TestCase):
    def test_pass_only(self) -> None:
        result = parse_evaluator_result('{"pass": true}')
        self.assertTrue(result.pass_)
        self.assertIsNone(result.score)

    def test_pass_with_numeric_score(self) -> None:
        result = parse_evaluator_result('{"pass": true, "score": 0.85}')
        self.assertTrue(result.pass_)
        self.assertEqual(result.score, 0.85)

    def test_fail_with_integer_score(self) -> None:
        result = parse_evaluator_result('{"pass": false, "score": 0}')
        self.assertFalse(result.pass_)
        self.assertEqual(result.score, 0)

    def test_invalid_json(self) -> None:
        with self.assertRaises(AutoresearchContractError):
            parse_evaluator_result("not json")

    def test_array_top_level(self) -> None:
        with self.assertRaises(AutoresearchContractError):
            parse_evaluator_result("[1, 2, 3]")

    def test_missing_pass(self) -> None:
        with self.assertRaises(AutoresearchContractError):
            parse_evaluator_result('{"score": 0.5}')

    def test_non_boolean_pass(self) -> None:
        with self.assertRaises(AutoresearchContractError):
            parse_evaluator_result('{"pass": "yes"}')

    def test_non_numeric_score(self) -> None:
        with self.assertRaises(AutoresearchContractError):
            parse_evaluator_result('{"pass": true, "score": "high"}')

    def test_explicit_null_score(self) -> None:
        result = parse_evaluator_result('{"pass": true, "score": null}')
        self.assertIsNone(result.score)


# --- contracts data classes -------------------------------------------------


class TestContractsDataclasses(unittest.TestCase):
    def test_evaluator_contract_roundtrip_default(self) -> None:
        contract = AutoresearchEvaluatorContract(command="node ./eval.js")
        self.assertEqual(
            contract.to_dict(), {"command": "node ./eval.js", "format": "json"}
        )
        restored = AutoresearchEvaluatorContract.from_dict(contract.to_dict())
        self.assertEqual(restored.command, contract.command)
        self.assertIsNone(restored.keep_policy)

    def test_evaluator_contract_roundtrip_with_policy(self) -> None:
        contract = AutoresearchEvaluatorContract(
            command="x", format="json", keep_policy="pass_only"
        )
        d = contract.to_dict()
        self.assertEqual(d["keep_policy"], "pass_only")
        restored = AutoresearchEvaluatorContract.from_dict(d)
        self.assertEqual(restored.keep_policy, "pass_only")

    def test_parsed_sandbox_contract_roundtrip(self) -> None:
        sc = ParsedSandboxContract(
            frontmatter={"evaluator": {"command": "x", "format": "json"}},
            evaluator=AutoresearchEvaluatorContract(command="x"),
            body="hello",
        )
        d = sc.to_dict()
        restored = ParsedSandboxContract.from_dict(d)
        self.assertEqual(restored.body, "hello")
        self.assertEqual(restored.evaluator.command, "x")

    def test_evaluator_result_roundtrip(self) -> None:
        r = AutoresearchEvaluatorResult(pass_=True, score=0.5)
        self.assertEqual(r.to_dict(), {"pass": True, "score": 0.5})
        restored = AutoresearchEvaluatorResult.from_dict(r.to_dict())
        self.assertTrue(restored.pass_)
        self.assertEqual(restored.score, 0.5)

    def test_mission_contract_roundtrip(self) -> None:
        contract = AutoresearchMissionContract(
            missionDir="/tmp/mission",
            repoRoot="/tmp",
            missionFile="/tmp/mission/mission.md",
            sandboxFile="/tmp/mission/sandbox.md",
            missionRelativeDir="mission",
            missionContent="task",
            sandboxContent="sandbox",
            sandbox=ParsedSandboxContract(
                frontmatter={},
                evaluator=AutoresearchEvaluatorContract(command="x"),
                body="",
            ),
            missionSlug="mission",
        )
        restored = AutoresearchMissionContract.from_dict(contract.to_dict())
        self.assertEqual(restored.missionDir, "/tmp/mission")
        self.assertEqual(restored.missionSlug, "mission")


# --- legacy lightweight dataclasses -----------------------------------------


class TestLegacyDataclasses(unittest.TestCase):
    def test_research_mission_to_dict(self) -> None:
        mission = ResearchMission(task="study X", max_iterations=3)
        self.assertEqual(
            mission.to_dict(),
            {
                "task": "study X",
                "max_iterations": 3,
                "evaluation_criteria": [],
                "constraints": [],
            },
        )

    def test_research_candidate_to_dict(self) -> None:
        c = ResearchCandidate(iteration=1, content="x", score=0.5, feedback="meh")
        self.assertEqual(
            c.to_dict(),
            {"iteration": 1, "content": "x", "score": 0.5, "feedback": "meh"},
        )


# --- load_autoresearch_mission_contract -------------------------------------


def _make_subprocess_result(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> mock.MagicMock:
    fake = mock.MagicMock()
    fake.stdout = stdout
    fake.stderr = stderr
    fake.returncode = returncode
    return fake


class TestLoadMissionContract(unittest.TestCase):
    def _write_mission_dir(
        self, tmpdir: str, *, slug_dir: str = "research/missions/m1"
    ) -> str:
        mission_dir = Path(tmpdir) / slug_dir
        mission_dir.mkdir(parents=True, exist_ok=True)
        (mission_dir / "mission.md").write_text(
            "Mission objective.\n", encoding="utf-8"
        )
        sandbox = (
            "---\n"
            "evaluator:\n"
            "  command: node ./scripts/eval.js\n"
            "  format: json\n"
            "  keep_policy: pass_only\n"
            "---\n"
            "Sandbox body."
        )
        (mission_dir / "sandbox.md").write_text(sandbox, encoding="utf-8")
        return str(mission_dir)

    def test_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mission_dir = self._write_mission_dir(tmpdir)
            with mock.patch(
                "omx.autoresearch.contracts.subprocess.run",
                return_value=_make_subprocess_result(stdout=tmpdir, returncode=0),
            ):
                contract = load_autoresearch_mission_contract(mission_dir)
            self.assertEqual(contract.missionDir, os.path.abspath(mission_dir))
            self.assertEqual(contract.repoRoot, tmpdir)
            self.assertEqual(contract.missionRelativeDir, "research/missions/m1")
            self.assertEqual(contract.missionSlug, "research-missions-m1")
            self.assertEqual(
                contract.sandbox.evaluator.command, "node ./scripts/eval.js"
            )
            self.assertEqual(contract.sandbox.evaluator.keep_policy, "pass_only")
            self.assertIn("Mission objective", contract.missionContent)

    def test_missing_mission_dir(self) -> None:
        with self.assertRaises(AutoresearchContractError) as cm:
            load_autoresearch_mission_contract("/does/not/exist/xyz123")
        self.assertIn("mission-dir does not exist", str(cm.exception))

    def test_missing_mission_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mission_dir = Path(tmpdir) / "m"
            mission_dir.mkdir()
            (mission_dir / "sandbox.md").write_text(
                "---\nevaluator:\n  command: x\n  format: json\n---\nbody",
                encoding="utf-8",
            )
            with mock.patch(
                "omx.autoresearch.contracts.subprocess.run",
                return_value=_make_subprocess_result(stdout=tmpdir, returncode=0),
            ):
                with self.assertRaises(AutoresearchContractError) as cm:
                    load_autoresearch_mission_contract(str(mission_dir))
            self.assertIn("mission.md is required", str(cm.exception))

    def test_missing_sandbox_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mission_dir = Path(tmpdir) / "m"
            mission_dir.mkdir()
            (mission_dir / "mission.md").write_text("task", encoding="utf-8")
            with mock.patch(
                "omx.autoresearch.contracts.subprocess.run",
                return_value=_make_subprocess_result(stdout=tmpdir, returncode=0),
            ):
                with self.assertRaises(AutoresearchContractError) as cm:
                    load_autoresearch_mission_contract(str(mission_dir))
            self.assertIn("sandbox.md is required", str(cm.exception))

    def test_git_failure_surfaces_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            mission_dir = Path(tmpdir) / "m"
            mission_dir.mkdir()
            (mission_dir / "mission.md").write_text("task", encoding="utf-8")
            (mission_dir / "sandbox.md").write_text(
                "---\nevaluator:\n  command: x\n  format: json\n---\nbody",
                encoding="utf-8",
            )
            with mock.patch(
                "omx.autoresearch.contracts.subprocess.run",
                return_value=_make_subprocess_result(
                    stderr="fatal: not a git repository", returncode=128
                ),
            ):
                with self.assertRaises(AutoresearchContractError) as cm:
                    load_autoresearch_mission_contract(str(mission_dir))
            self.assertIn("fatal: not a git repository", str(cm.exception))

    def test_mission_dir_equal_to_repo_root_uses_basename(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # mission dir IS the repo root
            (Path(tmpdir) / "mission.md").write_text("task", encoding="utf-8")
            (Path(tmpdir) / "sandbox.md").write_text(
                "---\nevaluator:\n  command: x\n  format: json\n---\nbody",
                encoding="utf-8",
            )
            with mock.patch(
                "omx.autoresearch.contracts.subprocess.run",
                return_value=_make_subprocess_result(stdout=tmpdir, returncode=0),
            ):
                contract = load_autoresearch_mission_contract(tmpdir)
            self.assertEqual(contract.missionRelativeDir, os.path.basename(tmpdir))

    def test_mission_dir_outside_repo_root_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as outer:
            mission_dir = Path(outer) / "m"
            mission_dir.mkdir()
            (mission_dir / "mission.md").write_text("task", encoding="utf-8")
            (mission_dir / "sandbox.md").write_text(
                "---\nevaluator:\n  command: x\n  format: json\n---\nbody",
                encoding="utf-8",
            )
            # Use a fake repo root path inside outer/other so mission_dir is outside it.
            fake_repo = Path(outer) / "other-repo"
            fake_repo.mkdir()
            with mock.patch(
                "omx.autoresearch.contracts.subprocess.run",
                return_value=_make_subprocess_result(
                    stdout=str(fake_repo), returncode=0
                ),
            ):
                with self.assertRaises(AutoresearchContractError) as cm:
                    load_autoresearch_mission_contract(str(mission_dir))
            self.assertEqual(str(cm.exception), MISSION_DIR_GIT_ERROR)


if __name__ == "__main__":
    unittest.main()
