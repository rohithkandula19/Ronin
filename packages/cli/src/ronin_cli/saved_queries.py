"""Saved queries — give a question a short name, recall it later.

Storage: ``.csk/queries.toml`` (project-local). Plaintext, safe to commit if you're
not embedding secrets in the query text.
"""
from __future__ import annotations

from pathlib import Path

import tomli_w
import tomllib
from pydantic import BaseModel, Field


QUERIES_FILENAME = "queries.toml"


class SavedQuery(BaseModel):
    name: str
    query: str
    description: str = ""


class QueryStore(BaseModel):
    queries: dict[str, SavedQuery] = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "QueryStore":
        if not path.exists():
            return cls()
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        queries = {
            name: SavedQuery(name=name, **payload)
            for name, payload in raw.get("queries", {}).items()
        }
        return cls(queries=queries)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "queries": {
                name: {"query": q.query, "description": q.description}
                for name, q in self.queries.items()
            }
        }
        with path.open("wb") as fh:
            tomli_w.dump(payload, fh)

    def add(self, name: str, query: str, description: str = "") -> None:
        if not name or not name.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"invalid name {name!r} — use letters, digits, '-', '_' only")
        self.queries[name] = SavedQuery(name=name, query=query, description=description)

    def get(self, name: str) -> SavedQuery:
        if name not in self.queries:
            raise KeyError(f"no saved query named {name!r}")
        return self.queries[name]

    def remove(self, name: str) -> bool:
        return self.queries.pop(name, None) is not None

    def list_all(self) -> list[SavedQuery]:
        return sorted(self.queries.values(), key=lambda q: q.name)


def default_path() -> Path:
    return Path(".csk") / QUERIES_FILENAME
