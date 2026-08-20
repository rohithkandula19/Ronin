"""Layered config: append vs override, provenance, and surviving a bad file.

Every test builds its own ``home`` and ``cwd`` under ``tmp_path``. Nothing here can read
the developer's real ``~/.ronin`` — which is both a correctness property of
``load_settings`` (it takes both directories as parameters) and the reason these tests
give the same answer on every machine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ronin.core.types import Mode
from ronin.safety.policy import AnyUse, CommandRegex, Decision, Exact, PathGlob, Rule
from ronin.safety.settings import (
    LOCAL_SETTINGS,
    PROJECT_SETTINGS,
    USER_SETTINGS,
    load_settings,
    parse_rule,
)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home"
    (path / ".ronin").mkdir(parents=True)
    return path


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    path = tmp_path / "work" / "repo"
    (path / ".ronin").mkdir(parents=True)
    return path


def write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def allow(pattern: str) -> dict[str, Any]:
    return {"tool": "bash", "decision": "allow", "command": pattern}


# --------------------------------------------------------------------------- #
# Layer order
# --------------------------------------------------------------------------- #


def test_with_no_files_the_builtin_layer_is_the_whole_configuration(home: Path, cwd: Path) -> None:
    settings = load_settings(home=home, cwd=cwd)
    assert settings.healthy
    assert settings.rules_from("builtin") == settings.rules
    assert settings.mode is Mode.ASK
    assert settings.source_of("mode") == "builtin"


def test_a_scalar_from_a_later_layer_overrides_an_earlier_one(home: Path, cwd: Path) -> None:
    write(home / USER_SETTINGS, {"mode": "full"})
    write(cwd / PROJECT_SETTINGS, {"mode": "auto_edit"})
    write(cwd / LOCAL_SETTINGS, {"mode": "ask"})
    settings = load_settings(home=home, cwd=cwd)
    assert settings.mode is Mode.ASK
    assert settings.source_of("mode") == "local"


def test_a_flag_beats_every_file(home: Path, cwd: Path) -> None:
    write(cwd / LOCAL_SETTINGS, {"mode": "plan"})
    settings = load_settings(home=home, cwd=cwd, flags={"mode": "full"})
    assert settings.mode is Mode.FULL
    assert settings.source_of("mode") == "flags"


def test_rule_lists_append_so_a_later_layer_cannot_drop_the_builtin_denies(
    home: Path, cwd: Path
) -> None:
    """The dangerous failure this prevents: a project file with one convenience rule
    silently replacing the builtin list, including the shell's tool-wide `ask` floor."""
    builtin = load_settings(home=home, cwd=cwd).rules
    write(cwd / PROJECT_SETTINGS, {"rules": [allow("^docker ps")]})
    settings = load_settings(home=home, cwd=cwd)
    assert settings.rules[: len(builtin)] == builtin
    assert len(settings.rules) == len(builtin) + 1


def test_every_layer_contributes_its_rules_in_order(home: Path, cwd: Path) -> None:
    write(home / USER_SETTINGS, {"rules": [allow("^htop")]})
    write(cwd / PROJECT_SETTINGS, {"rules": [allow("^docker ps")]})
    write(cwd / LOCAL_SETTINGS, {"rules": [allow("^kubectl get")]})
    settings = load_settings(home=home, cwd=cwd, flags={"rules": [allow("^terraform plan")]})
    sources = [rule.source for rule in settings.rules]
    assert sources[-4:] == ["user", "project", "local", "flags"]


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #


def test_every_effective_rule_names_the_layer_it_came_from(home: Path, cwd: Path) -> None:
    write(cwd / PROJECT_SETTINGS, {"rules": [allow("^docker ps")]})
    settings = load_settings(home=home, cwd=cwd)
    assert {rule.source for rule in settings.rules} == {"builtin", "project"}
    assert settings.rules_from("project")[0].matcher == CommandRegex("^docker ps")


