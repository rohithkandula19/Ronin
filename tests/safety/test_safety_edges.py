"""The safety layer's remaining branches: redirection operators, config errors, matchers.

Three groups, and they matter for different reasons.

**The lexer's redirection and quoting handling.** `<<-EOF`, `<<<word` and backticks in
awkward positions are not exotic — they are how a real shell command gets written, and
each is a place a segment splitter can lose track of where a command ends. Losing track
is how `cat <<<"x"; rm -rf /` gets read as a harmless `cat`. Every test below asserts
both directions: the operator is understood, *and* a destructive payload behind it is
still seen.

**Config errors.** A settings file that declares a malformed rule must fail loudly at
load, because the alternative is a rule that silently matches nothing — an approval gate
with a hole in it that looks configured. Every message names the offending key, since the
user's next move is to edit that line.

**Matcher and denylist edges.** `MatchTarget` is what every rule is evaluated against, so
an argument shape it does not understand is a rule that quietly stops covering that
tool. The denylist's `_delete_class` decides whether a word names the root, and both of
its answers are load-bearing: a missed root is a destroyed machine, a false positive is
a refusal nobody believes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ronin.core.types import DangerLevel, Mode, ToolSpec, ToolUse
from ronin.safety.command import Segment, parse_command
from ronin.safety.denylist import DenyCode, Denylist
from ronin.safety.policy import (
    Answer,
    AnyUse,
    Decision,
    Exact,
    MatchTarget,
    Outcome,
    PolicyEngine,
    Rule,
    RuleSet,
    _as_paths,
)
from ronin.safety.sandbox import DockerSandbox
from ronin.safety.settings import load_settings


def spec(
    name: str = "bash",
    *,
    danger: DangerLevel = DangerLevel.DESTRUCTIVE,
    approval: bool = True,
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} tool",
        danger_level=danger,
        requires_approval=approval,
    )


def heads(line: str) -> list[str]:
    return [segment.binary for segment in parse_command(line) if segment.binary]


# --------------------------------------------------------------------------- #
# Redirection and quoting the lexer has to understand
# --------------------------------------------------------------------------- #


def test_a_dash_heredoc_body_is_data_and_not_further_commands() -> None:
    """`<<-EOF` strips leading tabs; it is still a heredoc, so `rm -rf /` inside the
    body is text `cat` will print, not a command that runs."""
    segments = parse_command("cat <<-EOF\nrm -rf /\nEOF")
    assert [s.binary for s in segments] == ["cat"]
    assert any("rm -rf /" in body for body in segments[0].heredocs), segments[0].heredocs


def test_a_dash_heredoc_does_not_swallow_the_line_after_the_delimiter() -> None:
    """The failure this guards: treating everything after `<<-` as body means the
    command following the closing delimiter is never examined."""
    assert "rm" in heads("cat <<-EOF\nbody\nEOF\nrm -rf /")


@pytest.mark.parametrize(
    "line",
    ['cat <<<"hello"; rm -rf /', 'cat <<<"hello"\nrm -rf /', "cat <<<hello\nrm -rf /"],
    ids=["same-line", "next-line-quoted", "next-line-bare"],
)
def test_a_herestring_does_not_swallow_the_command_after_it(line: str) -> None:
    """`<<<` is a *herestring*: one word of data, and the command after it still runs.

    This was a live bypass. The `<` run is lexed greedily, so `<<<` arrived at the
    heredoc check already ending in `<<` and was read as one — which made the following
    *line* the heredoc body. `cat <<<x` then hid `rm -rf /` from every check in the
    package, and the guard that was supposed to catch this peeked for a fourth `<` and
    so could never fire.
    """
    segments = parse_command(line)
    assert [segment.binary for segment in segments] == ["cat", "rm"], segments
    assert segments[0].heredocs == (), "the herestring must not open a heredoc"
    assert [(r.operator, r.target) for r in segments[0].redirects] == [("<<<", "hello")]


def test_a_herestring_target_is_data_and_not_a_file_to_check() -> None:
    """`<<<word` names no file, so the word must stay out of the path lane — otherwise
    every herestring would be checked as a write to a file of that name."""
    redirect = parse_command("cat <<<notes.txt")[0].redirects[0]
    assert not redirect.writes
    assert not redirect.names_a_file
    assert parse_command("cat <<<notes.txt")[0].path_words == ()


@pytest.mark.parametrize(
    "line",
    ['cat <<<"hello"; rm -rf /', 'cat <<<"hello"\nrm -rf /'],
    ids=["same-line", "next-line"],
)
def test_a_destructive_command_after_a_herestring_is_still_denied(
    tmp_path: Path, line: str
) -> None:
    """The reason the operator matters at all: the denylist only ever sees what the
    splitter reached."""
    denylist = Denylist(workspace_root=tmp_path, home=tmp_path)
    hits = denylist.check_command(line)
    assert any(hit.code is DenyCode.RM_ROOT for hit in hits), hits


def test_a_shell_fed_by_a_herestring_has_its_payload_read_as_code(tmp_path: Path) -> None:
    """`bash <<<"rm -rf /"` executes the word — the same channel as a heredoc body, and
    given the same treatment. Left as a redirect target it is a string nobody reads,
    and the only visible binary is a harmless-looking `bash`."""
    segments = parse_command('bash <<<"rm -rf /"')
    assert [segment.binary for segment in segments] == ["bash", "rm"]
    assert segments[1].depth == 1
    assert segments[1].parent == 0

    denylist = Denylist(workspace_root=tmp_path, home=tmp_path)
    hits = denylist.check_command('bash <<<"rm -rf /"')
    assert any(hit.code is DenyCode.RM_ROOT for hit in hits), hits


def test_a_heredoc_delimiter_still_ends_a_heredoc_body() -> None:
    """The control for the fix above: `<<` must keep working exactly as before, body
    and all, and the command after the closing delimiter must still be reached."""
    segments = parse_command("cat <<hello\nbody\nhello\nrm -rf /")
    assert [segment.binary for segment in segments] == ["cat", "rm"]
    assert segments[0].heredocs == ("body",)


def test_two_redirects_with_no_space_between_them_are_read_as_two() -> None:
    """`cat <in>out` is one word short of ambiguous: the target `in` ends where `>`
    begins, and a reader that only stopped at whitespace would take `in>out` as one
    filename and lose the output redirect entirely."""
    segments = parse_command("cat <in>out")
    assert [(r.operator, r.target) for r in segments[0].redirects] == [("<", "in"), (">", "out")]


def test_a_backtick_substitution_becomes_its_own_segment() -> None:
    """`echo `rm -rf /`` runs the substitution first. A gate that only reads the outer
    command sees `echo` and waves it through."""
    segments = parse_command("echo `rm -rf /`")
    assert [s.binary for s in segments] == ["echo", "rm"]
    assert segments[1].depth == 1


def test_a_backtick_inside_double_quotes_is_still_a_substitution() -> None:
    """Quoting does not disable command substitution — `"`ls`"` still runs `ls`, which
    is exactly the case a "skip everything inside quotes" lexer gets wrong."""
    assert heads('echo "`rm -rf /`"') == ["echo", "rm"]


def test_a_backslash_inside_backticks_does_not_end_the_substitution() -> None:
    r"""``\` `` is an escaped backtick, so the substitution continues past it. Ending
    early would put the rest of the line in the wrong segment."""
    segments = parse_command("echo `echo a\\`")
    assert [s.binary for s in segments] == ["echo", "echo"]


def test_a_backslash_before_an_ordinary_character_inside_quotes_is_literal() -> None:
    r"""Inside double quotes bash keeps `\q` as two characters and only unescapes
    `\" \\ \$ \` `. Getting this wrong changes the word the rules match against."""
    segments = parse_command(r'echo "a\qb"')
    assert segments[0].argv == ("echo", r"a\qb")


