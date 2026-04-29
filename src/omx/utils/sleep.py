"""Sleep utilities.

Port of src/utils/sleep.ts. Uses stdlib ``time.sleep``.
"""

from __future__ import annotations

import time


def sleep(seconds: float) -> None:
    """Sleep for the given number of seconds.

    Args:
        seconds: Duration in seconds (fractional values accepted).
    """
    time.sleep(seconds)


def sleep_ms(ms: float) -> None:
    """Sleep for the given number of milliseconds.

    Args:
        ms: Duration in milliseconds.
    """
    time.sleep(ms / 1000.0)
