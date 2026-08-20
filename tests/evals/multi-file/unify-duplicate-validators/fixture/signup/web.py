"""Signup form handling."""

from __future__ import annotations


def _check(text: str) -> bool:
    return "@" in text


def accept_form(address: str) -> str:
    if not _check(address):
        return "rejected"
    return "accepted"