def test_a_quoted_string_inside_a_substitution_does_not_end_it_early() -> None:
    """A `)` inside quotes is not the closing paren. Stopping at it would cut the
    inner command in half and hide whatever followed."""
    segments = parse_command('echo $(echo "x y")')
    assert segments[1].argv == ("echo", "x y")


def test_an_unclosed_substitution_still_yields_its_inner_command() -> None:
    """A truncated line must not make the scanner give up: returning nothing is
    indistinguishable from "no dangerous command found"."""
    assert "rm" in heads("echo $(rm -rf /")


def test_a_redirect_touching_the_word_before_it_still_ends_that_word() -> None:
    """`echo hi>out.txt` has no space, and the operand is `hi` — not `hi>out.txt`."""
    segments = parse_command("echo hi>out.txt")
    assert segments[0].argv == ("echo", "hi")
    assert [r.target for r in segments[0].redirects] == ["out.txt"]


def test_a_double_dash_ends_a_wrapper_prefix() -> None:
    """`sudo -- rm -rf /` is `rm` with `sudo` peeled. Reading `--` as the program name
    would make the segment's binary `--` and every rule about `rm` miss it."""
    segments = parse_command("sudo -- rm -rf /")
    assert segments[0].binary == "rm"
    assert "sudo" in segments[0].prefixes


