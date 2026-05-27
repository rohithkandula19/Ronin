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


def test_leading_absolute_path_is_not_a_command(tmp_path: Path) -> None:
    # A message that starts with a filesystem path must reach the agent, not be
    # swallowed as a slash command (regression: "/Users/me/proj tell me…").
    action, _ = _call("/Users/rohithkandula/Desktop/ro-ecg-sentinel tell me about this project",
                      root=tmp_path)
    assert action == "passthrough"
    action, _ = _call("/home/x/project explain it", root=tmp_path)
    assert action == "passthrough"


def test_login_sets_provider_key_and_model(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import patch
    from ro_claude_kit_cli.code_mode import handle_slash_command
    monkeypatch.chdir(tmp_path)
    console, buf = _console()
    config = CSKConfig(provider="groq")
    with patch("rich.prompt.Prompt.ask", return_value="sk-or-v1-abc123"):
        action = handle_slash_command(
            "/login openrouter qwen/qwen3-coder:free", console=console, root=tmp_path,
            config=config, undo_stack=[], transcript=[],
        )
    assert action == "handled"
    assert config.provider == "openrouter"
    assert config.openai_api_key == "sk-or-v1-abc123"
    assert config.resolved_model() == "qwen/qwen3-coder:free"


def test_login_rejects_double_paste(tmp_path: Path, monkeypatch) -> None:
    from unittest.mock import patch
    from ro_claude_kit_cli.code_mode import handle_slash_command
    monkeypatch.chdir(tmp_path)
    console, buf = _console()
    config = CSKConfig(provider="groq")
    dbl = "sk-or-v1-aaaa" + "sk-or-v1-bbbb"  # two keys concatenated
    with patch("rich.prompt.Prompt.ask", return_value=dbl):
        handle_slash_command("/login openrouter", console=console, root=tmp_path,
                             config=config, undo_stack=[], transcript=[])
    assert config.openai_api_key is None          # rejected, not saved
    assert "pasted more than once" in buf.getvalue()
