"""The pure renderers: diff, todos, status, approval, and the panel set."""
from __future__ import annotations

from pathlib import Path

import pytest
from ui_harness import APPROVAL, TODOS, happy_turn

from ronin.core.types import ApprovalRequest, DangerLevel, Todo, TodoStatus
from ronin.ui.reduce import ViewState, reduce_all
from ronin.ui.render import (
    ANSI,
    APPROVAL_TRUNCATION,
    CR_GLYPH,
    MARKUP,
    NO_CHANGES_NOTE,
    OVER_CAPACITY,
    PLAIN,
    TRAILING_NEWLINE_NOTE,
    Styles,
    escape_markup,
    render_approval,
    render_diff,
    render_panels,
    render_status,
    render_todos,
    render_tool_lines,
    render_transcript,
    truncate_lines,
)

# --------------------------------------------------------------------------- #
# Styles
# --------------------------------------------------------------------------- #


def test_a_typo_in_a_style_token_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown style tokens"):
        Styles({"addedd": ("<", ">")})


def test_the_plain_style_map_is_the_identity() -> None:
    assert PLAIN.wrap("added", "+x") == "+x"


@pytest.mark.parametrize(
    "raw",
    [
        "plain text",
        "[red]not a tag[/red]",
        "items[0] = x",
        "windows C:\\dir[1]",
        "[unclosed",
        "[/]",
        "a backslash \\ alone",
    ],
)
def test_markup_metacharacters_in_model_text_are_escaped_for_an_in_band_dialect(
    raw: str,
) -> None:
    escaped = escape_markup(raw)
    assert PLAIN.text(raw) == raw
    assert MARKUP.text(raw) == escaped
    assert escaped.count("\\[") == raw.count("[")


def test_a_diff_that_looks_like_markup_survives_the_markup_dialect() -> None:
    out = render_diff("old [red]x[/red]\n", "new [dim]y[/dim]\n", path="a.txt", styles=MARKUP)
    assert "\\[red]" in out
    assert "\\[dim]" in out
    # our own tags are still there, unescaped, so colour still happens
    assert "[green]" in out
    assert "[red]-old" in out


def test_an_approval_body_that_looks_like_markup_is_escaped_not_swallowed() -> None:
    request = ApprovalRequest(
        tool_use_id="t",
        name="Bash",
        danger_level=DangerLevel.DESTRUCTIVE,
        rendered="rm -rf ./[cache]",
    )
    assert "\\[cache]" in render_approval(request, styles=MARKUP)
    assert "./[cache]" in render_approval(request)


def test_colour_comes_from_the_injected_map_not_from_the_renderer() -> None:
    plain = render_diff("a\n", "b\n", path="f.txt")
    assert "\x1b[" not in plain
    coloured = render_diff("a\n", "b\n", path="f.txt", styles=ANSI)
    assert "\x1b[32m+b\x1b[0m" in coloured


# --------------------------------------------------------------------------- #
# Diff
# --------------------------------------------------------------------------- #


def test_the_diff_is_a_real_unified_diff_with_per_line_markers() -> None:
    out = render_diff("one\ntwo\nthree\n", "one\n2\nthree\n", path="src/a.py")
    lines = out.split("\n")
    assert lines[0] == "--- a/src/a.py"
    assert lines[1] == "+++ b/src/a.py"
    assert any(line.startswith("@@") for line in lines)
    assert "-two" in lines
    assert "+2" in lines
    assert " one" in lines


def test_a_crlf_to_lf_change_is_visible(tmp_path: Path) -> None:
    # Read as bytes: Path.read_text() applies universal-newline translation and
    # would hide the very regression this test exists for.
    crlf = tmp_path / "crlf.txt"
    lf = tmp_path / "lf.txt"
    crlf.write_bytes(b"alpha\r\nbeta\r\n")
    lf.write_bytes(b"alpha\nbeta\n")
    old = crlf.read_bytes().decode("utf-8")
    new = lf.read_bytes().decode("utf-8")

    out = render_diff(old, new, path="crlf.txt")
    assert f"-alpha{CR_GLYPH}" in out
    assert "+alpha" in out
    assert NO_CHANGES_NOTE not in out


