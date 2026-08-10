"""The loader's failure modes, and the guarantee that the answer key stays behind."""
from __future__ import annotations

from pathlib import Path

import pytest
from evals_harness import write_manifest, write_task

from ronin.evals.task import (
    ManifestMismatch,
    NeedsNetwork,
    TaskError,
    check_manifest,
    load_manifest,
    load_suite,
    load_task,
    load_tasks,
    materialize,
)


def test_a_task_round_trips_from_toml(tmp_path: Path) -> None:
    root = write_task(
        tmp_path,
        "add-two",
        category="edit",
        prompt="make add return a sum",
        fixture={"app.py": "def add(a, b):\n    return a - b\n"},
        files_expected=["app.py"],
        extra_toml=(
            'max_turns = 8\ntimeout_seconds = 12.5\n'
            'tags = ["quick"]\nregression_gate = true\n'
        ),
    )
    loaded = load_task(root)
    assert loaded.id == "add-two"
    assert loaded.category == "edit"
    assert loaded.prompt == "make add return a sum"
    assert loaded.files_expected == ("app.py",)
    assert loaded.max_turns == 8
    assert loaded.timeout_seconds == 12.5
    assert loaded.tags == ("quick",)
    assert loaded.regression_gate is True
    assert loaded.needs_network is False


def test_a_task_may_be_loaded_from_the_toml_path_or_its_directory(tmp_path: Path) -> None:
    root = write_task(tmp_path, "t", fixture={"a.py": ""})
    assert load_task(root).id == load_task(root / "task.toml").id


@pytest.mark.parametrize(
    ("body", "key"),
    [
        ('category = "edit"\nprompt = "p"\n', "id"),
        ('id = "t"\nprompt = "p"\n', "category"),
        ('id = "t"\ncategory = "edit"\n', "prompt"),
        ('id = "t"\ncategory = "edit"\nprompt = "p"\nmax_turns = "eight"\n', "max_turns"),
        ('id = "t"\ncategory = "edit"\nprompt = "p"\nmax_turns = 0\n', "max_turns"),
        ('id = "t"\ncategory = "edit"\nprompt = "p"\ntimeout_seconds = "x"\n', "timeout_seconds"),
        (
            'id = "t"\ncategory = "edit"\nprompt = "p"\nfiles_expected = "app.py"\n',
            "files_expected",
        ),
        ('id = "t"\ncategory = "edit"\nprompt = "p"\ntags = [1]\n', "tags"),
        ('id = "t"\ncategory = "edit"\nprompt = "p"\nregression_gate = "yes"\n', "regression_gate"),
        ('id = 7\ncategory = "edit"\nprompt = "p"\n', "id"),
        ('id = ""\ncategory = "edit"\nprompt = "p"\n', "id"),
    ],
)
def test_a_malformed_task_names_the_file_and_the_offending_key(
    tmp_path: Path, body: str, key: str
) -> None:
    path = tmp_path / "task.toml"
    path.write_text(body, encoding="utf-8")
    with pytest.raises(TaskError) as caught:
        load_task(path)
    assert caught.value.key == key
    assert str(path) in str(caught.value)
    assert key in str(caught.value)


def test_invalid_toml_names_the_file_rather_than_raising_a_parse_error(tmp_path: Path) -> None:
    path = tmp_path / "task.toml"
    path.write_text('id = "t\ncategory = ', encoding="utf-8")
    with pytest.raises(TaskError) as caught:
        load_task(path)
    assert str(path) in str(caught.value)
    assert "not valid TOML" in str(caught.value)


def test_a_git_revision_without_a_url_is_rejected_naming_the_missing_half(
    tmp_path: Path,
) -> None:
    path = tmp_path / "task.toml"
    path.write_text(
        'id = "t"\ncategory = "repo"\nprompt = "p"\ngit_sha = "abc123"\n', encoding="utf-8"
    )
    with pytest.raises(TaskError) as caught:
        load_task(path)
    assert caught.value.key == "git_url"


