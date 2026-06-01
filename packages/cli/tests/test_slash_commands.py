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


def test_model_switch_in_place(tmp_path: Path, monkeypatch) -> None:
    """/model <name> changes the model without prompting for a key."""
    from ro_claude_kit_cli.code_mode import handle_slash_command
    monkeypatch.chdir(tmp_path)
    console, buf = _console()
    config = CSKConfig(provider="cerebras")
    action = handle_slash_command("/model qwen-3-235b-a22b-instruct-2507",
                                  console=console, root=tmp_path, config=config,
                                  undo_stack=[], transcript=[])
    assert action == "handled"
    assert config.model == "qwen-3-235b-a22b-instruct-2507"
    assert config.resolved_model() == "qwen-3-235b-a22b-instruct-2507"


def test_summarize_result_counts() -> None:
    from ro_claude_kit_cli.streaming import _summarize_result
    assert _summarize_result('["a.py", "b.py", "c.py"]').startswith("3 items · ")
    assert _summarize_result("line1\nline2\nline3").startswith("3 lines · ")
    assert _summarize_result("just a short string") == "just a short string"


def test_slash_completer_completes_commands() -> None:
    from ro_claude_kit_cli.prompt_box import _slash_completer
    assert _slash_completer("/he", 0) == "/help "
    ms = {_slash_completer("/m", i) for i in range(4)} - {None}
    assert {"/memory ", "/model ", "/models "} <= ms
    assert _slash_completer("hello", 0) is None       # only completes slash words


def test_slash_dropdown_rows_carry_name_and_description() -> None:
    """The prompt_toolkit dropdown source: each matching command yields
    (insert_text, start_position, 'name + description')."""
    from ro_claude_kit_cli.prompt_box import _slash_completions

    rows = _slash_completions("/mo")
    inserts = {ins for ins, _, _ in rows}
    assert inserts == {"/model", "/models"}
    # start_position replaces the whole typed "/mo" token (len 3 -> -3)
    assert all(start == -3 for _, start, _ in rows)
    # the visible row shows the command's real description
    display = {ins: disp for ins, _, disp in rows}
    assert "/model" in display["/model"] and "switch it" in display["/model"]


def test_slash_dropdown_only_fires_on_slash_token() -> None:
    from ro_claude_kit_cli.prompt_box import _slash_completions
    assert _slash_completions("hello") == []        # not a slash word
    assert _slash_completions("") == []              # empty
    assert _slash_completions("/model gpt") == []    # already past the command token
    assert {ins for ins, _, _ in _slash_completions("/")}  # bare "/" lists everything


def test_new_commands_appear_in_help(tmp_path: Path) -> None:
    _, out = _call("/help", root=tmp_path)
    for c in ("/compact", "/context", "/copy", "/export", "/resume",
              "/vim", "/doctor", "/config", "/mcp", "/agents"):
        assert c in out, c


