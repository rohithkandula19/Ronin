"""RONIN.md loading, precedence, `remember`, and the bootstrap draft.

The bootstrap tests are all variations on one requirement: it must never invent a
command. So each one asserts both halves — the command that *was* found, and that
an absent one is reported as absent rather than guessed.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from context_harness import plant

from ronin.context.memory import (
    REMEMBERED_HEADING,
    RONIN_FILENAME,
    CommandKind,
    MemoryScope,
    discover_commands,
    find_repo_root,
    load_memory,
    memory_paths,
    propose_bootstrap,
    remember,
)

TODAY = date(2026, 8, 9)


def repo(tmp_path: Path, tree: dict[str, str]) -> Path:
    return plant(tmp_path / "repo", tree)


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #


def test_the_repo_root_is_the_nearest_ancestor_with_a_dot_git(tmp_path: Path) -> None:
    root = repo(tmp_path, {"pkg/sub/a.py": "x = 1\n"})
    assert find_repo_root(root / "pkg" / "sub") == root
    assert find_repo_root(root / "pkg" / "sub" / "a.py") == root


def test_a_dot_git_file_counts_as_a_boundary(tmp_path: Path) -> None:
    """A worktree or submodule writes `.git` as a file, not a directory."""
    root = tmp_path / "wt"
    (root / "pkg").mkdir(parents=True)
    (root / ".git").write_bytes(b"gitdir: /elsewhere\n")
    assert find_repo_root(root / "pkg") == root


def test_outside_a_repo_there_is_no_root(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert find_repo_root(plain) is None


def test_the_walk_stops_at_the_git_boundary(tmp_path: Path) -> None:
    """Without the stop, a repo cloned inside a home directory inherits its file."""
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / RONIN_FILENAME).write_bytes(b"## Build\n- outer, must not be loaded\n")
    root = repo(outer, {RONIN_FILENAME: "## Build\n- inner\n", "pkg/a.py": "x = 1\n"})
    paths = memory_paths(root / "pkg")
    assert all(str(outer / RONIN_FILENAME) != str(path) for path in paths)
    memory = load_memory(root / "pkg")
    assert "must not be loaded" not in memory.render()


def test_candidates_are_ordered_global_then_outermost_to_innermost(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a/b/c/keep.py": "x = 1\n"})
    home = tmp_path / "home"
    paths = memory_paths(root / "a" / "b" / "c", home=home, root=root)
    relative = [str(path) for path in paths]
    assert relative[0] == str(home / ".ronin" / RONIN_FILENAME)
    assert relative[1:] == [
        str(root / RONIN_FILENAME),
        str(root / "a" / RONIN_FILENAME),
        str(root / "a" / "b" / RONIN_FILENAME),
        str(root / "a" / "b" / "c" / RONIN_FILENAME),
    ]


# --------------------------------------------------------------------------- #
# loading + precedence
# --------------------------------------------------------------------------- #


def test_the_most_local_file_is_rendered_last_so_it_wins(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            RONIN_FILENAME: "## Test\n- run `pytest`\n",
            "pkg/" + RONIN_FILENAME: "## Test\n- inside pkg run `pytest pkg -x`\n",
        },
    )
    home = tmp_path / "home"
    (home / ".ronin").mkdir(parents=True)
    (home / ".ronin" / RONIN_FILENAME).write_bytes(b"## Style\n- never force-push\n")
    rendered = load_memory(root / "pkg", home=home, root=root).render()
    assert rendered.index("never force-push") < rendered.index("run `pytest`")
    assert rendered.index("run `pytest`") < rendered.index("pytest pkg -x")
    assert "the LAST one wins" in rendered


def test_every_section_is_labelled_with_where_it_came_from(tmp_path: Path) -> None:
    root = repo(tmp_path, {RONIN_FILENAME: "- root rule\n", "pkg/" + RONIN_FILENAME: "- pkg rule\n"})
    memory = load_memory(root / "pkg", root=root)
    assert [file.path for file in memory.files] == [RONIN_FILENAME, f"pkg/{RONIN_FILENAME}"]
    rendered = memory.render()
    assert f"## project — pkg/{RONIN_FILENAME}" in rendered


def test_global_memory_is_labelled_global(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    home = tmp_path / "home"
    (home / ".ronin").mkdir(parents=True)
    (home / ".ronin" / RONIN_FILENAME).write_bytes(b"- global rule\n")
    memory = load_memory(root, home=home, root=root)
    assert [file.scope for file in memory.files] == [MemoryScope.GLOBAL]
    assert "## global —" in memory.render()


def test_omitting_home_never_reads_the_real_one(tmp_path: Path) -> None:
    """The default must not depend on whose machine the tests run on."""
    root = repo(tmp_path, {RONIN_FILENAME: "- project only\n"})
    memory = load_memory(root, root=root)
    assert [file.scope for file in memory.files] == [MemoryScope.PROJECT]


def test_no_memory_files_renders_nothing_at_all(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    memory = load_memory(root, root=root)
    assert memory.is_empty
    assert memory.render() == ""


def test_a_whitespace_only_memory_file_is_treated_as_empty(tmp_path: Path) -> None:
    root = repo(tmp_path, {RONIN_FILENAME: "\n   \n"})
    assert load_memory(root, root=root).is_empty


def test_undecodable_bytes_do_not_crash_loading(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    (root / RONIN_FILENAME).write_bytes(b"- caf\xff rule\n")
    assert "rule" in load_memory(root, root=root).render()


# --------------------------------------------------------------------------- #
# remember
# --------------------------------------------------------------------------- #


def test_remember_creates_the_file_at_the_repo_root_when_none_exists(tmp_path: Path) -> None:
    root = repo(tmp_path, {"pkg/a.py": "x = 1\n"})
    target = remember("always run make lint", cwd=root / "pkg", root=root, today=TODAY)
    assert target == root / RONIN_FILENAME
    text = target.read_bytes().decode()
    assert f"{REMEMBERED_HEADING} 2026-08-09" in text
    assert "- always run make lint" in text


def test_remember_appends_to_the_nearest_existing_file(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {RONIN_FILENAME: "- root\n", "pkg/" + RONIN_FILENAME: "## Build\n- pkg\n"},
    )
    target = remember("use pnpm here", cwd=root / "pkg", root=root, today=TODAY)
    assert target == root / "pkg" / RONIN_FILENAME
    assert "- use pnpm here" in target.read_bytes().decode()
    assert "use pnpm here" not in (root / RONIN_FILENAME).read_bytes().decode()


def test_remember_is_idempotent_on_an_exact_duplicate(tmp_path: Path) -> None:
    root = repo(tmp_path, {RONIN_FILENAME: "- existing\n"})
    remember("no force push", cwd=root, root=root, today=TODAY)
    first = (root / RONIN_FILENAME).read_bytes()
    remember("no force push", cwd=root, root=root, today=TODAY)
    assert (root / RONIN_FILENAME).read_bytes() == first


def test_remembering_the_same_rule_on_a_later_day_does_not_duplicate_it(
    tmp_path: Path,
) -> None:
    root = repo(tmp_path, {RONIN_FILENAME: "- existing\n"})
    remember("no force push", cwd=root, root=root, today=TODAY)
    remember("no force push", cwd=root, root=root, today=date(2026, 9, 1))
    text = (root / RONIN_FILENAME).read_bytes().decode()
    assert text.count("- no force push") == 1


def test_two_rules_on_the_same_day_share_one_section(tmp_path: Path) -> None:
    root = repo(tmp_path, {RONIN_FILENAME: "## Build\n- make\n"})
    remember("first rule", cwd=root, root=root, today=TODAY)
    remember("second rule", cwd=root, root=root, today=TODAY)
    text = (root / RONIN_FILENAME).read_bytes().decode()
    assert text.count(REMEMBERED_HEADING) == 1
    assert text.index("- first rule") < text.index("- second rule")
    assert text.index("## Build") < text.index(REMEMBERED_HEADING)


def test_a_rule_lands_in_the_existing_section_not_after_a_later_heading(
    tmp_path: Path,
) -> None:
    root = repo(
        tmp_path,
        {
            RONIN_FILENAME: (
                f"{REMEMBERED_HEADING} 2026-08-09\n\n- earlier rule\n\n## Gotchas\n- flaky\n"
            )
        },
    )
    remember("new rule", cwd=root, root=root, today=TODAY)
    text = (root / RONIN_FILENAME).read_bytes().decode()
    assert text.index("- new rule") < text.index("## Gotchas")


def test_remember_preserves_crlf_line_endings(tmp_path: Path) -> None:
    """Rewriting a CRLF file with LF shows up as a whole-file diff in the user's repo."""
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    (root / RONIN_FILENAME).write_bytes(b"## Build\r\n- make\r\n")
    remember("crlf safe", cwd=root, root=root, today=TODAY)
    raw = (root / RONIN_FILENAME).read_bytes()
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_a_new_file_is_written_with_lf(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    remember("lf default", cwd=root, root=root, today=TODAY)
    assert b"\r" not in (root / RONIN_FILENAME).read_bytes()


def test_remembering_nothing_is_an_error(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    with pytest.raises(ValueError, match="something to remember"):
        remember("   ", cwd=root, root=root)


def test_a_remembered_rule_is_loaded_back_as_memory(tmp_path: Path) -> None:
    root = repo(tmp_path, {"a.py": "x = 1\n"})
    remember("run make lint", cwd=root, root=root, today=TODAY)
    assert "run make lint" in load_memory(root, root=root).render()


# --------------------------------------------------------------------------- #
# bootstrap
# --------------------------------------------------------------------------- #


def test_package_json_scripts_are_found_with_the_right_runner(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "package.json": '{"scripts": {"build": "tsc", "test": "vitest", "lint": "eslint ."}}',
            "pnpm-lock.yaml": "lockfileVersion: 9\n",
        },
    )
    found = {command.command: command for command in discover_commands(root)}
    assert "pnpm build" in found
    assert "pnpm test" in found
    assert found["pnpm test"].kind is CommandKind.TEST
    assert found["pnpm test"].evidence == "package.json scripts.test"


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        ({"package.json": '{"scripts": {"test": "x"}}'}, "npm run test"),
        (
            {"package.json": '{"scripts": {"test": "x"}}', "yarn.lock": ""},
            "yarn test",
        ),
        (
            {"package.json": '{"packageManager": "pnpm@9.0.0", "scripts": {"test": "x"}}'},
            "pnpm test",
        ),
    ],
)
def test_the_node_runner_follows_the_declaration_then_the_lockfile(
    tmp_path: Path, files: dict[str, str], expected: str
) -> None:
    root = repo(tmp_path, files)
    assert expected in {command.command for command in discover_commands(root)}


