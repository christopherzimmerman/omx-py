"""Team reminder intent types.

Port of src/team/reminder-intents.ts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TeamReminderIntent:
    """Intent metadata for dispatch reminders."""

    kind: str = "task_dispatch"  # task_dispatch, nudge, mailbox_delivery
    task_id: str | None = None
    message_id: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind, "reason": self.reason}
        if self.task_id:
            d["task_id"] = self.task_id
        if self.message_id:
            d["message_id"] = self.message_id
        return d
