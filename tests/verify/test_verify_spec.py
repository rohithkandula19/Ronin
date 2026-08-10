"""Detection must be evidence-based: no command without a reason, ever."""

from __future__ import annotations

from pathlib import Path

import pytest
from verify_harness import PYPROJECT_WITH_EVERYTHING

from ronin.verify.spec import (
    CommandKind,
    DetectedCommand,
    VerifySpec,
    detect_verify_spec,
    from_declaration,
    is_scopable,
    parse_command,
)


def test_an_empty_directory_yields_no_commands_at_all(tmp_path: Path) -> None:
    spec = detect_verify_spec(tmp_path)
    assert spec.empty
    assert spec.available == ()
    assert spec.missing == tuple(CommandKind)
    assert any("no verify command could be justified" in note for note in spec.notes)


def test_a_python_file_alone_does_not_conjure_pytest(tmp_path: Path) -> None:
    """The failure this whole module exists to prevent.

    A repo containing ``.py`` files but no pytest anywhere must NOT get a
    ``pytest`` command: it would fail on every run for a reason unrelated to the
    edit, and teach the model that verification is noise.
    """
    (tmp_path / "script.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n', encoding="utf-8")

    spec = detect_verify_spec(tmp_path)

    assert spec.test is None
    assert any("does not depend on pytest" in note for note in spec.notes)


def test_pytest_config_is_evidence_and_the_lockfile_supplies_the_prefix(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT_WITH_EVERYTHING, encoding="utf-8")
    (tmp_path / "uv.lock").write_text("# lock\n", encoding="utf-8")

    spec = detect_verify_spec(tmp_path)

    assert spec.test is not None
    assert spec.test.argv == ("uv", "run", "pytest")
    assert spec.test.source == "pyproject.toml"
    assert "ini_options" in spec.test.evidence
    assert "uv.lock" in spec.test.evidence
    assert spec.lint is not None and spec.lint.argv == ("uv", "run", "ruff", "check", ".")
    assert spec.typecheck is not None and spec.typecheck.argv == ("uv", "run", "mypy")


def test_a_declared_pytest_dependency_is_enough_without_a_config_table(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\noptional-dependencies = {}\n'
        '[dependency-groups]\ndev = ["pytest>=8"]\n',
        encoding="utf-8",
    )
    spec = detect_verify_spec(tmp_path)
    assert spec.test is not None
    assert spec.test.argv == ("pytest",)
    assert "declared dependency" in spec.test.evidence


def test_a_build_system_alone_does_not_justify_a_build_command(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n',
        encoding="utf-8",
    )
    spec = detect_verify_spec(tmp_path)
    assert spec.build is None
    assert any("would fail on a bare install" in note for note in spec.notes)


def test_a_declared_build_dependency_does_justify_one(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["hatchling"]\n[dependency-groups]\ndev = ["build"]\n',
        encoding="utf-8",
    )
    spec = detect_verify_spec(tmp_path)
    assert spec.build is not None
    assert spec.build.argv == ("python", "-m", "build")


def test_pyright_is_used_only_when_mypy_is_absent(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pyright]\nstrict = []\n", encoding="utf-8")
    assert detect_verify_spec(tmp_path).typecheck == DetectedCommand(
        argv=("pyright",),
        source="pyproject.toml",
        evidence="[tool.pyright] is configured",
    )


@pytest.mark.parametrize(
    ("lockfile", "expected"),
    [
        ("pnpm-lock.yaml", ("pnpm", "run", "test")),
        ("yarn.lock", ("yarn", "run", "test")),
        ("bun.lockb", ("bun", "run", "test")),
        ("package-lock.json", ("npm", "run", "test")),
        (None, ("npm", "run", "test")),
    ],
)
def test_the_node_runner_comes_from_the_lockfile(
    tmp_path: Path, lockfile: str | None, expected: tuple[str, ...]
) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "vitest run", "typecheck": "tsc --noEmit"}}', encoding="utf-8"
    )
    if lockfile is not None:
        (tmp_path / lockfile).write_text("", encoding="utf-8")

    spec = detect_verify_spec(tmp_path)

    assert spec.test is not None and spec.test.argv == expected
    assert spec.typecheck is not None and spec.typecheck.argv[-1] == "typecheck"


def test_a_package_json_without_scripts_is_reported_not_guessed(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "x"}', encoding="utf-8")
    spec = detect_verify_spec(tmp_path)
    assert spec.test is None
    assert any("no scripts block" in note for note in spec.notes)


