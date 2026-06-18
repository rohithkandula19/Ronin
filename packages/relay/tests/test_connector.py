"""Tests for the connector forwarding logic, in isolation.

These prove that forward_one really calls the configured local target, that it
joins the path onto the configured base (and cannot be redirected to another
host), and that local failures are reported honestly instead of swallowed.
"""

from __future__ import annotations

import json

import pytest

from ronin_relay.connector import forward_one
from ronin_relay.protocol import ConnectorReply, RelayRequest, TaskRequest, encode

from .fakes import BrokenLocalCaller, FakeLocalCaller


def _frame(method: str = "POST", path: str = "/run", body: object = None) -> str:
    return encode(
        RelayRequest(
            id="req-1",
            request=TaskRequest(method=method, path=path, body=body),
        )
    )


@pytest.mark.asyncio
async def test_forward_one_calls_local_target(connector_config) -> None:
    caller = FakeLocalCaller()
    raw_reply = await forward_one(
        _frame(path="/run", body={"task": "ping"}), connector_config, caller
    )

    # The connector actually made one local call.
    assert len(caller.calls) == 1
    call = caller.calls[0]
    # It hit the configured target base plus the request path, nothing else.
    assert call["url"] == "http://127.0.0.1:9999/ronin/run"
    assert call["json"] == {"task": "ping"}

    reply = ConnectorReply.model_validate_json(raw_reply)
    assert reply.id == "req-1"
    assert reply.status == 200
    # The body genuinely round-tripped through the fake target.
    assert reply.body["echo"] == {"task": "ping"}


@pytest.mark.asyncio
async def test_forward_one_path_cannot_escape_configured_base(connector_config) -> None:
    # Even a path without a leading slash is appended to the base host; the
    # connector never lets the phone pick a different host.
    caller = FakeLocalCaller()
    await forward_one(_frame(path="status", body=None), connector_config, caller)
    assert caller.calls[0]["url"].startswith("http://127.0.0.1:9999/ronin")


@pytest.mark.asyncio
async def test_forward_one_reports_local_failure(connector_config) -> None:
    caller = BrokenLocalCaller()
    raw_reply = await forward_one(_frame(), connector_config, caller)
    reply = ConnectorReply.model_validate_json(raw_reply)
    assert reply.status == 0
    assert reply.error is not None
    assert "ConnectionError" in reply.error


@pytest.mark.asyncio
async def test_forward_one_rejects_wrong_kind(connector_config) -> None:
    bad = json.dumps({"kind": "not_a_request", "id": "x", "request": {}})
    with pytest.raises(ValueError):
        await forward_one(bad, connector_config, FakeLocalCaller())
