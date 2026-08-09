"""User rules."""

from __future__ import annotations

from ..config import Settings
from ..domain import User
from ..errors import Conflict, NotFound, ValidationError
from ..repository import UsersRepo
from ..util.text import tidy

PLANS = ("free", "pro")


class UsersService:
    def __init__(self, users: UsersRepo, settings: Settings) -> None:
        self._users = users
        self._settings = settings

    def create(self, email: str, display_name: str, plan: str = "free") -> User:
        clean_email = tidy(email).lower()
        if "@" not in clean_email:
            raise ValidationError(f"{email!r} is not an email address")
        if plan not in PLANS:
            raise ValidationError(f"plan must be one of {', '.join(PLANS)}, got {plan!r}")
        if self._users.by_email(clean_email) is not None:
            raise Conflict(f"a user with email {clean_email!r} already exists")
        user = User(
            id=self._users.next_id(),
            email=clean_email,
            display_name=tidy(display_name) or clean_email,
            plan=plan,
        )
        return self._users.add(user)

    def get(self, user_id: str) -> User:
        return self._users.get(user_id)

    def require(self, user_id: str) -> User:
        if not self._users.exists(user_id):
            raise NotFound(f"no user with id {user_id!r}")
        return self._users.get(user_id)