# --------------------------------------------------------------------------- #
# Segment's own accessors and validator
# --------------------------------------------------------------------------- #


def test_a_segment_cannot_have_a_negative_depth() -> None:
    """Depth is nesting inside `$( )`. A negative value would mean the scanner closed
    more groups than it opened, which is a bug rather than an input.

    Only reachable by direct construction, and tested anyway because this validator is
    the floor under anything that builds a segment programmatically.
    """
    with pytest.raises(ValueError, match="depth must be >= 0"):
        Segment(raw="x", binary="x", argv=("x",), depth=-1)


def test_a_segment_that_runs_nothing_renders_as_an_empty_line() -> None:
    """`FOO=bar` on its own is an assignment, not a command. `line` is what rules are
    matched against, so it has to be empty rather than the bare binary name — a
    non-empty string here would let `^$` or a catch-all pattern match an assignment."""
    segments = parse_command("FOO=bar")
    assert segments[0].argv == ()
    assert segments[0].line == ""
    assert segments[0].assignments == (("FOO", "bar"),)


def test_a_long_flag_with_a_value_is_recognised_by_its_name() -> None:
    """`--force=true` is the `--force` flag. A whole-word comparison would miss it,
    and `git push --force=true` would slip past a rule written against `--force`."""
    segments = parse_command("git push --force=true")
    assert segments[0].has_flag("--force")
    assert not segments[0].has_flag("--quiet")


def test_a_bare_dash_operand_is_not_a_path() -> None:
    """`cat -` reads stdin. Treating `-` as a filename would put a nonsense candidate
    in front of every path check."""
    segments = parse_command("cat -")
    assert segments[0].operands == ("-",)
    assert segments[0].path_words == ()


# --------------------------------------------------------------------------- #
# Malformed settings: loud at load, never a rule that matches nothing
# --------------------------------------------------------------------------- #


def write_settings(root: Path, body: str) -> Path:
    path = root / ".ronin" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def problems(root: Path) -> tuple[str, ...]:
    loaded = load_settings(cwd=root, home=root / "nohome")
    return tuple(error.message for error in loaded.errors)


@pytest.mark.parametrize(
    ("match", "needle"),
    [
        ('{"kind": "exact", "argument": 5, "value": "x"}', "'exact' match needs"),
        ('{"kind": "path"}', "'path' match needs"),
        ('{"kind": "path", "pattern": 7}', "'path' match needs"),
        ('{"kind": "path", "pattern": "src/**", "argument": 3}', "'path' match needs"),
        ('{"kind": "regex"}', "'regex' match needs"),
        ('{"kind": "regex", "pattern": 3}', "'regex' match needs"),
        ('{"kind": "regex", "pattern": "("}', "not a valid regex"),
        ("42", "'match' must be an object"),
    ],
    ids=[
        "exact-int-argument",
        "path-no-pattern",
        "path-int-pattern",
        "path-int-argument",
        "regex-no-pattern",
        "regex-int-pattern",
        "regex-uncompilable",
        "match-not-an-object",
    ],
)
def test_a_malformed_matcher_is_refused_with_the_shape_it_wanted(
    tmp_path: Path, match: str, needle: str
) -> None:
    """A matcher that cannot be built must not degrade into one that matches nothing:
    that is a configured-looking gate with a hole in it. The message says what the
    matcher needed, because the user is about to retype that line."""
    write_settings(
        tmp_path,
        '{"rules": [{"tool": "bash", "decision": "deny", "match": ' + match + "}]}",
    )
    found = problems(tmp_path)
    assert found, "a malformed matcher loaded silently"
    assert any(needle in message for message in found), found