def test_a_malformed_package_json_is_ignored_not_fatal(tmp_path: Path) -> None:
    root = repo(tmp_path, {"package.json": "{not json"})
    assert discover_commands(root) == ()


def test_pyproject_tool_sections_are_evidence(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "pyproject.toml": (
                "[tool.pytest.ini_options]\nasyncio_mode = 'auto'\n\n"
                "[tool.ruff]\nline-length = 100\n\n[tool.mypy]\nstrict = true\n"
            )
        },
    )
    found = {command.command for command in discover_commands(root)}
    assert {"pytest", "ruff check", "mypy"} <= found


def test_a_dev_dependency_group_is_evidence(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {"pyproject.toml": '[dependency-groups]\ndev = ["mypy>=1.11", "ruff>=0.6"]\n'},
    )
    found = {command.command: command.evidence for command in discover_commands(root)}
    assert "mypy" in found
    assert "dependency-groups" in found["mypy"]


def test_makefile_and_justfile_targets_are_found(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "Makefile": ".PHONY: test\ntest:\n\tpytest\n\nlint:\n\truff check\n\nCC=gcc\n",
            "justfile": "fmt:\n\truff format\n\nbuild target:\n\techo hi\n",
        },
    )
    found = {command.command for command in discover_commands(root)}
    assert {"make test", "make lint", "just fmt", "just build"} <= found
    assert "make CC" not in found