def test_the_provenance_report_names_every_file_and_every_scalar(home: Path, cwd: Path) -> None:
    write(cwd / PROJECT_SETTINGS, {"yolo": False, "rules": [allow("^docker ps")]})
    report = "\n".join(load_settings(home=home, cwd=cwd).provenance())
    assert str(home / USER_SETTINGS) in report
    assert "(absent)" in report
    assert str(cwd / PROJECT_SETTINGS) in report
    assert "1 rule(s)" in report
    assert "yolo = False  (from project)" in report


def test_a_layer_that_exists_but_is_empty_is_reported_as_absent(home: Path, cwd: Path) -> None:
    write(cwd / PROJECT_SETTINGS, {})
    settings = load_settings(home=home, cwd=cwd)
    assert settings.healthy
    assert "(absent)" in settings.layers[2].describe()


# --------------------------------------------------------------------------- #
# Malformed layers
# --------------------------------------------------------------------------- #


def test_a_json_syntax_error_skips_one_layer_and_keeps_the_rest(home: Path, cwd: Path) -> None:
    write(home / USER_SETTINGS, {"rules": [allow("^htop")]})
    (cwd / PROJECT_SETTINGS).write_text('{"rules": [ {"tool": "bash",', encoding="utf-8")
    write(cwd / LOCAL_SETTINGS, {"rules": [allow("^kubectl get")]})
    settings = load_settings(home=home, cwd=cwd)

    assert settings.healthy is False
    assert len(settings.errors) == 1
    assert settings.errors[0].layer == "project"
    assert "not valid JSON" in settings.errors[0].message
    assert "line 1" in settings.errors[0].message
    # The point: the session still has a working configuration.
    assert settings.rules_from("user") and settings.rules_from("local")
    assert settings.rules_from("builtin")


def test_a_json_document_that_is_not_an_object_is_a_named_error(home: Path, cwd: Path) -> None:
    write(cwd / PROJECT_SETTINGS, ["not", "an", "object"])
    settings = load_settings(home=home, cwd=cwd)
    assert "must contain a JSON object" in settings.errors[0].message


def test_one_bad_rule_is_dropped_and_the_rest_of_the_file_still_applies(
    home: Path, cwd: Path
) -> None:
    write(
        cwd / PROJECT_SETTINGS,
        {
            "rules": [
                allow("^docker ps"),
                {"tool": "bash", "decision": "maybe", "command": "^oops"},
                allow("^docker images"),
            ]
        },
    )
    settings = load_settings(home=home, cwd=cwd)
    assert len(settings.rules_from("project")) == 2
    assert "rule 1 was dropped" in settings.errors[0].message
    assert "must be one of allow, ask, deny" in settings.errors[0].message


def test_an_unknown_setting_is_an_error_rather_than_a_silent_no_op(home: Path, cwd: Path) -> None:
    """A typo that does nothing is a permission the user believes they granted."""
    write(cwd / PROJECT_SETTINGS, {"saandbox": True})
    settings = load_settings(home=home, cwd=cwd)
    assert "unknown setting 'saandbox'" in settings.errors[0].message
    assert settings.sandbox is False


@pytest.mark.parametrize(
    ("data", "fragment"),
    [
        ({"mode": "yolo"}, "must be one of plan, ask, auto_edit, full"),
        ({"sandbox": "yes"}, "must be true or false"),
        ({"taint_min_span": 2}, "must be an integer >= 4"),
        ({"protected_branches": "main"}, "must be a list of strings"),
        ({"default_decision": "maybe"}, "must be one of allow, ask, deny"),
        ({"rules": {"tool": "bash"}}, "must be a list"),
    ],
)
def test_a_bad_scalar_is_reported_and_the_previous_value_survives(
    home: Path, cwd: Path, data: dict[str, Any], fragment: str
) -> None:
    write(cwd / PROJECT_SETTINGS, data)
    settings = load_settings(home=home, cwd=cwd)
    assert settings.errors, f"{data} was accepted silently"
    assert fragment in settings.errors[0].message
    assert settings.mode is Mode.ASK


