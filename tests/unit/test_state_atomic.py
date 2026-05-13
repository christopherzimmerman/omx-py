"""Tests for omx.team.state.atomic.write_atomic."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from omx.team.state.atomic import (
    reset_rename_for_tests,
    set_rename_for_tests,
    write_atomic,
)


class TestWriteAtomic(unittest.TestCase):
    def tearDown(self) -> None:
        # Always restore the default rename hook so a failing test doesn't
        # leak state into others.
        reset_rename_for_tests()

    # --- Basic writes ---

    def test_write_to_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "fresh.txt"
            write_atomic(target, "hello")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "hello")

    def test_overwrite_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "existing.txt"
            target.write_text("original", encoding="utf-8")
            write_atomic(target, "replaced")
            self.assertEqual(target.read_text(encoding="utf-8"), "replaced")

    def test_overwrite_preserves_single_file(self) -> None:
        """After overwrite, no leftover .tmp.* siblings."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "single.txt"
            write_atomic(target, "one")
            write_atomic(target, "two")
            siblings = [p.name for p in Path(tmpdir).iterdir()]
            self.assertEqual(siblings, ["single.txt"])

    # --- Parent dir creation ---

    def test_parent_dir_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "a" / "b" / "c" / "deep.txt"
            self.assertFalse(target.parent.exists())
            write_atomic(target, "deep")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "deep")

    def test_parent_dir_already_exists_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "x.txt"
            # Parent exists; should not raise.
            write_atomic(target, "ok")
            self.assertEqual(target.read_text(encoding="utf-8"), "ok")

    # --- Input shape coverage ---

    def test_str_input_encoded_utf8(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "utf8.txt"
            write_atomic(target, "héllo ☃")
            # Read as bytes to verify the encoding choice.
            self.assertEqual(
                target.read_bytes(),
                "héllo ☃".encode("utf-8"),
            )

    def test_bytes_input_verbatim(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "blob.bin"
            payload = b"\x00\x01\x02\xff\xfe"
            write_atomic(target, payload)
            self.assertEqual(target.read_bytes(), payload)

    def test_str_path_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = os.path.join(tmpdir, "via_str.txt")
            write_atomic(target, "string-path")
            self.assertEqual(Path(target).read_text(encoding="utf-8"), "string-path")

    def test_empty_string(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "empty.txt"
            write_atomic(target, "")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"")

    def test_empty_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "empty.bin"
            write_atomic(target, b"")
            self.assertTrue(target.exists())
            self.assertEqual(target.read_bytes(), b"")

    # --- Crash injection (rename failure) ---

    def test_rename_failure_leaves_no_partial_destination(self) -> None:
        """If rename crashes, the destination must not exist or be unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "crash.txt"

            def boom(src: str, dst: str) -> None:
                raise OSError("simulated crash before rename completes")

            set_rename_for_tests(boom)
            with self.assertRaises(OSError):
                write_atomic(target, "should-never-land")

            # Destination must not exist — we crashed before the rename.
            self.assertFalse(
                target.exists(),
                "destination must not be created when rename fails",
            )

            # And no temp siblings should be left lying around.
            leftovers = list(Path(tmpdir).iterdir())
            self.assertEqual(leftovers, [], f"unexpected leftovers: {leftovers!r}")

    def test_rename_failure_preserves_existing_destination(self) -> None:
        """A failed write must not corrupt or remove the existing destination."""
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "preserve.txt"
            target.write_text("original-content", encoding="utf-8")

            def boom(src: str, dst: str) -> None:
                raise OSError("simulated crash")

            set_rename_for_tests(boom)
            with self.assertRaises(OSError):
                write_atomic(target, "new-content-that-should-not-land")

            # Original content survives untouched.
            self.assertEqual(target.read_text(encoding="utf-8"), "original-content")

            # Only the original file is present; no temp siblings.
            names = sorted(p.name for p in Path(tmpdir).iterdir())
            self.assertEqual(names, ["preserve.txt"])

    # --- Test hook lifecycle ---

    def test_set_and_reset_rename_hook(self) -> None:
        calls: list[tuple[str, str]] = []

        def tracker(src: str, dst: str) -> None:
            calls.append((src, dst))
            os.replace(src, dst)

        set_rename_for_tests(tracker)
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "tracked.txt"
            write_atomic(target, "tracked")
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], str(target))

        reset_rename_for_tests()
        # After reset, the hook is no longer invoked.
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "untracked.txt"
            write_atomic(target, "untracked")
            self.assertEqual(len(calls), 1, "tracker should not have been called again")
            self.assertTrue(target.exists())

    def test_reset_when_never_set_is_noop(self) -> None:
        reset_rename_for_tests()
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "ok.txt"
            write_atomic(target, "ok")
            self.assertEqual(target.read_text(encoding="utf-8"), "ok")


if __name__ == "__main__":
    unittest.main()
