"""Runtime bridge — subprocess wrapper for codex/claude CLI invocation.

Port of src/runtime/bridge.ts.
In the Python port, the bridge directly uses the Python core engine
rather than shelling out to a Rust binary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from omx.core.engine import RuntimeEngine
from omx.core.types import RuntimeCommand, RuntimeEvent, RuntimeSnapshot


class RuntimeBridgeError(Exception):
    """Typed error for bridge operations."""


class RuntimeBridge:
    """Bridge to the runtime engine.

    In the TS version this shells out to a Rust binary.
    In the Python port we use the Python engine directly.
    """

    def __init__(self, state_dir: Path) -> None:
        self._state_dir = state_dir
        self._engine: RuntimeEngine | None = None

    def _get_engine(self) -> RuntimeEngine:
        if self._engine is not None:
            return self._engine
        if (self._state_dir / "events.json").exists():
            self._engine = RuntimeEngine.load(self._state_dir)
        else:
            self._engine = RuntimeEngine().with_state_dir(self._state_dir)
        return self._engine

    def exec_command(self, command: RuntimeCommand) -> RuntimeEvent:
        """Execute a runtime command and persist state."""
        engine = self._get_engine()
        try:
            event = engine.process(command)
            engine.persist()
            engine.write_compatibility_view()
            return event
        except Exception as exc:
            raise RuntimeBridgeError(str(exc)) from exc

    def read_snapshot(self) -> RuntimeSnapshot:
        """Read the current runtime snapshot (loading engine state if needed)."""
        return self._get_engine().snapshot()

    def read_authority(self) -> dict[str, Any]:
        return self._get_engine().snapshot().authority.to_dict()

    def read_readiness(self) -> dict[str, Any]:
        return self._get_engine().snapshot().readiness.to_dict()

    def read_backlog(self) -> dict[str, Any]:
        return self._get_engine().snapshot().backlog.to_dict()

    def read_dispatch_records(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._get_engine().dispatch.records]

    def read_mailbox_records(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._get_engine().mailbox.records]
