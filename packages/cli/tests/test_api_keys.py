from __future__ import annotations

import datetime as dt
import json

import pytest

from ronin_cli.api_keys import ApiKeyError, ApiKeyPolicy, ApiKeyStore


def test_api_key_is_shown_once_and_only_a_salted_digest_is_persisted(tmp_path) -> None:
    store = ApiKeyStore(tmp_path)
    issued = store.create("ci", scopes=["mission:read", "proposal:read"])

    assert issued.token.startswith("rnk_live_")
    record = store.authorize(issued.token, scope="mission:read")
    saved = json.loads((tmp_path / ".ronin" / "api-keys.json").read_text())
    assert issued.token not in json.dumps(saved)
    assert record.prefix in issued.token
    assert store.list()[0].scopes == ("mission:read", "proposal:read")


def test_api_key_scopes_revocation_and_rotation_fail_closed(tmp_path) -> None:
    store = ApiKeyStore(tmp_path)
    issued = store.create("reader", scopes=["read"])
    with pytest.raises(ApiKeyError, match="scope"):
        store.authorize(issued.token, scope="mission:run")
    store.revoke(issued.record.id)
    with pytest.raises(ApiKeyError, match="inactive"):
        store.authorize(issued.token, scope="read")
    rotated = store.rotate(issued.record.id)
    assert rotated.token != issued.token
    assert store.authorize(rotated.token, scope="read").active


def test_api_key_enforces_rate_concurrency_and_budget_limits(tmp_path) -> None:
    store = ApiKeyStore(tmp_path)
    issued = store.create(
        "worker", scopes=["worker:dispatch"],
        policy=ApiKeyPolicy(requests_per_minute=10, max_tokens=10, max_cost_usd=1.0, max_concurrency=1),
    )
    lease = store.reserve(issued.token, scope="worker:dispatch")
    with pytest.raises(ApiKeyError, match="concurrency"):
        store.reserve(issued.token, scope="worker:dispatch")
    settled = store.settle(lease, tokens=7, cost_usd=0.5)
    assert settled.usage["tokens"] == 7
    lease = store.reserve(
        issued.token, scope="worker:dispatch",
        now=dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0) + dt.timedelta(minutes=1),
    )
    with pytest.raises(ApiKeyError, match="token budget"):
        store.settle(lease, tokens=4)
    assert store.audit()[0]["action"] == "token_budget_exceeded"
