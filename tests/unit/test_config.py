"""Tests for omx.config — TOML writer and config utilities."""

import tempfile
import unittest
from pathlib import Path

from omx.config.generator import merge_config, read_config, write_config
from omx.config.toml_writer import dumps
from omx.utils.toml_read import parse_toml


class TestConfig(unittest.TestCase):
    def test_toml_writer_basic_types(self):
        data = {
            "name": "test",
            "version": 1,
            "enabled": True,
            "ratio": 3.14,
            "tags": ["a", "b"],
        }
        output = dumps(data)
        self.assertIn('name = "test"', output)
        self.assertIn("version = 1", output)
        self.assertIn("enabled = true", output)
        self.assertIn("tags = ", output)

    def test_toml_writer_nested_tables(self):
        data = {
            "top": "value",
            "section": {
                "key": "val",
            },
        }
        output = dumps(data)
        self.assertIn("[section]", output)
        self.assertIn('key = "val"', output)

    def test_toml_writer_roundtrips_through_reader(self):
        data = {
            "model": "o4-mini",
            "debug": False,
        }
        toml_str = dumps(data)
        parsed = parse_toml(toml_str)
        self.assertEqual(parsed["model"], "o4-mini")
        self.assertFalse(parsed["debug"])

    def test_merge_config_deep(self):
        base = {"a": 1, "nested": {"x": 1, "y": 2}}
        overlay = {"b": 2, "nested": {"y": 3, "z": 4}}
        result = merge_config(base, overlay)
        self.assertEqual(result, {"a": 1, "b": 2, "nested": {"x": 1, "y": 3, "z": 4}})

    def test_read_write_config(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "config.toml"
            write_config({"model": "o3", "debug": True}, path)
            loaded = read_config(path)
            self.assertEqual(loaded["model"], "o3")
            self.assertTrue(loaded["debug"])


if __name__ == "__main__":
    unittest.main()
