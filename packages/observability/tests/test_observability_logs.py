"""Redaction is the contract of the logging module: secrets and PII must
never reach the sink. Secret-shaped values are built at runtime so no
credential-looking literal lives in this file."""

from __future__ import annotations

from ronin_observability import (
    REDACTED,
    InMemoryLogSink,
    LogLevel,
    Redactor,
    StructuredLogger,
)

NOW = "2026-07-22T00:00:00Z"


def _fake_token() -> str:
    # 40-char high-entropy string, assembled so no token literal appears here.
    return "".join(chr(ord("a") + (i * 7) % 26) for i in range(40))


def _fake_email() -> str:
    return "jane" + "." + "doe" + "@" + "example" + "." + "com"


def _logger() -> tuple[StructuredLogger, InMemoryLogSink]:
    sink = InMemoryLogSink()
    return StructuredLogger(sink), sink


def test_basic_record_shape():
    log, sink = _logger()
    rec = log.info("started", now=NOW, trace_id="t1", span_id="s1", region="us")
    assert rec is not None
    assert len(sink) == 1
    assert rec.level is LogLevel.info
    assert rec.trace_id == "t1"
    assert rec.span_id == "s1"
    assert rec.ts == NOW
    assert rec.fields["region"] == "us"


def test_sensitive_key_value_never_appears():
    log, sink = _logger()
    secret = _fake_token()
    log.info("auth attempt", now=NOW, password=secret, user="rohith")
    rec = sink.records[0]
    assert rec.fields["password"] == REDACTED
    # The secret must not appear anywhere in the serialized record.
    assert secret not in rec.model_dump_json()
    assert rec.fields["user"] == "rohith"


def test_token_shaped_value_scrubbed_even_under_innocent_key():
    log, sink = _logger()
    token = _fake_token()
    log.info("callback", now=NOW, note=f"value is {token} ok")
    rec = sink.records[0]
    assert token not in rec.fields["note"]
    assert REDACTED in rec.fields["note"]


def test_email_scrubbed_in_message():
    log, sink = _logger()
    email = _fake_email()
    log.warning(f"login for {email} failed", now=NOW)
    rec = sink.records[0]
    assert email not in rec.message
    assert REDACTED in rec.message


def test_various_sensitive_keys_redacted():
    log, sink = _logger()
    log.info(
        "mixed",
        now=NOW,
        api_key="anything",
        authorization="anything",
        session_id="anything",
        patient_name="anything",
        keep="visible",
    )
    f = sink.records[0].fields
    assert f["api_key"] == REDACTED
    assert f["authorization"] == REDACTED
    assert f["session_id"] == REDACTED
    assert f["patient_name"] == REDACTED
    assert f["keep"] == "visible"


def test_nested_dict_fields_scrubbed():
    log, sink = _logger()
    secret = _fake_token()
    log.info("nested", now=NOW, ctx={"token": secret, "ok": "fine"})
    f = sink.records[0].fields
    assert f["ctx"]["token"] == REDACTED
    assert f["ctx"]["ok"] == "fine"
    assert secret not in sink.records[0].model_dump_json()


def test_list_of_values_scrubbed():
    log, sink = _logger()
    email = _fake_email()
    log.info("list", now=NOW, items=[f"contact {email}", "plain"])
    items = sink.records[0].fields["items"]
    assert email not in items[0]
    assert items[1] == "plain"


def test_ssn_shape_scrubbed():
    log, sink = _logger()
    ssn = "123" + "-" + "45" + "-" + "6789"
    log.info("record", now=NOW, note=f"id {ssn}")
    assert ssn not in sink.records[0].fields["note"]


def test_bearer_prefix_scrubbed():
    log, sink = _logger()
    token = _fake_token()
    log.info("hdr", now=NOW, note=f"Bearer {token}")
    note = sink.records[0].fields["note"]
    assert token not in note
    assert REDACTED in note


def test_min_level_filters_out_lower_records():
    sink = InMemoryLogSink()
    log = StructuredLogger(sink, min_level=LogLevel.error)
    assert log.info("skipped", now=NOW) is None
    assert log.error("kept", now=NOW) is not None
    assert len(sink) == 1


def test_trace_and_span_ids_are_not_scrubbed():
    # Correlation ids can be long/high-entropy but are identifiers, not secrets.
    log, sink = _logger()
    tid = _fake_token()
    rec = log.info("ok", now=NOW, trace_id=tid)
    assert rec is not None
    assert rec.trace_id == tid  # preserved verbatim


def test_redactor_is_injectable_and_standalone():
    r = Redactor()
    assert r.is_sensitive_key("X-Secret-Header")
    assert r.scrub_text(_fake_email()) == REDACTED
    assert r.scrub_value("plain", "hello world") == "hello world"
