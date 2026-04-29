"""MCP-based inter-worker communication.

Port of src/team/mcp-comm.ts.
"""

from __future__ import annotations

from typing import Any

from omx.core.types import RuntimeCommand
from omx.runtime.bridge import RuntimeBridge


def create_mailbox_message(
    bridge: RuntimeBridge,
    message_id: str,
    from_worker: str,
    to_worker: str,
    body: str,
) -> None:
    """Create an inter-worker mailbox message via the runtime bridge.

    Args:
        bridge: RuntimeBridge instance for command execution.
        message_id: Unique message identifier.
        from_worker: Sender worker ID.
        to_worker: Recipient worker ID.
        body: Message body text.
    """
    bridge.exec_command(
        RuntimeCommand.create_mailbox_message(
            message_id=message_id,
            from_worker=from_worker,
            to_worker=to_worker,
            body=body,
        )
    )


def queue_dispatch(
    bridge: RuntimeBridge,
    request_id: str,
    target: str,
    metadata: Any | None = None,
) -> None:
    """Queue a dispatch request via the runtime bridge.

    Args:
        bridge: RuntimeBridge instance for command execution.
        request_id: Unique dispatch request identifier.
        target: Delivery target (e.g. tmux pane handle).
        metadata: Optional payload to attach to the dispatch.
    """
    bridge.exec_command(
        RuntimeCommand.queue_dispatch(
            request_id=request_id,
            target=target,
            metadata=metadata,
        )
    )
