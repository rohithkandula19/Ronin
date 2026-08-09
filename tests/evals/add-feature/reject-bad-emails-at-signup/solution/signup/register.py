"""User registration.

Addresses are normalised on the way in (trimmed and lower-cased) so that the
uniqueness check downstream compares like with like, and rejected before that
so the error message quotes what the caller actually sent.
"""

from __future__ import annotations


class Store:
    """The in-memory stand-in for the users table."""

    def __init__(self):
        self._users = []

    def add(self, user):
        self._users.append(user)
        return user

    def all(self):
        return list(self._users)

    def __len__(self):
        return len(self._users)


def _valid_email(candidate):
    """True when *candidate* is one "@" with something either side of it."""
    local, _, domain = candidate.partition("@")
    return bool(local) and bool(domain) and "@" not in domain


def register(store, email, name):
    """Store a new user and return the stored record.

    The address is validated before normalisation so the rejection message
    quotes the value the caller passed rather than a cleaned-up version of it.
    """
    if not _valid_email(email.strip()):
        raise ValueError(f"invalid email address: {email}")
    user = {"email": email.strip().lower(), "name": name.strip()}
    return store.add(user)
