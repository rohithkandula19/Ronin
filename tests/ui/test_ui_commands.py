"""Slash commands: the registry, validation, substitution, and suggestions."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin.ui.commands import (
    BUILTIN_COMMANDS,
    BUILTIN_REGISTRY,
    ArgSpec,
    Command,
    ParsedCommand,
    ParseError,
    arg_spec_for_template,
    is_command,
    load_registry,
    load_user_commands,
    parse,
    placeholders,
    render_help,
    substitute,
    suggest,
)

EXPECTED = (
    "help",
    "clear",
    "compact",
    "undo",
    "diff",
    "model",
    "cost",
    "plan",
    "resume",
    "agents",
    "hooks",
    "init",
    "doctor",
)


def test_every_specified_builtin_exists_exactly_once() -> None:
    names = tuple(command.name for command in BUILTIN_COMMANDS)
    assert names == EXPECTED
    assert len(set(names)) == len(names)


def test_a_command_without_a_summary_is_rejected() -> None:
    with pytest.raises(ValueError, match="needs a summary"):
        Command("thing", "")


def test_a_command_name_with_a_space_is_rejected() -> None:
    with pytest.raises(ValueError, match="must match"):
        Command("two words", "no")


@pytest.mark.parametrize("name", EXPECTED)
def test_every_builtin_parses_bare(name: str) -> None:
    parsed = parse(f"/{name}")
    assert isinstance(parsed, ParsedCommand)
    assert parsed.name == name
    assert parsed.argument == ""
    assert parsed.body == ""
    assert parsed.is_user_defined is False


@pytest.mark.parametrize(
    ("line", "expected_argument"),
    [
        ("/model opus", "opus"),
        ("/model   opus  ", "opus"),
        ("/compact keep the test names", "keep the test names"),
        ("/diff src/a.py", "src/a.py"),
        ("/resume 20260716-045123", "20260716-045123"),
        ("/MODEL opus", "opus"),
    ],
)
def test_an_optional_argument_is_carried_through(line: str, expected_argument: str) -> None:
    parsed = parse(line)
    assert isinstance(parsed, ParsedCommand)
    assert parsed.argument == expected_argument


@pytest.mark.parametrize("line", ["/clear now", "/cost --json", "/plan please", "/doctor -v"])
def test_a_command_that_takes_nothing_rejects_an_argument(line: str) -> None:
    error = parse(line)
    assert isinstance(error, ParseError)
    assert "takes no arguments" in error.message
    assert "usage:" in error.message


def test_an_unknown_command_suggests_the_closest_known_one() -> None:
    error = parse("/moddel opus")
    assert isinstance(error, ParseError)
    assert error.suggestion == "model"
    assert error.display == "unknown command /moddel (did you mean /model?)"


def test_an_unknown_command_with_no_close_match_says_so_without_guessing() -> None:
    error = parse("/zzzzzzzz")
    assert isinstance(error, ParseError)
    assert error.suggestion == ""
    assert error.display == "unknown command /zzzzzzzz"


@pytest.mark.parametrize(
    ("typo", "expected"),
    [("hlep", "help"), ("compat", "compact"), ("dcotor", "doctor"), ("undoo", "undo")],
)
def test_suggestions_come_from_the_known_set(typo: str, expected: str) -> None:
    assert suggest(typo, BUILTIN_REGISTRY.names()) == expected


def test_a_line_that_is_not_a_command_is_refused_not_guessed_at() -> None:
    error = parse("please fix the test")
    assert isinstance(error, ParseError)
    assert "not a slash command" in error.message


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("/help", True),
        ("/help topics", True),
        ("/", False),
        ("/ help", False),
        ("hello", False),
        ("/usr/bin/env", False),
        ("", False),
    ],
)
def test_is_command_screens_lines_before_parsing(line: str, expected: bool) -> None:
    assert is_command(line) is expected


def test_a_lone_slash_is_a_typo_with_a_pointer_to_help() -> None:
    error = parse("/")
    assert isinstance(error, ParseError)
    assert "/help" in error.message


def test_an_unbalanced_quote_becomes_a_message_not_an_exception() -> None:
    error = parse('/model "unclosed')
    assert isinstance(error, ParseError)
    assert "cannot split arguments" in error.message


def test_help_is_built_from_the_registry_so_it_cannot_drift() -> None:
    out = render_help()
    for command in BUILTIN_COMMANDS:
        assert command.summary in out
        assert command.usage.split()[0] in out


# --------------------------------------------------------------------------- #
# Substitution
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("template", "argument", "args", "expected"),
    [
        ("Review $ARGUMENTS please", "a b c", ("a", "b", "c"), "Review a b c please"),
        ("first=$1 second=$2", "a b", ("a", "b"), "first=a second=b"),
        ("$1 and $1", "x", ("x",), "x and x"),
        ("cost is $$5", "", (), "cost is $5"),
        ("literal $$1 not positional", "a", ("a",), "literal $1 not positional"),
        ("$HOME stays", "", (), "$HOME stays"),
        ("$ARGUMENTSX is not a placeholder", "", (), "$ARGUMENTSX is not a placeholder"),
        ("quoted: $1", '"two words" x', ("two words", "x"), "quoted: two words"),
        ("nothing here", "ignored", ("ignored",), "nothing here"),
    ],
)
def test_substitution_is_explicit(
    template: str, argument: str, args: tuple[str, ...], expected: str
) -> None:
    outcome = substitute(template, argument, args)
    assert outcome.text == expected
    assert outcome.missing == ()


def test_a_missing_positional_is_reported_never_filled_in() -> None:
    outcome = substitute("$1 then $3", "only", ("only",))
    assert outcome.missing == ("$3",)


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("nothing", ArgSpec.NONE),
        ("uses $ARGUMENTS", ArgSpec.OPTIONAL),
        ("uses $1", ArgSpec.REQUIRED),
        ("uses $1 and $ARGUMENTS", ArgSpec.REQUIRED),
        ("escaped $$1 only", ArgSpec.NONE),
    ],
)
def test_a_templates_placeholders_decide_whether_it_needs_arguments(
    template: str, expected: ArgSpec
) -> None:
    assert arg_spec_for_template(template) is expected


def test_placeholders_are_listed_in_order_without_the_escape() -> None:
    assert placeholders("$$ $1 $ARGUMENTS $1 $2") == ("$1", "$ARGUMENTS", "$2")


# --------------------------------------------------------------------------- #
# User-defined commands
# --------------------------------------------------------------------------- #


def _write(root: Path, name: str, body: str) -> Path:
    directory = root / ".ronin" / "commands"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.md"
    path.write_text(body, encoding="utf-8")
    return path


def test_a_user_command_is_loaded_with_a_summary_from_its_first_line(tmp_path: Path) -> None:
    _write(tmp_path, "review", "# review a file\nLook at $1 for $2.\n")
    registry = load_registry(tmp_path)
    command = registry.get("review")
    assert command is not None
    assert command.summary == "review a file"
    assert command.arg_spec is ArgSpec.REQUIRED
    assert "review" in registry.names()


def test_a_user_command_substitutes_and_reports_its_source(tmp_path: Path) -> None:
    path = _write(tmp_path, "review", "Look at $1 for $2 issues. All: $ARGUMENTS\n")
    parsed = parse("/review src/a.py typing", registry=load_registry(tmp_path))
    assert isinstance(parsed, ParsedCommand)
    assert parsed.body == "Look at src/a.py for typing issues. All: src/a.py typing\n"
    assert parsed.source == str(path)
    assert parsed.is_user_defined is True
    assert parsed.args == ("src/a.py", "typing")


def test_a_user_command_referencing_a_missing_positional_names_its_file(tmp_path: Path) -> None:
    path = _write(tmp_path, "triage", "Compare $1 with $2 and then $3.\n")
    error = parse("/triage one two", registry=load_registry(tmp_path))
    assert isinstance(error, ParseError)
    assert str(path) in error.message
    assert "$3" in error.message
    assert "2 argument(s)" in error.message


def test_a_user_command_needing_arguments_refuses_a_bare_invocation(tmp_path: Path) -> None:
    _write(tmp_path, "review", "Look at $1.\n")
    error = parse("/review", registry=load_registry(tmp_path))
    assert isinstance(error, ParseError)
    assert "needs an argument" in error.message


def test_a_user_command_with_no_placeholders_takes_no_arguments(tmp_path: Path) -> None:
    _write(tmp_path, "standup", "Summarize what changed today.\n")
    registry = load_registry(tmp_path)
    parsed = parse("/standup", registry=registry)
    assert isinstance(parsed, ParsedCommand)
    assert parsed.body == "Summarize what changed today.\n"
    assert isinstance(parse("/standup extra", registry=registry), ParseError)


def test_a_user_file_cannot_shadow_a_builtin(tmp_path: Path) -> None:
    _write(tmp_path, "clear", "rm -rf everything: $ARGUMENTS\n")
    registry = load_registry(tmp_path)
    parsed = parse("/clear", registry=registry)
    assert isinstance(parsed, ParsedCommand)
    assert parsed.is_user_defined is False
    assert parsed.body == ""


def test_a_badly_named_file_is_skipped_rather_than_breaking_the_ui(tmp_path: Path) -> None:
    _write(tmp_path, "Not A Command", "whatever\n")
    _write(tmp_path, "good", "fine $ARGUMENTS\n")
    names = [entry.command.name for entry in load_user_commands(tmp_path / ".ronin" / "commands")]
    assert names == ["good"]


def test_a_missing_command_directory_is_simply_empty(tmp_path: Path) -> None:
    assert load_user_commands(tmp_path / "nope") == ()
    assert load_registry(tmp_path).user == ()


def test_user_commands_are_loaded_in_a_stable_order(tmp_path: Path) -> None:
    for name in ("zebra", "apple", "mango"):
        _write(tmp_path, name, "body $ARGUMENTS\n")
    loaded = load_user_commands(tmp_path / ".ronin" / "commands")
    assert [entry.command.name for entry in loaded] == ["apple", "mango", "zebra"]


def test_help_lists_user_commands_with_their_paths(tmp_path: Path) -> None:
    path = _write(tmp_path, "review", "# review a file\nLook at $1.\n")
    out = render_help(load_registry(tmp_path))
    assert "/review <arguments>" in out
    assert str(path) in out


def test_a_unicode_template_round_trips(tmp_path: Path) -> None:
    _write(tmp_path, "resume-jp", "続けて: $ARGUMENTS\n")
    parsed = parse("/resume-jp テスト", registry=load_registry(tmp_path))
    assert isinstance(parsed, ParsedCommand)
    assert parsed.body == "続けて: テスト\n"
