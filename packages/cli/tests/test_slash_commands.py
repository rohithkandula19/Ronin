from __future__ import annotations

import io
from pathlib import Path

from rich.console import Console

from ro_claude_kit_cli.code_mode import handle_slash_command
from ro_claude_kit_cli.config import CSKConfig


def _console() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, width=100), buf


def _call(user: str, *, root: Path, undo_stack=None, transcript=None):
    console, buf = _console()
    action = handle_slash_command(
        user, console=console, root=root, config=CSKConfig(provider="anthropic"),
        undo_stack=undo_stack if undo_stack is not None else [],
        transcript=transcript if transcript is not None else [],
    )
    return action, buf.getvalue()


def test_passthrough_for_plain_text(tmp_path: Path) -> None:
    action, _ = _call("fix the bug", root=tmp_path)
    assert action == "passthrough"


def test_quit_variants_exit(tmp_path: Path) -> None:
    for q in ("/quit", "/q", ":q", "/exit"):
        action, _ = _call(q, root=tmp_path)
        assert action == "exit", q


def test_help_lists_commands(tmp_path: Path) -> None:
    action, out = _call("/help", root=tmp_path)
    assert action == "handled"
    assert "/clear" in out and "/diff" in out and "/memory" in out


def test_clear_empties_transcript(tmp_path: Path) -> None:
    transcript = ["USER: hi", "ASSISTANT: yo"]
    action, out = _call("/clear", root=tmp_path, transcript=transcript)
    assert action == "handled"
    assert transcript == []
    assert "cleared" in out


def test_model_shows_provider(tmp_path: Path) -> None:
    action, out = _call("/model", root=tmp_path)
    assert action == "handled"
    assert "anthropic" in out


def test_memory_when_absent_and_present(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))  # clean long-term memory store
    action, out = _call("/memory", root=tmp_path)
    assert action == "handled" and "no long-term memories" in out
    (tmp_path / "RONIN.md").write_text("be terse", encoding="utf-8")
    action, out = _call("/memory", root=tmp_path)
    assert "be terse" in out


def test_init_scaffolds(tmp_path: Path) -> None:
    action, out = _call("/init", root=tmp_path)
    assert action == "handled"
    assert (tmp_path / "RONIN.md").is_file()


def test_tools_lists_update_todos(tmp_path: Path) -> None:
    action, out = _call("/tools", root=tmp_path)
    assert action == "handled"
    assert "read_file" in out and "update_todos" in out


def test_diff_clean_tree(tmp_path: Path) -> None:
    # not a git repo → git diff errors gracefully; either message is acceptable
    action, out = _call("/diff", root=tmp_path)
    assert action == "handled"
    assert "clean" in out or "git diff failed" in out


def test_colon_prefix_also_works(tmp_path: Path) -> None:
    action, out = _call(":help", root=tmp_path)
    assert action == "handled" and "/clear" in out


def test_unknown_command(tmp_path: Path) -> None:
    action, out = _call("/bogus", root=tmp_path)
    assert action == "handled" and "unknown command" in out
