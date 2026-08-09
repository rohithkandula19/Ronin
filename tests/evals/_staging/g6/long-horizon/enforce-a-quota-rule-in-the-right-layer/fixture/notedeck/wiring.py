"""Composition root: the only place that knows how the layers are joined."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings
from .repository import MemoryStore, NotesRepo, UsersRepo
from .services import NotesService, SearchService, UsersService
from .util.clock import Clock, SystemClock
from .web import App
from .web.routes import health_routes, note_routes, user_routes


@dataclass(frozen=True)
class Services:
    store: MemoryStore
    notes_repo: NotesRepo
    users_repo: UsersRepo
    notes: NotesService
    users: UsersService
    search: SearchService
    settings: Settings


def build_services(settings: Settings | None = None, clock: Clock | None = None) -> Services:
    settings = settings or Settings.load()
    clock = clock or SystemClock()
    store = MemoryStore()
    notes_repo = NotesRepo(store)
    users_repo = UsersRepo(store)
    return Services(
        store=store,
        notes_repo=notes_repo,
        users_repo=users_repo,
        notes=NotesService(notes_repo, users_repo, settings, clock),
        users=UsersService(users_repo, settings),
        search=SearchService(notes_repo, settings),
        settings=settings,
    )


def build_app(services: Services) -> App:
    app = App()
    app.include(health_routes())
    app.include(user_routes(services.users, services.notes))
    app.include(note_routes(services.notes, services.search))
    return app
