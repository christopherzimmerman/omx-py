"""Visual verification constants.

Port of src/visual/constants.ts.
"""

from __future__ import annotations

from enum import StrEnum

VISUAL_NEXT_ACTIONS_LIMIT = 5

VISUAL_VERDICT_STATUSES = ("pass", "revise", "fail")


class VisualVerdictStatus(StrEnum):
    """Visual verdict status values."""

    PASS = "pass"
    REVISE = "revise"
    FAIL = "fail"
