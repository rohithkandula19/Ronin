"""Note routes."""

from __future__ import annotations

from typing import Mapping, Sequence

from ...errors import ValidationError
from ...services import NotesService, SearchService
from ..request import Request, Response


def note_routes(notes: NotesService, search: SearchService) -> Sequence[tuple[str, str, object]]:
    def create(request: Request, params: Mapping[str, str]) -> Response:
        try:
            title = request.require("title")
        except KeyError:
            raise ValidationError("body must contain 'title'") from None
        note = notes.create(
            user_id=params["user_id"],
            title=str(title),
            body=str(request.optional("body_text", "")),
        )
        return Response(status=201, body=note.as_dict())

    def index(request: Request, params: Mapping[str, str]) -> Response:
        include_archived = request.query.get("archived") == "1"
        found = notes.list_for(params["user_id"], include_archived=include_archived)
        return Response(
            status=200,
            body={"notes": [note.as_summary() for note in found], "count": len(found)},
        )

    def show(request: Request, params: Mapping[str, str]) -> Response:
        note = notes.get(params["note_id"], params["user_id"])
        return Response(status=200, body=note.as_dict())

    def archive(request: Request, params: Mapping[str, str]) -> Response:
        note = notes.archive(params["note_id"], params["user_id"])
        return Response(status=200, body=note.as_dict())

    def query(request: Request, params: Mapping[str, str]) -> Response:
        page = int(request.query.get("page", "1"))
        hits = search.search(params["user_id"], request.query.get("q", ""), page=page)
        return Response(
            status=200,
            body={"hits": [note.as_summary() for note in hits], "count": len(hits)},
        )

    return (
        ("POST", "users/{user_id}/notes", create),
        ("GET", "users/{user_id}/notes", index),
        ("GET", "users/{user_id}/notes/{note_id}", show),
        ("POST", "users/{user_id}/notes/{note_id}/archive", archive),
        ("GET", "users/{user_id}/search", query),
    )
