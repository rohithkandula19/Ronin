"""Generate realistic mock data from a JSON Schema — `ronin mock`.

Feed it a JSON Schema (hand-written, or one `ronin schema` / `ronin endpoint`
inferred) and it produces representative sample records — using the *field names*
to pick believable values (``email`` → an email, ``city`` → a city, ``created_at``
→ a date). Great for fixtures, API stubs, and seeding tests. Deterministic and
pure, so it's fully unit-tested.
"""
from __future__ import annotations

from typing import Any

# field-name hint -> representative value (checked as substrings, first match wins)
_SAMPLES: list[tuple[str, Any]] = [
    ("email", "user@example.com"),
    ("first_name", "Ada"), ("last_name", "Lovelace"), ("username", "ada_l"),
    ("name", "Ada Lovelace"), ("title", "Example Title"),
    ("description", "An example description."), ("summary", "A short summary."),
    ("phone", "+1-555-0100"), ("url", "https://example.com"), ("link", "https://example.com"),
    ("image", "https://example.com/img.png"), ("avatar", "https://example.com/a.png"),
    ("city", "London"), ("country", "United Kingdom"), ("state", "CA"),
    ("address", "1 Example Street"), ("zip", "10001"), ("postcode", "SW1A 1AA"),
    ("company", "Acme Inc"), ("job", "Engineer"), ("department", "R&D"),
    ("created", "2026-01-01T00:00:00Z"), ("updated", "2026-01-02T00:00:00Z"),
    ("date", "2026-01-01"), ("time", "12:00:00"), ("timestamp", "2026-01-01T00:00:00Z"),
    ("color", "#3366ff"), ("status", "active"), ("currency", "USD"),
    ("password", "•••••••"), ("token", "tok_example"), ("slug", "example-slug"),
    ("id", "1"), ("uuid", "00000000-0000-4000-8000-000000000000"),
]


def _value_for_string(key: str) -> str:
    k = key.lower()
    for hint, val in _SAMPLES:
        if hint in k:
            return val
    return "string"


def generate_mock(schema: dict, key: str = "") -> Any:
    """Produce one representative value matching ``schema``. Deterministic. Pure."""
    if "enum" in schema and schema["enum"]:
        return schema["enum"][0]
    t = schema.get("type")
    if t == "object":
        return {k: generate_mock(v, k) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        item = schema.get("items") or {}
        return [generate_mock(item, key)] if item else []
    if t == "string":
        return _value_for_string(key)
    if t == "integer":
        k = key.lower()
        return 1 if ("id" in k or "count" in k or "age" in k) else 42
    if t == "number":
        return 3.14
    if t == "boolean":
        return True
    if t == "null":
        return None
    return None


def generate_records(schema: dict, count: int = 1) -> list:
    """Generate ``count`` mock records from a schema (objects get distinct ids/names
    where obvious). Pure."""
    out = []
    for i in range(max(1, count)):
        rec = generate_mock(schema)
        if isinstance(rec, dict):
            for k in rec:
                kl = k.lower()
                if kl in ("id", "uuid") or kl.endswith("_id"):
                    rec[k] = str(i + 1)
                elif "email" in kl:
                    rec[k] = f"user{i + 1}@example.com"
        out.append(rec)
    return out