def test_an_unknown_key_is_recorded_rather_than_rejected(tmp_path: Path) -> None:
    """A loader that refuses an unheard-of key makes every schema addition a breakage."""
    path = tmp_path / "task.toml"
    path.write_text(
        'id = "t"\ncategory = "edit"\nprompt = "p"\nfuture_field = 3\n', encoding="utf-8"
    )
    assert load_task(path).extra_keys == ("future_field",)


def test_tasks_load_in_a_stable_order_and_reject_duplicate_ids(tmp_path: Path) -> None:
    write_task(tmp_path, "b", category="edit")
    write_task(tmp_path, "a", category="edit")
    write_task(tmp_path, "c", category="ask")
    assert [t.id for t in load_tasks(tmp_path)] == ["c", "a", "b"]

    write_task(tmp_path, "a", category="ask")
    with pytest.raises(TaskError) as caught:
        load_tasks(tmp_path)
    assert caught.value.key == "id"
    assert "duplicate" in str(caught.value)


def test_a_task_toml_inside_a_fixture_is_not_loaded_as_a_sibling(tmp_path: Path) -> None:
    write_task(tmp_path, "outer", fixture={"task.toml": 'id = "inner"\n'})
    assert [t.id for t in load_tasks(tmp_path)] == ["outer"]


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", ["tasks", "task_ids", "ids"])
def test_the_manifest_accepts_every_spelling_of_its_id_list(tmp_path: Path, key: str) -> None:
    write_manifest(tmp_path, ["a", "b"], key=key)
    assert load_manifest(tmp_path).task_ids == ("a", "b")


def test_the_manifest_accepts_table_entries(tmp_path: Path) -> None:
    (tmp_path / "manifest.toml").write_text(
        '[[tasks]]\nid = "a"\n\n[[tasks]]\nid = "b"\n', encoding="utf-8"
    )
    assert load_manifest(tmp_path).task_ids == ("a", "b")


def test_a_manifest_with_no_id_list_names_the_key_it_wanted(tmp_path: Path) -> None:
    (tmp_path / "manifest.toml").write_text('count = 3\n', encoding="utf-8")
    with pytest.raises(TaskError) as caught:
        load_manifest(tmp_path)
    assert caught.value.key == "tasks"


def test_a_task_on_disk_but_not_in_the_manifest_is_an_error_naming_both(tmp_path: Path) -> None:
    write_task(tmp_path, "declared")
    write_task(tmp_path, "undeclared")
    manifest_path = write_manifest(tmp_path, ["declared"])
    with pytest.raises(ManifestMismatch) as caught:
        load_suite(tmp_path)
    message = str(caught.value)
    assert caught.value.missing_from_manifest == ("undeclared",)
    assert "undeclared" in message
    assert str(manifest_path) in message
    assert str(tmp_path) in message


def test_a_task_in_the_manifest_but_not_on_disk_is_an_error_naming_both(tmp_path: Path) -> None:
    write_task(tmp_path, "present")
    write_manifest(tmp_path, ["present", "deleted"])
    with pytest.raises(ManifestMismatch) as caught:
        load_suite(tmp_path)
    assert caught.value.missing_from_disk == ("deleted",)
    assert "deleted" in str(caught.value)


def test_an_agreeing_manifest_loads_the_suite(tmp_path: Path) -> None:
    write_task(tmp_path, "a")
    write_task(tmp_path, "b")
    write_manifest(tmp_path, ["a", "b"])
    assert [t.id for t in load_suite(tmp_path)] == ["a", "b"]
    check_manifest(load_manifest(tmp_path), load_tasks(tmp_path), suite_root=tmp_path)


def test_a_missing_manifest_is_an_error_by_default_and_waivable_explicitly(
    tmp_path: Path,
) -> None:
    write_task(tmp_path, "a")
    with pytest.raises(FileNotFoundError):
        load_suite(tmp_path)
    assert len(load_suite(tmp_path, require_manifest=False)) == 1


# --------------------------------------------------------------------------- #
# Materializing
# --------------------------------------------------------------------------- #


