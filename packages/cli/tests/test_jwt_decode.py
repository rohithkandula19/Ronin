"""Tests for the JWT decoder."""
from __future__ import annotations

import base64
import json

from ronin_cli.jwt_decode import decode_jwt


def _b64url(obj: dict) -> str:
    raw = json.dumps(obj).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _make_token(header: dict, payload: dict, sig: str = "sig") -> str:
    return f"{_b64url(header)}.{_b64url(payload)}.{sig}"


def test_decodes_header_and_payload() -> None:
    token = _make_token({"alg": "HS256", "typ": "JWT"}, {"sub": "123", "name": "ro"})
    out = decode_jwt(token)
    assert out["header"]["alg"] == "HS256"
    assert out["payload"]["sub"] == "123"
    assert out["alg"] == "HS256"


def test_timestamp_claims_rendered() -> None:
    # 2021-01-01T00:00:00Z = 1609459200
    out = decode_jwt(_make_token({"alg": "none"}, {"exp": 1609459200}))
    assert out["times"]["exp"].startswith("2021-01-01T00:00:00")


def test_handles_missing_padding() -> None:
    # payload whose base64 length is not a multiple of 4 must still decode
    out = decode_jwt(_make_token({"alg": "HS256"}, {"a": "b"}))
    assert "error" not in out


def test_wrong_part_count() -> None:
    assert "error" in decode_jwt("only.two")


def test_garbage_segment() -> None:
    assert "error" in decode_jwt("!!!.@@@.###")
