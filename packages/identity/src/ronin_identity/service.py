"""Identity, organizations, and fail-closed RBAC service.

All logic is deterministic: ids, salts, raw key material, and the current time
(``now``) are supplied by the caller. Persistence is injectable and defaults to
an in-memory store. Every lookup is org-scoped; there is no cross-org leakage.
"""

from __future__ import annotations

from datetime import datetime

from .errors import (
    ConflictError,
    InvitationError,
    PermissionDenied,
    SeatLimitExceeded,
)
from .hashing import hash_secret, verify_secret
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
from .rbac import PermissionMatrix
from .store import IdentityStore, InMemoryStore


class IdentityService:
    """Coordinates users, orgs, memberships, invites, API keys, and RBAC."""

    def __init__(
        self,
        store: IdentityStore | None = None,
        matrix: PermissionMatrix | None = None,
    ) -> None:
        self.store: IdentityStore = store if store is not None else InMemoryStore()
        self.matrix: PermissionMatrix = (
            matrix if matrix is not None else PermissionMatrix()
        )

    # --- users -----------------------------------------------------------
    def create_user(
        self,
        user_id: str,
        email: str,
        display_name: str,
        status: UserStatus = UserStatus.active,
    ) -> User:
        """Register a new user. Ids and emails must be globally unique."""
        if self.store.get_user(user_id) is not None:
            raise ConflictError(f"user id {user_id!r} already exists")
        if self.store.get_user_by_email(email) is not None:
            raise ConflictError(f"email {email!r} already registered")
        user = User(
            id=user_id, email=email, display_name=display_name, status=status
        )
        self.store.add_user(user)
        return user

    def get_user(self, user_id: str) -> User | None:
        """Fetch a user by id, or None."""
        return self.store.get_user(user_id)

    def suspend_user(self, user_id: str) -> User:
        """Mark a user suspended (revokes all their access, fail-closed)."""
        return self._set_status(user_id, UserStatus.suspended)

    def activate_user(self, user_id: str) -> User:
        """Reactivate a suspended user."""
        return self._set_status(user_id, UserStatus.active)

    def _set_status(self, user_id: str, status: UserStatus) -> User:
        user = self._require_user(user_id)
        updated = user.model_copy(update={"status": status})
        self.store.add_user(updated)
        return updated

    def set_verifier(self, user_id: str, raw: str, *, salt: str) -> User:
        """Store a salted hash of a login verifier (never the raw value)."""
        user = self._require_user(user_id)
        updated = user.model_copy(
            update={"verifier": hash_secret(raw, salt=salt)}
        )
        self.store.add_user(updated)
        return updated

    def verify_user(self, user_id: str, raw: str) -> bool:
        """Return True iff ``raw`` matches the user's stored verifier.

        Fail-closed: no verifier set or a suspended user returns False.
        """
        user = self.store.get_user(user_id)
        if user is None or not user.is_active or user.verifier is None:
            return False
        return verify_secret(raw, user.verifier)

    # --- organizations ---------------------------------------------------
    def create_org(
        self, org_id: str, name: str, plan_slug: str, seat_limit: int
    ) -> Organization:
        """Create an organization with an opaque plan slug and seat limit."""
        if self.store.get_org(org_id) is not None:
            raise ConflictError(f"org id {org_id!r} already exists")
        org = Organization(
            id=org_id, name=name, plan_slug=plan_slug, seat_limit=seat_limit
        )
        self.store.add_org(org)
        return org

    def get_org(self, org_id: str) -> Organization | None:
        """Fetch an org by id, or None."""
        return self.store.get_org(org_id)

    def seat_count(self, org_id: str) -> int:
        """Number of members currently in the org."""
        return self.store.count_members(org_id)

    # --- memberships -----------------------------------------------------
    def add_member(self, user_id: str, org_id: str, role: Role) -> Membership:
        """Add a user to an org with a role, enforcing the seat limit."""
        self._require_user(user_id)
        org = self._require_org(org_id)
        if self.store.get_membership(user_id, org_id) is not None:
            raise ConflictError(
                f"user {user_id!r} already a member of org {org_id!r}"
            )
        if self.store.count_members(org_id) >= org.seat_limit:
            raise SeatLimitExceeded(org_id, org.seat_limit)
        membership = Membership(user_id=user_id, org_id=org_id, role=role)
        self.store.add_membership(membership)
        return membership

    def get_membership(self, user_id: str, org_id: str) -> Membership | None:
        """Org-scoped membership lookup. Never crosses org boundaries."""
        return self.store.get_membership(user_id, org_id)

    def remove_member(self, user_id: str, org_id: str) -> None:
        """Remove a user's membership in an org (idempotent)."""
        self.store.remove_membership(user_id, org_id)

    # --- RBAC ------------------------------------------------------------
    def can(self, user_id: str, org_id: str, permission: str) -> bool:
        """Fail-closed access check.

        Returns False for: unknown user, suspended user, non-member of the org,
        unknown role, unknown permission, or a permission the role lacks.
        """
        user = self.store.get_user(user_id)
        if user is None or not user.is_active:
            return False
        membership = self.store.get_membership(user_id, org_id)
        if membership is None:
            return False
        return self.matrix.role_can(membership.role, permission)

    def require(self, user_id: str, org_id: str, permission: str) -> None:
        """Raise :class:`PermissionDenied` unless :meth:`can` allows it."""
        if not self.can(user_id, org_id, permission):
            raise PermissionDenied(user_id, org_id, permission)

    # --- invitations -----------------------------------------------------
    def create_invitation(
        self,
        invite_id: str,
        org_id: str,
        email: str,
        role: Role,
        *,
        now: datetime,
        expires_at: datetime,
    ) -> Invitation:
        """Create a pending, org-scoped invitation with a caller-set expiry."""
        self._require_org(org_id)
        if self.store.get_invitation(invite_id) is not None:
            raise ConflictError(f"invitation id {invite_id!r} already exists")
        if expires_at <= now:
            raise InvitationError("expiry must be in the future")
        invite = Invitation(
            id=invite_id,
            org_id=org_id,
            email=email,
            role=role,
            created_at=now,
            expires_at=expires_at,
        )
        self.store.add_invitation(invite)
        return invite

    def accept_invitation(
        self, invite_id: str, user_id: str, *, now: datetime
    ) -> Membership:
        """Consume an invite once and create the membership.

        Enforces expiry (via ``now``) and the org seat limit at accept time.
        """
        invite = self.store.get_invitation(invite_id)
        if invite is None:
            raise InvitationError(f"invitation {invite_id!r} not found")
        if invite.status is not InvitationStatus.pending:
            raise InvitationError(
                f"invitation {invite_id!r} is {invite.status.value}"
            )
        if invite.is_expired(now):
            raise InvitationError(f"invitation {invite_id!r} has expired")
        # add_member enforces seat limit and membership uniqueness.
        membership = self.add_member(user_id, invite.org_id, invite.role)
        consumed = invite.model_copy(
            update={
                "status": InvitationStatus.accepted,
                "accepted_by": user_id,
            }
        )
        self.store.add_invitation(consumed)
        return membership

    def revoke_invitation(self, invite_id: str) -> Invitation:
        """Revoke a pending invitation so it can never be accepted."""
        invite = self.store.get_invitation(invite_id)
        if invite is None:
            raise InvitationError(f"invitation {invite_id!r} not found")
        revoked = invite.model_copy(
            update={"status": InvitationStatus.revoked}
        )
        self.store.add_invitation(revoked)
        return revoked

    # --- API keys --------------------------------------------------------
    def issue_api_key(
        self,
        key_id: str,
        user_id: str,
        org_id: str,
        raw: str,
        *,
        salt: str,
        scopes: frozenset[str] | set[str] | None = None,
        prefix_len: int = 8,
    ) -> ApiKeyRecord:
        """Issue a key for a (user, org). Stores only the hash + a prefix.

        The user must be an active member of the org. ``raw`` and ``salt`` are
        caller-supplied so issuance is deterministic; the raw value is not kept.
        """
        user = self._require_user(user_id)
        if not user.is_active:
            raise PermissionDenied(user_id, org_id, "apikey.issue")
        if self.store.get_membership(user_id, org_id) is None:
            raise PermissionDenied(user_id, org_id, "apikey.issue")
        if self.store.get_api_key(key_id) is not None:
            raise ConflictError(f"api key id {key_id!r} already exists")
        record = ApiKeyRecord(
            id=key_id,
            org_id=org_id,
            user_id=user_id,
            prefix=raw[:prefix_len],
            hashed=hash_secret(raw, salt=salt),
            scopes=frozenset(scopes or frozenset()),
        )
        self.store.add_api_key(record)
        return record

    def authenticate(self, raw: str) -> ApiKeyRecord | None:
        """Resolve raw key material to its binding, or None (fail-closed).

        Returns None for a revoked key, a suspended user, or no match. The
        returned binding is org-scoped; a key never resolves to another org.
        """
        if not raw:
            return None
        for record in self.store.all_api_keys():
            if record.revoked:
                continue
            if not verify_secret(raw, record.hashed):
                continue
            user = self.store.get_user(record.user_id)
            if user is None or not user.is_active:
                return None
            if self.store.get_membership(record.user_id, record.org_id) is None:
                return None
            return record
        return None

    def revoke_api_key(self, key_id: str) -> ApiKeyRecord:
        """Revoke a key so it can never authenticate again."""
        record = self.store.get_api_key(key_id)
        if record is None:
            raise ConflictError(f"api key id {key_id!r} not found")
        revoked = record.model_copy(update={"revoked": True})
        self.store.add_api_key(revoked)
        return revoked

    # --- internals -------------------------------------------------------
    def _require_user(self, user_id: str) -> User:
        user = self.store.get_user(user_id)
        if user is None:
            raise ConflictError(f"user {user_id!r} not found")
        return user

    def _require_org(self, org_id: str) -> Organization:
        org = self.store.get_org(org_id)
        if org is None:
            raise ConflictError(f"org {org_id!r} not found")
        return org