def test_an_unreadable_file_is_survivable(home: Path, cwd: Path) -> None:
    """A directory where a settings file should be: an OSError, not a crash."""
    (cwd / PROJECT_SETTINGS).mkdir()
    settings = load_settings(home=home, cwd=cwd)
    assert settings.errors
    assert "this layer was skipped" in settings.errors[0].message


# --------------------------------------------------------------------------- #
# Rule parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"decision": "allow"}, AnyUse()),
        ({"decision": "allow", "match": {"kind": "tool"}}, AnyUse()),
        ({"decision": "allow", "command": "^pytest"}, CommandRegex("^pytest")),
        (
            {"decision": "allow", "match": {"kind": "regex", "pattern": "^pytest"}},
            CommandRegex("^pytest"),
        ),
        ({"decision": "allow", "path": "src/**/*.py"}, PathGlob("src/**/*.py")),
        (
            {"decision": "allow", "match": {"kind": "path", "pattern": "s/*", "argument": "path"}},
            PathGlob("s/*", "path"),
        ),
        (
            {"decision": "allow", "match": {"kind": "exact", "argument": "command", "value": "ls"}},
            Exact("command", "ls"),
        ),
        (
            {"decision": "allow", "exact": {"argument": "command", "value": "ls"}},
            Exact("command", "ls"),
        ),
    ],
)
def test_both_the_nested_and_the_flat_rule_spellings_parse(
    entry: dict[str, Any], expected: object
) -> None:
    assert parse_rule(entry, source="project").matcher == expected


def test_a_rule_defaults_to_every_tool(home: Path, cwd: Path) -> None:
    assert parse_rule({"decision": "deny"}, source="project").tool == "*"


def test_always_ask_marks_a_rule_unwaivable() -> None:
    rule = parse_rule(
        {"tool": "bash", "decision": "ask", "command": "^terraform apply", "always_ask": True},
        source="project",
    )
    assert rule.unwaivable is True


@pytest.mark.parametrize(
    ("entry", "fragment"),
    [
        ("a string", "must be an object"),
        ({"tool": "", "decision": "allow"}, "non-empty string"),
        ({"decision": "allow", "match": {"kind": "wat"}}, "unknown match kind"),
        ({"decision": "allow", "match": {"kind": "regex", "pattern": "(["}}, "not a valid regex"),
        ({"decision": "allow", "match": "nope"}, "'match' must be an object"),
        ({"decision": "allow", "match": {"kind": "exact", "value": 3}}, "string 'argument'"),
        ({"decision": "allow", "reason": 7}, "'reason' must be a string"),
        ({"decision": "ask", "always_ask": "yes"}, "must be true or false"),
    ],
)
def test_a_malformed_rule_raises_a_message_a_user_can_act_on(entry: object, fragment: str) -> None:
    with pytest.raises(ValueError, match=fragment.replace("(", r"\(").replace("[", r"\[")):
        parse_rule(entry, source="project")


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #


def test_the_settings_produce_a_ruleset_that_honours_the_configured_default(
    home: Path, cwd: Path
) -> None:
    write(cwd / PROJECT_SETTINGS, {"default_decision": "deny"})
    ruleset = load_settings(home=home, cwd=cwd).ruleset()
    assert ruleset.default is Decision.DENY
    assert ruleset.rules


def test_a_project_deny_rule_reaches_the_ruleset_with_its_provenance(home: Path, cwd: Path) -> None:
    write(
        cwd / PROJECT_SETTINGS,
        {
            "rules": [
                {
                    "tool": "bash",
                    "decision": "deny",
                    "command": r"^psql\b.*\bprod\b",
                    "reason": "never touch production from an agent",
                }
            ]
        },
    )
    settings = load_settings(home=home, cwd=cwd)
    rule = settings.rules_from("project")[0]
    assert isinstance(rule, Rule)
    assert "never touch production" in rule.describe()
    assert "from project" in rule.describe()
