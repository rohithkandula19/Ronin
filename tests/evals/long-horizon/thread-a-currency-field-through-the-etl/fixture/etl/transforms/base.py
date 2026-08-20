"""Transform and filter protocols.

Transforms run before validation, so a transform must never reject a value: if it
cannot clean something it leaves it as it found it and lets the validator report
the row.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

Record = dict[str, str]


class Transform(ABC):
    name = "transform"

    @abstractmethod
    def apply(self, record: Record) -> Record:
        raise NotImplementedError


class Filter(ABC):
    name = "filter"

    @abstractmethod
    def keep(self, record: Record) -> bool:
        raise NotImplementedError
