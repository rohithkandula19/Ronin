"""UUID generation & inspection — `ronin uuid`.

`ronin uuid` makes UUIDs (v4 random by default; v1 time-based; v5 name-based) and
`ronin uuid --inspect <value>` reports a UUID's version, variant and (for v1) its
embedded timestamp. Pure stdlib (``uuid``), offline.
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime, timedelta, timezone

# UUID v1 counts 100-ns intervals since the Gregorian epoch (1582-10-15).
_GREGORIAN_EPOCH = datetime(1582, 10, 15, tzinfo=timezone.utc)

_NAMESPACES = {
    "dns": _uuid.NAMESPACE_DNS,
    "url": _uuid.NAMESPACE_URL,
    "oid": _uuid.NAMESPACE_OID,
    "x500": _uuid.NAMESPACE_X500,
}


def generate(version: int = 4, name: str | None = None, namespace: str = "dns") -> dict:
    """Generate a UUID of the given version. Pure.

    v4 → random; v1 → time/host based; v5 → SHA-1 of (namespace, name). v3/v5
    require ``name``. Returns ``{"uuid": ...}`` or ``{"error": ...}``.
    """
    if version == 4:
        return {"uuid": str(_uuid.uuid4())}
    if version == 1:
        return {"uuid": str(_uuid.uuid1())}
    if version in (3, 5):
        if not name:
            return {"error": f"uuid v{version} requires a --name"}
        ns = _NAMESPACES.get(namespace.lower())
        if ns is None:
            return {"error": f"unknown namespace {namespace!r}; choose from {', '.join(_NAMESPACES)}"}
        factory = _uuid.uuid3 if version == 3 else _uuid.uuid5
        return {"uuid": str(factory(ns, name))}
    return {"error": f"unsupported UUID version {version}; choose 1, 3, 4 or 5"}


def inspect(value: str) -> dict:
    """Report version/variant (and v1 timestamp) for a UUID string. Pure."""
    try:
        u = _uuid.UUID(value.strip())
    except ValueError as e:
        return {"error": str(e)}
    info = {
        "uuid": str(u),
        "version": u.version,
        "variant": u.variant,
        "hex": u.hex,
        "int": u.int,
        "is_nil": u.int == 0,
    }
    if u.version == 1:
        ts = _GREGORIAN_EPOCH + timedelta(microseconds=u.time // 10)
        info["timestamp"] = ts.isoformat()
    return info
