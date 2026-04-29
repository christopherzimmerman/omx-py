"""Team filesystem-based locking primitives.

Port of src/team/state/locks.ts.
Uses directory creation for mutual exclusion (mkdir is atomic on all platforms).
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path


LOCK_STALE_MS = 5 * 60 * 1000  # 5 minutes
LOCK_RETRY_MS = 25


def _lock_owner_token() -> str:
    """Generate a unique lock owner token."""
    return f"{os.getpid()}.{int(time.time() * 1000)}.{uuid.uuid4().hex[:8]}"


def _is_lock_stale(lock_dir: Path, stale_ms: int = LOCK_STALE_MS) -> bool:
    """Check if a lock directory is stale based on modification time."""
    try:
        mtime = lock_dir.stat().st_mtime
        age_ms = (time.time() - mtime) * 1000
        return age_ms > stale_ms
    except OSError:
        return True


def _try_acquire_lock(lock_dir: Path) -> bool:
    """Attempt to create a lock directory atomically."""
    try:
        lock_dir.mkdir(parents=True, exist_ok=False)
        return True
    except FileExistsError:
        return False


def _release_lock(lock_dir: Path) -> None:
    """Release a lock by removing the directory."""
    try:
        lock_dir.rmdir()
    except OSError:
        pass


def with_lock(lock_dir: Path, timeout_ms: int = 10_000, stale_ms: int = LOCK_STALE_MS):
    """Context manager for filesystem-based locking.

    Args:
        lock_dir: Path to use as the lock directory.
        timeout_ms: Maximum time to wait for lock acquisition.
        stale_ms: Age threshold for stale lock recovery.

    Yields:
        None (lock is held for the duration of the context).

    Raises:
        TimeoutError: If the lock cannot be acquired within timeout.
    """

    class _LockContext:
        def __enter__(self):
            deadline = time.monotonic() + (timeout_ms / 1000.0)
            delay = LOCK_RETRY_MS / 1000.0

            while True:
                if _try_acquire_lock(lock_dir):
                    return self

                # Try to recover stale lock
                if lock_dir.exists() and _is_lock_stale(lock_dir, stale_ms):
                    try:
                        lock_dir.rmdir()
                        if _try_acquire_lock(lock_dir):
                            return self
                    except OSError:
                        pass

                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Failed to acquire lock: {lock_dir}")

                time.sleep(delay)
                delay = min(delay * 2, 0.5)

        def __exit__(self, *args):
            _release_lock(lock_dir)

    return _LockContext()


def with_task_claim_lock(team_dir: Path, task_id: str, timeout_ms: int = 5_000):
    """Acquire a task claim lock."""
    lock_dir = team_dir / "claims" / f"{task_id}.lock"
    return with_lock(lock_dir, timeout_ms=timeout_ms)


def with_dispatch_lock(team_dir: Path, timeout_ms: int = 15_000):
    """Acquire the dispatch coordination lock."""
    lock_dir = team_dir / "dispatch" / ".lock"
    return with_lock(lock_dir, timeout_ms=timeout_ms)


def with_mailbox_lock(team_dir: Path, worker_name: str, timeout_ms: int = 5_000):
    """Acquire a worker mailbox lock."""
    lock_dir = team_dir / "mailbox" / f"{worker_name}.lock"
    return with_lock(lock_dir, timeout_ms=timeout_ms)


def with_scaling_lock(team_dir: Path, timeout_ms: int = 10_000):
    """Acquire the scaling lock."""
    lock_dir = team_dir / ".lock.scaling"
    return with_lock(lock_dir, timeout_ms=timeout_ms)
