"""Domain models: users, organizations, memberships, invitations, API keys."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

from .hashing import SecretHash


class UserStatus(str, Enum):
    """Lifecycle status of a user account."""

    active = "active"
    suspended = "suspended"


class Role(str, Enum):
    """Membership role within an organization (extensible)."""

    owner = "owner"
    admin = "admin"
    member = "member"
    viewer = "viewer"


class InvitationStatus(str, Enum):
    """Lifecycle status of an org invitation."""

    pending = "pending"
    accepted = "accepted"
    revoked = "revoked"


class User(BaseModel):
    """A person. Never holds a plaintext credential — only a salted hash."""

    model_config = ConfigDict(extra="forbid")

    id: str
    email: str
    display_name: str
    status: UserStatus = UserStatus.active
    verifier: SecretHash | None = None

    @property
    def is_active(self) -> bool:
        """True iff the account is active (not suspended)."""
        return self.status is UserStatus.active


class Organization(BaseModel):
    """A tenant. Members are scoped to exactly one organization each."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    plan_slug: str
    seat_limit: int = Field(ge=0)


class Membership(BaseModel):
    """Binding of a user to an org with a single role."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    org_id: str
    role: Role


class Invitation(BaseModel):
    """An org-scoped invite for an email to join with a given role."""

    model_config = ConfigDict(extra="forbid")

    id: str
    org_id: str
    email: str
    role: Role
    created_at: datetime
    expires_at: datetime
    status: InvitationStatus = InvitationStatus.pending
    accepted_by: str | None = None

    def is_expired(self, now: datetime) -> bool:
        """True iff the invite is past its expiry at ``now``."""
        return now >= self.expires_at


class ApiKeyRecord(BaseModel):
    """A stored API key binding: only the hash + a display prefix are kept."""

    model_config = ConfigDict(extra="forbid")

    id: str
    org_id: str
    user_id: str
    prefix: str
    hashed: SecretHash
    scopes: frozenset[str] = frozenset()
    revoked: bool = False
