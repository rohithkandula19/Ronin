"""JWT decoder — `ronin jwt`.

`ronin jwt <token>` decodes a JSON Web Token's header and payload **without
verifying the signature** (offline, no network, no secret needed). Useful for
inspecting `exp`/`iat`/`alg` while debugging auth. Pure stdlib (base64 + json).

Decoding is NOT verification — never trust an unverified token for authz.
"""
from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime, timezone

# Claims whose values are unix timestamps, rendered human-readable alongside.
_TIME_CLAIMS = ("exp", "iat", "nbf", "auth_time", "updated_at")


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment, restoring stripped `=` padding."""
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_jwt(token: str) -> dict:
    """Decode a JWT's header + payload without verifying its signature. Pure.

    Returns a dict with ``header``, ``payload``, ``signature`` (raw b64url string),
    and ``times`` (a map of timestamp-claims → ISO-8601 UTC). On malformed input
    returns ``{"error": ...}``.
    """
    parts = token.strip().split(".")
    if len(parts) != 3:
        return {"error": f"not a JWT: expected 3 dot-separated parts, got {len(parts)}"}
    header_b64, payload_b64, signature_b64 = parts
    try:
        header = json.loads(_b64url_decode(header_b64))
        payload = json.loads(_b64url_decode(payload_b64))
    except (binascii.Error, ValueError) as e:
        return {"error": f"could not decode JWT: {e}"}

    times = {}
    for claim in _TIME_CLAIMS:
        value = payload.get(claim)
        if isinstance(value, (int, float)):
            try:
                times[claim] = datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                pass

    return {
        "header": header,
        "payload": payload,
        "signature": signature_b64,
        "alg": header.get("alg"),
        "times": times,
    }
