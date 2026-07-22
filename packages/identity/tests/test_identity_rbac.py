"""Tests for the fail-closed role -> permission matrix and hashing."""

from __future__ import annotations

import pytest

from ronin_identity import (
    BILLING_MANAGE,
    DEFAULT_MATRIX,
    KNOWN_PERMISSIONS,
    MEMBER_INVITE,
    MODEL_RELEASE,
    READ_ONLY_PERMISSIONS,
    WORKSPACE_READ,
    WORKSPACE_WRITE,
    PermissionMatrix,
    Role,
    hash_secret,
    verify_secret,
)


def _salt() -> str:
    """Assemble a salt from fragments (no credential-shaped literal)."""
    return "-".join(["sl", "0011", "aa22"])


def _raw_value() -> str:
    """Assemble raw credential material from fragments at runtime."""
    return "-".join(["rv", "live", "0000", "abcd"])


def test_owner_has_all_known_permissions() -> None:
    matrix = PermissionMatrix()
    for perm in KNOWN_PERMISSIONS:
        assert matrix.role_can(Role.owner, perm)


def test_viewer_is_read_only() -> None:
    matrix = PermissionMatrix()
    assert matrix.role_can(Role.viewer, WORKSPACE_READ)
    assert not matrix.role_can(Role.viewer, WORKSPACE_WRITE)
    assert not matrix.role_can(Role.viewer, MEMBER_INVITE)
    assert not matrix.role_can(Role.viewer, MODEL_RELEASE)
    assert not matrix.role_can(Role.viewer, BILLING_MANAGE)


def test_viewer_grants_are_subset_of_read_only() -> None:
    assert DEFAULT_MATRIX[Role.viewer] <= READ_ONLY_PERMISSIONS


def test_admin_can_invite_and_release_but_not_bill() -> None:
    matrix = PermissionMatrix()
    assert matrix.role_can(Role.admin, MEMBER_INVITE)
    assert matrix.role_can(Role.admin, MODEL_RELEASE)
    assert not matrix.role_can(Role.admin, BILLING_MANAGE)


def test_member_can_write_but_not_invite() -> None:
    matrix = PermissionMatrix()
    assert matrix.role_can(Role.member, WORKSPACE_WRITE)
    assert not matrix.role_can(Role.member, MEMBER_INVITE)


def test_unknown_permission_is_denied_for_everyone() -> None:
    matrix = PermissionMatrix()
    bogus = "workspace.detonate"
    assert not matrix.is_known_permission(bogus)
    for role in Role:
        assert not matrix.role_can(role, bogus)


def test_custom_matrix_overrides_defaults() -> None:
    matrix = PermissionMatrix({Role.viewer: frozenset({WORKSPACE_WRITE})})
    # Roles absent from a custom matrix grant nothing (fail-closed).
    assert matrix.role_can(Role.viewer, WORKSPACE_WRITE)
    assert not matrix.role_can(Role.owner, WORKSPACE_READ)


def test_hash_round_trip_verifies() -> None:
    raw = _raw_value()
    hashed = hash_secret(raw, salt=_salt())
    assert verify_secret(raw, hashed)


def test_hash_does_not_store_plaintext() -> None:
    raw = _raw_value()
    hashed = hash_secret(raw, salt=_salt())
    assert raw not in hashed.digest
    assert hashed.digest != raw


def test_wrong_value_fails_verification() -> None:
    hashed = hash_secret(_raw_value(), salt=_salt())
    assert not verify_secret(_raw_value() + "x", hashed)


def test_different_salt_changes_digest() -> None:
    raw = _raw_value()
    a = hash_secret(raw, salt=_salt())
    b = hash_secret(raw, salt=_salt() + "z")
    assert a.digest != b.digest


def test_empty_value_rejected() -> None:
    with pytest.raises(ValueError):
        hash_secret("", salt=_salt())
    assert not verify_secret("", hash_secret(_raw_value(), salt=_salt()))
