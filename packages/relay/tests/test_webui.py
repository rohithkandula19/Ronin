"""Tests for the mobile web UI served at "/".

These prove two things the doctrine asks for, fully offline:

  1. The page loads from the real relay app with NO external resources. There is
     no <script src>, no <link href> to a CDN, no remote font, no remote image.
     Everything (markup, CSS, JS) is inline, so a phone browser fetches the page
     in one request and nothing leaks to a third party.

  2. The page posts to /api/task and sends the token as a bearer header. We
     assert this by reading the page source (the fetch target and the bearer
     header are literally in the JS) and by driving the same flow the page would:
     a POST to /api/task with Authorization: Bearer <token> reaches the relay's
     task handler (here it returns 503 because no connector is attached, which is
     the authenticated path, not the 401 unauthenticated path).

No browser engine is needed: the page is plain HTML with inline vanilla JS, so
we verify its contract by inspecting the served bytes and exercising the
endpoint it targets.
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from ronin_relay.relay import create_app

from .conftest import TEST_TOKEN


def test_index_serves_html(relay_config) -> None:
    app = create_app(relay_config)
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    assert "<!DOCTYPE html>" in body
    assert "<title>Ronin Relay</title>" in body


def test_index_has_no_external_resources(relay_config) -> None:
    app = create_app(relay_config)
    client = TestClient(app)
    body = client.get("/").text.lower()

    # No remote protocols anywhere in the page. If any of these appear the page
    # is pulling a resource from off-box, which this test forbids.
    for needle in ("http://", "https://", "//cdn", "src=\"http", "@import"):
        assert needle not in body, f"page must not reference {needle!r}"

    # No external scripts or stylesheets: every <script> must be inline (no src)
    # and there must be no <link> tags (which would be fonts or stylesheets).
    for script in re.findall(r"<script[^>]*>", body):
        assert "src=" not in script, "scripts must be inline, no external src"
    assert "<link" not in body, "page must not <link> any external resource"


def test_index_posts_to_api_task_with_bearer_token(relay_config) -> None:
    app = create_app(relay_config)
    client = TestClient(app)
    body = client.get("/").text

    # The page's JS targets the relay's own /api/task and sends a bearer token.
    assert '"/api/task"' in body
    assert "Bearer " in body
    assert "Authorization" in body


def test_index_token_is_not_baked_into_page(relay_config) -> None:
    # The served HTML must not contain the real token. The page asks for it in
    # the browser and stores it on the device, so it is safe to serve to anyone.
    app = create_app(relay_config)
    client = TestClient(app)
    assert TEST_TOKEN not in client.get("/").text


def test_index_sets_restrictive_csp(relay_config) -> None:
    # A content security policy that forbids any remote origin both documents and
    # enforces "no external resources" at the browser level.
    app = create_app(relay_config)
    client = TestClient(app)
    csp = client.get("/").headers.get("content-security-policy", "")
    assert "default-src 'none'" in csp
    assert "connect-src 'self'" in csp


def test_api_task_path_the_page_uses_is_authenticated(relay_config) -> None:
    # The page posts to /api/task with a bearer token. With a GOOD token and no
    # connector attached we get 503 (the authenticated path), proving the token
    # is what gates the endpoint, not merely the presence of the page.
    app = create_app(relay_config)
    client = TestClient(app)

    # Good token: passes auth, then 503 because no laptop connector is attached.
    ok = client.post(
        "/api/task",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        json={"method": "POST", "path": "/run", "body": {"task": "status"}},
    )
    assert ok.status_code == 503

    # No token (what an attacker who only loaded the page has): 401.
    no_auth = client.post(
        "/api/task",
        json={"method": "POST", "path": "/run", "body": {"task": "status"}},
    )
    assert no_auth.status_code == 401
