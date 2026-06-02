from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from ronin_cli.config import RoninConfig
from ronin_cli.runner import AgentResultRich
from ronin_cli.server import make_app


def test_health_endpoint() -> None:
    config = RoninConfig(demo_mode=True, provider="anthropic")
    client = TestClient(make_app(config))
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["provider"] == "anthropic"
    assert body["demo_mode"] is True


def test_ask_endpoint_runs_via_run_ask() -> None:
    fake_result = AgentResultRich(
        success=True,
        output="$78/mo MRR.",
        iterations=1,
        trace=[{"kind": "final", "content": "$78/mo MRR."}],
        usage={"input_tokens": 10, "output_tokens": 5},
        demo_mode=True,
    )

    config = RoninConfig(demo_mode=True)
    client = TestClient(make_app(config))
    with patch("ronin_cli.server.run_ask", return_value=fake_result):
        response = client.post("/ask", json={"question": "what is our MRR?"})

    assert response.status_code == 200
    body = response.json()
    assert body["output"] == "$78/mo MRR."
    assert body["demo_mode"] is True
    assert body["trace"]


def test_ask_rejects_empty_question() -> None:
    config = RoninConfig(demo_mode=True)
    client = TestClient(make_app(config))
    response = client.post("/ask", json={"question": "   "})
    assert response.status_code == 400
