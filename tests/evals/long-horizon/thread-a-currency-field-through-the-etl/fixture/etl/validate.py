"""Row validation.

Reports problems as values (:class:`RowError`) instead of raising, so a single bad
row never aborts a nightly run. Checks are driven by ``etl.schema``: the ``kind``
picks a checker, and ``allowlist`` names a list in the config.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .config import Config
from .errors import SchemaError
from .schema import ORDER_FIELDS, Field
from .util.money import AMOUNT_RE


@dataclass(frozen=True)
class RowError:
    row_number: int
    field: str
    message: str

    def render(self) -> str:
        return f"row {self.row_number}: {self.field}: {self.message}"


def _check_str(value: str) -> str | None:
    if len(value) > 64:
        return f"value is longer than 64 characters ({len(value)})"
    if any(ch < " " for ch in value):
        return "value contains control characters"
    return None


def _check_int(value: str) -> str | None:
    try:
        parsed = int(value)
    except ValueError:
        return f"{value!r} is not an integer"
    if parsed <= 0:
        return f"{parsed} is not positive"
    return None


def _check_decimal2(value: str) -> str | None:
    if not AMOUNT_RE.match(value):
        return f"{value!r} is not an amount with exactly two decimal places"
    return None


_CHECKERS: Mapping[str, Callable[[str], str | None]] = {
    "str": _check_str,
    "int": _check_int,
    "decimal2": _check_decimal2,
}


class Validator:
    def __init__(self, config: Config, fields: tuple[Field, ...] = ORDER_FIELDS) -> None:
        self.config = config
        self.fields = fields
        self._allowlists: dict[str, tuple[str, ...]] = {}

    def _allowlist(self, name: str) -> tuple[str, ...]:
        if name not in self._allowlists:
            self._allowlists[name] = self.config.allowlist(name)
        return self._allowlists[name]

    def check(self, row_number: int, record: Mapping[str, str]) -> list[RowError]:
        errors: list[RowError] = []
        for field in self.fields:
            value = record.get(field.name, "")
            if not value:
                if field.required:
                    errors.append(
                        RowError(row_number, field.name, "required value is missing")
                    )
                continue
            checker = _CHECKERS.get(field.kind)
            if checker is None:
                raise SchemaError(
                    f"field {field.name!r} declares kind {field.kind!r}, which no "
                    f"checker handles; known kinds are {sorted(_CHECKERS)}"
                )
            problem = checker(value)
            if problem is not None:
                errors.append(RowError(row_number, field.name, problem))
                continue
            if field.allowlist is not None:
                allowed = self._allowlist(field.allowlist)
                if value not in allowed:
                    errors.append(
                        RowError(
                            row_number,
                            field.name,
                            f"{value!r} is not one of {', '.join(allowed)}",
                        )
                    )
        return errors
