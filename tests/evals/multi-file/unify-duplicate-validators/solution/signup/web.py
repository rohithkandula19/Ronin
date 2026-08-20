"""Signup form handling."""

from __future__ import annotations

from signup.validate import is_valid_address


def accept_form(address: str) -> str:
    if not is_valid_address(address):
        return "rejected"
    return "accepted"
