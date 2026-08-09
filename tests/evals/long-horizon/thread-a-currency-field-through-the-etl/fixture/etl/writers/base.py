"""Writer protocol. Writers are context managers so a run cannot leak a handle."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Mapping


class Writer(ABC):
    name = "writer"

    def __enter__(self) -> "Writer":
        self.open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def open(self) -> None:
        """Prepare the destination. Default: nothing to do."""

    def close(self) -> None:
        """Release the destination. Default: nothing to do."""

    @abstractmethod
    def write(self, record: Mapping[str, str]) -> None:
        raise NotImplementedError
