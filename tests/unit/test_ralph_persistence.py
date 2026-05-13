"""Tests for ralph persistence dataclasses and visual-feedback recording."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omx.ralph.persistence import (
    DEFAULT_VISUAL_THRESHOLD,
    RalphCanonicalArtifacts,
    RalphProgressLedger,
    RalphVisualFeedback,
    record_ralph_visual_feedback,
)
from omx.visual.constants import VISUAL_NEXT_ACTIONS_LIMIT


class TestRalphVisualFeedbackDataclass(unittest.TestCase):
    def test_round_trip_minimal(self):
        fb = RalphVisualFeedback(
            score=82.5,
            verdict="revise",
            category_match=True,
        )
        d = fb.to_dict()
        self.assertEqual(d["score"], 82.5)
        self.assertEqual(d["verdict"], "revise")
        self.assertTrue(d["category_match"])
        self.assertEqual(d["differences"], [])
        self.assertEqual(d["suggestions"], [])
        self.assertNotIn("reasoning", d)
        self.assertNotIn("threshold", d)

        restored = RalphVisualFeedback.from_dict(d)
        self.assertEqual(restored.score, fb.score)
        self.assertEqual(restored.verdict, fb.verdict)
        self.assertEqual(restored.category_match, fb.category_match)
        self.assertIsNone(restored.reasoning)
        self.assertIsNone(restored.threshold)

    def test_round_trip_full(self):
        fb = RalphVisualFeedback(
            score=95.0,
            verdict="pass",
            category_match=True,
            differences=["padding mismatch"],
            suggestions=["tighten spacing"],
            reasoning="close to reference",
            threshold=92.0,
        )
        restored = RalphVisualFeedback.from_dict(fb.to_dict())
        self.assertEqual(restored.differences, ["padding mismatch"])
        self.assertEqual(restored.suggestions, ["tighten spacing"])
        self.assertEqual(restored.reasoning, "close to reference")
        self.assertEqual(restored.threshold, 92.0)


class TestRalphProgressLedgerDataclass(unittest.TestCase):
    def test_round_trip_defaults(self):
        ledger = RalphProgressLedger()
        d = ledger.to_dict()
        self.assertEqual(d["schema_version"], 2)
        self.assertEqual(d["entries"], [])
        self.assertEqual(d["visual_feedback"], [])
        for key in ("source", "source_sha256", "strategy", "created_at", "updated_at"):
            self.assertNotIn(key, d)

        restored = RalphProgressLedger.from_dict(d)
        self.assertEqual(restored.schema_version, 2)
        self.assertEqual(restored.entries, [])
        self.assertEqual(restored.visual_feedback, [])

    def test_round_trip_full(self):
        ledger = RalphProgressLedger(
            schema_version=2,
            entries=[{"index": 1, "text": "first"}],
            visual_feedback=[{"score": 90}],
            source=".omx/progress.txt",
            source_sha256="deadbeef",
            strategy="one-way-read-only",
            created_at="2025-01-01T00:00:00.000Z",
            updated_at="2025-01-02T00:00:00.000Z",
        )
        restored = RalphProgressLedger.from_dict(ledger.to_dict())
        self.assertEqual(restored.entries, [{"index": 1, "text": "first"}])
        self.assertEqual(restored.visual_feedback, [{"score": 90}])
        self.assertEqual(restored.source, ".omx/progress.txt")
        self.assertEqual(restored.source_sha256, "deadbeef")
        self.assertEqual(restored.strategy, "one-way-read-only")
        self.assertEqual(restored.created_at, "2025-01-01T00:00:00.000Z")
        self.assertEqual(restored.updated_at, "2025-01-02T00:00:00.000Z")

    def test_from_dict_repairs_missing_lists(self):
        ledger = RalphProgressLedger.from_dict({"schema_version": 2})
        self.assertEqual(ledger.entries, [])
        self.assertEqual(ledger.visual_feedback, [])

    def test_from_dict_defaults_schema_version(self):
        ledger = RalphProgressLedger.from_dict({})
        self.assertEqual(ledger.schema_version, 2)


class TestRalphCanonicalArtifactsDataclass(unittest.TestCase):
    def test_round_trip_minimal(self):
        artifacts = RalphCanonicalArtifacts(
            canonical_progress_path="/tmp/foo/ralph-progress.json",
        )
        d = artifacts.to_dict()
        self.assertEqual(d["canonical_progress_path"], "/tmp/foo/ralph-progress.json")
        self.assertFalse(d["migrated_prd"])
        self.assertFalse(d["migrated_progress"])
        self.assertNotIn("canonical_prd_path", d)

        restored = RalphCanonicalArtifacts.from_dict(d)
        self.assertEqual(restored, artifacts)

    def test_round_trip_full(self):
        artifacts = RalphCanonicalArtifacts(
            canonical_progress_path="/tmp/foo/ralph-progress.json",
            migrated_prd=True,
            migrated_progress=True,
            canonical_prd_path="/tmp/foo/.omx/plans/prd-test.md",
        )
        restored = RalphCanonicalArtifacts.from_dict(artifacts.to_dict())
        self.assertEqual(restored, artifacts)


class TestRecordRalphVisualFeedback(unittest.TestCase):
    def test_writes_canonical_progress_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            fb = RalphVisualFeedback(
                score=88,
                verdict="revise",
                category_match=True,
                differences=["spacing off"],
                suggestions=["increase gap"],
                reasoning="close but not perfect",
            )
            record_ralph_visual_feedback(tmp, fb)

            progress_path = Path(tmp) / ".omx" / "state" / "ralph-progress.json"
            self.assertTrue(progress_path.exists())
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 2)
            self.assertEqual(len(data["visual_feedback"]), 1)
            entry = data["visual_feedback"][0]
            self.assertEqual(entry["score"], 88)
            self.assertEqual(entry["verdict"], "revise")
            self.assertTrue(entry["category_match"])
            self.assertEqual(entry["threshold"], DEFAULT_VISUAL_THRESHOLD)
            self.assertFalse(entry["passes_threshold"])
            self.assertEqual(entry["differences"], ["spacing off"])
            self.assertEqual(entry["suggestions"], ["increase gap"])
            self.assertEqual(entry["reasoning"], "close but not perfect")
            self.assertEqual(
                entry["next_actions"],
                ["increase gap", "Resolve difference: spacing off"],
            )
            self.assertEqual(
                entry["qualitative_feedback"]["summary"],
                "close but not perfect",
            )

    def test_creates_missing_state_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Confirm the dir does not exist yet.
            state_dir = Path(tmp) / ".omx" / "state"
            self.assertFalse(state_dir.exists())

            fb = RalphVisualFeedback(score=70, verdict="fail", category_match=False)
            record_ralph_visual_feedback(tmp, fb)

            self.assertTrue(state_dir.is_dir())
            self.assertTrue((state_dir / "ralph-progress.json").is_file())

    def test_appends_then_caps_at_retention_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            for i in range(35):
                fb = RalphVisualFeedback(
                    score=float(i),
                    verdict="revise",
                    category_match=False,
                )
                record_ralph_visual_feedback(tmp, fb)

            progress_path = Path(tmp) / ".omx" / "state" / "ralph-progress.json"
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            # Should keep only the most recent 30 entries.
            self.assertEqual(len(data["visual_feedback"]), 30)
            scores = [e["score"] for e in data["visual_feedback"]]
            self.assertEqual(scores, list(range(5, 35)))

    def test_threshold_override_and_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fb = RalphVisualFeedback(
                score=95,
                verdict="pass",
                category_match=True,
                threshold=85,
            )
            record_ralph_visual_feedback(tmp, fb)
            data = json.loads(
                (Path(tmp) / ".omx" / "state" / "ralph-progress.json").read_text(
                    encoding="utf-8",
                ),
            )
            entry = data["visual_feedback"][-1]
            self.assertEqual(entry["threshold"], 85)
            self.assertTrue(entry["passes_threshold"])

    def test_next_actions_truncated_and_trimmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fb = RalphVisualFeedback(
                score=50,
                verdict="fail",
                category_match=False,
                # Mix whitespace + many entries; ensure trim + cap.
                suggestions=["  s1  ", "s2", "s3", "", "s4"],
                differences=["d1", "d2", "d3"],
            )
            record_ralph_visual_feedback(tmp, fb)
            data = json.loads(
                (Path(tmp) / ".omx" / "state" / "ralph-progress.json").read_text(
                    encoding="utf-8",
                ),
            )
            entry = data["visual_feedback"][-1]
            self.assertLessEqual(len(entry["next_actions"]), VISUAL_NEXT_ACTIONS_LIMIT)
            # First action should be trimmed.
            self.assertEqual(entry["next_actions"][0], "s1")
            # Empty strings filtered out before the cap.
            self.assertNotIn("", entry["next_actions"])

    def test_session_id_scopes_under_sessions_subdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            fb = RalphVisualFeedback(score=80, verdict="revise", category_match=True)
            record_ralph_visual_feedback(tmp, fb, session_id="abc-123")

            scoped = (
                Path(tmp)
                / ".omx"
                / "state"
                / "sessions"
                / "abc-123"
                / "ralph-progress.json"
            )
            self.assertTrue(scoped.exists())
            # Unscoped path must NOT be written when session_id is provided.
            unscoped = Path(tmp) / ".omx" / "state" / "ralph-progress.json"
            self.assertFalse(unscoped.exists())

    def test_multiple_records_preserve_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            for score, verdict in [(50, "fail"), (70, "revise"), (95, "pass")]:
                record_ralph_visual_feedback(
                    tmp,
                    RalphVisualFeedback(
                        score=score,
                        verdict=verdict,
                        category_match=True,
                    ),
                )
            data = json.loads(
                (Path(tmp) / ".omx" / "state" / "ralph-progress.json").read_text(
                    encoding="utf-8",
                ),
            )
            self.assertEqual(len(data["visual_feedback"]), 3)
            self.assertEqual(
                [e["verdict"] for e in data["visual_feedback"]],
                ["fail", "revise", "pass"],
            )


class TestRecordRalphVisualFeedbackErrors(unittest.TestCase):
    def test_recovers_from_corrupt_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            progress_path = Path(tmp) / ".omx" / "state" / "ralph-progress.json"
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_path.write_text("not valid json {{{", encoding="utf-8")

            fb = RalphVisualFeedback(score=80, verdict="revise", category_match=True)
            record_ralph_visual_feedback(tmp, fb)

            data = json.loads(progress_path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema_version"], 2)
            self.assertEqual(len(data["visual_feedback"]), 1)


if __name__ == "__main__":
    unittest.main()
