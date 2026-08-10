"""User storage."""

from __future__ import annotations

from ..domain import User
from ..errors import NotFound
from .memory_store import MemoryStore


class UsersRepo:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def next_id(self) -> str:
        return self._store.user_ids.take()

    def add(self, user: User) -> User:
        self._store.users[user.id] = user
        return user

    def get(self, user_id: str) -> User:
        try:
            return self._store.users[user_id]
        except KeyError:
            raise NotFound(f"no user with id {user_id!r}") from None

    def exists(self, user_id: str) -> bool:
        return user_id in self._store.users

    def by_email(self, email: str) -> User | None:
        for user in self._store.users.values():
            if user.email == email:
                return user
        return None

    def all(self) -> tuple[User, ...]:
        return tuple(self._store.users.values())
