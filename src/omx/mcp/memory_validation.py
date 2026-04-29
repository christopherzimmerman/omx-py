"""Memory validation helpers.

Port of src/mcp/memory-validation.ts.
"""

from __future__ import annotations

DEFAULT_NOTEPAD_PRUNE_DAYS_OLD = 7


def parse_notepad_prune_days_old(
    value: object,
    default_days: int = DEFAULT_NOTEPAD_PRUNE_DAYS_OLD,
) -> tuple[bool, int | None, str | None]:
    """Parse and validate the daysOld parameter for notepad pruning.

    Args:
        value: The raw value to validate.
        default_days: Default number of days if value is None.

    Returns:
        Tuple of (ok, days, error). If ok is True, days is set. Otherwise error is set.
    """
    if value is None:
        return (True, default_days, None)
    if not isinstance(value, (int, float)):
        return (False, None, "daysOld must be a non-negative integer")
    if not isinstance(value, int) or value < 0:
        # Allow float values that are whole numbers
        if isinstance(value, float) and value == int(value) and value >= 0:
            return (True, int(value), None)
        return (False, None, "daysOld must be a non-negative integer")
    return (True, value, None)