def test_the_exact_shorthand_must_be_an_object(tmp_path: Path) -> None:
    """`{"exact": "ls"}` is the mistake the shorthand invites, since `command` and
    `path` do take a bare string. The error names both keys it wanted."""
    write_settings(tmp_path, '{"rules": [{"tool": "bash", "decision": "allow", "exact": 5}]}')
    found = problems(tmp_path)
    assert any("'exact' must be an object" in message for message in found), found
    assert any("'argument' and 'value'" in message for message in found), found


def test_the_exact_shorthand_written_correctly_still_loads(tmp_path: Path) -> None:
    """The control: refusing valid config is its own failure."""
    write_settings(
        tmp_path,
        '{"rules": [{"tool": "bash", "decision": "allow",'
        ' "exact": {"argument": "command", "value": "ls"}}]}',
    )
    loaded = load_settings(cwd=tmp_path, home=tmp_path / "nohome")
    assert not loaded.errors, [str(error) for error in loaded.errors]
    assert loaded.rules_from("project")[0].matcher == Exact(argument="command", value="ls")


@pytest.mark.parametrize(
    ("key", "value", "needle"),
    [
        ("yolo", '"yes"', "true or false"),
        ("sandbox", "1", "true or false"),
        ("taint_min_span", "2", "integer >= 4"),
        ("taint_min_span", "true", "integer >= 4"),
        ("mode", '"turbo"', "must be one of"),
        ("default_decision", '"maybe"', "must be one of"),
        ("protected_branches", '"main"', "list of strings"),
        ("protected_branches", "[1, 2]", "list of strings"),
    ],
    ids=[
        "bool-as-string",
        "int-as-bool-key",
        "int-below-floor",
        "bool-as-int",
        "unknown-mode",
        "unknown-decision",
        "string-as-list",
        "list-of-ints",
    ],
)
def test_a_scalar_setting_of_the_wrong_type_is_refused_with_its_range(
    tmp_path: Path, key: str, value: str, needle: str
) -> None:
    """Each message states the legal values, because "invalid" alone does not tell the
    user what to type instead."""
    write_settings(tmp_path, "{" + f'"{key}": {value}' + "}")
    found = problems(tmp_path)
    assert found
    assert any(needle in message for message in found), found


def test_every_legal_scalar_loads_and_reports_where_it_came_from(tmp_path: Path) -> None:
    """The control for all eight refusals above, and the provenance check: a user
    debugging a surprising decision needs to know which file set the value."""
    write_settings(
        tmp_path,
        '{"yolo": false, "sandbox": true, "taint_min_span": 8, "mode": "auto_edit",'
        ' "default_decision": "deny", "protected_branches": ["main", "release"]}',
    )
    loaded = load_settings(cwd=tmp_path, home=tmp_path / "nohome")
    assert not loaded.errors, [str(error) for error in loaded.errors]
    assert loaded.taint_min_span == 8
    assert loaded.mode is Mode.AUTO_EDIT
    assert loaded.default_decision is Decision.DENY
    assert loaded.protected_branches == frozenset({"main", "release"})
    assert loaded.source_of("taint_min_span") == "project"


# --------------------------------------------------------------------------- #
# Rules and match targets
# --------------------------------------------------------------------------- #


def test_a_rule_must_name_a_tool() -> None:
    """`'*'` is how a rule says "any tool". An empty string is a rule that can never be
    reached, which is worse than an error because it looks configured."""
    with pytest.raises(ValueError, match=r"Rule.tool is required"):
        Rule(tool="", matcher=AnyUse(), decision=Decision.DENY)


def test_an_answer_must_carry_a_real_outcome() -> None:
    """A string that happens to read like approval must not approve anything: `Answer`
    is what a UI hands the engine, and `"yes"` is the plausible mistake."""
    with pytest.raises(TypeError, match="must be an Outcome"):
        Answer(outcome="yes")  # type: ignore[arg-type]


def test_rules_for_one_tool_exclude_rules_for_another() -> None:
    """`for_tool` is what a UI uses to show "the rules that apply here", so a leaked
    rule from another tool is a misleading explanation of a decision."""
    rules = RuleSet(
        rules=(
            Rule(tool="bash", matcher=AnyUse(), decision=Decision.DENY),
            Rule(tool="read", matcher=AnyUse(), decision=Decision.ALLOW),
            Rule(tool="*", matcher=AnyUse(), decision=Decision.ASK),
        )
    )
    assert [rule.tool for rule in rules.for_tool("bash")] == ["bash", "*"]
    assert [rule.tool for rule in rules.for_tool("write")] == ["*"]


