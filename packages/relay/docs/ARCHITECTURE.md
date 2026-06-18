# Architecture

This document describes the message protocol and the module layout. For the
security model and setup, see the top-level README.

## The path a request takes

```
  phone                relay (free VM)            connector (laptop)        Ronin gateway
    |                       |                            |                       |
    |  POST /api/task       |                            |                       |
    |  Bearer token         |                            |                       |
    |---------------------->|                            |                       |
    |                       | check token, rate limit    |                       |
    |                       | assign request id          |                       |
    |                       | RelayRequest over websocket|                       |
    |                       |--------------------------->|                       |
    |                       |                            | one local HTTP call   |
    |                       |                            |---------------------->|
    |                       |                            |   status + JSON body  |
    |                       |                            |<----------------------|
    |                       | ConnectorReply (same id)   |                       |
    |                       |<---------------------------|                       |
    |  200 status + body    |                            |                       |
    |<----------------------|                            |                       |
```

The relay never connects to the laptop. The laptop opened the websocket
outbound; the relay only sends frames back on that existing socket. Requests and
replies are correlated by a request id so concurrent phone requests do not get
crossed.

## Message protocol

The connector websocket carries plain JSON. Two message kinds exist.

### RelayRequest (relay to connector)

Sent when a phone request needs to be served.

```
{
  "kind": "relay_request",
  "id": "32-char-hex",
  "request": {
    "method": "POST",
    "path": "/run",
    "headers": {},
    "body": { "task": "status" }
  }
}
```

The `request` mirrors the phone's POST body (a TaskRequest). Note there is no
host or URL field. The connector always uses its configured `target_url` as the
host; the phone only supplies method, path, headers, and a JSON body. This is
what prevents the phone from redirecting the connector to any other address.

### ConnectorReply (connector to relay)

Sent after the connector calls the local target.

```
{
  "kind": "connector_reply",
  "id": "32-char-hex",
  "status": 200,
  "headers": {},
  "body": { "result": "ok" },
  "error": null
}
```

`status` is the HTTP status the local target returned. `body` is the target's
JSON (or its text if it was not JSON). `error` is set, with `status` 0, when the
connector could not reach the local target at all; the relay turns that into a
502 for the phone.

### Phone request and response

The phone POSTs a TaskRequest to `POST /api/task`:

```
{ "method": "POST", "path": "/run", "headers": {}, "body": { "task": "status" } }
```

On success the relay returns:

```
{ "status": 200, "body": { "result": "ok" } }
```

Error responses from the relay:

- 401 missing or wrong token
- 429 rate limited
- 503 no connector attached (laptop offline)
- 504 connector attached but did not reply in time
- 502 connector could not reach the local target

## HTTP and websocket endpoints (relay)

- `POST /api/task` phone request, token required, returns the target's reply.
  Also reachable at the alias `POST /task`.
- `GET /connect` websocket, token required (query param or auth header), the
  laptop connector attaches here. Authenticated before the socket is accepted; a
  bad token closes with code 4401. Also reachable at the alias `GET /connector`.
- `GET /healthz` no auth, returns `{ ok, connector_attached }`. Useful for a
  free uptime pinger and to confirm whether the laptop is attached.
- `GET /` no auth, returns the mobile web UI: a single self-contained HTML page
  with inline CSS and vanilla JS, no external resources. It carries no secret
  (the user enters the token in the browser) and posts to `/api/task` with the
  token as a bearer header. The response sets a strict Content-Security-Policy
  that forbids any off-box resource.

## Module layout

```
ronin_relay/
  __init__.py      package metadata and the one-paragraph summary
  config.py        config from env or explicit args; fails closed if token weak
  protocol.py      pydantic models for the wire messages, encode helper
  security.py      bearer extraction, constant-time compare, rate limiter
  webui.py         PAGE_HTML: the self-contained mobile web UI (no external deps)
  relay.py         create_app(): the relay FastAPI app, GET / UI, session map
  connector.py     forward_one() and run_connector(): the laptop side
  __main__.py      "serve" (alias "relay") and "connect" (alias "connector")

tests/
  conftest.py      offline fixtures and the test token
  fakes.py         fake local target and fake callers (records calls)
  test_security.py auth, rate limiting, config-fails-closed
  test_connector.py forward_one really calls the configured target
  test_roundtrip.py real relay app + in-process connector + fake target
  test_logging.py  log_event emits one JSON line and never logs the token
  test_webui.py    UI loads with no external resources, posts to /api/task
  test_cli.py      "connect" flags build a valid config and fail closed
```

## Why this design is testable for real

`connector.forward_one` takes the local caller as an argument, so a test can
pass a fake target that records exactly which URL was called with which body.
The round-trip test runs the real relay through FastAPI's TestClient, attaches a
real in-process websocket as the connector, and drives `forward_one` against the
fake target. The only thing that can produce the echo body the test asserts on
is the fake target reached through the connector. A relay that faked a reply
without going through the websocket, or a connector that skipped the local call,
would fail the assertions. The tests use no real network and no external
services.

## State and scaling

The relay keeps a single `ConnectorSession` in memory: the attached websocket
plus a map of in-flight request ids to futures. There is no database. When the
connector disconnects, any in-flight requests are failed immediately so phones
do not hang, and the connector reconnects with a backoff. This keeps the relay
inside the limits of a 512MB free VM and keeps the moving parts few.