def test_a_cargo_manifest_yields_build_and_test(tmp_path: Path) -> None:
    root = repo(tmp_path, {"Cargo.toml": '[package]\nname = "x"\nversion = "0.1.0"\n'})
    found = {command.command: command.evidence for command in discover_commands(root)}
    assert set(found) == {"cargo build", "cargo test"}
    assert all("Cargo.toml" in evidence for evidence in found.values())


def test_a_cargo_toml_with_no_package_or_workspace_yields_nothing(tmp_path: Path) -> None:
    root = repo(tmp_path, {"Cargo.toml": "[dependencies]\nserde = '1'\n"})
    assert discover_commands(root) == ()


def test_workflow_run_steps_are_found_including_block_scalars(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            ".github/workflows/ci.yml": (
                "jobs:\n"
                "  test:\n"
                "    steps:\n"
                "      - run: uv run pytest -q\n"
                "      - name: lint\n"
                "        run: |\n"
                "          uv run ruff check .\n"
                "          uv run mypy src\n"
                "      - uses: actions/checkout@v4\n"
            )
        },
    )
    found = {command.command: command for command in discover_commands(root)}
    assert "uv run pytest -q" in found
    assert found["uv run pytest -q"].kind is CommandKind.TEST
    assert "uv run ruff check ." in found
    assert found["uv run ruff check ."].kind is CommandKind.LINT
    assert "uv run mypy src" in found
    assert "actions/checkout@v4" not in found
    assert ".github/workflows/ci.yml" in found["uv run pytest -q"].evidence


