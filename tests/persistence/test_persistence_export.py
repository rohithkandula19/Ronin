"""Exports: readable, self-contained, and hostile-content-proof."""

from __future__ import annotations

from pathlib import Path

import pytest
from persistence_harness import (
    adversarial_session,
    crashed_session,
    two_turn_session,
)

from ronin.core.types import (
    Event,
    TextDelta,
    ToolEnd,
    ToolResult,
    ToolStart,
    TurnEnd,
    TurnStart,
    TurnState,
)
from ronin.persistence.export import to_html, to_markdown
from ronin.persistence.transcript import SessionMeta

META = SessionMeta(
    session_id="20260809-101112-abc123",
    cwd="/work/repo",
    model="fake-model-1",
    title="fix the 404",
    started_at=1_770_000_000.0,
    updated_at=1_770_000_123.0,
    turns=2,
    events=10,
    cost_usd=0.0231,
    duration_seconds=123.0,
    files_touched=("src/app.py", "docs/NOTES.md"),
    checkpoint_ids=("ckpt-1", "ckpt-2"),
)

HOSTILE_META = SessionMeta(
    session_id="<script>alert(1)</script>",
    cwd='/work/"quoted"|piped',
    model="</details>",
    title="a | b",
)


# --------------------------------------------------------------------------- #
# Purity
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("exporter", [to_markdown, to_html])
def test_an_exporter_writes_nothing_and_reads_nothing(
    exporter: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pure functions of events + metadata: the caller owns the file."""
    monkeypatch.chdir(tmp_path)
    rendered = exporter(two_turn_session(), META)  # type: ignore[operator]
    assert isinstance(rendered, str) and rendered
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("exporter", [to_markdown, to_html])
def test_an_empty_session_still_exports(exporter: object) -> None:
    rendered = exporter((), META)  # type: ignore[operator]
    assert "no turns were recorded" in rendered
    assert META.session_id in rendered


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #


def test_markdown_carries_the_metadata_header() -> None:
    out = to_markdown(two_turn_session(), META)
    for expected in (
        "fake-model-1",
        "$0.0231",
        "123.00s",
        "src/app.py, docs/NOTES.md",
        "ckpt-1, ckpt-2",
        "2026-02-02T02:40:00+00:00",
    ):
        assert expected in out, expected


def test_markdown_marks_turn_boundaries_and_fences_tool_calls() -> None:
    out = to_markdown(two_turn_session(), META)
    assert "## Turn 1 (index 0)" in out
    assert "## Turn 2 (index 1)" in out
    assert "### tool `read` — `toolu_read_1`" in out
    assert "```json" in out
    assert '"path": "src/app.py"' in out
    assert "**result (ok):**" in out
    assert "_turn ended: done (no_tool_calls)_" in out


def test_markdown_reports_a_reset_rather_than_showing_the_discarded_text() -> None:
    out = to_markdown(two_turn_session(), META)
    assert "DISCARD ME" not in out
    assert "stream was reset 1 time(s)" in out


def test_markdown_says_when_a_tool_call_has_no_result() -> None:
    out = to_markdown(crashed_session(), META)
    assert "no result recorded" in out
    assert "turn has no recorded end" in out


def test_a_fence_grows_past_any_backtick_run_in_the_content() -> None:
    """A result containing a fence must not be able to break out of its block and
    start injecting Markdown into the document."""
    events: tuple[Event, ...] = (
        TurnStart(turn_index=0),
        ToolStart(tool_use_id="t1", name="bash"),
        ToolEnd(
            tool_use_id="t1",
            name="bash",
            result=ToolResult(ok=True, content="```\nnested\n````\nmore"),
        ),
        TurnEnd(turn_index=0, state=TurnState.DONE),
    )
    out = to_markdown(events, META)
    assert "`````\n```\nnested\n````\nmore\n`````" in out


def test_a_pipe_in_metadata_cannot_break_the_table() -> None:
    out = to_markdown((), HOSTILE_META)
    rows = [line for line in out.splitlines() if line.startswith("| ")]
    assert rows[0] == "| field | value |"
    assert rows[1] == "| --- | --- |"
    for row in rows[2:]:
        unescaped = row.count("|") - row.count("\\|")
        assert unescaped == 3, f"{row} has an unescaped pipe and breaks the table"


def test_markdown_shows_the_approval_that_was_requested() -> None:
    out = to_markdown(crashed_session(), META)
    assert "approval requested (mutating)" in out
    assert "write(src/app.py)" in out


# --------------------------------------------------------------------------- #
# HTML: escaping and self-containment
# --------------------------------------------------------------------------- #


def test_html_is_a_complete_self_contained_document() -> None:
    out = to_html(two_turn_session(), META)
    assert out.startswith("<!doctype html>")
    assert '<meta charset="utf-8">' in out
    assert "<style>" in out and out.rstrip().endswith("</html>")
    for external in ("<script", "<link", "http://", "https://", "src=", "@import", "//cdn"):
        assert external not in out, external


def test_html_collapses_every_tool_call() -> None:
    out = to_html(two_turn_session(), META)
    # one per tool call plus one for the turn's reasoning
    assert out.count("<details") == 2
    assert out.count("</details>") == 2
    assert "<details open" not in out, "collapsed by default is the point"
    assert "toolu_read_1" in out


def test_html_escapes_a_script_tag_in_model_output() -> None:
    """An unescaped export is an XSS hole in a file people paste around."""
    out = to_html(adversarial_session(), META)
    assert "<script" not in out
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in out
    assert "onerror=x" not in out and 'onerror="alert' not in out


def test_html_escapes_a_closing_details_tag_in_tool_output() -> None:
    """A ``</details>`` in a file the agent read would otherwise close the
    disclosure early and spill the rest of the transcript out of it."""
    out = to_html(adversarial_session(), META)
    assert "&lt;/details&gt;" in out
    assert out.count("</details>") == out.count("<details")


def test_html_escapes_quotes_in_a_summary_and_in_metadata() -> None:
    out = to_html(adversarial_session(), HOSTILE_META)
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in out
    assert "&quot;quoted&quot;" in out
    assert "<script" not in out


def test_html_marks_an_error_result_and_a_missing_one() -> None:
    hostile = to_html(adversarial_session(), META)
    assert "error" in hostile
    crashed = to_html(crashed_session(), META)
    assert "no result recorded" in crashed
    assert "the session ended before this tool returned" in crashed


def test_html_shows_the_reset_and_not_the_discarded_prose() -> None:
    out = to_html(two_turn_session(), META)
    assert "DISCARD ME" not in out
    assert "stream was reset 1 time(s)" in out


def test_html_carries_the_same_metadata_header_as_markdown() -> None:
    out = to_html(two_turn_session(), META)
    for expected in ("fake-model-1", "$0.0231", "123.00s", "ckpt-1, ckpt-2"):
        assert expected in out, expected


def test_an_unrecorded_timestamp_is_labelled_not_rendered_as_1970() -> None:
    out = to_markdown((TextDelta(text="x"),), SessionMeta(session_id="s"))
    assert "unrecorded" in out
    assert "1970" not in out
