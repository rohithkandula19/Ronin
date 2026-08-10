"""The public surface callers use."""

from __future__ import annotations

from api.session import Session


class Client:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, path: str) -> str:
        return self.session.request(path)
