"""Assert on an HTTP endpoint — `ronin api-test`.

Probe a URL and check expectations: a status code and/or JSON field values
(``data.0.id=5``). Reports each assertion pass/fail and an overall verdict, so you
can smoke-test an API from the terminal or a script (non-zero exit on failure).
The assertion logic is pure and unit-tested; the request lives in the command.
"""
from __future__ import annotations

from typing import Any


def parse_expect(pairs: list[str]) -> list[tuple[str, str]]:
    """Parse ``path=value`` assertion strings into tuples. Pure."""
    out: list[tuple[str, str]] = []
    for p in pairs or []:
        if "=" in p:
            path, _, val = p.partition("=")
            out.append((path.strip(), val.strip()))
    return out


def _coerce(actual: Any, expected: str) -> bool:
    if str(actual) == expected:
        return True
    # tolerant numeric / boolean comparison
    el = expected.lower()
    if isinstance(actual, bool):
        return el in ("true", "false") and str(actual).lower() == el
    try:
        return float(actual) == float(expected)
    except (TypeError, ValueError):
        return False


def check_response(status: int, body: Any, *, expect_status: int | None = None,
                   expect: list[tuple[str, str]] | None = None) -> dict:
    """Evaluate assertions against a response. Returns per-check results + overall
    ``passed``. Pure (no network)."""
    from .json_query import QueryError, query_json

    results: list[dict] = []
    if expect_status is not None:
        results.append({"check": f"status == {expect_status}",
                        "passed": status == expect_status, "actual": status})
    for path, expected in (expect or []):
        try:
            actual = query_json(body, path)
            ok = _coerce(actual, expected)
        except QueryError:
            actual, ok = None, False
        results.append({"check": f"{path} == {expected}", "passed": ok, "actual": actual})
    return {"results": results, "passed": all(r["passed"] for r in results) if results else True}
