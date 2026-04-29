"""Tests for omx.agents — role definitions and policy."""

import unittest

from omx.agents.policy import (
    assert_native_agent_canonical_targets,
    get_installable_native_agent_names,
    get_non_installable_native_agent_names,
)
from omx.agents.roles import AGENT_DEFINITIONS, get_agent, list_agent_names


class TestAgentRoles(unittest.TestCase):
    def test_definitions_exist(self):
        self.assertGreater(len(AGENT_DEFINITIONS), 10)

    def test_get_agent_by_name(self):
        agent = get_agent("executor")
        self.assertIsNotNone(agent)
        self.assertEqual(agent.name, "executor")
        self.assertEqual(agent.routing_role, "executor")
        self.assertEqual(agent.tools, "execution")

    def test_get_agent_nonexistent(self):
        self.assertIsNone(get_agent("nonexistent"))

    def test_list_agent_names(self):
        names = list_agent_names()
        self.assertIn("explore", names)
        self.assertIn("executor", names)
        self.assertIn("architect", names)

    def test_all_definitions_have_required_fields(self):
        for agent in AGENT_DEFINITIONS:
            self.assertTrue(agent.name)
            self.assertIn(agent.reasoning_effort, ("low", "medium", "high"))
            self.assertIn(agent.model_class, ("frontier", "standard", "fast"))
            self.assertIn(agent.routing_role, ("leader", "specialist", "executor"))


class TestAgentPolicy(unittest.TestCase):
    def test_get_installable_agents(self):
        manifest = {
            "agents": {
                "executor": {"status": "active"},
                "debug": {"status": "internal"},
                "old": {"status": "deprecated"},
            }
        }
        installable = get_installable_native_agent_names(manifest)
        self.assertEqual(installable, {"executor", "debug"})

    def test_get_non_installable_agents(self):
        manifest = {
            "agents": {
                "executor": {"status": "active"},
                "old": {"status": "deprecated"},
                "alias": {"status": "alias", "canonical": "executor"},
            }
        }
        non_installable = get_non_installable_native_agent_names(manifest)
        self.assertEqual(non_installable, {"old", "alias"})

    def test_assert_canonical_targets_valid(self):
        manifest = {
            "agents": {
                "executor": {"status": "active"},
                "fast-exec": {"status": "alias", "canonical": "executor"},
            }
        }
        # Should not raise
        assert_native_agent_canonical_targets(manifest)

    def test_assert_canonical_targets_missing(self):
        manifest = {
            "agents": {
                "fast-exec": {"status": "alias", "canonical": "nonexistent"},
            }
        }
        with self.assertRaises(ValueError):
            assert_native_agent_canonical_targets(manifest)

    def test_assert_canonical_targets_no_canonical_field(self):
        manifest = {
            "agents": {
                "fast-exec": {"status": "alias"},
            }
        }
        with self.assertRaises(ValueError):
            assert_native_agent_canonical_targets(manifest)


if __name__ == "__main__":
    unittest.main()
