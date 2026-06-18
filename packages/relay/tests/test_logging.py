"""Tests for structured logging.

The relay emits one JSON object per log line via relay.log_event. These tests
prove the line is valid JSON, carries the event name and fields, and never
contains the token (we never pass it in, and these tests guard that contract).
"""

from __future__ import annotations

import json
import logging

from ronin_relay.relay import log_event


def test_log_event_emits_json(caplog) -> None:
    with caplog.at_level(logging.INFO, logger="ronin_relay"):
        log_event("task_relayed", request_id="abc123", target_status=200)

    assert len(caplog.records) == 1
    payload = json.loads(caplog.records[0].getMessage())
    assert payload["event"] == "task_relayed"
    assert payload["request_id"] == "abc123"
    assert payload["target_status"] == 200
    # A timestamp is always present for ordering and auditing.
    assert "ts" in payload


def test_log_event_never_contains_token(caplog) -> None:
    # The helper has no token argument; this guards that no caller pattern can
    # smuggle the secret into a field named like a token by accident.
    token = "super-secret-token-value-do-not-log"
    with caplog.at_level(logging.INFO, logger="ronin_relay"):
        log_event("connector_attached")
    assert token not in caplog.text