def test_a_non_command_argument_is_read_straight_from_the_arguments() -> None:
    target = MatchTarget(tool="write", arguments={"path": "notes.md", "lines": 4})
    assert target.argument("path") == "notes.md"
    assert target.argument("lines") is None, "a non-string must not be stringified"
    assert target.argument("missing") is None


def test_a_segment_target_takes_its_paths_from_the_segment() -> None:
    """Once a compound command is split, a path rule has to be judged against the
    segment it is evaluating — not against the whole command's arguments, which would
    make `>` targets from another segment count as this one's."""
    segment = parse_command("cat src/a.py; rm /etc/hosts")[1]
    target = MatchTarget(tool="bash", arguments={"command": "…"}, segment=segment)
    assert target.path_values() == ("/etc/hosts",)


def test_a_path_argument_given_as_a_list_is_read_entry_by_entry() -> None:
    """`paths: [...]` is a real tool shape, and a matcher that only understood a single
    string would silently stop covering it."""
    target = MatchTarget(
        tool="write", arguments={"paths": ["src/a.py", "src/b.py"], "content": "x"}
    )
    assert set(target.path_values()) == {"src/a.py", "src/b.py"}


def test_a_non_string_entry_in_a_path_list_is_dropped_rather_than_stringified() -> None:
    """Coercing `None` to `"None"` would invent a path, and an invented path can match
    a glob."""
    target = MatchTarget(tool="write", arguments={"paths": ["ok.py", None, 7]})
    assert target.path_values() == ("ok.py",)


def test_a_path_argument_of_an_unexpected_shape_is_ignored() -> None:
    """A dict where a path was expected is a malformed call, and the tool's own error
    says so better than a path rule silently matching nothing."""
    args: dict[str, Any] = {"path": {"nested": "dict"}}
    assert MatchTarget(tool="write", arguments=args).path_values() == ()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a.py", ("a.py",)),
        (["a.py", "b.py"], ("a.py", "b.py")),
        (["a.py", None, 7], ("a.py",)),
        (None, ()),
        (42, ()),
        ({"path": "a.py"}, ()),
    ],
    ids=["string", "list", "mixed-list", "none", "int", "mapping"],
)
def test_the_path_coercion_accepts_exactly_two_shapes(
    value: object, expected: tuple[str, ...]
) -> None:
    """The helper every path check funnels through. Bytes are deliberately excluded
    even though they are a `Sequence`: iterating them yields integers."""
    assert _as_paths(value) == expected


# --------------------------------------------------------------------------- #
# Remembering an approval
# --------------------------------------------------------------------------- #


def engine(**kwargs: Any) -> PolicyEngine:
    return PolicyEngine(rules=RuleSet(rules=()), **kwargs)


def test_remembering_an_approval_records_the_command_not_a_pattern() -> None:
    """A human who approves `rm -rf build` has not approved `rm -rf` anything."""
    use = ToolUse(id="t1", name="bash", arguments={"command": "rm -rf build"})
    rule = engine().remembered_rule(spec(), use, Outcome.YES_SESSION)
    assert rule.matcher == Exact(argument="command", value="rm -rf build")
    assert rule.source == "session"


def test_remembering_a_call_with_only_a_path_keys_on_that_path() -> None:
    """A tool with no `command` argument still needs a specific rule. A tool-wide
    allow here would approve every future write, from one approval of one file."""
    use = ToolUse(id="t1", name="write", arguments={"path": "notes.md", "content": "x"})
    rule = engine().remembered_rule(
        spec("write", danger=DangerLevel.MUTATING), use, Outcome.YES_PERSIST
    )
    assert rule.matcher == Exact(argument="path", value="notes.md")
    assert rule.source == "remembered", "a persisted rule must not be labelled session-only"


def test_remembering_a_call_with_a_list_of_paths_keys_on_the_first() -> None:
    """The list shape has to reach the same lane; without it a `paths: [...]` call
    would fall through to a tool-wide allow."""
    use = ToolUse(id="t1", name="write", arguments={"paths": ["a.py", "b.py"]})
    rule = engine().remembered_rule(
        spec("write", danger=DangerLevel.MUTATING), use, Outcome.YES_SESSION
    )
    assert rule.matcher == Exact(argument="path", value="a.py")


