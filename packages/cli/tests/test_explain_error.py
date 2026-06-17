from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from ronin_agent_patterns import FakeProvider, LLMResponse
from ronin_cli.explain_error import (
    Frame,
    SourceSnippet,
    build_prompt,
    extract_error_message,
    format_frames,
    gather_snippets,
    parse_trace,
)
from ronin_cli.main import app

runner = CliRunner()


# ---------- parse_trace: per-language ----------

PY_TRACE = '''Traceback (most recent call last):
  File "app/main.py", line 12, in handler
    return work(x)
  File "app/work.py", line 30, in work
    return 1 / x
ZeroDivisionError: division by zero'''


def test_parse_python_traceback() -> None:
    frames = parse_trace(PY_TRACE)
    assert frames == [
        Frame(file="app/main.py", line=12, func="handler", language="python"),
        Frame(file="app/work.py", line=30, func="work", language="python"),
    ]


NODE_TRACE = '''TypeError: x is not a function
    at Object.<anonymous> (/srv/app/index.js:42:13)
    at Module._compile (node:internal/modules/cjs/loader:1254:14)
    at /srv/app/lib/util.js:7:5'''


def test_parse_node_stack() -> None:
    frames = parse_trace(NODE_TRACE)
    files = [(f.file, f.line, f.language) for f in frames]
    assert ("/srv/app/index.js", 42, "node") in files
    assert ("/srv/app/lib/util.js", 7, "node") in files
    # node:internal frames have no real on-disk path, so they are not captured.
    assert not any(f.file.startswith("node:") for f in frames)


GO_PANIC = '''panic: runtime error: index out of range [3] with length 3

goroutine 1 [running]:
main.process(...)
\t/home/u/proj/main.go:25 +0x1d
main.main()
\t/home/u/proj/run.go:10 +0x65'''


def test_parse_go_panic() -> None:
    frames = parse_trace(GO_PANIC)
    files = [(f.file, f.line, f.language) for f in frames]
    assert ("/home/u/proj/main.go", 25, "go") in files
    assert ("/home/u/proj/run.go", 10, "go") in files


RUST_PANIC = """thread 'main' panicked at src/lib.rs:14:9:
attempt to divide by zero
stack backtrace:
   0: rust_begin_unwind
   3: app::run
             at src/run.rs:8:5"""


def test_parse_rust_panic() -> None:
    frames = parse_trace(RUST_PANIC)
    files = [(f.file, f.line, f.language) for f in frames]
    assert ("src/lib.rs", 14, "rust") in files
    assert ("src/run.rs", 8, "rust") in files


def test_parse_trace_dedupes_and_ignores_prose() -> None:
    text = (
        'File "x.py", line 5, in f\n'
        "this is just prose, no frame here\n"
        'File "x.py", line 5, in f\n'  # duplicate
    )
    frames = parse_trace(text)
    assert frames == [Frame(file="x.py", line=5, func="f", language="python")]


def test_parse_trace_empty() -> None:
    assert parse_trace("") == []
    assert parse_trace("nothing to see") == []


# ---------- extract_error_message ----------

def test_extract_error_message_python() -> None:
    assert extract_error_message(PY_TRACE) == "ZeroDivisionError: division by zero"


def test_extract_error_message_node() -> None:
    assert extract_error_message(NODE_TRACE) == "TypeError: x is not a function"


def test_extract_error_message_go() -> None:
    msg = extract_error_message(GO_PANIC)
    assert "index out of range" in msg


def test_extract_error_message_rust() -> None:
    msg = extract_error_message(RUST_PANIC)
    assert "panicked at" in msg


def test_extract_error_message_empty() -> None:
    assert extract_error_message("") == ""


# ---------- gather_snippets ----------

def test_gather_snippets_reads_cited_lines(tmp_path: Path) -> None:
    src = tmp_path / "mod.py"
    src.write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n")
    frames = [Frame(file="mod.py", line=5, func="f", language="python")]
    snippets = gather_snippets(frames, tmp_path, context=2)
    assert len(snippets) == 1
    assert snippets[0].found
    assert "> 5 | line5" in snippets[0].snippet
    assert "line3" in snippets[0].snippet and "line7" in snippets[0].snippet


