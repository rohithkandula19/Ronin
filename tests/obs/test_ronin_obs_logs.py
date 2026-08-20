"""Structured logs: valid JSON, the session id on every line, filtering, verbose.

Everything runs against a list-sink and a scripted ``clock``, so a line is asserted
byte for byte rather than matched against a wildcard timestamp.
"""

from __future__ import annotations

import json

import pytest

from ronin.obs import LogLevel, StructuredLogger, write_stderr


def _logger(
    lines: list[str],
    *,
    level: LogLevel = LogLevel.INFO,
    verbose: bool = False,
) -> StructuredLogger:
    return StructuredLogger(
        session_id="sess-1",
        sink=lines.append,
        level=level,
        verbose=verbose,
        clock=lambda: 1234.5,
    )


def test_each_line_is_valid_json_with_the_core_fields() -> None:
    lines: list[str] = []
    _logger(lines).info("turn_started", turn=0)
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "ts": 1234.5,
        "level": "info",
        "session_id": "sess-1",
        "event": "turn_started",
        "turn": 0,
    }


def test_the_correlation_fields_lead_every_line() -> None:
    lines: list[str] = []
    _logger(lines).info("evt", extra="z")
    assert list(json.loads(lines[0]).keys())[:4] == ["ts", "level", "session_id", "event"]


def test_the_session_id_is_on_every_line() -> None:
    lines: list[str] = []
    logger = _logger(lines)
    logger.info("a")
    logger.warn("b")
    logger.error("c")
    assert lines
    for line in lines:
        assert json.loads(line)["session_id"] == "sess-1"


def test_levels_filter_below_the_threshold() -> None:
    lines: list[str] = []
    logger = _logger(lines, level=LogLevel.WARN)
    assert logger.debug("d") is None
    assert logger.info("i") is None
    assert logger.warn("w") is not None
    assert logger.error("e") is not None
    assert [json.loads(line)["event"] for line in lines] == ["w", "e"]


def test_the_level_name_is_the_wire_value() -> None:
    lines: list[str] = []
    logger = _logger(lines, level=LogLevel.DEBUG)
    logger.debug("d")
    logger.info("i")
    logger.warn("w")
    logger.error("e")
    assert [json.loads(line)["level"] for line in lines] == ["debug", "info", "warn", "error"]


def test_verbose_lowers_the_threshold_to_debug() -> None:
    lines: list[str] = []
    # level is INFO, but verbose overrides it down to DEBUG.
    logger = _logger(lines, level=LogLevel.INFO, verbose=True)
    assert logger.debug("trace") is not None
    assert json.loads(lines[0])["event"] == "trace"


def test_verbose_adds_detail_that_is_hidden_otherwise() -> None:
    quiet_lines: list[str] = []
    quiet = _logger(quiet_lines)
    quiet_record = quiet.emit(LogLevel.INFO, "x", {"a": 1}, detail={"b": 2})
    assert quiet_record is not None
    assert quiet_record["a"] == 1
    assert "b" not in quiet_record

    loud_lines: list[str] = []
    loud = _logger(loud_lines, verbose=True)
    loud_record = loud.emit(LogLevel.INFO, "x", {"a": 1}, detail={"b": 2})
    assert loud_record is not None
    assert loud_record["a"] == 1
    assert loud_record["b"] == 2


def test_the_default_sink_writes_a_line_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    write_stderr("hello")
    assert capsys.readouterr().err == "hello\n"