def test_remembering_a_call_with_neither_falls_back_to_tool_wide() -> None:
    """Honest about its own breadth: with nothing specific to key on, the rule says so
    by being an `AnyUse` rather than pretending to be narrow."""
    use = ToolUse(id="t1", name="ping", arguments={})
    rule = engine().remembered_rule(
        spec("ping", danger=DangerLevel.MUTATING), use, Outcome.YES_SESSION
    )
    assert isinstance(rule.matcher, AnyUse)


def test_a_blank_command_does_not_become_an_exact_rule_matching_nothing() -> None:
    """`command: "   "` would otherwise produce a rule keyed on whitespace — a
    remembered approval that can never fire again."""
    use = ToolUse(id="t1", name="bash", arguments={"command": "   "})
    rule = engine().remembered_rule(spec(), use, Outcome.YES_SESSION)
    assert isinstance(rule.matcher, AnyUse)


# --------------------------------------------------------------------------- #
# Mode relaxation
# --------------------------------------------------------------------------- #


def test_auto_edit_allows_a_mutating_tool_that_asks_for_approval() -> None:
    """This is what `auto_edit` is *for*: `write` declares `requires_approval=True`, and
    the mode's promise is that ordinary edits stop prompting. Anything above mutating
    still gates, which the next test pins."""
    verdict = engine(mode=Mode.AUTO_EDIT).evaluate(
        spec("write", danger=DangerLevel.MUTATING),
        ToolUse(id="t1", name="write", arguments={"path": "a.py", "content": "x"}),
    )
    assert verdict.decision is Decision.ALLOW
    assert any("auto_edit" in line for line in verdict.trace), verdict.trace


def test_auto_edit_does_not_allow_a_destructive_tool() -> None:
    """The boundary. If `auto_edit` relaxed destructive tools it would be `full` under
    another name, and the ladder in `Mode` would be a lie."""
    verdict = engine(mode=Mode.AUTO_EDIT).evaluate(
        spec("bash"), ToolUse(id="t1", name="bash", arguments={"command": "rm -rf build"})
    )
    assert verdict.decision is Decision.ASK


# --------------------------------------------------------------------------- #
# Denylist
# --------------------------------------------------------------------------- #


def test_yolo_refuses_nothing_on_the_path_lane_either(tmp_path: Path) -> None:
    """`yolo` is the user's own choice and has to be honoured completely — a
    half-disabled denylist is a gate whose behaviour nobody can predict. It is also
    why `waived` exists: the UI can say so out loud."""
    denylist = Denylist(workspace_root=tmp_path, home=tmp_path, yolo=True)
    assert denylist.check_path(tmp_path / ".ssh" / "id_rsa", write=False) == ()
    assert denylist.waived


def test_reading_private_key_material_is_refused_even_without_write(tmp_path: Path) -> None:
    """Reading the key *is* the harm, so `write=False` must not soften it."""
    denylist = Denylist(workspace_root=tmp_path, home=tmp_path)
    assert denylist.check_path(tmp_path / ".ssh" / "id_rsa", write=False)


@pytest.mark.parametrize("suffix", ["/*", "/.*", "/**"])
def test_deleting_the_root_by_glob_is_the_same_act_as_deleting_the_root(
    tmp_path: Path, suffix: str
) -> None:
    """`rm -rf /*` destroys the machine exactly as `rm -rf /` does, and a denylist that
    only knows the literal form is a denylist with a two-character bypass."""
    denylist = Denylist(workspace_root=tmp_path, home=tmp_path)
    hits = denylist.check_command(f"rm -rf {suffix}")
    assert any(hit.code is DenyCode.RM_ROOT for hit in hits), hits


def test_a_bare_glob_is_not_treated_as_the_root(tmp_path: Path) -> None:
    """`rm -rf *` in a project directory is destructive but ordinary, and refusing it
    as "you tried to delete /" would be a false accusation — the kind that teaches
    people to stop reading the gate."""
    denylist = Denylist(workspace_root=tmp_path, home=tmp_path)
    hits = denylist.check_command("rm -rf *")
    assert not any(hit.code in {DenyCode.RM_ROOT, DenyCode.RM_HOME} for hit in hits), hits


