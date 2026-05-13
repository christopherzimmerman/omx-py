"""Atomic file write utility.

Port of `writeAtomic` from `src/team/state.ts` (oh-my-codex TypeScript).

Strategy:
  1. Ensure parent directory exists.
  2. Write payload to a uniquely-named temp file in the *same* directory as the
     destination (so the final rename is intra-device and atomic on every
     supported OS).
  3. Atomically replace the destination via :func:`os.replace`. On Windows this
     is the only stdlib call that performs an atomic rename across an existing
     target without raising ``FileExistsError`` / ``EBUSY``; on POSIX it maps to
     ``rename(2)`` which is atomic by spec.

The ``set_rename_for_tests`` / ``reset_rename_for_tests`` hooks mirror the TS
test injection points and allow crash-injection tests to simulate a failure
between the temp-write and the final rename.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Callable

__all__ = [
    "write_atomic",
    "set_rename_for_tests",
    "reset_rename_for_tests",
]


# Type alias for the rename callable. Matches the (src, dst) signature of
# os.replace so a test hook can drop in transparently.
RenameFn = Callable[[str, str], None]


def _default_rename(src: str, dst: str) -> None:
    """Default rename implementation — :func:`os.replace`.

    ``os.replace`` is the cross-platform atomic-rename primitive in the stdlib.
    On Windows it avoids the ``EEXIST`` / access-denied errors that ``os.rename``
    raises when the destination already exists; on POSIX it is identical to
    ``rename(2)``.
    """
    os.replace(src, dst)


_rename_for_atomic_write: RenameFn = _default_rename


def set_rename_for_tests(fn: RenameFn) -> None:
    """Install a substitute rename function. Test-only hook."""
    global _rename_for_atomic_write
    _rename_for_atomic_write = fn


def reset_rename_for_tests() -> None:
    """Restore the default rename function. Test-only hook."""
    global _rename_for_atomic_write
    _rename_for_atomic_write = _default_rename


def write_atomic(file_path: Path | str, data: str | bytes) -> None:
    """Atomically write ``data`` to ``file_path``.

    Writes to a temporary sibling file then renames into place via
    :func:`os.replace`, so observers never see a partially written destination.

    Args:
        file_path: Destination path. Accepts ``str`` or ``Path``.
        data: Payload. ``str`` is encoded as UTF-8; ``bytes`` is written verbatim.

    Raises:
        OSError: If the parent directory cannot be created, the temp file
            cannot be written, or the rename fails.
    """
    dest = Path(file_path)
    parent = dest.parent
    parent.mkdir(parents=True, exist_ok=True)

    if isinstance(data, str):
        payload = data.encode("utf-8")
    else:
        payload = data

    # Place the temp file in the destination's directory so the rename is
    # guaranteed to be intra-device (no cross-filesystem EXDEV).
    unique = f"{os.getpid()}.{time.time_ns()}.{secrets.token_hex(4)}"
    tmp_path = parent / f"{dest.name}.tmp.{unique}"

    try:
        # Use a fresh file descriptor and fsync-free write — matching the TS
        # writeFile behavior. Callers that need durability should fsync the
        # parent dir themselves.
        with open(tmp_path, "wb") as f:
            f.write(payload)
        _rename_for_atomic_write(str(tmp_path), str(dest))
    except Exception:
        # Best-effort cleanup; never mask the original error.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise
