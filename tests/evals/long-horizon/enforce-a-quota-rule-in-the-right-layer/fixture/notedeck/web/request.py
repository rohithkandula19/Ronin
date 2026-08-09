"""Request and response shapes.

Framework-free: a Request is data, a Response is data, and the transport (stdin,
a socket, a test) is somebody else's problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    body: Mapping[str, Any] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)

    def require(self, key: str) -> Any:
        if key not in self.body:
            raise KeyError(key)
        return self.body[key]

    def optional(self, key: str, default: Any = None) -> Any:
        return self.body.get(key, default)


@dataclass(frozen=True)
class Response:
    status: int
    body: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "body": dict(self.body)}
