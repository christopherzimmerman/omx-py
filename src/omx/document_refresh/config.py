"""Document refresh configuration.

Port of src/document-refresh/config.ts.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocumentRefreshRule:
    """A document refresh rule.

    Attributes:
        id: Rule identifier.
        description: Human-readable description.
        source_globs: Glob patterns for source files.
        refresh_targets: Glob patterns for refresh target files.
        ignored_globs: Glob patterns for ignored files.
    """

    id: str = ""
    description: str = ""
    source_globs: list[str] = field(default_factory=list)
    refresh_targets: list[str] = field(default_factory=list)
    ignored_globs: list[str] = field(default_factory=list)


DEFAULT_DOCUMENT_REFRESH_RULES: list[DocumentRefreshRule] = [
    DocumentRefreshRule(
        id="native-hook-behavior",
        description="Codex native hook behavior and managed hook configuration",
        source_globs=[
            "src/scripts/codex-native-hook.ts",
            "src/scripts/codex-native-pre-post.ts",
            "src/scripts/__tests__/codex-native-hook.test.ts",
            "src/config/codex-hooks.ts",
            "src/config/__tests__/codex-hooks.test.ts",
        ],
        refresh_targets=[
            "docs/codex-native-hooks.md",
            ".omx/plans/*codex-native*",
            ".omx/specs/*codex-native*",
            ".omx/plans/*native-hook*",
            ".omx/specs/*native-hook*",
        ],
    ),
    DocumentRefreshRule(
        id="document-refresh-enforcer",
        description="Document-refresh warning classifier and rule behavior",
        source_globs=["src/document-refresh/**"],
        refresh_targets=[
            "docs/codex-native-hooks.md",
            ".omx/plans/*document-refresh*",
            ".omx/specs/*document-refresh*",
        ],
    ),
    DocumentRefreshRule(
        id="cli-operator-behavior",
        description="CLI and operator-facing behavior",
        source_globs=["src/cli/**"],
        refresh_targets=[
            "README.md",
            "docs/getting-started.html",
            ".omx/plans/*cli*",
            ".omx/specs/*cli*",
            ".omx/plans/*operator*",
            ".omx/specs/*operator*",
        ],
        ignored_globs=[
            "src/cli/**/__tests__/**",
            "src/cli/**/*.test.ts",
        ],
    ),
    DocumentRefreshRule(
        id="prompt-guidance-behavior",
        description="Prompt guidance and hook routing behavior",
        source_globs=[
            "src/hooks/keyword-detector.ts",
            "src/hooks/triage-config.ts",
            "src/hooks/triage-heuristic.ts",
            "src/hooks/__tests__/prompt-guidance-*.test.ts",
            "src/hooks/__tests__/analyze-*-contract.test.ts",
        ],
        refresh_targets=[
            "docs/prompt-guidance-contract.md",
            ".omx/plans/*prompt*",
            ".omx/specs/*prompt*",
            ".omx/plans/*guidance*",
            ".omx/specs/*guidance*",
        ],
    ),
]