def test_makefile_targets_are_read_from_column_zero_only(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text(
        "PYTHON := python3\n"
        ".PHONY: test lint\n"
        "test:\n"
        "\tcurl http://localhost:8080\n"
        "\t$(PYTHON) -m pytest\n"
        "lint:\n"
        "\truff check .\n",
        encoding="utf-8",
    )
    spec = detect_verify_spec(tmp_path)

    assert spec.test is not None and spec.test.argv == ("make", "test")
    assert spec.lint is not None and spec.lint.argv == ("make", "lint")
    # `curl http://localhost:8080` is an indented recipe line, not a target, and
    # `PYTHON := python3` is an assignment.
    assert spec.typecheck is None
    assert spec.build is None


def test_justfile_recipes_are_detected(tmp_path: Path) -> None:
    (tmp_path / "justfile").write_text(
        "test:\n    pytest\n\ntypecheck:\n    mypy .\n", encoding="utf-8"
    )
    spec = detect_verify_spec(tmp_path)
    assert spec.test is not None and spec.test.argv == ("just", "test")
    assert spec.typecheck is not None and spec.typecheck.argv == ("just", "typecheck")
    assert "recipe" in spec.test.evidence


def test_cargo_infers_the_builtin_subcommands_and_refuses_to_guess_clippy(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n', encoding="utf-8")
    spec = detect_verify_spec(tmp_path)
    assert spec.test is not None and spec.test.argv == ("cargo", "test")
    assert spec.typecheck is not None and spec.typecheck.argv == ("cargo", "check")
    assert spec.lint is None
    assert any("clippy" in note for note in spec.notes)


def test_go_uses_vet_as_the_linter_and_declares_no_separate_typecheck(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/x\n\ngo 1.22\n", encoding="utf-8")
    spec = detect_verify_spec(tmp_path)
    assert spec.test is not None and spec.test.argv == ("go", "test", "./...")
    assert spec.lint is not None and spec.lint.argv == ("go", "vet", "./...")
    assert spec.typecheck is None
    assert any("`go build` is the type checker" in note for note in spec.notes)


def test_a_declaration_beats_every_inferred_source(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT_WITH_EVERYTHING, encoding="utf-8")
    (tmp_path / "Makefile").write_text("test:\n\tpytest\n", encoding="utf-8")

    spec = detect_verify_spec(tmp_path, declared={"test": "pytest -x -q tests/"})

    assert spec.test is not None
    assert spec.test.argv == ("pytest", "-x", "-q", "tests/")
    assert spec.test.source == "RONIN.md"
    # The gaps the declaration left are still filled by inference.
    assert spec.lint is not None and spec.lint.argv[-1] == "."


def test_pyproject_beats_the_makefile_in_the_documented_order(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT_WITH_EVERYTHING, encoding="utf-8")
    (tmp_path / "Makefile").write_text("test:\n\tpytest\nbuild:\n\techo\n", encoding="utf-8")

    spec = detect_verify_spec(tmp_path)

    assert spec.test is not None and spec.test.argv[-1] == "pytest"
    # …and the Makefile still fills what pyproject could not justify.
    assert spec.build is not None and spec.build.argv == ("make", "build")


def test_a_nested_verify_declaration_is_accepted(tmp_path: Path) -> None:
    spec = from_declaration({"verify": {"test": ["pytest", "-q"], "lint": "ruff check"}})
    assert spec.test is not None and spec.test.argv == ("pytest", "-q")
    assert spec.lint is not None and spec.lint.argv == ("ruff", "check")


def test_an_unusable_declared_value_is_dropped_with_a_note() -> None:
    spec = from_declaration({"test": 3, "lint": ["ruff", 7]})
    assert spec.test is None
    assert spec.lint is None
    assert len(spec.notes) == 2
    assert all("ignored" in note for note in spec.notes)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("pytest -q", ("pytest", "-q")),
        ('pytest -k "a b"', ("pytest", "-k", "a b")),
        (["pytest", "-q"], ("pytest", "-q")),
        ("", None),
        (3, None),
        (["pytest", 3], None),
        ({"a": 1}, None),
    ],
)
def test_parse_command_refuses_to_coerce_nonsense(value: object, expected: object) -> None:
    assert parse_command(value) == expected


def test_with_args_drops_a_trailing_dot_so_scoping_actually_narrows() -> None:
    command = DetectedCommand(argv=("ruff", "check", "."), source="s", evidence="e")
    assert command.with_args("a.py").argv == ("ruff", "check", "a.py")


@pytest.mark.parametrize(
    ("argv", "scopable"),
    [
        (("pytest",), True),
        (("uv", "run", "pytest"), True),
        (("make", "test"), False),
        (("npm", "run", "test"), False),
        (("just", "test"), False),
        (("uv", "run", "nox", "-s", "tests"), False),
    ],
)
def test_is_scopable_knows_which_commands_survive_an_appended_path(
    argv: tuple[str, ...], scopable: bool
) -> None:
    assert is_scopable(DetectedCommand(argv=argv, source="s", evidence="e")) is scopable


def test_explain_names_the_source_and_the_evidence_for_doctor(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(PYPROJECT_WITH_EVERYTHING, encoding="utf-8")
    rendered = detect_verify_spec(tmp_path).explain()
    assert "from pyproject.toml: [tool.ruff] is configured" in rendered
    assert "build     — not detected" in rendered


def test_a_malformed_pyproject_is_a_note_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project\nname =", encoding="utf-8")
    spec = detect_verify_spec(tmp_path)
    assert spec.empty
    assert any("could not be parsed as TOML" in note for note in spec.notes)


def test_a_command_without_evidence_cannot_be_constructed() -> None:
    with pytest.raises(ValueError, match="evidence is required"):
        DetectedCommand(argv=("pytest",), source="pyproject.toml", evidence="")


def test_the_spec_reports_available_and_missing_in_cheapest_first_order() -> None:
    spec = VerifySpec(
        lint=DetectedCommand(argv=("ruff",), source="s", evidence="e"),
        test=DetectedCommand(argv=("pytest",), source="s", evidence="e"),
    )
    assert spec.available == (CommandKind.LINT, CommandKind.TEST)
    assert spec.missing == (CommandKind.TYPECHECK, CommandKind.BUILD)
