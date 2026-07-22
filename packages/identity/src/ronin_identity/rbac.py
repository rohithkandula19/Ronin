"""Fail-closed role -> permission matrix.

Permissions are namespaced strings. The matrix maps each known role to the
set of permissions it grants. Anything not explicitly granted is denied.
"""

from __future__ import annotations

from .models import Role

# Namespaced permissions recognised by the platform. A permission not in this
# set is treated as unknown and is always denied.
WORKSPACE_READ = "workspace.read"
WORKSPACE_WRITE = "workspace.write"
MEMBER_READ = "member.read"
MEMBER_INVITE = "member.invite"
BILLING_MANAGE = "billing.manage"
MODEL_RELEASE = "model.release"

KNOWN_PERMISSIONS: frozenset[str] = frozenset(
    {
        WORKSPACE_READ,
        WORKSPACE_WRITE,
        MEMBER_READ,
        MEMBER_INVITE,
        BILLING_MANAGE,
        MODEL_RELEASE,
    }
)

# Read-only permissions. Roles restricted to these can never mutate state.
READ_ONLY_PERMISSIONS: frozenset[str] = frozenset({WORKSPACE_READ, MEMBER_READ})

# Default role -> permission grants. Owner has everything; viewer is read-only.
DEFAULT_MATRIX: dict[Role, frozenset[str]] = {
    Role.owner: KNOWN_PERMISSIONS,
    Role.admin: frozenset(
        {WORKSPACE_READ, WORKSPACE_WRITE, MEMBER_READ, MEMBER_INVITE, MODEL_RELEASE}
    ),
    Role.member: frozenset({WORKSPACE_READ, WORKSPACE_WRITE, MEMBER_READ}),
    Role.viewer: READ_ONLY_PERMISSIONS,
}


class PermissionMatrix:
    """Immutable role -> permission lookup, fail-closed on unknown input."""

    def __init__(
        self,
        matrix: dict[Role, frozenset[str]] | None = None,
        *,
        known_permissions: frozenset[str] | None = None,
    ) -> None:
        self._matrix: dict[Role, frozenset[str]] = dict(
            matrix if matrix is not None else DEFAULT_MATRIX
        )
        self._known: frozenset[str] = (
            known_permissions
            if known_permissions is not None
            else KNOWN_PERMISSIONS
        )

    def is_known_permission(self, permission: str) -> bool:
        """True iff ``permission`` is a recognised, namespaced permission."""
        return permission in self._known

    def permissions_for(self, role: Role) -> frozenset[str]:
        """Permissions granted to ``role`` (empty set for an unknown role)."""
        return self._matrix.get(role, frozenset())

    def role_can(self, role: Role, permission: str) -> bool:
        """Fail-closed: deny unknown permissions and ungranted permissions."""
        if not self.is_known_permission(permission):
            return False
        return permission in self.permissions_for(role)
