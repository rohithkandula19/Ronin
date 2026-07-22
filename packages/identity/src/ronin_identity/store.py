"""Injectable persistence interface and an in-memory implementation.

No real database or network — the store is a set of dictionaries. Callers may
substitute any object satisfying :class:`IdentityStore`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import ApiKeyRecord, Invitation, Membership, Organization, User


@runtime_checkable
class IdentityStore(Protocol):
    """Persistence surface used by :class:`~ronin_identity.service.IdentityService`."""

    def add_user(self, user: User) -> None: ...
    def get_user(self, user_id: str) -> User | None: ...
    def get_user_by_email(self, email: str) -> User | None: ...

    def add_org(self, org: Organization) -> None: ...
    def get_org(self, org_id: str) -> Organization | None: ...

    def add_membership(self, membership: Membership) -> None: ...
    def get_membership(self, user_id: str, org_id: str) -> Membership | None: ...
    def remove_membership(self, user_id: str, org_id: str) -> None: ...
    def count_members(self, org_id: str) -> int: ...

    def add_invitation(self, invite: Invitation) -> None: ...
    def get_invitation(self, invite_id: str) -> Invitation | None: ...

    def add_api_key(self, record: ApiKeyRecord) -> None: ...
    def get_api_key(self, key_id: str) -> ApiKeyRecord | None: ...
    def all_api_keys(self) -> list[ApiKeyRecord]: ...


class InMemoryStore:
    """Dictionary-backed :class:`IdentityStore`. Deterministic, no I/O."""

    def __init__(self) -> None:
        self._users: dict[str, User] = {}
        self._email_index: dict[str, str] = {}
        self._orgs: dict[str, Organization] = {}
        self._memberships: dict[tuple[str, str], Membership] = {}
        self._invites: dict[str, Invitation] = {}
        self._api_keys: dict[str, ApiKeyRecord] = {}

    # --- users -----------------------------------------------------------
    def add_user(self, user: User) -> None:
        self._users[user.id] = user
        self._email_index[user.email.lower()] = user.id

    def get_user(self, user_id: str) -> User | None:
        return self._users.get(user_id)

    def get_user_by_email(self, email: str) -> User | None:
        user_id = self._email_index.get(email.lower())
        return self._users.get(user_id) if user_id is not None else None

    # --- orgs ------------------------------------------------------------
    def add_org(self, org: Organization) -> None:
        self._orgs[org.id] = org

    def get_org(self, org_id: str) -> Organization | None:
        return self._orgs.get(org_id)

    # --- memberships -----------------------------------------------------
    def add_membership(self, membership: Membership) -> None:
        self._memberships[(membership.user_id, membership.org_id)] = membership

    def get_membership(self, user_id: str, org_id: str) -> Membership | None:
        return self._memberships.get((user_id, org_id))

    def remove_membership(self, user_id: str, org_id: str) -> None:
        self._memberships.pop((user_id, org_id), None)

    def count_members(self, org_id: str) -> int:
        return sum(1 for (_, oid) in self._memberships if oid == org_id)

    # --- invitations -----------------------------------------------------
    def add_invitation(self, invite: Invitation) -> None:
        self._invites[invite.id] = invite

    def get_invitation(self, invite_id: str) -> Invitation | None:
        return self._invites.get(invite_id)

    # --- api keys --------------------------------------------------------
    def add_api_key(self, record: ApiKeyRecord) -> None:
        self._api_keys[record.id] = record

    def get_api_key(self, key_id: str) -> ApiKeyRecord | None:
        return self._api_keys.get(key_id)

    def all_api_keys(self) -> list[ApiKeyRecord]:
        return list(self._api_keys.values())
