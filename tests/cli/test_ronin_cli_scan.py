"""``ronin scan`` — the walk, the git seams, the render, and the exit code.

The exit code is the product here, not a detail of it: a pre-commit hook and a CI step
both read the status and neither reads the text, so `--quiet` printing nothing and
still refusing is the feature. Three codes, three meanings, and the third is the one
worth having tests for — **2 is "could not look", which is not 0.** A scanner that
reports a clean tree because git was missing is worse than no scanner, because somebody
believed it.

Every git call is an injected seam (`read_diff`, `staged`), so the failure paths are
exercised without uninstalling git, and the walk runs against a `tmp_path` tree. No
subprocess, no network, no repository.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ronin.cli.main import Command, Options, Usage, main, parse
from ronin.cli.scan import (
    MAX_FILE_BYTES,
    ScanOptions,
    git_diff,
    git_staged,
    ignore_names,
    readable,
    render,
    run_scan,
    walk_tree,
)
from ronin.safety.credentials import Finding

LIVE = "ghp_" + "g5Kd8Wq2LzNb7XcVaTeRyUiOpMnBhG"
AWS = "AKIAQ7RWZP2MLN4KXTBV"


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


# --------------------------------------------------------------------------- #
# what the walk reads, and what it refuses to
# --------------------------------------------------------------------------- #


def test_a_key_in_the_tree_is_found_and_located(tmp_path: Path) -> None:
    _tree(tmp_path, {"app/settings.py": f'TOKEN = "{LIVE}"\n'})
    code, out, err = run_scan(ScanOptions(root=tmp_path))
    assert code == 1 and err == ""
    assert "app/settings.py:1" in out
    assert "github-pat" in out
    assert LIVE not in out, "the report must never be another copy of the key"


def test_a_clean_tree_says_so_and_exits_zero(tmp_path: Path) -> None:
    _tree(tmp_path, {"app.py": "print('hello')\n"})
    code, out, _ = run_scan(ScanOptions(root=tmp_path))
    assert code == 0
    assert "no secrets found in the working tree" in out


def test_vendored_and_build_directories_are_not_somebody_elses_problem(tmp_path: Path) -> None:
    """A hit inside `node_modules` is a report about a dependency's repository."""
    _tree(
        tmp_path,
        {
            "node_modules/pkg/index.js": f'const t = "{LIVE}";\n',
            "dist/bundle.js": f'const t = "{LIVE}";\n',
            ".venv/lib/x.py": f'T = "{LIVE}"\n',
        },
    )
    assert run_scan(ScanOptions(root=tmp_path))[0] == 0


def test_a_gitignored_directory_name_is_pruned(tmp_path: Path) -> None:
    _tree(tmp_path, {".gitignore": "generated/\n", "generated/keys.py": f'T = "{LIVE}"\n'})
    assert run_scan(ScanOptions(root=tmp_path))[0] == 0


def test_a_glob_in_an_ignore_file_is_left_alone_rather_than_approximated(tmp_path: Path) -> None:
    """The conservative half of name-level ignore handling.

    `build-*/` cannot be honoured at the name level, and guessing at it would mean
    skipping directories nobody asked to skip — which for this tool is missing the key
    in one. So it is not applied, and the scan reads more than git would.
    """
    _tree(tmp_path, {".gitignore": "build-*/\n", "build-x/k.py": f'T = "{LIVE}"\n'})
    assert "build-x" not in ignore_names(tmp_path)
    assert run_scan(ScanOptions(root=tmp_path))[0] == 1