def test_an_ordinary_absolute_path_is_not_a_root_delete(tmp_path: Path) -> None:
    """`/var/tmp/build` starts with `/` and is nobody's catastrophe."""
    denylist = Denylist(workspace_root=tmp_path, home=tmp_path)
    hits = denylist.check_command("rm -rf /var/tmp/build")
    assert not any(hit.code in {DenyCode.RM_ROOT, DenyCode.RM_HOME} for hit in hits), hits


def test_a_git_command_with_no_subcommand_is_not_a_hit(tmp_path: Path) -> None:
    """Bare `git` prints help. Flagging it would train people to ignore the gate."""
    denylist = Denylist(workspace_root=tmp_path, home=tmp_path)
    assert denylist.check_command("git") == ()


def test_a_hard_reset_is_refused_when_nothing_has_been_checkpointed(tmp_path: Path) -> None:
    """The default `has_checkpoint` says no, deliberately: this module does not open a
    git repo, so the honest default is to assume the work is not recoverable."""
    denylist = Denylist(workspace_root=tmp_path, home=tmp_path)
    hits = denylist.check_command("git reset --hard HEAD~3")
    assert any(hit.code is DenyCode.RESET_HARD_NO_CHECKPOINT for hit in hits), hits


def test_a_hard_reset_is_allowed_once_a_checkpoint_exists(tmp_path: Path) -> None:
    """The other half, and why the probe is injected rather than discovered."""
    denylist = Denylist(workspace_root=tmp_path, home=tmp_path, has_checkpoint=lambda: True)
    hits = denylist.check_command("git reset --hard HEAD~3")
    assert not any(hit.code is DenyCode.RESET_HARD_NO_CHECKPOINT for hit in hits), hits


def confined(tmp_path: Path) -> Denylist:
    """A denylist with no scratch roots, so `/tmp` is not itself a permitted area.

    `tmp_path` lives under `/tmp`, which is in `DEFAULT_SCRATCH_ROOTS` — so a test that
    kept the defaults would find every write allowed for the wrong reason and prove
    nothing about `cd` tracking.
    """
    workspace = tmp_path / "ws"
    (workspace / "sub").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    return Denylist(workspace_root=workspace, home=tmp_path / "home", scratch_roots=())


def test_a_bare_cd_moves_the_working_directory_home(tmp_path: Path) -> None:
    """Bare `cd` goes home, so a later write in the same line is resolved against home
    rather than the workspace. Without this, `cd && rm -rf .` is checked against the
    workspace root and looks like an in-workspace delete — the gate would approve a
    command that deletes the user's home directory."""
    hits = confined(tmp_path).check_command("cd && rm -rf .")
    assert any(hit.code is DenyCode.OUTSIDE_WORKSPACE for hit in hits), hits
    assert any(str(tmp_path / "home") in hit.detail for hit in hits), hits


def test_cd_to_a_named_directory_still_resolves_against_it(tmp_path: Path) -> None:
    """The control: bare `cd` is the special case, and `cd sub` must not be redirected
    to home. If it were, every compound command with a `cd` in it would be refused."""
    hits = confined(tmp_path).check_command("cd sub && rm -rf .")
    assert hits == (), hits


# --------------------------------------------------------------------------- #
# Sandbox mounts
# --------------------------------------------------------------------------- #


def test_each_extra_writable_path_becomes_its_own_bind_mount(tmp_path: Path) -> None:
    """Without this loop a session configured with a writable cache directory would
    silently get a read-only one, and every build inside the sandbox would fail in a
    way that looks like a broken toolchain."""
    cache = tmp_path / "cache"
    wheels = tmp_path / "wheels"
    argv = DockerSandbox(image="python:3.12").wrap(
        ("pytest",), workspace=tmp_path, writable=(cache, wheels)
    )

    assert f"{cache}:{cache}:rw" in argv
    assert f"{wheels}:{wheels}:rw" in argv
    assert argv.count("-v") == 3, "expected the workspace plus both writable paths"
    assert argv[-1] == "pytest"


def test_the_workspace_is_mounted_even_when_nothing_else_is_writable(tmp_path: Path) -> None:
    argv = DockerSandbox(image="python:3.12").wrap(("pytest",), workspace=tmp_path, writable=())
    assert f"{tmp_path}:{tmp_path}:rw" in argv
    assert argv.count("-v") == 1
