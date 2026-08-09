"""The whole layer wired together, on real files: record → crash → resume → export.

No fakes at all. This subsystem's only seam is the filesystem, and faking that
would test nothing that matters here — the failures worth catching (a torn line, a
lagging sidecar, an unpaired tool call) are properties of real bytes on real disk.
"""
from __future__ import annotations

from pathlib import Path

from persistence_harness import crashed_session, truncate_mid_line

from ronin.core.types import ToolResultBlock, ToolUse, unpaired_tool_uses
from ronin.persistence import (
    INTERRUPTED_NO_RESULT,
    StateSource,
    Transcript,
    list_sessions,
    new_session_id,
    read_events,
    replay,
    resume_latest,
    sessions_dir,
    to_html,
    to_markdown,
)
from ronin.persistence.demo import main as demo_main

MODEL = "fake-model-1"


def test_a_crashed_session_is_recorded_resumed_and_exported(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    directory = sessions_dir(tmp_path)
    session_id = new_session_id(clock=lambda: 1_770_000_000.0, entropy=lambda n: "aa" * n)

    events = crashed_session()
    with Transcript.open(
        directory, session_id, model=MODEL, cwd=str(project), title="fix the 404"
    ) as transcript:
        transcript.extend(events)
        path = transcript.path

    # The crash: the last write never finished.
    lost = truncate_mid_line(path)
    assert lost > 0

    read = read_events(path)
    assert read.header is not None and read.header.model == MODEL
    assert len(read.skipped) == 1 and "incomplete final record" in read.skipped[0]
    assert read.events == events[:-1]

    result = replay(read.events)
    assert result.source is StateSource.RECORDED
    assert result.state.pairing_errors == ()
    assert unpaired_tool_uses(result.state.messages) == ()
    calls = [
        block.id
        for message in result.state.messages
        for block in message.content_blocks
        if isinstance(block, ToolUse)
    ]
    stubs = [
        block
        for message in result.state.messages
        for block in message.content_blocks
        if isinstance(block, ToolResultBlock) and block.content == INTERRUPTED_NO_RESULT
    ]
    assert calls == ["toolu_read_1", "toolu_write_2"]
    assert [block.tool_use_id for block in stubs] == ["toolu_write_2"]

    # The picker sees it without reading a single event.
    rows = list_sessions(directory)
    assert [row.session_id for row in rows] == [session_id]
    assert rows[0].files_touched == ("src/app.py",)

    continued = resume_latest(directory, str(project))
    assert continued is not None
    assert continued.state == result.state
    assert continued.skipped == read.skipped

    markdown = to_markdown(read.events, continued.meta)
    html = to_html(read.events, continued.meta)
    assert session_id in markdown and session_id in html
    assert "<script" not in html
    for external in ("http://", "https://", "<link", "src="):
        assert external not in html

    # Writing them is the caller's job, and both survive a round trip to disk.
    (tmp_path / "session.md").write_text(markdown, encoding="utf-8")
    (tmp_path / "session.html").write_text(html, encoding="utf-8")
    assert (tmp_path / "session.md").read_text(encoding="utf-8") == markdown
    assert (tmp_path / "session.html").read_text(encoding="utf-8") == html


def test_appending_to_a_resumed_session_keeps_the_file_loadable(tmp_path: Path) -> None:
    """The sequence that would corrupt a naive implementation: crash, resume, append.

    The torn line has to be dropped on re-open, or it stops being the last line and
    the whole session stops loading.
    """
    directory = sessions_dir(tmp_path)
    events = crashed_session()
    with Transcript.open(directory, "s1", model=MODEL, cwd=str(tmp_path)) as first:
        first.extend(events)
        path = first.path
    truncate_mid_line(path)

    with Transcript.open(directory, "s1", model=MODEL, cwd=str(tmp_path)) as second:
        assert second.repairs
        second.extend(events[:2])

    read = read_events(path)
    assert read.skipped == ()
    assert read.events == (*events[:-1], *events[:2])
    assert replay(read.events).state.pairing_errors == ()


async def test_the_demo_runs_offline_and_proves_what_it_claims(
    capsys: object,
) -> None:
    """The demo is a deliverable, so it is also a test — if it stops working, the
    thing a human uses to see this layer work has stopped working."""
    assert await demo_main() == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    output = captured.out
    assert "incomplete final record dropped" in output
    assert "synthesized an interrupted, no-result block" in output
    assert "pairing_errors: empty" in output
    assert "external references in the HTML: none" in output
