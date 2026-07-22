"""Tests for IdentityService: users, orgs, RBAC, invites, keys, isolation."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ronin_identity import (
    ConflictError,
    IdentityService,
    InvitationError,
    PermissionDenied,
    Role,
    SeatLimitExceeded,
    UserStatus,
    WORKSPACE_READ,
    WORKSPACE_WRITE,
)

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _raw_key() -> str:
    """Assemble raw API key material from fragments at runtime."""
    return "-".join(["rk", "live", "0000", "beef"])


def _salt() -> str:
    return "-".join(["sl", "9999", "cc11"])


def _svc_with_org() -> IdentityService:
    svc = IdentityService()
    svc.create_org("org-a", "Alpha", plan_slug="beta", seat_limit=3)
    return svc


def test_create_user_and_fetch() -> None:
    svc = IdentityService()
    user = svc.create_user("u1", "a@x.io", "Ann")
    assert svc.get_user("u1") == user
    assert user.is_active


def test_duplicate_user_id_rejected() -> None:
    svc = IdentityService()
    svc.create_user("u1", "a@x.io", "Ann")
    with pytest.raises(ConflictError):
        svc.create_user("u1", "b@x.io", "Bea")


def test_duplicate_email_rejected() -> None:
    svc = IdentityService()
    svc.create_user("u1", "a@x.io", "Ann")
    with pytest.raises(ConflictError):
        svc.create_user("u2", "A@X.IO", "Ann Two")


def test_add_member_and_can() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.member)
    assert svc.can("u1", "org-a", WORKSPACE_WRITE)
    assert svc.can("u1", "org-a", WORKSPACE_READ)


def test_non_member_denied() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    assert not svc.can("u1", "org-a", WORKSPACE_READ)


def test_suspended_user_denied_everywhere() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.owner)
    assert svc.can("u1", "org-a", WORKSPACE_WRITE)
    svc.suspend_user("u1")
    assert not svc.can("u1", "org-a", WORKSPACE_WRITE)
    svc.activate_user("u1")
    assert svc.can("u1", "org-a", WORKSPACE_WRITE)


def test_viewer_cannot_write_via_service() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.viewer)
    assert svc.can("u1", "org-a", WORKSPACE_READ)
    assert not svc.can("u1", "org-a", WORKSPACE_WRITE)


def test_require_raises_permission_denied() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.viewer)
    svc.require("u1", "org-a", WORKSPACE_READ)  # no raise
    with pytest.raises(PermissionDenied):
        svc.require("u1", "org-a", WORKSPACE_WRITE)


def test_unknown_user_and_permission_denied() -> None:
    svc = _svc_with_org()
    assert not svc.can("ghost", "org-a", WORKSPACE_READ)
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.owner)
    assert not svc.can("u1", "org-a", "not.a.real.permission")


def test_multi_org_roles_are_independent() -> None:
    svc = _svc_with_org()
    svc.create_org("org-b", "Beta", plan_slug="beta", seat_limit=3)
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.owner)
    svc.add_member("u1", "org-b", Role.viewer)
    assert svc.can("u1", "org-a", WORKSPACE_WRITE)
    assert not svc.can("u1", "org-b", WORKSPACE_WRITE)


def test_cross_org_permission_isolation() -> None:
    svc = _svc_with_org()
    svc.create_org("org-b", "Beta", plan_slug="beta", seat_limit=3)
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.owner)
    # Member of A has no standing in B.
    assert not svc.can("u1", "org-b", WORKSPACE_READ)
    assert svc.get_membership("u1", "org-b") is None


def test_seat_limit_enforced_on_add_member() -> None:
    svc = IdentityService()
    svc.create_org("org-a", "Alpha", plan_slug="beta", seat_limit=2)
    for i in range(2):
        svc.create_user(f"u{i}", f"u{i}@x.io", f"U{i}")
        svc.add_member(f"u{i}", "org-a", Role.member)
    svc.create_user("u2", "u2@x.io", "U2")
    with pytest.raises(SeatLimitExceeded):
        svc.add_member("u2", "org-a", Role.member)


def test_invitation_accept_creates_membership() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.create_invitation(
        "inv1", "org-a", "a@x.io", Role.admin,
        now=T0, expires_at=T0 + timedelta(days=7),
    )
    svc.accept_invitation("inv1", "u1", now=T0 + timedelta(days=1))
    assert svc.get_membership("u1", "org-a").role == Role.admin


def test_invitation_consumed_once() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.create_user("u2", "b@x.io", "Bea")
    svc.create_invitation(
        "inv1", "org-a", "a@x.io", Role.member,
        now=T0, expires_at=T0 + timedelta(days=7),
    )
    svc.accept_invitation("inv1", "u1", now=T0)
    with pytest.raises(InvitationError):
        svc.accept_invitation("inv1", "u2", now=T0)


def test_invitation_expiry() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.create_invitation(
        "inv1", "org-a", "a@x.io", Role.member,
        now=T0, expires_at=T0 + timedelta(hours=1),
    )
    with pytest.raises(InvitationError):
        svc.accept_invitation("inv1", "u1", now=T0 + timedelta(hours=2))


def test_invitation_revoked_cannot_be_accepted() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.create_invitation(
        "inv1", "org-a", "a@x.io", Role.member,
        now=T0, expires_at=T0 + timedelta(days=1),
    )
    svc.revoke_invitation("inv1")
    with pytest.raises(InvitationError):
        svc.accept_invitation("inv1", "u1", now=T0)


def test_invitation_accept_respects_seat_limit() -> None:
    svc = IdentityService()
    svc.create_org("org-a", "Alpha", plan_slug="beta", seat_limit=1)
    svc.create_user("u1", "a@x.io", "Ann")
    svc.create_user("u2", "b@x.io", "Bea")
    svc.add_member("u1", "org-a", Role.owner)
    svc.create_invitation(
        "inv1", "org-a", "b@x.io", Role.member,
        now=T0, expires_at=T0 + timedelta(days=1),
    )
    with pytest.raises(SeatLimitExceeded):
        svc.accept_invitation("inv1", "u2", now=T0)


def test_api_key_issue_authenticate_revoke() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.member)
    raw = _raw_key()
    rec = svc.issue_api_key(
        "k1", "u1", "org-a", raw, salt=_salt(), scopes={WORKSPACE_READ}
    )
    assert rec.prefix == raw[:8]
    assert svc.authenticate(raw).id == "k1"
    svc.revoke_api_key("k1")
    assert svc.authenticate(raw) is None


def test_api_key_not_stored_in_plaintext() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.member)
    raw = _raw_key()
    rec = svc.issue_api_key("k1", "u1", "org-a", raw, salt=_salt())
    assert raw not in rec.hashed.digest
    assert rec.hashed.digest != raw


def test_api_key_requires_membership() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    with pytest.raises(PermissionDenied):
        svc.issue_api_key("k1", "u1", "org-a", _raw_key(), salt=_salt())


def test_api_key_suspended_user_cannot_authenticate() -> None:
    svc = _svc_with_org()
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.member)
    raw = _raw_key()
    svc.issue_api_key("k1", "u1", "org-a", raw, salt=_salt())
    svc.suspend_user("u1")
    assert svc.authenticate(raw) is None


def test_api_key_cross_org_isolation() -> None:
    svc = _svc_with_org()
    svc.create_org("org-b", "Beta", plan_slug="beta", seat_limit=3)
    svc.create_user("u1", "a@x.io", "Ann")
    svc.add_member("u1", "org-a", Role.member)
    raw = _raw_key()
    rec = svc.issue_api_key("k1", "u1", "org-a", raw, salt=_salt())
    # The key resolves only to its issuing org, never to org-b.
    resolved = svc.authenticate(raw)
    assert resolved.org_id == "org-a"
    assert resolved.org_id != "org-b"


def test_authenticate_unknown_returns_none() -> None:
    svc = _svc_with_org()
    assert svc.authenticate("") is None
    assert svc.authenticate("no-such-value") is None


def test_verifier_round_trip_and_suspension() -> None:
    svc = IdentityService()
    svc.create_user("u1", "a@x.io", "Ann")
    raw = _raw_key()
    svc.set_verifier("u1", raw, salt=_salt())
    assert svc.verify_user("u1", raw)
    assert not svc.verify_user("u1", raw + "x")
    svc.suspend_user("u1")
    assert not svc.verify_user("u1", raw)


def test_verify_user_without_verifier_is_false() -> None:
    svc = IdentityService()
    svc.create_user("u1", "a@x.io", "Ann")
    assert not svc.verify_user("u1", _raw_key())
    assert svc.get_user("u1").status is UserStatus.active