def test_a_negation_and_a_comment_in_an_ignore_file_add_nothing(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_text("# a note\n!keep\n\n", encoding="utf-8")
    names = ignore_names(tmp_path)
    assert "keep" not in names and "# a note" not in names and "!keep" not in names


def test_a_missing_ignore_file_is_not_an_error(tmp_path: Path) -> None:
    assert ignore_names(tmp_path)  # the builtin set, and no exception


def test_a_binary_file_is_skipped_before_its_bytes_become_a_string(tmp_path: Path) -> None:
    path = tmp_path / "blob.json"
    path.write_bytes(b"\x00\x01" + LIVE.encode())
    assert readable(path) is None


def test_an_oversized_file_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "huge.json"
    path.write_text("x" * (MAX_FILE_BYTES + 1), encoding="utf-8")
    assert readable(path) is None


def test_an_unknown_suffix_is_not_read(tmp_path: Path) -> None:
    path = tmp_path / "photo.png"
    path.write_text(LIVE, encoding="utf-8")
    assert readable(path) is None


def test_an_extensionless_dotfile_is_read_because_that_is_where_keys_live(
    tmp_path: Path,
) -> None:
    """`.env` has no suffix as far as `Path.suffix` is concerned, and it is the single
    likeliest file in a repository to contain a live key."""
    _tree(tmp_path, {"config/.env": f"GITHUB_TOKEN={LIVE}\n"})
    assert run_scan(ScanOptions(root=tmp_path))[0] == 1


def test_an_unreadable_file_is_skipped_rather_than_raised(tmp_path: Path) -> None:
    path = tmp_path / "gone.py"
    assert readable(path) is None  # never created


def test_the_walk_yields_paths_relative_to_the_root(tmp_path: Path) -> None:
    _tree(tmp_path, {"a/b/c.py": "x = 1\n"})
    assert [name for name, _ in walk_tree(tmp_path)] == [str(Path("a/b/c.py"))]


# --------------------------------------------------------------------------- #
# "could not look" is not "nothing there"
# --------------------------------------------------------------------------- #


def test_history_without_git_refuses_instead_of_reporting_clean(tmp_path: Path) -> None:
    """The failure this tool must not have, as a test.

    `git_diff` returns None for "no git" and for "not a repository". Folding that into
    an empty diff would render as `no secrets found in git history` and exit 0 — a
    green CI step on a repository nobody scanned.
    """
    code, out, err = run_scan(ScanOptions(root=tmp_path, history=True), read_diff=lambda *_: None)
    assert code == 2
    assert out == ""
    assert "not a clean result" in err


def test_staged_without_git_refuses_the_same_way(tmp_path: Path) -> None:
    code, out, err = run_scan(ScanOptions(root=tmp_path, staged=True), staged=lambda _: None)
    assert code == 2 and out == "" and "not a clean result" in err


def test_a_repository_clean_today_can_still_be_leaking(tmp_path: Path) -> None:
    """Why `--history` exists. The working tree finds nothing; the history does."""
    _tree(tmp_path, {"app/settings.py": 'TOKEN = os.environ["TOKEN"]\n'})
    diff = (
        "commit 4f2c1ab9d3e5b7a8c0d1e2f3a4b5c6d7e8f9a0b1\n"
        "+++ b/app/settings.py\n"
        "@@ -0,0 +1,1 @@\n"
        f'+TOKEN = "{LIVE}"\n'
    )

    assert run_scan(ScanOptions(root=tmp_path))[0] == 0

    code, out, _ = run_scan(ScanOptions(root=tmp_path, history=True), read_diff=lambda *_: diff)
    assert code == 1
    assert "4f2c1ab9d3" in out, "the commit is the actionable part of a history finding"
    assert LIVE not in out


def test_history_flags_are_passed_through_to_git(tmp_path: Path) -> None:
    seen: list[tuple[Path, int, str]] = []

    def spy(root: Path, max_commits: int, since: str) -> str:
        seen.append((root, max_commits, since))
        return ""

    run_scan(
        ScanOptions(root=tmp_path, history=True, max_commits=25, since="3 months ago"),
        read_diff=spy,
    )
    assert seen == [(tmp_path, 25, "3 months ago")]


def test_staged_scans_only_what_is_staged(tmp_path: Path) -> None:
    _tree(tmp_path, {"staged.py": f'T = "{LIVE}"\n', "unstaged.py": f'T = "{AWS}"\n'})
    code, out, _ = run_scan(ScanOptions(root=tmp_path, staged=True), staged=lambda _: ["staged.py"])
    assert code == 1
    assert "staged.py:1" in out and "unstaged.py" not in out


def test_a_staged_path_that_no_longer_exists_is_skipped(tmp_path: Path) -> None:
    """A rename or a delete leaves a staged name with nothing behind it."""
    code, _, err = run_scan(
        ScanOptions(root=tmp_path, staged=True), staged=lambda _: ["deleted.py"]
    )
    assert code == 0 and err == ""


def test_a_staged_path_inside_a_pruned_directory_is_skipped(tmp_path: Path) -> None:
    _tree(tmp_path, {"node_modules/pkg/i.js": f'const t = "{LIVE}";\n'})
    code, _, _ = run_scan(
        ScanOptions(root=tmp_path, staged=True),
        staged=lambda _: [str(Path("node_modules/pkg/i.js"))],
    )
    assert code == 0


# --------------------------------------------------------------------------- #
# the report
# --------------------------------------------------------------------------- #


def test_quiet_prints_nothing_and_still_refuses(tmp_path: Path) -> None:
    """What a pre-commit hook uses: the status without the noise."""
    _tree(tmp_path, {"k.py": f'T = "{LIVE}"\n'})
    assert run_scan(ScanOptions(root=tmp_path, quiet=True)) == (1, "", "")
    assert run_scan(ScanOptions(root=tmp_path.parent / "empty", quiet=True))[1] == ""


def test_json_output_is_parseable_and_carries_no_value(tmp_path: Path) -> None:
    _tree(tmp_path, {"k.py": f'T = "{LIVE}"\n', "d/j.tf": f'k = "{AWS}"\n'})
    code, out, _ = run_scan(ScanOptions(root=tmp_path, as_json=True))
    payload: dict[str, Any] = json.loads(out)
    assert code == 1
    assert payload["count"] == 2
    assert payload["scope"] == "the working tree"
    assert {finding["kind"] for finding in payload["findings"]} == {"github-pat", "aws-akid"}
    assert LIVE not in out


def test_the_report_says_what_to_do_next(tmp_path: Path) -> None:
    """Rotation before deletion — a key that was committed is disclosed either way."""
    _tree(tmp_path, {"k.py": f'T = "{LIVE}"\n'})
    out = run_scan(ScanOptions(root=tmp_path))[1]
    assert "Rotate" in out
    assert "--history" in out
    assert "ronin:allow-secret" in out


def test_the_report_counts_files_not_just_findings() -> None:
    findings = [
        Finding(path="a.py", line=1, kind="aws-akid", hint="aws-akid"),
        Finding(path="a.py", line=9, kind="github-pat", hint="ghp_…aaaa"),
    ]
    body = render(findings, scope="the working tree")
    assert "2 potential secrets" in body
    assert "across 1 file" in body


def test_one_finding_is_singular() -> None:
    body = render(
        [Finding(path="a.py", line=1, kind="aws-akid", hint="aws-akid")],
        scope="the working tree",
    )
    assert "1 potential secret in" in body


def test_history_and_working_tree_findings_render_through_one_path() -> None:
    """v1 had a second bespoke loop for history that had drifted to another layout."""
    tree = render([Finding(path="a.py", line=1, kind="jwt", hint="eyJh…B92K")], scope="s")
    history = render(
        [Finding(path="a.py", line=1, kind="jwt", hint="eyJh…B92K", commit="abcdef1234567")],
        scope="s",
    )
    assert "a.py:1  jwt  (eyJh…B92K)" in tree
    assert "abcdef1234 a.py:1  jwt  (eyJh…B92K)" in history


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #


def test_scan_parses_into_options(tmp_path: Path) -> None:
    options = parse(["scan", "--root", str(tmp_path)])
    assert isinstance(options, Options)
    assert options.command is Command.SCAN
    assert options.scan == ScanOptions(root=tmp_path)


def test_history_and_staged_are_alternatives_not_a_combination(tmp_path: Path) -> None:
    """Two different things to read. Resolving it by declaration order would mean the
    flag the user typed second silently did nothing."""
    refused = parse(["scan", "--history", "--staged"])
    assert isinstance(refused, Usage)
    assert "Pick one" in refused.message


def test_a_positional_argument_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    """`ronin scan src/` looks like a path-taking command to anybody who has used
    another scanner. Sweeping the whole tree instead is a clean report about the wrong
    thing."""
    refused = parse(["scan", "src"])
    assert isinstance(refused, Usage)
    assert "--root" in refused.message


@pytest.mark.parametrize("flag", [["--since", "1 week ago"], ["--max-commits", "10"]])
def test_a_history_flag_without_history_is_refused(flag: list[str], tmp_path: Path) -> None:
    refused = parse(["scan", *flag])
    assert isinstance(refused, Usage)
    assert "--history" in refused.message


def test_a_negative_commit_cap_is_refused(tmp_path: Path) -> None:
    refused = parse(["scan", "--history", "--max-commits", "-1"])
    assert isinstance(refused, Usage)


def test_output_format_json_reaches_the_scan_options(tmp_path: Path) -> None:
    options = parse(["scan", "--output-format", "json"])
    assert isinstance(options, Options)
    assert options.scan is not None and options.scan.as_json


def test_scan_is_in_the_help_so_somebody_can_find_it(capsys: Any) -> None:
    assert main(["--help"]) == 0
    assert "scan [--history|--staged]" in capsys.readouterr().out


def test_scan_runs_end_to_end_without_writing_a_workspace(tmp_path: Path, capsys: Any) -> None:
    """Read-only, and before the first-run wizard.

    Somebody asking whether their tree is leaking must not get `.ronin/` written into
    it as the answer — the same rule `repo` follows, and the reason both dispatch ahead
    of the wizard.
    """
    _tree(tmp_path, {"k.py": f'T = "{LIVE}"\n'})
    code = main(["scan", "--cwd", str(tmp_path)])
    assert code == 1
    assert "k.py:1" in capsys.readouterr().out
    assert not (tmp_path / ".ronin").exists()
    assert not (tmp_path / "RONIN.md").exists()


def test_a_typo_of_scan_is_refused_with_a_suggestion(tmp_path: Path, capsys: Any) -> None:
    """`scn` must not become a paid model turn asking the model about the word."""
    assert main(["scn", "--cwd", str(tmp_path)]) != 0
    assert "scan" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# the two seams that actually run git
# --------------------------------------------------------------------------- #
#
# Everything above injects past these, which is what keeps the logic testable. These
# few drive the real binary, because the thing they encode is what `git` does — that a
# non-repository exits non-zero, that `log -p` prints added lines with a `+`, that
# `diff --cached` lists the index — and a double that agreed with my belief about git
# would prove only that I hold the belief.
#
# Local, offline, and skipped where git is absent: `git init` in a tmp dir talks to
# nothing.

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _repo(root: Path) -> Path:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    return root


def test_a_git_that_cannot_be_executed_is_the_same_as_no_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`OSError` from `subprocess.run` — git missing, or not executable — must land on
    the same `None` that a non-repository does, and not escape as a traceback."""

    def explode(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("no such file: git")

    monkeypatch.setattr(subprocess, "run", explode)
    assert git_diff(tmp_path, 0, "") is None
    assert git_staged(tmp_path) is None


@requires_git
def test_git_diff_returns_none_outside_a_repository(tmp_path: Path) -> None:
    """The distinction the exit code rests on, taken from git rather than assumed."""
    assert git_diff(tmp_path, 0, "") is None


@requires_git
def test_git_staged_returns_none_outside_a_repository(tmp_path: Path) -> None:
    assert git_staged(tmp_path) is None


@requires_git
def test_a_key_committed_and_then_deleted_is_found_end_to_end(tmp_path: Path) -> None:
    """The whole point of `--history`, against real git.

    Commit a key, delete it, commit the deletion. The working tree is clean and the
    repository is still leaking, because `git show` will print the blob back to anyone
    who cloned.
    """
    root = _repo(tmp_path)
    (root / "settings.py").write_text(f'TOKEN = "{LIVE}"\n', encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "add"], cwd=root, check=True)
    (root / "settings.py").write_text("TOKEN = os.environ['T']\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "remove"], cwd=root, check=True)

    assert run_scan(ScanOptions(root=root))[0] == 0, "the working tree is clean"

    code, out, _ = run_scan(ScanOptions(root=root, history=True))
    assert code == 1
    assert "settings.py:1" in out
    assert "github-pat" in out
    assert LIVE not in out


@requires_git
def test_max_commits_caps_the_walk(tmp_path: Path) -> None:
    """The cap is what makes `--history` usable on a repository with real history."""
    root = _repo(tmp_path)
    for index in range(3):
        (root / f"f{index}.py").write_text(f"x = {index}\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", f"c{index}"], cwd=root, check=True)

    assert (git_diff(root, 0, "") or "").count("\ncommit ") + 1 == 3
    assert (git_diff(root, 1, "") or "").count("commit ") == 1


@requires_git
def test_git_staged_lists_the_index_and_not_the_tree(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "staged.py").write_text(f'T = "{LIVE}"\n', encoding="utf-8")
    (root / "untracked.py").write_text(f'T = "{AWS}"\n', encoding="utf-8")
    subprocess.run(["git", "add", "staged.py"], cwd=root, check=True)

    assert list(git_staged(root) or []) == ["staged.py"]

    code, out, _ = run_scan(ScanOptions(root=root, staged=True))
    assert code == 1
    assert "staged.py:1" in out and "untracked.py" not in out


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
