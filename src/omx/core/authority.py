"""Authority lease management.

Port of omx-runtime-core/src/authority.rs.
"""

from __future__ import annotations

from omx.core.types import AuthoritySnapshot


class AuthorityError(Exception):
    """Base error for authority operations."""


class AlreadyHeldByOther(AuthorityError):
    """Raised when attempting to acquire a lease held by a different owner."""

    def __init__(self, current_owner: str) -> None:
        self.current_owner = current_owner
        super().__init__(f"lease already held by {current_owner}")


class OwnerMismatch(AuthorityError):
    """Raised when a renewal is attempted by a non-owner."""

    def __init__(self, current_owner: str) -> None:
        self.current_owner = current_owner
        super().__init__(f"owner mismatch: lease held by {current_owner}")


class NotHeld(AuthorityError):
    """Raised when renewing a lease that has not been acquired."""

    def __init__(self) -> None:
        super().__init__("no lease currently held")


class AuthorityLease:
    """Mutual-exclusion lease for runtime authority."""

    def __init__(self) -> None:
        self._owner: str | None = None
        self._lease_id: str | None = None
        self._leased_until: str | None = None
        self._stale: bool = False
        self._stale_reason: str | None = None

    def acquire(self, owner: str, lease_id: str, leased_until: str) -> None:
        """Acquire the authority lease.

        Args:
            owner: Identifier of the requesting owner.
            lease_id: Unique lease identifier.
            leased_until: ISO timestamp for lease expiry.

        Raises:
            AlreadyHeldByOther: If the lease is held by a different owner.
        """
        if self._owner is not None and self._owner != owner:
            raise AlreadyHeldByOther(self._owner)
        self._owner = owner
        self._lease_id = lease_id
        self._leased_until = leased_until
        self._stale = False
        self._stale_reason = None

    def renew(self, owner: str, lease_id: str, leased_until: str) -> None:
        """Renew an existing authority lease.

        Args:
            owner: Identifier of the current owner.
            lease_id: New lease identifier.
            leased_until: New ISO timestamp for lease expiry.

        Raises:
            NotHeld: If no lease is currently held.
            OwnerMismatch: If the caller is not the current owner.
        """
        if self._owner is None:
            raise NotHeld()
        if self._owner != owner:
            raise OwnerMismatch(self._owner)
        self._lease_id = lease_id
        self._leased_until = leased_until
        self._stale = False
        self._stale_reason = None

    def force_release(self) -> None:
        """Unconditionally release the lease regardless of owner."""
        self._owner = None
        self._lease_id = None
        self._leased_until = None
        self._stale = False
        self._stale_reason = None

    def mark_stale(self, reason: str) -> None:
        self._stale = True
        self._stale_reason = reason

    def clear_stale(self) -> None:
        self._stale = False
        self._stale_reason = None

    def is_held(self) -> bool:
        return self._owner is not None

    def is_stale(self) -> bool:
        return self._stale

    def current_owner(self) -> str | None:
        return self._owner

    def to_snapshot(self) -> AuthoritySnapshot:
        return AuthoritySnapshot(
            owner=self._owner,
            lease_id=self._lease_id,
            leased_until=self._leased_until,
            stale=self._stale,
            stale_reason=self._stale_reason,
        )
