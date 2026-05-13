"""Tests for omx.autoresearch.runtime — full lifecycle port coverage.

Subprocess-calling functions (``run_autoresearch_evaluator``,
``assert_reset_safe_worktree``, the prepare/resume/process paths) are exercised
via :mod:`unittest.mock` so the tests are hermetic — they never invoke git or
spawn an actual evaluator.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest import mock

from omx.autoresearch.contracts import (
    AutoresearchEvaluatorContract,
    AutoresearchMissionContract,
    ParsedSandboxContract,
)
from omx.autoresearch.runtime import (
    AUTORESEARCH_RESULTS_HEADER,
    AutoresearchCandidateArtifact,
    AutoresearchEvaluationRecord,
    AutoresearchRunManifest,
    ResearchCandidate,
    ResearchMission,
    assert_reset_safe_worktree,
    build_autoresearch_instructions,
    build_autoresearch_run_tag,
    count_trailing_autoresearch_noops,
    decide_autoresearch_outcome,
    finalize_autoresearch_run_state,
    load_autoresearch_run_manifest,
    materialize_autoresearch_mission_to_worktree,
    parse_autoresearch_candidate_artifact,
    prepare_autoresearch_runtime,
    process_autoresearch_candidate,
    run_autoresearch_evaluator,
    run_research_loop,
    stop_autoresearch_runtime,
)


# --- helpers ----------------------------------------------------------------


def _make_contract(
    *,
    mission_dir: str = "/tmp/mission",
    repo_root: str = "/tmp",
    mission_file: str = "/tmp/mission/mission.md",
    sandbox_file: str = "/tmp/mission/sandbox.md",
    mission_relative_dir: str = "mission",
    mission_content: str = "Mission goes here.",
    sandbox_content: str = "---\nevaluator:\n  command: echo {}\n  format: json\n---\nbody",
    keep_policy: str | None = None,
    command: str = "echo {}",
) -> AutoresearchMissionContract:
    return AutoresearchMissionContract(
        missionDir=mission_dir,
        repoRoot=repo_root,
        missionFile=mission_file,
        sandboxFile=sandbox_file,
        missionRelativeDir=mission_relative_dir,
        missionContent=mission_content,
        sandboxContent=sandbox_content,
        sandbox=ParsedSandboxContract(
            frontmatter={},
            evaluator=AutoresearchEvaluatorContract(
                command=command,
                format="json",
                keep_policy=keep_policy,  # type: ignore[arg-type]
            ),
            body="Sandbox body",
        ),
        missionSlug="mission",
    )


def _make_manifest(
    *,
    run_dir: str,
    worktree_path: str,
    project_root: str,
    keep_policy: str = "score_improvement",
    last_kept_score: float | int | None = None,
    last_kept_commit: str = "0" * 40,
    baseline_commit: str = "abc1234",
    iteration: int = 0,
    contract: AutoresearchMissionContract | None = None,
) -> AutoresearchRunManifest:
    contract = contract or _make_contract()
    return AutoresearchRunManifest(
        schema_version=1,
        run_id="mission-test",
        run_tag="20260101T000000Z",
        mission_dir=contract.missionDir,
        mission_file=contract.missionFile,
        sandbox_file=contract.sandboxFile,
        repo_root=project_root,
        worktree_path=worktree_path,
        mission_slug=contract.missionSlug,
        branch_name="main",
        baseline_commit=baseline_commit,
        last_kept_commit=last_kept_commit,
        last_kept_score=last_kept_score,
        latest_candidate_commit=None,
        results_file=os.path.join(worktree_path, "results.tsv"),
        instructions_file=os.path.join(run_dir, "bootstrap-instructions.md"),
        manifest_file=os.path.join(run_dir, "manifest.json"),
        ledger_file=os.path.join(run_dir, "iteration-ledger.json"),
        latest_evaluator_file=os.path.join(run_dir, "latest-evaluator-result.json"),
        candidate_file=os.path.join(run_dir, "candidate.json"),
        evaluator=AutoresearchEvaluatorContract(
            command="echo {}",
            format="json",
            keep_policy=keep_policy,  # type: ignore[arg-type]
        ),
        keep_policy=keep_policy,  # type: ignore[arg-type]
        status="running",
        stop_reason=None,
        iteration=iteration,
        created_at="2026-01-01T00:00:00.000Z",
        updated_at="2026-01-01T00:00:00.000Z",
        completed_at=None,
    )


def _make_candidate(
    *,
    status: str = "candidate",
    candidate_commit: str | None = "deadbeef" + "0" * 32,
    base_commit: str = "0" * 40,
    description: str = "tweak",
    notes: list[str] | None = None,
    created_at: str = "2026-01-01T00:00:00.000Z",
) -> AutoresearchCandidateArtifact:
    return AutoresearchCandidateArtifact(
        status=status,  # type: ignore[arg-type]
        candidate_commit=candidate_commit,
        base_commit=base_commit,
        description=description,
        notes=list(notes or []),
        created_at=created_at,
    )


# --- pure functions: run tag --------------------------------------------------


class TestBuildAutoresearchRunTag(unittest.TestCase):
    def test_fixed_date_format(self) -> None:
        d = datetime(2026, 5, 13, 10, 30, 45, 123456, tzinfo=timezone.utc)
        tag = build_autoresearch_run_tag(d)
        # YYYY-MM-DDTHH:MM:SS.123Z → strip dashes/colons → reduce trailing .123Z to Z
        self.assertEqual(tag, "20260513T103045Z")

    def test_no_argument_uses_current_time(self) -> None:
        tag = build_autoresearch_run_tag()
        # YYYYMMDD T HHMMSS Z = 8 + 1 + 6 + 1 = 16
        self.assertEqual(len(tag), 16)
        self.assertTrue(tag.endswith("Z"))
        self.assertIn("T", tag)


# --- pure functions: decide ---------------------------------------------------


class TestDecideAutoresearchOutcome(unittest.TestCase):
    def _manifest(
        self,
        *,
        keep_policy: str = "score_improvement",
        last_kept_score: float | int | None = None,
    ) -> dict[str, Any]:
        return {"keep_policy": keep_policy, "last_kept_score": last_kept_score}

    def test_abort_status(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(), _make_candidate(status="abort"), None
        )
        self.assertEqual(decision.decision, "abort")
        self.assertFalse(decision.keep)
        self.assertIsNone(decision.evaluator)

    def test_noop_status(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(), _make_candidate(status="noop"), None
        )
        self.assertEqual(decision.decision, "noop")
        self.assertFalse(decision.keep)

    def test_interrupted_status(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(), _make_candidate(status="interrupted"), None
        )
        self.assertEqual(decision.decision, "interrupted")
        self.assertFalse(decision.keep)

    def test_evaluator_missing(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(), _make_candidate(), None
        )
        self.assertEqual(decision.decision, "discard")
        self.assertEqual(decision.decision_reason, "evaluator error")

    def test_evaluator_errored(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(),
            _make_candidate(),
            AutoresearchEvaluationRecord(command="x", ran_at="t", status="error"),
        )
        self.assertEqual(decision.decision, "discard")
        self.assertEqual(decision.decision_reason, "evaluator error")

    def test_evaluator_fail(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(),
            _make_candidate(),
            AutoresearchEvaluationRecord(
                command="x", ran_at="t", status="fail", pass_=False
            ),
        )
        self.assertEqual(decision.decision, "discard")
        self.assertEqual(decision.decision_reason, "evaluator reported failure")

    def test_pass_only_keep(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(keep_policy="pass_only"),
            _make_candidate(),
            AutoresearchEvaluationRecord(
                command="x", ran_at="t", status="pass", pass_=True
            ),
        )
        self.assertEqual(decision.decision, "keep")
        self.assertTrue(decision.keep)

    def test_score_improvement_ambiguous_no_score(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(last_kept_score=None),
            _make_candidate(),
            AutoresearchEvaluationRecord(
                command="x", ran_at="t", status="pass", pass_=True, score=0.5
            ),
        )
        self.assertEqual(decision.decision, "ambiguous")

    def test_score_improvement_ambiguous_no_eval_score(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(last_kept_score=0.5),
            _make_candidate(),
            AutoresearchEvaluationRecord(
                command="x", ran_at="t", status="pass", pass_=True, score=None
            ),
        )
        self.assertEqual(decision.decision, "ambiguous")

    def test_score_improvement_keep_on_improvement(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(last_kept_score=0.5),
            _make_candidate(),
            AutoresearchEvaluationRecord(
                command="x", ran_at="t", status="pass", pass_=True, score=0.8
            ),
        )
        self.assertEqual(decision.decision, "keep")
        self.assertTrue(decision.keep)

    def test_score_improvement_discard_when_equal(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(last_kept_score=0.5),
            _make_candidate(),
            AutoresearchEvaluationRecord(
                command="x", ran_at="t", status="pass", pass_=True, score=0.5
            ),
        )
        self.assertEqual(decision.decision, "discard")
        self.assertEqual(decision.decision_reason, "score did not improve")

    def test_score_improvement_discard_when_worse(self) -> None:
        decision = decide_autoresearch_outcome(
            self._manifest(last_kept_score=0.9),
            _make_candidate(),
            AutoresearchEvaluationRecord(
                command="x", ran_at="t", status="pass", pass_=True, score=0.3
            ),
        )
        self.assertEqual(decision.decision, "discard")

    def test_accepts_dataclass_manifest(self) -> None:
        # decide should accept a real manifest, not just a dict
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest = _make_manifest(
                run_dir=tmpdir,
                worktree_path=tmpdir,
                project_root=tmpdir,
                keep_policy="pass_only",
            )
            decision = decide_autoresearch_outcome(
                manifest,
                _make_candidate(),
                AutoresearchEvaluationRecord(
                    command="x", ran_at="t", status="pass", pass_=True
                ),
            )
            self.assertEqual(decision.decision, "keep")


# --- ledger: noop counter ----------------------------------------------------


class TestCountTrailingNoops(unittest.TestCase):
    def _write_ledger(self, path: Path, entries: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"entries": entries}), encoding="utf-8")

    def test_missing_file_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(
                count_trailing_autoresearch_noops(os.path.join(tmpdir, "no.json")), 0
            )

    def test_no_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "ledger.json"
            self._write_ledger(p, [])
            self.assertEqual(count_trailing_autoresearch_noops(str(p)), 0)

    def test_trailing_noops(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "ledger.json"
            entries = [
                _make_ledger_dict(iteration=1, kind="iteration", decision="keep"),
                _make_ledger_dict(iteration=2, kind="iteration", decision="noop"),
                _make_ledger_dict(iteration=3, kind="iteration", decision="noop"),
                _make_ledger_dict(iteration=4, kind="iteration", decision="noop"),
            ]
            self._write_ledger(p, entries)
            self.assertEqual(count_trailing_autoresearch_noops(str(p)), 3)

    def test_breaks_on_non_iteration(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "ledger.json"
            entries = [
                _make_ledger_dict(iteration=0, kind="baseline", decision="baseline"),
                _make_ledger_dict(iteration=1, kind="iteration", decision="noop"),
                _make_ledger_dict(iteration=2, kind="iteration", decision="noop"),
            ]
            self._write_ledger(p, entries)
            self.assertEqual(count_trailing_autoresearch_noops(str(p)), 2)

    def test_zero_when_last_is_keep(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "ledger.json"
            entries = [
                _make_ledger_dict(iteration=1, kind="iteration", decision="noop"),
                _make_ledger_dict(iteration=2, kind="iteration", decision="keep"),
            ]
            self._write_ledger(p, entries)
            self.assertEqual(count_trailing_autoresearch_noops(str(p)), 0)


def _make_ledger_dict(
    *,
    iteration: int,
    kind: str,
    decision: str,
    base_commit: str = "abc",
    candidate_commit: str | None = None,
    kept_commit: str = "abc",
) -> dict[str, Any]:
    return {
        "iteration": iteration,
        "kind": kind,
        "decision": decision,
        "decision_reason": "",
        "candidate_status": "candidate",
        "base_commit": base_commit,
        "candidate_commit": candidate_commit,
        "kept_commit": kept_commit,
        "keep_policy": "score_improvement",
        "evaluator": None,
        "created_at": "2026-01-01T00:00:00.000Z",
        "notes": [],
        "description": "",
    }


# --- candidate artifact parser -----------------------------------------------


class TestParseAutoresearchCandidateArtifact(unittest.TestCase):
    def _payload(self, **overrides: Any) -> str:
        base = {
            "status": "candidate",
            "candidate_commit": "abc123",
            "base_commit": "def456",
            "description": "tweak",
            "notes": ["note"],
            "created_at": "2026-01-01T00:00:00.000Z",
        }
        base.update(overrides)
        return json.dumps(base)

    def test_happy_path(self) -> None:
        artifact = parse_autoresearch_candidate_artifact(self._payload())
        self.assertEqual(artifact.status, "candidate")
        self.assertEqual(artifact.candidate_commit, "abc123")

    def test_null_candidate_commit_ok_for_noop(self) -> None:
        artifact = parse_autoresearch_candidate_artifact(
            self._payload(status="noop", candidate_commit=None)
        )
        self.assertEqual(artifact.status, "noop")
        self.assertIsNone(artifact.candidate_commit)

    def test_invalid_json(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_autoresearch_candidate_artifact("not-json")

    def test_array_not_allowed(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_autoresearch_candidate_artifact("[]")

    def test_bad_status(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_autoresearch_candidate_artifact(self._payload(status="other"))

    def test_bad_candidate_commit_type(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_autoresearch_candidate_artifact(self._payload(candidate_commit=123))

    def test_missing_base_commit(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_autoresearch_candidate_artifact(self._payload(base_commit=""))

    def test_non_string_description(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_autoresearch_candidate_artifact(self._payload(description=42))

    def test_non_array_notes(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_autoresearch_candidate_artifact(self._payload(notes="oops"))

    def test_non_string_note_member(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_autoresearch_candidate_artifact(self._payload(notes=[1, 2]))

    def test_missing_created_at(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_autoresearch_candidate_artifact(self._payload(created_at=""))


# --- instructions builder ----------------------------------------------------


class TestBuildAutoresearchInstructions(unittest.TestCase):
    def test_contains_all_key_fields(self) -> None:
        contract = _make_contract()
        text = build_autoresearch_instructions(
            contract,
            run_id="mission-20260101t000000z",
            iteration=2,
            baseline_commit="aaa1111",
            last_kept_commit="bbb2222",
            results_file="/tmp/results.tsv",
            candidate_file="/tmp/candidate.json",
            keep_policy="pass_only",
            last_kept_score=0.5,
            previous_iteration_outcome="keep:score improved",
            recent_ledger_summary=None,
        )
        self.assertIn("# OMX Autoresearch Supervisor Instructions", text)
        self.assertIn("Run ID: mission-20260101t000000z", text)
        self.assertIn("Mission slug: mission", text)
        self.assertIn("Iteration: 2", text)
        self.assertIn("Last kept score: 0.5", text)
        self.assertIn("Keep policy: pass_only", text)
        self.assertIn("Candidate artifact contract:", text)
        self.assertIn("Evaluator contract:", text)
        self.assertIn(f"- command: {contract.sandbox.evaluator.command}", text)
        self.assertIn(contract.missionContent.strip(), text)
        # state snapshot JSON
        self.assertIn('"previous_iteration_outcome": "keep:score improved"', text)
        self.assertIn('"keep_policy": "pass_only"', text)

    def test_default_no_score_renders_na(self) -> None:
        contract = _make_contract()
        text = build_autoresearch_instructions(
            contract,
            run_id="r",
            iteration=1,
            baseline_commit="a",
            last_kept_commit="b",
            results_file="/r",
            candidate_file="/c",
            keep_policy="score_improvement",
        )
        self.assertIn("Last kept score: n/a", text)
        self.assertIn('"previous_iteration_outcome": "none yet"', text)

    def test_trims_long_mission_content(self) -> None:
        contract = _make_contract(mission_content="x" * 5000)
        text = build_autoresearch_instructions(
            contract,
            run_id="r",
            iteration=1,
            baseline_commit="a",
            last_kept_commit="b",
            results_file="/r",
            candidate_file="/c",
            keep_policy="score_improvement",
        )
        self.assertIn("\n...", text)


# --- assert_reset_safe_worktree ----------------------------------------------


class TestAssertResetSafeWorktree(unittest.TestCase):
    def test_clean_worktree_passes(self) -> None:
        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run",
            return_value=_make_proc(stdout="", returncode=0),
        ):
            assert_reset_safe_worktree("/tmp/wt")  # should not raise

    def test_allows_excluded_untracked(self) -> None:
        # Only untracked entries pointing at runtime excludes are allowed.
        out = "?? results.tsv\n?? .omx/state.json\n?? node_modules\n"
        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run",
            return_value=_make_proc(stdout=out, returncode=0),
        ):
            assert_reset_safe_worktree("/tmp/wt")  # should not raise

    def test_blocking_modified_file_raises(self) -> None:
        out = " M src/foo.py\n"
        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run",
            return_value=_make_proc(stdout=out, returncode=0),
        ):
            with self.assertRaises(RuntimeError) as cm:
                assert_reset_safe_worktree("/tmp/wt")
            self.assertIn(
                "autoresearch_reset_requires_clean_worktree", str(cm.exception)
            )

    def test_blocking_untracked_outside_exclude_raises(self) -> None:
        out = "?? other.txt\n"
        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run",
            return_value=_make_proc(stdout=out, returncode=0),
        ):
            with self.assertRaises(RuntimeError):
                assert_reset_safe_worktree("/tmp/wt")


def _make_proc(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> mock.MagicMock:
    fake = mock.MagicMock()
    fake.stdout = stdout
    fake.stderr = stderr
    fake.returncode = returncode
    return fake


# --- run_autoresearch_evaluator ---------------------------------------------


class TestRunAutoresearchEvaluator(unittest.TestCase):
    def test_passes_through_subprocess_call(self) -> None:
        contract = _make_contract(command="bash run.sh")
        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run",
            return_value=_make_proc(
                stdout='{"pass": true, "score": 0.7}', returncode=0
            ),
        ) as run_mock:
            record = run_autoresearch_evaluator(contract, "/tmp/wt")
        self.assertEqual(record.status, "pass")
        self.assertTrue(record.pass_)
        self.assertEqual(record.score, 0.7)
        self.assertEqual(record.exit_code, 0)
        # Confirm we ran the evaluator command via shell (TS parity)
        kwargs = run_mock.call_args.kwargs
        self.assertTrue(kwargs.get("shell"))
        self.assertEqual(kwargs.get("cwd"), "/tmp/wt")
        self.assertEqual(run_mock.call_args.args[0], "bash run.sh")

    def test_fail_when_pass_false(self) -> None:
        contract = _make_contract()
        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run",
            return_value=_make_proc(stdout='{"pass": false}', returncode=0),
        ):
            record = run_autoresearch_evaluator(contract, "/tmp/wt")
        self.assertEqual(record.status, "fail")
        self.assertFalse(record.pass_)

    def test_non_zero_exit_becomes_error(self) -> None:
        contract = _make_contract()
        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run",
            return_value=_make_proc(stdout="", stderr="boom", returncode=1),
        ):
            record = run_autoresearch_evaluator(contract, "/tmp/wt")
        self.assertEqual(record.status, "error")
        self.assertEqual(record.exit_code, 1)
        self.assertEqual(record.stderr, "boom")

    def test_invalid_json_becomes_error_with_parse_error(self) -> None:
        contract = _make_contract()
        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run",
            return_value=_make_proc(stdout="not json", returncode=0),
        ):
            record = run_autoresearch_evaluator(contract, "/tmp/wt")
        self.assertEqual(record.status, "error")
        self.assertIsNotNone(record.parse_error)

    def test_writes_latest_evaluator_file(self) -> None:
        contract = _make_contract()
        with tempfile.TemporaryDirectory() as tmpdir:
            latest = os.path.join(tmpdir, "latest.json")
            with mock.patch(
                "omx.autoresearch.runtime.subprocess.run",
                return_value=_make_proc(stdout='{"pass": true}', returncode=0),
            ):
                run_autoresearch_evaluator(
                    contract, tmpdir, latest_evaluator_file=latest
                )
            data = json.loads(Path(latest).read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "pass")
            self.assertTrue(data["pass"])

    def test_writes_synthetic_ledger_when_requested(self) -> None:
        contract = _make_contract()
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = os.path.join(tmpdir, "ledger.json")

            # Two subprocess calls: 1) the evaluator command, 2) the `git rev-parse --short=7 HEAD`
            # used to fill the synthetic ledger entry.
            def fake_run(args: Any, **kwargs: Any) -> mock.MagicMock:
                if isinstance(args, list) and args[:1] == ["git"]:
                    return _make_proc(stdout="abcdefg", returncode=0)
                return _make_proc(stdout='{"pass": true}', returncode=0)

            with mock.patch(
                "omx.autoresearch.runtime.subprocess.run", side_effect=fake_run
            ):
                run_autoresearch_evaluator(contract, tmpdir, ledger_file=ledger)
            data = json.loads(Path(ledger).read_text(encoding="utf-8"))
            self.assertEqual(len(data["entries"]), 1)
            self.assertEqual(data["entries"][0]["iteration"], -1)
            self.assertEqual(data["entries"][0]["decision"], "keep")


# --- materialize_autoresearch_mission_to_worktree ---------------------------


class TestMaterializeMissionToWorktree(unittest.TestCase):
    def test_writes_mission_and_sandbox_under_relative_dir(self) -> None:
        contract = _make_contract(
            mission_relative_dir="path/to/mission",
            mission_content="MISSION CONTENT",
            sandbox_content="SANDBOX CONTENT",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with mock.patch(
                "omx.autoresearch.runtime.subprocess.run",
                return_value=_make_proc(returncode=0),
            ):
                result = materialize_autoresearch_mission_to_worktree(contract, tmpdir)
            mission_path = Path(tmpdir) / "path" / "to" / "mission"
            self.assertTrue((mission_path / "mission.md").exists())
            self.assertTrue((mission_path / "sandbox.md").exists())
            self.assertEqual(
                (mission_path / "mission.md").read_text(encoding="utf-8"),
                "MISSION CONTENT",
            )
            # On Windows `os.path.join` preserves the embedded `/` characters
            # in missionRelativeDir; compare via Path so the assertion is
            # platform-independent.
            self.assertEqual(Path(result.missionDir).resolve(), mission_path.resolve())


# --- load / write manifest --------------------------------------------------


class TestManifestRoundtrip(unittest.TestCase):
    def test_to_dict_from_dict_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            m = _make_manifest(
                run_dir=tmpdir,
                worktree_path=tmpdir,
                project_root=tmpdir,
                keep_policy="pass_only",
                last_kept_score=0.4,
            )
            restored = AutoresearchRunManifest.from_dict(m.to_dict())
            self.assertEqual(restored.run_id, m.run_id)
            self.assertEqual(restored.keep_policy, "pass_only")
            self.assertEqual(restored.last_kept_score, 0.4)
            self.assertEqual(restored.evaluator.command, m.evaluator.command)

    def test_load_missing_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(RuntimeError) as cm:
                load_autoresearch_run_manifest(tmpdir, "ghost-run")
            self.assertIn("autoresearch_resume_manifest_missing", str(cm.exception))

    def test_load_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / ".omx" / "logs" / "autoresearch" / "myrun"
            run_dir.mkdir(parents=True, exist_ok=True)
            manifest = _make_manifest(
                run_dir=str(run_dir), worktree_path=tmpdir, project_root=tmpdir
            )
            manifest.run_id = "myrun"
            (run_dir / "manifest.json").write_text(
                json.dumps(manifest.to_dict()), encoding="utf-8"
            )
            loaded = load_autoresearch_run_manifest(tmpdir, "myrun")
            self.assertEqual(loaded.run_id, "myrun")


# --- prepare / process / finalize / stop -------------------------------------


class TestPrepareAndProcessRuntime(unittest.TestCase):
    """End-to-end happy/edge paths for the runtime lifecycle.

    All git/evaluator subprocess calls are routed through a single
    ``side_effect`` dispatcher so each test stays self-contained.
    """

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(self._rm_tmpdir)
        self.worktree = self.tmpdir
        self.project_root = self.tmpdir

    def _rm_tmpdir(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _git_dispatcher(
        self,
        *,
        head_short: str = "abc1234",
        head_full: str = "a" * 40,
        symbolic_ref: str = "main",
        status_lines: str = "",
        candidate_full: str | None = None,
        info_exclude_rel: str = ".git/info/exclude",
    ) -> Any:
        """Build a side_effect dispatcher mimicking the git commands used by the runtime."""
        rev_parse_full = head_full
        rev_parse_short = head_short
        os.path.join(self.worktree, info_exclude_rel)

        def fake_run(args: Any, **kwargs: Any) -> mock.MagicMock:
            if not isinstance(args, list):
                return _make_proc(returncode=0)
            if args[:2] == ["git", "rev-parse"]:
                if "--show-toplevel" in args:
                    return _make_proc(stdout=self.worktree, returncode=0)
                if "--git-path" in args:
                    return _make_proc(stdout=info_exclude_rel, returncode=0)
                if "--short=7" in args:
                    return _make_proc(stdout=rev_parse_short, returncode=0)
                if "--verify" in args:
                    ref = args[-1]
                    if candidate_full and ref.startswith(candidate_full[:8]):
                        return _make_proc(stdout=candidate_full, returncode=0)
                    # Resolve full HEAD aliases
                    if ref.startswith(rev_parse_full[:8]):
                        return _make_proc(stdout=rev_parse_full, returncode=0)
                    if ref.startswith("HEAD"):
                        return _make_proc(stdout=rev_parse_full, returncode=0)
                    # base_commit fixtures often equal manifest.last_kept_commit
                    return _make_proc(stdout=ref.split("^")[0], returncode=0)
                # Default: full HEAD
                return _make_proc(stdout=rev_parse_full, returncode=0)
            if args[:2] == ["git", "symbolic-ref"]:
                return _make_proc(stdout=symbolic_ref, returncode=0)
            if args[:2] == ["git", "status"]:
                return _make_proc(stdout=status_lines, returncode=0)
            if args[:2] == ["git", "reset"]:
                return _make_proc(returncode=0)
            if args[:2] == ["git", "add"] or args[:2] == ["git", "commit"]:
                return _make_proc(returncode=0)
            return _make_proc(returncode=0)

        return fake_run

    def test_prepare_writes_manifest_ledger_and_state(self) -> None:
        contract = _make_contract(
            mission_dir=os.path.join(self.worktree, "missions/m"),
            repo_root=self.worktree,
            mission_relative_dir="missions/m",
        )

        def fake_run(args: Any, **kwargs: Any) -> mock.MagicMock:
            # Evaluator goes through shell=True with a string command.
            if kwargs.get("shell"):
                return _make_proc(stdout='{"pass": true, "score": 0.5}', returncode=0)
            return self._git_dispatcher(
                head_short="abc1234",
                head_full="a" * 40,
                symbolic_ref="main",
                status_lines="",
            )(args, **kwargs)

        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run", side_effect=fake_run
        ):
            prepared = prepare_autoresearch_runtime(
                contract,
                self.project_root,
                self.worktree,
                run_tag="20260101T000000Z",
            )

        self.assertEqual(prepared.runId, "mission-20260101t000000z")
        self.assertTrue(os.path.exists(prepared.manifestFile))
        self.assertTrue(os.path.exists(prepared.ledgerFile))
        self.assertTrue(os.path.exists(prepared.latestEvaluatorFile))
        self.assertTrue(os.path.exists(prepared.instructionsFile))
        self.assertTrue(os.path.exists(prepared.resultsFile))
        self.assertTrue(os.path.exists(prepared.candidateFile))
        # Mode state should be active
        state_file = os.path.join(
            self.project_root, ".omx", "state", "autoresearch-state.json"
        )
        self.assertTrue(os.path.exists(state_file))
        mode_state = os.path.join(
            self.project_root, ".omx", "state", "autoresearch-state.json"
        )
        self.assertTrue(os.path.exists(mode_state))
        # Baseline ledger entry written
        ledger = json.loads(Path(prepared.ledgerFile).read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["entries"]), 1)
        self.assertEqual(ledger["entries"][0]["kind"], "baseline")

    def test_prepare_refuses_when_active_run_exists(self) -> None:
        # Pre-write an active autoresearch run state file
        state_path = os.path.join(
            self.project_root, ".omx", "state", "autoresearch-state.json"
        )
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        Path(state_path).write_text(
            json.dumps(
                {
                    "active": True,
                    "run_id": "mission-existing",
                    "schema_version": 1,
                    "mission_slug": "x",
                    "repo_root": self.project_root,
                    "worktree_path": self.worktree,
                    "status": "running",
                    "updated_at": "2026-01-01T00:00:00.000Z",
                }
            ),
            encoding="utf-8",
        )
        contract = _make_contract(
            mission_dir=os.path.join(self.worktree, "missions/m"),
            repo_root=self.worktree,
            mission_relative_dir="missions/m",
        )
        with self.assertRaises(RuntimeError) as cm:
            with mock.patch(
                "omx.autoresearch.runtime.subprocess.run", return_value=_make_proc()
            ):
                prepare_autoresearch_runtime(
                    contract,
                    self.project_root,
                    self.worktree,
                    run_tag="20260101T000000Z",
                )
        self.assertIn("autoresearch_active_run_exists", str(cm.exception))

    def test_process_candidate_keep_path(self) -> None:
        contract = _make_contract(keep_policy="pass_only")
        last_kept = "f" * 40

        run_dir = os.path.join(
            self.project_root, ".omx", "logs", "autoresearch", "mission-test"
        )
        os.makedirs(run_dir, exist_ok=True)
        manifest = _make_manifest(
            run_dir=run_dir,
            worktree_path=self.worktree,
            project_root=self.project_root,
            keep_policy="pass_only",
            last_kept_commit=last_kept,
            contract=contract,
        )
        # Seed an empty mode-state so update_mode_state succeeds
        os.makedirs(os.path.join(self.project_root, ".omx", "state"), exist_ok=True)
        Path(
            os.path.join(self.project_root, ".omx", "state", "autoresearch-state.json")
        ).write_text(
            json.dumps(
                {
                    "active": True,
                    "mode": "autoresearch",
                    "iteration": 0,
                    "max_iterations": 1,
                    "current_phase": "running",
                    "started_at": "t",
                }
            ),
            encoding="utf-8",
        )

        # Seed candidate.json — base_commit must match last_kept (after rev-parse resolution)
        candidate_payload = {
            "status": "candidate",
            "candidate_commit": "deadbee",
            "base_commit": last_kept,
            "description": "improve thing",
            "notes": ["one"],
            "created_at": "2026-01-01T00:00:00.000Z",
        }
        Path(manifest.candidate_file).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest.candidate_file).write_text(
            json.dumps(candidate_payload), encoding="utf-8"
        )
        # Initialize results file so iteration row appending works
        Path(manifest.results_file).write_text(
            AUTORESEARCH_RESULTS_HEADER, encoding="utf-8"
        )

        # rev-parse --verify must return last_kept for base_commit and a 40-char
        # candidate SHA for candidate_commit; head full = candidate_commit
        candidate_full = "d" * 40

        def fake_run(args: Any, **kwargs: Any) -> mock.MagicMock:
            if kwargs.get("shell"):
                return _make_proc(stdout='{"pass": true}', returncode=0)
            if isinstance(args, list) and args[:2] == ["git", "rev-parse"]:
                if "--verify" in args:
                    ref = args[-1]
                    if ref.startswith("deadbee"):
                        return _make_proc(stdout=candidate_full, returncode=0)
                    if ref.startswith(last_kept):
                        return _make_proc(stdout=last_kept, returncode=0)
                    return _make_proc(stdout=ref.split("^")[0], returncode=0)
                if "--short=7" in args:
                    return _make_proc(stdout="abc1234", returncode=0)
                # full HEAD
                return _make_proc(stdout=candidate_full, returncode=0)
            return _make_proc(returncode=0)

        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run", side_effect=fake_run
        ):
            decision = process_autoresearch_candidate(
                contract, manifest, self.project_root
            )

        self.assertEqual(decision, "keep")
        self.assertEqual(manifest.last_kept_commit, candidate_full)
        self.assertEqual(manifest.iteration, 1)
        # Ledger received an iteration row
        ledger = json.loads(Path(manifest.ledger_file).read_text(encoding="utf-8"))
        self.assertEqual(len(ledger["entries"]), 1)
        self.assertEqual(ledger["entries"][0]["decision"], "keep")

    def test_process_candidate_abort_finalizes_run(self) -> None:
        contract = _make_contract()
        run_dir = os.path.join(
            self.project_root, ".omx", "logs", "autoresearch", "abort-run"
        )
        os.makedirs(run_dir, exist_ok=True)
        manifest = _make_manifest(
            run_dir=run_dir,
            worktree_path=self.worktree,
            project_root=self.project_root,
            contract=contract,
        )
        manifest.run_id = "abort-run"
        os.makedirs(os.path.join(self.project_root, ".omx", "state"), exist_ok=True)
        Path(
            os.path.join(self.project_root, ".omx", "state", "autoresearch-state.json")
        ).write_text(
            json.dumps(
                {
                    "active": True,
                    "mode": "autoresearch",
                    "iteration": 0,
                    "max_iterations": 1,
                    "current_phase": "running",
                    "started_at": "t",
                }
            ),
            encoding="utf-8",
        )

        candidate_payload = {
            "status": "abort",
            "candidate_commit": None,
            "base_commit": manifest.last_kept_commit,
            "description": "operator abort",
            "notes": [],
            "created_at": "2026-01-01T00:00:00.000Z",
        }
        Path(manifest.candidate_file).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest.candidate_file).write_text(
            json.dumps(candidate_payload), encoding="utf-8"
        )
        Path(manifest.results_file).write_text(
            AUTORESEARCH_RESULTS_HEADER, encoding="utf-8"
        )

        def fake_run(args: Any, **kwargs: Any) -> mock.MagicMock:
            if isinstance(args, list) and args[:2] == ["git", "rev-parse"]:
                if "--verify" in args:
                    ref = args[-1]
                    return _make_proc(stdout=ref.split("^")[0], returncode=0)
                if "--short=7" in args:
                    return _make_proc(stdout="abc1234", returncode=0)
                return _make_proc(stdout=manifest.last_kept_commit, returncode=0)
            return _make_proc(returncode=0)

        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run", side_effect=fake_run
        ):
            decision = process_autoresearch_candidate(
                contract, manifest, self.project_root
            )
        self.assertEqual(decision, "abort")
        self.assertEqual(manifest.status, "stopped")
        self.assertEqual(manifest.stop_reason, "candidate abort")

    def test_process_candidate_validation_failure_marks_error(self) -> None:
        contract = _make_contract()
        run_dir = os.path.join(
            self.project_root, ".omx", "logs", "autoresearch", "err-run"
        )
        os.makedirs(run_dir, exist_ok=True)
        manifest = _make_manifest(
            run_dir=run_dir,
            worktree_path=self.worktree,
            project_root=self.project_root,
            contract=contract,
        )
        manifest.run_id = "err-run"
        os.makedirs(os.path.join(self.project_root, ".omx", "state"), exist_ok=True)
        Path(
            os.path.join(self.project_root, ".omx", "state", "autoresearch-state.json")
        ).write_text(
            json.dumps(
                {
                    "active": True,
                    "mode": "autoresearch",
                    "iteration": 0,
                    "max_iterations": 1,
                    "current_phase": "running",
                    "started_at": "t",
                }
            ),
            encoding="utf-8",
        )

        # base_commit will not resolve in git → validation failure
        candidate_payload = {
            "status": "candidate",
            "candidate_commit": "deadbee",
            "base_commit": "bad-ref",
            "description": "x",
            "notes": [],
            "created_at": "2026-01-01T00:00:00.000Z",
        }
        Path(manifest.candidate_file).parent.mkdir(parents=True, exist_ok=True)
        Path(manifest.candidate_file).write_text(
            json.dumps(candidate_payload), encoding="utf-8"
        )
        Path(manifest.results_file).write_text(
            AUTORESEARCH_RESULTS_HEADER, encoding="utf-8"
        )

        def fake_run(args: Any, **kwargs: Any) -> mock.MagicMock:
            if isinstance(args, list) and args[:2] == ["git", "rev-parse"]:
                if "--verify" in args:
                    # Refuse to resolve bad-ref
                    return _make_proc(returncode=1)
                if "--short=7" in args:
                    return _make_proc(stdout="abc1234", returncode=0)
            return _make_proc(returncode=0)

        with mock.patch(
            "omx.autoresearch.runtime.subprocess.run", side_effect=fake_run
        ):
            decision = process_autoresearch_candidate(
                contract, manifest, self.project_root
            )
        self.assertEqual(decision, "error")
        self.assertEqual(manifest.status, "failed")
        self.assertIn("does not resolve", manifest.stop_reason or "")


# --- finalize / stop ---------------------------------------------------------


class TestFinalizeAndStop(unittest.TestCase):
    def test_finalize_non_running_manifest_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            run_dir = Path(tmpdir) / ".omx" / "logs" / "autoresearch" / "done-run"
            run_dir.mkdir(parents=True, exist_ok=True)
            manifest = _make_manifest(
                run_dir=str(run_dir),
                worktree_path=tmpdir,
                project_root=tmpdir,
            )
            manifest.run_id = "done-run"
            manifest.status = "completed"
            (run_dir / "manifest.json").write_text(
                json.dumps(manifest.to_dict()), encoding="utf-8"
            )
            # Should not raise and should not require mode state.
            finalize_autoresearch_run_state(
                tmpdir, "done-run", status="stopped", stop_reason="x"
            )
            after = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(after["status"], "completed")

    def test_stop_runtime_inactive_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # No mode state file → stop is a noop
            stop_autoresearch_runtime(tmpdir)  # should not raise


# --- legacy lightweight loop -------------------------------------------------


class TestRunResearchLoop(unittest.TestCase):
    def test_basic_loop_terminates_on_full_score(self) -> None:
        mission = ResearchMission(task="x", max_iterations=10)

        def gen(_m: ResearchMission, _h: list[ResearchCandidate]) -> ResearchCandidate:
            return ResearchCandidate(iteration=0, content="c")

        def evl(_c: ResearchCandidate, _m: ResearchMission) -> float:
            return 1.0

        out = run_research_loop(mission, gen, evl)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].iteration, 1)
        self.assertEqual(out[0].score, 1.0)

    def test_calls_on_iteration_callback(self) -> None:
        mission = ResearchMission(task="x", max_iterations=3)
        seen: list[int] = []

        out = run_research_loop(
            mission,
            generate=lambda *_: ResearchCandidate(iteration=0, content="c"),
            evaluate=lambda *_: 0.1,
            on_iteration=lambda i, _c: seen.append(i),
        )
        self.assertEqual(seen, [1, 2, 3])
        self.assertEqual(len(out), 3)


if __name__ == "__main__":
    unittest.main()
