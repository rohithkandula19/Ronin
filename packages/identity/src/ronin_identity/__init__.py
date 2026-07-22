"""Ronin Identity — users, organizations, memberships, and fail-closed RBAC.

Guarantees this package enforces (not merely documents):

1. No plaintext credentials: verifiers and API keys are stored only as salted
   hashes; raw values are supplied by the caller and never persisted.
2. Fail-closed access: unknown user, suspended user, non-member, unknown role,
   or unknown permission all resolve to *deny*. Read-only roles (viewer) can
   never perform write/sensitive permissions.
3. Organization isolation: every lookup is org-scoped. A member of org A can
   never resolve permissions or API keys belonging to org B.
4. Determinism: ids, salts, raw key material, and the current time are all
   caller-supplied — no hidden clocks or randomness.
"""

from __future__ import annotations

from .errors import (
    ConflictError,
    IdentityError,
    InvitationError,
    PermissionDenied,
    SeatLimitExceeded,
)
from .hashing import SecretHash, hash_secret, verify_secret
from .models import (
    ApiKeyRecord,
    Invitation,
    InvitationStatus,
    Membership,
    Organization,
    Role,
    User,
    UserStatus,
)
from .rbac import (
    BILLING_MANAGE,
    DEFAULT_MATRIX,
    KNOWN_PERMISSIONS,
    MEMBER_INVITE,
    MEMBER_READ,
    MODEL_RELEASE,
    READ_ONLY_PERMISSIONS,
    WORKSPACE_READ,
    WORKSPACE_WRITE,
    PermissionMatrix,
)
from .service import IdentityService
from .store import IdentityStore, InMemoryStore

__all__ = [
    # errors
    "IdentityError",
    "PermissionDenied",
    "SeatLimitExceeded",
    "InvitationError",
    "ConflictError",
    # hashing
    "SecretHash",
    "hash_secret",
    "verify_secret",
    # models
    "User",
    "UserStatus",
    "Organization",
    "Membership",
    "Role",
    "Invitation",
    "InvitationStatus",
    "ApiKeyRecord",
    # rbac
    "PermissionMatrix",
    "DEFAULT_MATRIX",
    "KNOWN_PERMISSIONS",
    "READ_ONLY_PERMISSIONS",
    "WORKSPACE_READ",
    "WORKSPACE_WRITE",
    "MEMBER_READ",
    "MEMBER_INVITE",
    "BILLING_MANAGE",
    "MODEL_RELEASE",
    # store
    "IdentityStore",
    "InMemoryStore",
    # service
    "IdentityService",
]
