"""Team worker mailbox for direct messaging.

Port of src/team/state/mailbox.ts.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from omx.team.state.types import TeamMailboxMessage


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mailbox_path(team_dir: Path, worker_name: str) -> Path:
    return team_dir / "mailbox" / f"{worker_name}.json"


def read_mailbox(team_dir: Path, worker_name: str) -> list[TeamMailboxMessage]:
    """Read a worker's mailbox messages.

    Args:
        team_dir: Path to team state directory.
        worker_name: Target worker name.

    Returns:
        List of mailbox messages.
    """
    path = _mailbox_path(team_dir, worker_name)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        messages = data.get("messages", [])
        return [TeamMailboxMessage.from_dict(m) for m in messages]
    except (json.JSONDecodeError, OSError):
        return []


def write_mailbox(
    team_dir: Path, worker_name: str, messages: list[TeamMailboxMessage]
) -> None:
    """Write a worker's mailbox messages.

    Args:
        team_dir: Path to team state directory.
        worker_name: Target worker name.
        messages: Messages to write.
    """
    path = _mailbox_path(team_dir, worker_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"worker": worker_name, "messages": [m.to_dict() for m in messages]}
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def send_direct_message(
    team_dir: Path,
    from_worker: str,
    to_worker: str,
    body: str,
) -> TeamMailboxMessage:
    """Send a direct message to a worker's mailbox.

    Deduplicates against existing undelivered messages with the same body.

    Args:
        team_dir: Path to team state directory.
        from_worker: Sender worker name.
        to_worker: Recipient worker name.
        body: Message body.

    Returns:
        The created or existing message.
    """
    messages = read_mailbox(team_dir, to_worker)

    # Deduplicate against undelivered messages
    for msg in messages:
        if (
            msg.from_worker == from_worker
            and msg.body == body
            and msg.delivered_at is None
        ):
            return msg

    message = TeamMailboxMessage(
        message_id=uuid.uuid4().hex[:16],
        from_worker=from_worker,
        to_worker=to_worker,
        body=body,
        created_at=_now_iso(),
    )
    messages.append(message)
    write_mailbox(team_dir, to_worker, messages)
    return message


def mark_message_notified(team_dir: Path, worker_name: str, message_id: str) -> bool:
    """Mark a mailbox message as notified.

    Args:
        team_dir: Path to team state directory.
        worker_name: Worker whose mailbox to update.
        message_id: Message to mark.

    Returns:
        True if the message was found and updated.
    """
    messages = read_mailbox(team_dir, worker_name)
    for msg in messages:
        if msg.message_id == message_id:
            msg.notified_at = _now_iso()
            write_mailbox(team_dir, worker_name, messages)
            return True
    return False


def mark_message_delivered(team_dir: Path, worker_name: str, message_id: str) -> bool:
    """Mark a mailbox message as delivered.

    Args:
        team_dir: Path to team state directory.
        worker_name: Worker whose mailbox to update.
        message_id: Message to mark.

    Returns:
        True if the message was found and updated.
    """
    messages = read_mailbox(team_dir, worker_name)
    for msg in messages:
        if msg.message_id == message_id:
            msg.delivered_at = _now_iso()
            write_mailbox(team_dir, worker_name, messages)
            return True
    return False


def get_undelivered_messages(
    team_dir: Path, worker_name: str
) -> list[TeamMailboxMessage]:
    """Get all undelivered messages for a worker.

    Args:
        team_dir: Path to team state directory.
        worker_name: Target worker name.

    Returns:
        List of undelivered messages.
    """
    return [m for m in read_mailbox(team_dir, worker_name) if m.delivered_at is None]