def test_an_absent_test_command_is_reported_as_absent(tmp_path: Path) -> None:
    """The requirement that keeps a drafted RONIN.md trustworthy."""
    root = repo(tmp_path, {"README.md": "# nothing to run here\n"})
    draft = propose_bootstrap(root)
    assert "## Test" in draft
    assert "none found: no test command appears in" in draft
    assert "pytest" not in draft
    assert "make " not in draft


def test_the_draft_carries_evidence_for_every_command_it_lists(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "package.json": '{"scripts": {"build": "tsc"}}',
            "Makefile": "test:\n\tpytest\n",
        },
    )
    draft = propose_bootstrap(root)
    for line in draft.splitlines():
        if line.startswith("- `"):
            assert "<!-- found in" in line, line
    assert "`npm run build`" in draft
    assert "`make test`" in draft


def test_the_draft_lists_what_it_inspected(tmp_path: Path) -> None:
    root = repo(tmp_path, {"package.json": "{}"})
    draft = propose_bootstrap(root)
    assert "## Inspected" in draft
    assert "- package.json: found" in draft
    assert "- pyproject.toml: missing" in draft


def test_the_draft_is_only_returned_and_never_written(tmp_path: Path) -> None:
    root = repo(tmp_path, {"package.json": '{"scripts": {"test": "x"}}'})
    propose_bootstrap(root)
    assert not (root / RONIN_FILENAME).exists()


def test_discovery_is_deterministic(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "package.json": '{"scripts": {"build": "tsc", "test": "vitest", "lint": "eslint"}}',
            "Makefile": "test:\n\tpytest\nlint:\n\truff check\n",
            "pyproject.toml": "[tool.ruff]\nline-length = 100\n",
        },
    )
    assert discover_commands(root) == discover_commands(root)
    assert propose_bootstrap(root) == propose_bootstrap(root)


def test_one_command_is_not_listed_twice_under_two_evidences(tmp_path: Path) -> None:
    root = repo(
        tmp_path,
        {
            "pyproject.toml": (
                "[tool.pytest.ini_options]\nx = 1\n\n"
                '[dependency-groups]\ndev = ["pytest>=8"]\n'
            )
        },
    )
    commands = [command.command for command in discover_commands(root)]
    assert commands.count("pytest") == 1
