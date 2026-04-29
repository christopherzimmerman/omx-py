"""HUD state reconciliation.

Port of src/hud/reconcile.ts.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

from omx.hud.constants import HUD_TMUX_HEIGHT_LINES
from omx.hud.tmux import (
    TmuxPaneSnapshot,
    build_hud_watch_command,
    create_hud_watch_pane,
    find_hud_watch_pane_ids,
    is_hud_watch_pane,
    kill_tmux_pane,
    list_current_window_panes,
    resize_tmux_pane,
)


@dataclass
class ReconcileHudForPromptSubmitResult:
    """Result of reconciling HUD for a prompt submit.

    Attributes:
        status: Reconcile outcome status.
        pane_id: Tmux pane ID or None.
        desired_height: Desired pane height or None.
        duplicate_count: Number of duplicate panes found.
    """

    status: str  # skipped_not_tmux | skipped_no_entry | resized | recreated | replaced_duplicates | failed
    pane_id: str | None = None
    desired_height: int | None = None
    duplicate_count: int = 0


def reconcile_hud_for_prompt_submit(
    cwd: str,
    *,
    env: dict[str, str] | None = None,
    session_id: str | None = None,
    list_panes_fn: Callable[..., list[TmuxPaneSnapshot]] | None = None,
    create_pane_fn: Callable[..., str | None] | None = None,
    kill_pane_fn: Callable[[str], bool] | None = None,
    resize_pane_fn: Callable[[str, int], bool] | None = None,
    resolve_omx_bin_fn: Callable[[], str | None] | None = None,
    read_hud_config_fn: Callable[..., Any] | None = None,
) -> ReconcileHudForPromptSubmitResult:
    """Reconcile HUD pane state on prompt submit.

    Ensures exactly one HUD watch pane exists in the current tmux window.

    Args:
        cwd: Working directory.
        env: Environment variables override.
        session_id: Session ID override.
        list_panes_fn: Pane listing function override.
        create_pane_fn: Pane creation function override.
        kill_pane_fn: Pane killing function override.
        resize_pane_fn: Pane resize function override.
        resolve_omx_bin_fn: OMX binary resolution override.
        read_hud_config_fn: HUD config reader override.

    Returns:
        ReconcileHudForPromptSubmitResult describing the outcome.
    """
    env = env or dict(os.environ)
    if not env.get("TMUX"):
        return ReconcileHudForPromptSubmitResult(status="skipped_not_tmux")

    if resolve_omx_bin_fn:
        omx_bin = resolve_omx_bin_fn()
    else:
        omx_bin = None  # Structural stub
    if not omx_bin:
        return ReconcileHudForPromptSubmitResult(status="skipped_no_entry")

    _list_panes = list_panes_fn or list_current_window_panes
    _create_pane = create_pane_fn or (
        lambda c, cmd, **kw: create_hud_watch_pane(c, cmd, **kw)
    )
    _kill_pane = kill_pane_fn or kill_tmux_pane
    _resize_pane = resize_pane_fn or resize_tmux_pane

    current_pane_id = env.get("TMUX_PANE", "").strip() or None
    panes = _list_panes(current_pane_id)
    hud_pane_ids = find_hud_watch_pane_ids(panes, current_pane_id)
    duplicate_count = max(0, len(hud_pane_ids) - 1)
    desired_height = HUD_TMUX_HEIGHT_LINES

    resolved_session_id = (
        (session_id or "").strip() or env.get("OMX_SESSION_ID", "").strip() or None
    )

    preset = None
    if read_hud_config_fn:
        try:
            config = read_hud_config_fn(cwd)
            preset = getattr(config, "preset", None) if config else None
        except Exception:
            pass

    hud_cmd = build_hud_watch_command(omx_bin, preset, resolved_session_id)

    if len(hud_pane_ids) == 1:
        resized = _resize_pane(hud_pane_ids[0], desired_height)
        return ReconcileHudForPromptSubmitResult(
            status="resized" if resized else "failed",
            pane_id=hud_pane_ids[0],
            desired_height=desired_height,
            duplicate_count=duplicate_count,
        )

    for pane_id in hud_pane_ids:
        _kill_pane(pane_id)

    non_hud_count = sum(1 for p in panes if not is_hud_watch_pane(p))
    pane_id = _create_pane(
        cwd,
        hud_cmd,
        height_lines=desired_height,
        full_width=non_hud_count > 1,
        target_pane_id=current_pane_id,
    )
    if not pane_id:
        return ReconcileHudForPromptSubmitResult(
            status="failed",
            desired_height=desired_height,
            duplicate_count=duplicate_count,
        )

    _resize_pane(pane_id, desired_height)

    return ReconcileHudForPromptSubmitResult(
        status="replaced_duplicates" if len(hud_pane_ids) > 1 else "recreated",
        pane_id=pane_id,
        desired_height=desired_height,
        duplicate_count=duplicate_count,
    )
