"""Explain HTTP status codes — `ronin http`.

`ronin http 429` → "Too Many Requests — the client has sent too many requests in
a given time (rate limited)." A curated, offline reference for the codes you
actually hit. Pure and unit-tested.
"""
from __future__ import annotations

_CODES: dict[int, tuple[str, str]] = {
    100: ("Continue", "the server received the request headers; send the body"),
    101: ("Switching Protocols", "the server is switching protocols as requested"),
    200: ("OK", "the request succeeded"),
    201: ("Created", "the request succeeded and a new resource was created"),
    202: ("Accepted", "the request was accepted for processing but isn't complete"),
    204: ("No Content", "success, but there's no body to return"),
    206: ("Partial Content", "the server is delivering part of the resource (range request)"),
    301: ("Moved Permanently", "the resource has a new permanent URL"),
    302: ("Found", "the resource is temporarily at a different URL"),
    304: ("Not Modified", "the cached version is still valid; nothing changed"),
    307: ("Temporary Redirect", "repeat the request at another URL, keeping the method"),
    308: ("Permanent Redirect", "the resource permanently moved; keep the method"),
    400: ("Bad Request", "the server couldn't understand the request (malformed)"),
    401: ("Unauthorized", "authentication is required or failed"),
    402: ("Payment Required", "reserved for future payment-gated use"),
    403: ("Forbidden", "authenticated, but not allowed to access this"),
    404: ("Not Found", "the resource doesn't exist"),
    405: ("Method Not Allowed", "the HTTP method isn't supported for this resource"),
    406: ("Not Acceptable", "the resource can't be served in a form the client accepts"),
    408: ("Request Timeout", "the server timed out waiting for the request"),
    409: ("Conflict", "the request conflicts with the current state (e.g. edit clash)"),
    410: ("Gone", "the resource used to exist but is permanently removed"),
    413: ("Payload Too Large", "the request body is bigger than the server allows"),
    415: ("Unsupported Media Type", "the body's content-type isn't supported"),
    418: ("I'm a teapot", "an April Fools' joke code; the server refuses to brew coffee"),
    422: ("Unprocessable Entity", "the request was well-formed but semantically invalid"),
    429: ("Too Many Requests", "the client has sent too many requests (rate limited)"),
    500: ("Internal Server Error", "the server hit an unexpected condition"),
    501: ("Not Implemented", "the server doesn't support the functionality required"),
    502: ("Bad Gateway", "an upstream server returned an invalid response"),
    503: ("Service Unavailable", "the server is overloaded or down for maintenance"),
    504: ("Gateway Timeout", "an upstream server didn't respond in time"),
}


def explain_status(code: int) -> dict | None:
    """Name + explanation for a status code, or None if unknown. Pure."""
    entry = _CODES.get(int(code))
    if entry is None:
        return None
    name, desc = entry
    klass = {1: "informational", 2: "success", 3: "redirect",
             4: "client error", 5: "server error"}.get(int(code) // 100, "unknown")
    return {"code": int(code), "name": name, "category": klass, "explanation": desc}


def known_codes() -> list[int]:
    """All documented codes. Pure."""
    return sorted(_CODES)
