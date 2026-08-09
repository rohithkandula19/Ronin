"""The unit of work: a named chunk of bytes, decoded lazily."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property


@dataclass(frozen=True)
class Blob:
    """Bytes plus the name they came from.

    Plugins receive a Blob rather than a path so that a plugin is trivially
    testable and never touches the filesystem itself.
    """

    name: str
    data: bytes

    @cached_property
    def text(self) -> str:
        """Best-effort decode. Undecodable bytes become U+FFFD, never an error."""
        return self.data.decode("utf-8", errors="replace")

    @cached_property
    def lines(self) -> tuple[str, ...]:
        return tuple(self.text.splitlines())

    @property
    def size(self) -> int:
        return len(self.data)