def test_a_tab_survives_the_diff_verbatim() -> None:
    out = render_diff("\tindented\n", "\tindented deeper\n", path="t.py")
    assert "-\tindented" in out
    assert "+\tindented deeper" in out


def test_an_empty_file_diffs_as_pure_additions() -> None:
    out = render_diff("", "new line\n", path="new.txt")
    assert "+new line" in out
    assert not any(line.startswith("-") and not line.startswith("---") for line in out.split("\n"))


def test_deleting_everything_diffs_as_pure_removals() -> None:
    out = render_diff("gone\n", "", path="old.txt")
    assert "-gone" in out
    assert not any(line.startswith("+") and not line.startswith("+++") for line in out.split("\n"))


def test_two_empty_files_report_no_changes() -> None:
    assert NO_CHANGES_NOTE in render_diff("", "", path="empty.txt")


def test_a_trailing_newline_only_change_is_reported_rather_than_called_identical() -> None:
    out = render_diff("a", "a\n", path="nl.txt")
    assert TRAILING_NEWLINE_NOTE in out
    assert NO_CHANGES_NOTE not in out


def test_unicode_survives_the_diff() -> None:
    out = render_diff("héllo wörld\n", "héllo 世界 🎉\n", path="u.txt")
    assert "-héllo wörld" in out
    assert "+héllo 世界 🎉" in out


def test_an_enormous_diff_is_truncated_with_a_marker_naming_the_cut() -> None:
    old = "".join(f"line {index}\n" for index in range(1000))
    new = "".join(f"changed {index}\n" for index in range(1000))
    out = render_diff(old, new, path="big.txt", max_lines=50)
    lines = out.split("\n")
    assert len(lines) == 51
    assert lines[-1] == "…[diff truncated: 50 of 2003 lines shown]"


# --------------------------------------------------------------------------- #
# Todos
# --------------------------------------------------------------------------- #


def test_the_checklist_marks_each_status_distinctly() -> None:
    out = render_todos(TODOS)
    assert out.split("\n") == [
        "☑ read the test",
        "◐ fix the bug",
        "☐ run the suite",
    ]


def test_an_empty_todo_list_renders_empty_so_the_pane_can_hide() -> None:
    assert render_todos(()) == ""


def test_a_huge_todo_list_is_truncated_with_a_marker() -> None:
    todos = tuple(
        Todo(id=str(index), subject=f"task {index}", status=TodoStatus.PENDING)
        for index in range(500)
    )
    out = render_todos(todos, max_items=10)
    assert out.split("\n")[-1] == "…[todo list truncated: 10 of 500 lines shown]"


# --------------------------------------------------------------------------- #
# Status
# --------------------------------------------------------------------------- #


def test_the_status_line_carries_model_context_cost_cwd_and_branch() -> None:
    out = render_status(
        model="claude-sonnet",
        context_used=0.42,
        cost_usd=0.0342,
        cwd="/home/user/Ronin",
        branch="ui-layer",
    )
    assert out == "claude-sonnet  ·  42% ctx  ·  $0.0342  ·  /home/user/Ronin  ·  ui-layer"


def test_the_branch_is_passed_in_and_a_missing_one_is_labelled() -> None:
    out = render_status(model="m", context_used=0.0, cost_usd=0.0, cwd=".", branch="")
    assert "(no branch)" in out


def test_a_context_percentage_over_one_hundred_is_marked_not_invented() -> None:
    out = render_status(model="m", context_used=1.37, cost_usd=0.0, cwd=".", branch="b")
    assert f"{OVER_CAPACITY} ctx" in out


def test_a_negative_context_fraction_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="context_used"):
        render_status(model="m", context_used=-0.1, cost_usd=0.0, cwd=".", branch="b")


def test_a_long_path_is_elided_from_the_left_and_the_line_fits() -> None:
    out = render_status(
        model="claude-sonnet",
        context_used=0.5,
        cost_usd=1.0,
        cwd="/" + "/".join(f"very-long-directory-{index}" for index in range(10)),
        branch="main",
        max_width=80,
    )
    assert len(out) <= 80
    assert "…" in out
    assert "main" in out


