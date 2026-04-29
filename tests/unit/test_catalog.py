"""Tests for omx.catalog and omx.autoresearch."""

import unittest

from omx.autoresearch.contracts import ResearchCandidate, ResearchMission
from omx.autoresearch.runtime import run_research_loop
from omx.catalog.metadata import CatalogEntry


class TestCatalog(unittest.TestCase):
    def test_catalog_entry_to_dict(self):
        entry = CatalogEntry(
            name="autopilot", kind="skill", status="active", description="Auto mode"
        )
        d = entry.to_dict()
        self.assertEqual(d["name"], "autopilot")
        self.assertEqual(d["kind"], "skill")
        self.assertIn("description", d)

    def test_catalog_entry_without_optional(self):
        entry = CatalogEntry(name="test", kind="agent")
        d = entry.to_dict()
        self.assertNotIn("description", d)
        self.assertNotIn("canonical", d)


class TestAutoresearch(unittest.TestCase):
    def test_research_loop_basic(self):
        mission = ResearchMission(task="find answer", max_iterations=5)

        call_count = {"n": 0}

        def generate(m, candidates):
            call_count["n"] += 1
            return ResearchCandidate(iteration=0, content=f"attempt {call_count['n']}")

        def evaluate(candidate, m):
            return 1.0 if candidate.content == "attempt 3" else 0.5

        results = run_research_loop(mission, generate, evaluate)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[-1].score, 1.0)

    def test_research_loop_max_iterations(self):
        mission = ResearchMission(task="impossible", max_iterations=3)

        def generate(m, candidates):
            return ResearchCandidate(iteration=0, content="nope")

        def evaluate(candidate, m):
            return 0.1  # never satisfactory

        results = run_research_loop(mission, generate, evaluate)
        self.assertEqual(len(results), 3)

    def test_mission_serialization(self):
        mission = ResearchMission(
            task="test",
            max_iterations=10,
            evaluation_criteria=["accuracy"],
            constraints=["no external deps"],
        )
        d = mission.to_dict()
        self.assertEqual(d["task"], "test")
        self.assertEqual(d["constraints"], ["no external deps"])


if __name__ == "__main__":
    unittest.main()