def test_gather_snippets_missing_file(tmp_path: Path) -> None:
    frames = [Frame(file="nope.py", line=3)]
    snippets = gather_snippets(frames, tmp_path)
    assert snippets[0].found is False
    assert snippets[0].snippet == ""


def test_gather_snippets_rejects_path_outside_root(tmp_path: Path) -> None:
    # A path that escapes the repo root must not be read.
    outside = tmp_path.parent / "secret.py"
    outside.write_text("SECRET\n")
    try:
        frames = [Frame(file="../secret.py", line=1)]
        snippets = gather_snippets(frames, tmp_path / "repo")
        (tmp_path / "repo").mkdir(exist_ok=True)
        assert snippets[0].found is False
    finally:
        if outside.exists():
            outside.unlink()


def test_gather_snippets_line_out_of_range(tmp_path: Path) -> None:
    src = tmp_path / "short.py"
    src.write_text("only one line\n")
    frames = [Frame(file="short.py", line=99)]
    snippets = gather_snippets(frames, tmp_path)
    assert snippets[0].found is False


def test_gather_snippets_uses_injected_reader(tmp_path: Path) -> None:
    frames = [Frame(file="virtual.py", line=2)]

    def reader(p):  # noqa: ANN001
        return "a\nb\nc\n"

    (tmp_path / "virtual.py").write_text("placeholder\n")
    snippets = gather_snippets(frames, tmp_path, context=1, reader=reader)
    assert snippets[0].found
    assert "> 2 | b" in snippets[0].snippet


# ---------- build_prompt / format_frames ----------

def test_build_prompt_includes_error_and_source() -> None:
    snippets = [SourceSnippet(frame=Frame(file="x.py", line=5, func="f"), found=True,
                              snippet="> 5 | boom")]
    prompt = build_prompt("ValueError: bad", snippets, "the full trace text")
    assert "ValueError: bad" in prompt
    assert "x.py:5" in prompt
    assert "> 5 | boom" in prompt
    assert "the full trace text" in prompt


def test_format_frames_empty() -> None:
    assert "no source frames" in format_frames([])


def test_format_frames_renders() -> None:
    out = format_frames([Frame(file="a.py", line=3, func="g", language="python")])
    assert "a.py:3" in out
    assert "in g" in out
    assert "[python]" in out


# ---------- CLI integration ----------

def test_explain_error_offline_shows_frames_and_source(tmp_path: Path) -> None:
    (tmp_path / "work.py").write_text("\n".join(["import x"] * 40) + "\n")
    trace = 'Traceback (most recent call last):\n  File "work.py", line 30, in work\n    boom\nValueError: nope'
    # No provider auth => offline path.
    with patch("ronin_cli.config.RoninConfig.has_provider_auth", return_value=False):
        r = runner.invoke(app, ["explain-error", trace, "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "ValueError: nope" in r.output
    assert "work.py:30" in r.output
    assert "no provider key" in r.output


def test_explain_error_with_provider(tmp_path: Path) -> None:
    (tmp_path / "work.py").write_text("\n".join(["import x"] * 40) + "\n")
    trace = 'File "work.py", line 30, in work\nValueError: nope'
    provider = FakeProvider(responses=[
        LLMResponse(text="Root cause: bad input. Fix: validate it.",
                    stop_reason="end_turn", usage={})])
    with patch("ronin_cli.runner.build_single_provider", return_value=provider), \
         patch("ronin_cli.config.RoninConfig.has_provider_auth", return_value=True):
        r = runner.invoke(app, ["explain-error", trace, "--root", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "Root cause" in r.output


def test_explain_error_stdin_pipe(tmp_path: Path) -> None:
    trace = 'File "x.py", line 1, in f\nValueError: piped'
    with patch("ronin_cli.config.RoninConfig.has_provider_auth", return_value=False):
        r = runner.invoke(app, ["explain-error", "--root", str(tmp_path)], input=trace)
    assert r.exit_code == 0, r.output
    assert "ValueError: piped" in r.output


def test_explain_error_no_input(tmp_path: Path) -> None:
    # No args and an empty piped stdin => usage error.
    r = runner.invoke(app, ["explain-error", "--root", str(tmp_path)], input="")
    assert r.exit_code == 2
    assert "nothing to explain" in r.output
