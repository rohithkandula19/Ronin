"""User routes."""

from __future__ import annotations

from typing import Mapping, Sequence

from ...errors import ValidationError
from ...services import NotesService, UsersService
from ..request import Request, Response


def user_routes(users: UsersService, notes: NotesService) -> Sequence[tuple[str, str, object]]:
    def create(request: Request, params: Mapping[str, str]) -> Response:
        try:
            email = request.require("email")
        except KeyError:
            raise ValidationError("body must contain 'email'") from None
        user = users.create(
            email=str(email),
            display_name=str(request.optional("display_name", "")),
            plan=str(request.optional("plan", "free")),
        )
        return Response(status=201, body=user.as_dict())

    def show(request: Request, params: Mapping[str, str]) -> Response:
        user = users.get(params["user_id"])
        body = dict(user.as_dict())
        body["active_notes"] = notes.active_count(user.id)
        return Response(status=200, body=body)

    return (
        ("POST", "users", create),
        ("GET", "users/{user_id}", show),
    )