def test_the_mode_appears_when_given() -> None:
    out = render_status(
        model="m", context_used=0.0, cost_usd=0.0, cwd=".", branch="b", mode="auto-accept"
    )
    assert "auto-accept" in out


# --------------------------------------------------------------------------- #
# Approval
# --------------------------------------------------------------------------- #


def test_the_approval_renders_the_rendered_field_byte_for_byte() -> None:
    request = ApprovalRequest(
        tool_use_id="t1",
        name="Bash",
        danger_level=DangerLevel.DESTRUCTIVE,
        rendered="rm -rf /tmp/build && echo done\n# second line",
        reason="destructive",
    )
    out = render_approval(request)
    assert request.rendered in out
    assert "Bash" in out
    assert "destructive" in out


def test_a_renderer_cannot_substitute_its_own_text_for_the_rendered_field() -> None:
    # Same tool, same danger level, deliberately mismatched `rendered`: the only
    # thing that changes in the output is what the field says, which is the proof
    # that nothing is re-derived from the tool name or its arguments.
    honest = ApprovalRequest(
        tool_use_id="t1",
        name="Bash",
        danger_level=DangerLevel.DESTRUCTIVE,
        rendered="rm -rf ./build",
    )
    misleading = ApprovalRequest(
        tool_use_id="t1",
        name="Bash",
        danger_level=DangerLevel.DESTRUCTIVE,
        rendered="rm -rf /",
    )
    first = render_approval(honest)
    second = render_approval(misleading)
    assert "rm -rf ./build" in first
    assert "rm -rf /" not in first
    assert "rm -rf /" in second
    assert first.replace("rm -rf ./build", "rm -rf /") == second


def test_the_approval_renderer_takes_no_arguments_it_could_re_derive_from() -> None:
    import inspect

    parameters = set(inspect.signature(render_approval).parameters)
    assert parameters == {"request", "styles", "max_lines"}
    assert not parameters & {"arguments", "tool", "spec", "diff", "old", "new"}


def test_a_gigantic_approval_body_is_truncated_and_says_so_twice() -> None:
    request = ApprovalRequest(
        tool_use_id="t1",
        name="Edit",
        danger_level=DangerLevel.MUTATING,
        rendered="\n".join(f"line {index}" for index in range(500)),
    )
    out = render_approval(request, max_lines=20)
    assert "…[approval text truncated: 20 of 500 lines shown]" in out
    assert APPROVAL_TRUNCATION in out


# --------------------------------------------------------------------------- #
# Transcript, tool pane, panels
# --------------------------------------------------------------------------- #


def test_reasoning_is_hidden_unless_asked_for() -> None:
    state = ViewState(committed_text="answer", committed_thinking="secret reasoning")
    assert render_transcript(state) == "answer"
    assert "secret reasoning" in render_transcript(state, show_thinking=True)


def test_the_tool_pane_is_one_line_per_call() -> None:
    state = reduce_all(happy_turn())
    assert len(render_tool_lines(state.tool_lines).split("\n")) == len(state.tool_lines)


def test_an_empty_tool_pane_renders_empty() -> None:
    assert render_tool_lines(()) == ""


def test_truncate_lines_names_what_it_cut() -> None:
    out = truncate_lines("a\nb\nc\nd", 2, what="thing")
    assert out == "a\nb\n…[thing truncated: 2 of 4 lines shown]"


def test_truncate_lines_leaves_short_text_untouched() -> None:
    assert truncate_lines("a\nb", 5, what="thing") == "a\nb"


def test_the_panel_set_covers_every_region_of_the_screen() -> None:
    state = reduce_all(happy_turn()).with_status(model="m", branch="main")
    panels = render_panels(state)
    assert panels.transcript == "Reading src/main.py now."
    assert panels.tools.startswith("● Read(")
    assert panels.todos.startswith("☑")
    assert "main" in panels.status
    assert panels.approval == ""
    assert panels.errors == ""


def test_a_pending_approval_reaches_the_approval_panel() -> None:
    panels = render_panels(reduce_all((APPROVAL,)))
    assert APPROVAL.rendered in panels.approval
