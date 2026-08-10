"""User registration.

Addresses are normalised on the way in (trimmed and lower-cased) so that the
uniqueness check downstream compares like with like.
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


def register(store, email, name):
    """Store a new user and return the stored record."""
    user = {"email": email.strip().lower(), "name": name.strip()}
    return store.add(user)
