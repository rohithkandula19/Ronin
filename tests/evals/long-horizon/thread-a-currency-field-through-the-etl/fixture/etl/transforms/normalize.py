"""Whitespace, case and money normalization.

Case is per field and deliberately explicit: the shop exports are inconsistent,
and the allowlists in the config are written in one case only.
"""

from __future__ import annotations

from ..util.money import parse_amount
from ..util.text import squeeze
from .base import Record, Transform


class Normalize(Transform):
    name = "normalize"

    #: Fields whose allowlists are written in lower case.
    LOWER_FIELDS: tuple[str, ...] = ("channel", "region")

    #: Fields whose allowlists are written in upper case.
    UPPER_FIELDS: tuple[str, ...] = ()

    #: Fields held as two-decimal strings.
    AMOUNT_FIELDS: tuple[str, ...] = ("amount",)

    def apply(self, record: Record) -> Record:
        out: Record = {key: squeeze(value) for key, value in record.items()}
        for name in self.LOWER_FIELDS:
            if name in out:
                out[name] = out[name].lower()
        for name in self.UPPER_FIELDS:
            if name in out:
                out[name] = out[name].upper()
        for name in self.AMOUNT_FIELDS:
            if out.get(name):
                out[name] = parse_amount(out[name])
        return out
