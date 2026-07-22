"""Typed errors for the identity layer."""

from __future__ import annotations


class IdentityError(Exception):
    """Base class for all identity/RBAC errors."""


class PermissionDenied(IdentityError):
    """Raised when an access check fails (fail-closed)."""

    def __init__(self, user_id: str, org_id: str, permission: str) -> None:
        self.user_id = user_id
        self.org_id = org_id
        self.permission = permission
        super().__init__(
            f"user {user_id!r} may not {permission!r} in org {org_id!r}"
        )


class SeatLimitExceeded(IdentityError):
    """Raised when adding a member would exceed an org's seat limit."""

    def __init__(self, org_id: str, seat_limit: int) -> None:
        self.org_id = org_id
        self.seat_limit = seat_limit
        super().__init__(f"org {org_id!r} is full (seat limit {seat_limit})")


class InvitationError(IdentityError):
    """Raised when an invitation cannot be accepted (missing/expired/consumed)."""


class ConflictError(IdentityError):
    """Raised on duplicate ids or emails."""