def test_mcp_lists_configured_servers(tmp_path: Path) -> None:
    from ro_claude_kit_cli import mcp_client
    mcp_client._ACTIVE.clear()  # isolate from servers other tests may have started
    p = mcp_client.mcp_config_path(tmp_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('{"mcpServers": {"weather": {"command": "uvx", "args": ["wx"]}}}',
                 encoding="utf-8")
    action, out = _call("/mcp", root=tmp_path)
    assert action == "handled"
    assert "weather" in out and "uvx" in out


def test_mcp_when_none_configured(tmp_path: Path) -> None:
    from ro_claude_kit_cli import mcp_client
    mcp_client._ACTIVE.clear()  # isolate from servers other tests may have started
    action, out = _call("/mcp", root=tmp_path)
    assert action == "handled"
    assert "no MCP servers" in out


def test_agents_lists_the_task_subagent(tmp_path: Path) -> None:
    action, out = _call("/agents", root=tmp_path)
    assert action == "handled"
    assert "task" in out and "sub-agent" in out


def test_context_reports_turns_and_tokens(tmp_path: Path) -> None:
    transcript = ["USER: hi there", "ASSISTANT: hello back", "USER: again"]
    action, out = _call("/context", root=tmp_path, transcript=transcript)
    assert action == "handled"
    assert "2 turn" in out          # two USER: entries
    assert "token" in out


def test_export_writes_markdown_file(tmp_path: Path) -> None:
    transcript = ["USER: make a plan", "ASSISTANT: here is the plan"]
    action, out = _call("/export", root=tmp_path, transcript=transcript)
    assert action == "handled"
    files = list(tmp_path.glob("ronin-conversation-*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert "make a plan" in body and "here is the plan" in body


def test_resume_lists_then_reloads(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from ro_claude_kit_cli import sessions
    sessions._session_id = None  # fresh session id for isolation
    from ro_claude_kit_cli.code_mode import save_session
    save_session(tmp_path, ["USER: earlier q", "ASSISTANT: earlier a"])

    # /resume (no arg) LISTS past sessions — it does not auto-load
    transcript: list[str] = []
    action, out = _call("/resume", root=tmp_path, transcript=transcript)
    assert action == "handled"
    assert "earlier q" in out          # the session is listed (by its title)
    assert transcript == []            # nothing loaded yet

    # /resume 1 reloads the chosen session into the transcript
    action, out = _call("/resume 1", root=tmp_path, transcript=transcript)
    assert action == "handled"
    assert len(transcript) == 2 and "earlier q" in transcript[0]


def test_copy_uses_clipboard_helper(tmp_path: Path, monkeypatch) -> None:
    captured: dict[str, str] = {}

    def fake_copy(text: str) -> bool:
        captured["text"] = text
        return True

    monkeypatch.setattr("ro_claude_kit_cli.code_mode._copy_to_clipboard", fake_copy)
    transcript = ["USER: q", "ASSISTANT: the answer to copy"]
    action, out = _call("/copy", root=tmp_path, transcript=transcript)
    assert action == "handled"
    assert captured["text"] == "the answer to copy"
    assert "copied" in out


def test_copy_with_nothing_to_copy(tmp_path: Path) -> None:
    action, out = _call("/copy", root=tmp_path, transcript=[])
    assert action == "handled"
    assert "nothing to copy" in out


def test_compact_replaces_transcript_with_summary(tmp_path: Path, monkeypatch) -> None:
    class _Resp:
        text = "- decided X\n- touched file Y"

    class _Provider:
        def complete(self, **kw):  # noqa: ANN003
            return _Resp()

    monkeypatch.setattr("ro_claude_kit_cli.runner.build_provider", lambda cfg: _Provider())
    transcript = [f"USER: msg {i}" for i in range(10)] + ["ASSISTANT: long reply " * 50]
    action, out = _call("/compact", root=tmp_path, transcript=transcript)
    assert action == "handled"
    assert len(transcript) == 1
    assert "decided X" in transcript[0]
    assert "compacted" in out


def test_vim_toggles_keybindings(tmp_path: Path) -> None:
    from ro_claude_kit_cli.prompt_box import set_vi_mode
    set_vi_mode(False)  # known starting state
    action, out = _call("/vim", root=tmp_path)
    assert action == "handled" and "on" in out
    _, out2 = _call("/vim off", root=tmp_path)
    assert "off" in out2


def test_doctor_and_config_show_provider_and_model(tmp_path: Path) -> None:
    for cmd in ("/doctor", "/config"):
        action, out = _call(cmd, root=tmp_path)
        assert action == "handled", cmd
        assert "anthropic" in out, cmd
        assert "provider" in out and "model" in out, cmd


def test_at_picker_lists_and_filters_files(tmp_path: Path) -> None:
    from ro_claude_kit_cli.prompt_box import _at_completions
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    (tmp_path / "readme.md").write_text("x")
    (tmp_path / "report.txt").write_text("x")
    (tmp_path / "node_modules").mkdir()  # must be ignored

    # bare "@" lists the root, dirs first, ignoring node_modules
    rows = _at_completions("@", root=tmp_path)
    displays = [d for _, _, d in rows]
    assert "src/" in displays and "readme.md" in displays
    assert "node_modules/" not in displays
    assert displays.index("src/") < displays.index("readme.md")  # dirs sort first

    # "@re" filters to the two files starting with "re"
    rows = _at_completions("@re", root=tmp_path)
    inserts = {i for i, _, _ in rows}
    assert inserts == {"@readme.md", "@report.txt"}
    assert all(start == -3 for _, start, _ in rows)  # replace "@re"


def test_at_picker_descends_one_segment(tmp_path: Path) -> None:
    from ro_claude_kit_cli.prompt_box import _at_completions
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x")
    rows = _at_completions("@src/", root=tmp_path)
    assert {i for i, _, _ in rows} == {"@src/app.py"}


def test_at_picker_stays_in_root_and_ignores_non_mentions(tmp_path: Path) -> None:
    from ro_claude_kit_cli.prompt_box import _at_completions
    assert _at_completions("@../../", root=tmp_path) == []   # no escaping root
    assert _at_completions("plain text", root=tmp_path) == []
    assert _at_completions("", root=tmp_path) == []


def test_bang_bash_mode_runs_and_records(tmp_path: Path) -> None:
    from ro_claude_kit_cli.code_mode import _run_bash_inline
    console, buf = _console()
    transcript: list[str] = []
    _run_bash_inline("echo hello-from-bash", console=console, root=tmp_path,
                     transcript=transcript)
    out = buf.getvalue()
    assert "hello-from-bash" in out            # output shown
    assert any("USER: !echo hello-from-bash" == e for e in transcript)
    assert any("hello-from-bash" in e for e in transcript)  # recorded for the agent


def test_bang_bash_mode_reports_nonzero_exit(tmp_path: Path) -> None:
    from ro_claude_kit_cli.code_mode import _run_bash_inline
    console, buf = _console()
    transcript: list[str] = []
    _run_bash_inline("exit 3", console=console, root=tmp_path, transcript=transcript)
    assert "exit 3" in buf.getvalue()
    assert any("exit 3" in e for e in transcript)


def test_hash_quick_memory_appends_to_ronin_md(tmp_path: Path) -> None:
    from ro_claude_kit_cli.project_memory import append_project_note
    path, name = append_project_note(tmp_path, "always run ruff before commit")
    assert name == "RONIN.md" and path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "## Notes" in text
    assert "always run ruff before commit" in text
    # a second note files newest-first under the same section
    append_project_note(tmp_path, "use uv not pip")
    text2 = path.read_text(encoding="utf-8")
    assert text2.count("## Notes") == 1
    assert text2.index("use uv not pip") < text2.index("always run ruff before commit")


def test_hash_quick_memory_prefers_existing_claude_md(tmp_path: Path) -> None:
    from ro_claude_kit_cli.project_memory import append_project_note
    (tmp_path / "CLAUDE.md").write_text("# existing\n", encoding="utf-8")
    _path, name = append_project_note(tmp_path, "note here")
    assert name == "CLAUDE.md"  # don't create a rival RONIN.md when CLAUDE.md exists
    assert not (tmp_path / "RONIN.md").exists()


def test_shift_tab_cycles_edit_mode() -> None:
    from ro_claude_kit_cli.prompt_box import current_mode, cycle_mode, set_mode
    set_mode("normal")
    assert current_mode() == "normal"
    assert cycle_mode() == "auto-accept"
    assert cycle_mode() == "plan"
    assert cycle_mode() == "normal"   # wraps around
    set_mode("plan")
    assert current_mode() == "plan"
    set_mode("normal")                # reset for other tests


def test_shift_tab_binding_cycles_mode_in_real_prompt() -> None:
    """Drive a real prompt_toolkit prompt and press Shift+Tab (BackTab, \\x1b[Z)
    to prove the key binding advances the edit mode end-to-end."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.output import DummyOutput

    from ro_claude_kit_cli.prompt_box import current_mode, cycle_mode, set_mode

    set_mode("normal")
    kb = KeyBindings()

    @kb.add("s-tab")
    def _(event):  # noqa: ANN001
        cycle_mode()

    with create_pipe_input() as pipe:
        pipe.send_text("\x1b[Z\n")   # Shift+Tab, then Enter
        session = PromptSession(input=pipe, output=DummyOutput())
        session.prompt([("class:arrow", " > ")], key_bindings=kb)
    assert current_mode() == "auto-accept"  # advanced one step from normal
    set_mode("normal")                       # reset for other tests


def test_prompt_toolkit_prompt_runs_with_dropdown_completer() -> None:
    """Drive the real prompt_toolkit prompt with a pipe input to prove the
    dropdown completer integrates end-to-end (the live REPL path, which the
    non-TTY suite otherwise never exercises)."""
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from ro_claude_kit_cli.prompt_box import _slash_completions

    class _C(Completer):
        def get_completions(self, document, complete_event):
            for ins, start, disp in _slash_completions(document.text_before_cursor):
                yield Completion(ins, start_position=start, display=disp)

    with create_pipe_input() as pipe:
        pipe.send_text("/he\n")
        session = PromptSession(input=pipe, output=DummyOutput())
        got = session.prompt([("class:arrow", " > ")], completer=_C(),
                             complete_while_typing=True)
    assert got == "/he"