def test_the_solution_directory_never_reaches_the_workspace(tmp_path: Path) -> None:
    root = write_task(
        tmp_path / "suite",
        "leaky",
        fixture={"app.py": "broken\n", "pkg/mod.py": "x = 1\n"},
        solution={"app.py": "THE ANSWER\n"},
    )
    workspace = materialize(load_task(root), tmp_path / "ws")
    assert (workspace.root / "app.py").read_text(encoding="utf-8") == "broken\n"
    assert (workspace.root / "pkg" / "mod.py").exists()
    assert not (workspace.root / "solution").exists()
    assert "THE ANSWER" not in "".join(
        path.read_text(encoding="utf-8")
        for path in workspace.root.rglob("*")
        if path.is_file()
    )


def test_a_solution_nested_inside_the_fixture_is_also_skipped(tmp_path: Path) -> None:
    """Filtering by layout would be an accident; the filter is by name at any depth."""
    root = write_task(
        tmp_path / "suite",
        "nested",
        fixture={"a.py": "x\n", "deep/solution/answer.py": "ANSWER\n", "deep/keep.py": "y\n"},
    )
    workspace = materialize(load_task(root), tmp_path / "ws")
    assert (workspace.root / "deep" / "keep.py").exists()
    assert not (workspace.root / "deep" / "solution").exists()


def test_verify_sh_is_found_beside_task_toml_and_inside_the_fixture(tmp_path: Path) -> None:
    beside = write_task(tmp_path / "a", "beside", fixture={"x.py": ""}, verify="exit 0\n")
    inside = write_task(
        tmp_path / "b",
        "inside",
        fixture={"x.py": ""},
        verify="exit 0\n",
        verify_inside_fixture=True,
    )
    for root, dest in ((beside, tmp_path / "w1"), (inside, tmp_path / "w2")):
        workspace = materialize(load_task(root), dest)
        assert workspace.verify_script is not None
        assert workspace.verify_script.name == "verify.sh"
        assert workspace.verify_script.parent == workspace.root
        assert workspace.verifiable


def test_a_task_with_no_verify_script_is_materialized_without_one(tmp_path: Path) -> None:
    root = write_task(tmp_path, "bare", fixture={"x.py": ""})
    workspace = materialize(load_task(root), tmp_path / "ws")
    assert workspace.verify_script is None
    assert workspace.verifiable is False


def test_a_task_with_no_fixture_at_all_yields_an_empty_workspace(tmp_path: Path) -> None:
    root = write_task(tmp_path, "scratch", category="create")
    workspace = materialize(load_task(root), tmp_path / "ws")
    assert workspace.root.is_dir()
    assert workspace.files == ()


def test_a_named_fixture_that_does_not_exist_names_the_key(tmp_path: Path) -> None:
    root = write_task(tmp_path, "typo", fixture_dirname="fixure", fixture=None)
    with pytest.raises(TaskError) as caught:
        materialize(load_task(root), tmp_path / "ws")
    assert caught.value.key == "fixture"


def test_a_git_task_refuses_to_clone_and_says_what_it_needed(tmp_path: Path) -> None:
    path = tmp_path / "task.toml"
    path.write_text(
        'id = "swe-1"\ncategory = "repo"\nprompt = "p"\n'
        'git_sha = "deadbeef"\ngit_url = "https://example.invalid/r.git"\n',
        encoding="utf-8",
    )
    loaded = load_task(path)
    assert loaded.needs_network
    with pytest.raises(NeedsNetwork) as caught:
        materialize(loaded, tmp_path / "ws")
    assert caught.value.git_url == "https://example.invalid/r.git"
    assert "deadbeef" in str(caught.value)


def test_copied_bytes_are_identical_including_line_endings(tmp_path: Path) -> None:
    """Read as bytes: ``read_text`` translates newlines and would hide a CRLF change."""
    root = write_task(tmp_path / "suite", "crlf", fixture={})
    (root / "fixture").mkdir(parents=True, exist_ok=True)
    (root / "fixture" / "win.txt").write_bytes(b"one\r\ntwo\r\n")
    workspace = materialize(load_task(root), tmp_path / "ws")
    assert (workspace.root / "win.txt").read_bytes() == b"one\r\ntwo\r\n"
