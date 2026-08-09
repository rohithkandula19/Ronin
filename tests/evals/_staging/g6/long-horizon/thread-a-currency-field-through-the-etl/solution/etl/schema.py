"""The canonical order record.

Every other layer reads this declaration: the validator picks a checker per
``kind``, the jsonl reader projects onto :data:`FIELD_NAMES`, and the summary
writer groups by declared fields. Readers of *foreign* formats still own their own
column mapping, because source column names are not ours to choose.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import SchemaError


@dataclass(frozen=True)
class Field:
    """One column of the canonical record.

    ``allowlist`` names a list in ``config/pipeline.json`` under ``allowlists``;
    when set, the validator rejects any value outside it.
    """

    name: str
    kind: str
    required: bool = True
    allowlist: str | None = None


ORDER_FIELDS: tuple[Field, ...] = (
    Field("order_id", "str"),
    Field("customer_id", "str"),
    Field("channel", "str", allowlist="channels"),
    Field("amount", "decimal2"),
    Field("currency", "str", allowlist="currencies"),
    Field("quantity", "int"),
    Field("region", "str", required=False, allowlist="regions"),
)

FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in ORDER_FIELDS)


def field(name: str) -> Field:
    """Return the declared field called ``name``."""
    for candidate in ORDER_FIELDS:
        if candidate.name == name:
            return candidate
    raise SchemaError(f"no field named {name!r}; the record declares {list(FIELD_NAMES)}")


def required_names() -> tuple[str, ...]:
    """Names of the fields a row cannot omit."""
    return tuple(f.name for f in ORDER_FIELDS if f.required)
