"""Temporary notification contract.

Manages the OMX_NOTIFY_TEMP and OMX_NOTIFY_TEMP_CONTRACT environment variables
for temporary notification mode activation.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from enum import StrEnum


OMX_NOTIFY_TEMP_ENV = "OMX_NOTIFY_TEMP"
OMX_NOTIFY_TEMP_CONTRACT_ENV = "OMX_NOTIFY_TEMP_CONTRACT"


class NotifyTempSource(StrEnum):
    """Source of temporary notification activation."""

    NONE = "none"
    CLI = "cli"
    ENV = "env"
    PROVIDERS = "providers"


@dataclass
class NotifyTempContract:
    """Temporary notification contract.

    Attributes:
        active: Whether temporary notifications are active.
        selectors: Raw selector strings from CLI args.
        canonical_selectors: Deduplicated selector strings.
        warnings: Warning messages generated during parsing.
        source: How the contract was activated.
    """

    active: bool = False
    selectors: list[str] = field(default_factory=list)
    canonical_selectors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: NotifyTempSource = NotifyTempSource.NONE


@dataclass
class ParseNotifyTempContractResult:
    """Result of parsing CLI args for temp contract.

    Attributes:
        contract: The parsed contract.
        passthrough_args: Args not consumed by the parser.
    """

    contract: NotifyTempContract = field(default_factory=NotifyTempContract)
    passthrough_args: list[str] = field(default_factory=list)


def _normalize_custom_selector(raw: str) -> str | None:
    """Normalize a custom selector string."""
    normalized = raw.strip().lower()
    if not normalized:
        return None
    if normalized.startswith("openclaw:"):
        gateway = normalized[len("openclaw:") :].strip()
        if not gateway:
            return None
        return f"openclaw:{gateway}"
    return f"custom:{normalized}"


def _to_unique(values: list[str]) -> list[str]:
    """Deduplicate while preserving order."""
    seen: set[str] = set()
    result: list[str] = []
    for v in values:
        if v not in seen:
            seen.add(v)
            result.append(v)
    return result


def parse_notify_temp_contract_from_args(
    args: list[str],
    env: dict[str, str] | None = None,
) -> ParseNotifyTempContractResult:
    """Parse CLI args for temporary notification contract.

    Args:
        args: CLI argument list.
        env: Environment variables (defaults to os.environ).

    Returns:
        Parsed contract and passthrough args.
    """
    if env is None:
        env = dict(os.environ)

    passthrough_args: list[str] = []
    selectors: list[str] = []
    warnings: list[str] = []
    cli_activated = False

    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--notify-temp":
            cli_activated = True
            i += 1
            continue

        if arg in ("--discord", "--slack", "--telegram"):
            selectors.append(arg[2:])
            i += 1
            continue

        if arg == "--custom":
            next_arg = args[i + 1] if i + 1 < len(args) else None
            if not next_arg or next_arg.startswith("-"):
                warnings.append(
                    "notify temp: ignoring --custom without a provider name"
                )
                i += 1
                continue
            normalized = _normalize_custom_selector(next_arg)
            if not normalized:
                warnings.append(
                    f'notify temp: ignoring invalid --custom selector "{next_arg}"'
                )
            else:
                selectors.append(normalized)
            i += 2
            continue

        if arg.startswith("--custom="):
            raw = arg[len("--custom=") :]
            normalized = _normalize_custom_selector(raw)
            if not normalized:
                warnings.append(
                    f'notify temp: ignoring invalid --custom selector "{raw}"'
                )
            else:
                selectors.append(normalized)
            i += 1
            continue

        passthrough_args.append(arg)
        i += 1

    env_activated = env.get(OMX_NOTIFY_TEMP_ENV) == "1"
    canonical_selectors = _to_unique(selectors)
    provider_activated = len(canonical_selectors) > 0
    active = cli_activated or env_activated or provider_activated

    if provider_activated and not cli_activated and not env_activated:
        warnings.append(
            "notify temp: provider selectors imply temp mode (auto-activated)"
        )

    source = NotifyTempSource.NONE
    if cli_activated:
        source = NotifyTempSource.CLI
    elif env_activated:
        source = NotifyTempSource.ENV
    elif provider_activated:
        source = NotifyTempSource.PROVIDERS

    return ParseNotifyTempContractResult(
        contract=NotifyTempContract(
            active=active,
            selectors=list(selectors),
            canonical_selectors=canonical_selectors,
            warnings=warnings,
            source=source,
        ),
        passthrough_args=passthrough_args,
    )


def serialize_notify_temp_contract(contract: NotifyTempContract) -> str:
    """Serialize a contract to JSON string.

    Args:
        contract: The contract to serialize.

    Returns:
        JSON string representation.
    """
    return json.dumps(
        {
            "active": contract.active,
            "selectors": contract.selectors,
            "canonicalSelectors": contract.canonical_selectors,
            "warnings": contract.warnings,
            "source": contract.source,
        }
    )


def is_notify_temp_env_active(env: dict[str, str] | None = None) -> bool:
    """Check if OMX_NOTIFY_TEMP env is set to '1'.

    Args:
        env: Environment variables (defaults to os.environ).

    Returns:
        True if temp notification mode is active via env.
    """
    if env is None:
        env = dict(os.environ)
    return env.get(OMX_NOTIFY_TEMP_ENV) == "1"


def read_notify_temp_contract_from_env(
    env: dict[str, str] | None = None,
) -> NotifyTempContract | None:
    """Read a temp contract from the OMX_NOTIFY_TEMP_CONTRACT env var.

    Args:
        env: Environment variables (defaults to os.environ).

    Returns:
        The parsed contract, or None.
    """
    if env is None:
        env = dict(os.environ)
    raw = env.get(OMX_NOTIFY_TEMP_CONTRACT_ENV)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        if (
            not isinstance(parsed.get("active"), bool)
            or not isinstance(parsed.get("selectors"), list)
            or not isinstance(parsed.get("canonicalSelectors"), list)
            or not isinstance(parsed.get("warnings"), list)
            or not isinstance(parsed.get("source"), str)
        ):
            return None
        return NotifyTempContract(
            active=parsed["active"],
            selectors=[s for s in parsed["selectors"] if isinstance(s, str)],
            canonical_selectors=[
                s for s in parsed["canonicalSelectors"] if isinstance(s, str)
            ],
            warnings=[s for s in parsed["warnings"] if isinstance(s, str)],
            source=NotifyTempSource(parsed["source"])
            if parsed["source"] in [e.value for e in NotifyTempSource]
            else NotifyTempSource.NONE,
        )
    except Exception:
        return None


def is_openclaw_selected_in_temp_contract(contract: NotifyTempContract | None) -> bool:
    """Check if OpenClaw is selected in a temp contract.

    Args:
        contract: The temp contract.

    Returns:
        True if any openclaw or custom selector is present.
    """
    if not contract or not contract.active:
        return False
    return any(
        s.startswith("openclaw:") or s.startswith("custom:")
        for s in contract.canonical_selectors
    )


def get_temp_builtin_selectors(contract: NotifyTempContract | None) -> set[str]:
    """Get the set of built-in selectors from a temp contract.

    Args:
        contract: The temp contract.

    Returns:
        Set of builtin selector names (discord, slack, telegram).
    """
    if not contract or not contract.active:
        return set()
    return {
        s for s in contract.canonical_selectors if s in ("discord", "slack", "telegram")
    }


def get_selected_openclaw_gateway_names(
    contract: NotifyTempContract | None,
) -> set[str]:
    """Get the set of selected OpenClaw gateway names.

    Args:
        contract: The temp contract.

    Returns:
        Set of gateway names.
    """
    if not contract or not contract.active:
        return set()
    names: list[str] = []
    for selector in contract.canonical_selectors:
        if selector.startswith("openclaw:"):
            name = selector[len("openclaw:") :].strip().lower()
            if name:
                names.append(name)
        elif selector.startswith("custom:"):
            name = selector[len("custom:") :].strip().lower()
            if name:
                names.append(name)
    return set(names)
