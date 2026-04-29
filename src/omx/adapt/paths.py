"""Adapt path resolution.

Port of src/adapt/paths.ts.
"""

from __future__ import annotations

from pathlib import Path

from omx.adapt.contracts import AdaptPathSet
from omx.utils.paths import omx_adapters_dir


def resolve_adapt_paths(cwd: str, target: str) -> AdaptPathSet:
    """Resolve adapter artifact paths for a given target.

    Args:
        cwd: Working directory.
        target: Adapt target name.

    Returns:
        An AdaptPathSet with all resolved paths.
    """
    adapter_root = str(omx_adapters_dir(Path(cwd)) / target)
    reports_dir = str(Path(adapter_root) / "reports")
    return AdaptPathSet(
        adapter_root=adapter_root,
        config_path=str(Path(adapter_root) / "adapter.json"),
        envelope_path=str(Path(adapter_root) / "envelope.json"),
        reports_dir=reports_dir,
        probe_report_path=str(Path(reports_dir) / "probe.json"),
        status_report_path=str(Path(reports_dir) / "status.json"),
    )
