"""``ronin repo`` at the edge: argv parsing and the render/error paths of ``run_repo``.

The engine is tested in ``tests/repo``; here the concern is the CLI contract — that
``parse`` turns ``repo <sub>`` into the right :class:`RepoOptions`, that a bad invocation
becomes ``Usage`` rather than a traceback, and that ``run_repo`` renders both human and
JSON and reports the two user-facing failures (unknown subcommand, unknown explain target)
on stderr with a non-zero code.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Never

from ronin.cli.main import Command, Options, Usage, parse
from ronin.cli.repo import SKIP_DIRS, SUBCOMMANDS, RepoOptions, _default_sources, run_repo
from ronin.repo import scan_repo


def _repo(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (pkg / "app.py").write_text(
        "from pkg.core import helper\n\n\ndef main():\n    helper()\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_it.py").write_text(
        "def test_it():\n    assert True\n", encoding="utf-8"
    )
    return tmp_path


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #


def _repo_opts(argv: list[str]) -> RepoOptions:
    parsed = parse(argv)
    assert isinstance(parsed, Options) and parsed.command is Command.REPO
    assert parsed.repo is not None
    return parsed.repo


def test_parse_map_default() -> None:
    repo = _repo_opts(["repo", "map"])
    assert repo.subcommand == "map"
    assert repo.as_json is False
    assert repo.target == ""


def test_parse_explain_takes_a_target() -> None:
    repo = _repo_opts(["repo", "explain", "src/x.py"])
    assert repo.subcommand == "explain"
    assert repo.target == "src/x.py"


def test_parse_output_format_json_sets_as_json() -> None:
    assert _repo_opts(["repo", "map", "--output-format", "json"]).as_json is True


def test_parse_top_and_root() -> None:
    repo = _repo_opts(["repo", "map", "--top", "3", "--root", "some/dir"])
    assert repo.top == 3
    assert repo.root == Path("some/dir")


def test_parse_rejects_missing_subcommand() -> None:
    assert isinstance(parse(["repo"]), Usage)


def test_parse_rejects_unknown_subcommand() -> None:
    assert isinstance(parse(["repo", "wat"]), Usage)


def test_parse_rejects_explain_without_target() -> None:
    assert isinstance(parse(["repo", "explain"]), Usage)


def test_parse_rejects_nonpositive_top() -> None:
    assert isinstance(parse(["repo", "map", "--top", "0"]), Usage)


# --------------------------------------------------------------------------- #
# run_repo
# --------------------------------------------------------------------------- #


def test_run_map_human_and_json(tmp_path: Path) -> None:
    scan = scan_repo(_repo(tmp_path))
    options = RepoOptions(subcommand="map", root=tmp_path)
    code, out, err = run_repo(options, scan=lambda _root: scan)
    assert code == 0 and err == "" and "repo map" in out

    code, out, err = run_repo(
        RepoOptions(subcommand="map", root=tmp_path, as_json=True), scan=lambda _root: scan
    )
    payload = json.loads(out)
    assert payload["total_files"] == 4
    assert payload["files_by_language"] == {"Python": 4}


def test_run_health_and_deadcode(tmp_path: Path) -> None:
    scan = scan_repo(_repo(tmp_path))
    code, out, _ = run_repo(RepoOptions(subcommand="health", root=tmp_path), scan=lambda _r: scan)
    assert code == 0 and "repo health" in out
    code, out, _ = run_repo(
        RepoOptions(subcommand="deadcode", root=tmp_path, as_json=True), scan=lambda _r: scan
    )
    assert code == 0
    assert "note" in json.loads(out)  # the candidates-only caveat rides along


def test_run_explain_unknown_target_is_a_clean_error(tmp_path: Path) -> None:
    scan = scan_repo(_repo(tmp_path))
    code, out, err = run_repo(
        RepoOptions(subcommand="explain", root=tmp_path, target="nope.py"),
        scan=lambda _r: scan,
    )
    assert code == 1 and out == "" and "not a scanned code file" in err


def test_run_explain_without_target_is_usage(tmp_path: Path) -> None:
    scan = scan_repo(_repo(tmp_path))
    code, _, err = run_repo(RepoOptions(subcommand="explain", root=tmp_path), scan=lambda _r: scan)
    assert code == 2 and "needs a file path" in err


def test_run_unknown_subcommand_is_rejected(tmp_path: Path) -> None:
    # run_repo guards even if a caller bypasses parse — the guard returns before the
    # scanner is ever called, so this scanner (which would fail the assertion) is inert.
    def _never(_root: Path) -> object:
        raise AssertionError("scan must not run for an unknown subcommand")

    code, _, err = run_repo(RepoOptions(subcommand="bogus", root=tmp_path), scan=_never)  # type: ignore[arg-type]
    assert code == 2 and "unknown subcommand" in err


# --------------------------------------------------------------------------- #
# complexity — the subcommand that does not use the scan
# --------------------------------------------------------------------------- #
#
# Injected through `sources` rather than `scan`, on purpose. `health` states as an
# invariant that every signal comes from the scan with no second read of the tree, and
# `RepoScan` keeps signatures rather than bodies — so measuring paths through a function
# had to be either a new field on every signature the token-budgeted map also carries, or
# its own subcommand. It is its own subcommand.

BRANCHY = """
def tangled(a, b, c):
    if a:
        for item in b:
            if item and c:
                pass
    try:
        pass
    except ValueError:
        pass
    except KeyError:
        pass
    return [x for x in b if x if x > 1]
"""

PLAIN = "def calm():\n    return 1\n"


def _sources(files: dict[str, str]) -> Callable[[Path], list[tuple[str, str]]]:
    def read(_root: Path) -> list[tuple[str, str]]:
        return list(files.items())

    return read


def _complexity(files: dict[str, str], **options: object) -> tuple[int, str, str]:
    return run_repo(
        RepoOptions(subcommand="complexity", root=Path("."), **options),  # type: ignore[arg-type]
        sources=_sources(files),
    )


def test_complexity_ranks_the_worst_functions() -> None:
    code, out, err = _complexity({"tangled.py": BRANCHY, "calm.py": PLAIN})
    assert (code, err) == (0, "")
    assert "tangled.py:2" in out
    assert "calm" not in out, "below the threshold, so not worth a line"


def test_complexity_never_builds_the_import_graph() -> None:
    """The scan is the slow part of every other subcommand and this one does not read
    it. A `scan` that raises is how that is asserted rather than assumed."""

    def explode(_root: Path) -> Never:
        raise AssertionError("complexity must not scan the repo")

    code, _out, _err = run_repo(
        RepoOptions(subcommand="complexity", root=Path(".")),
        scan=explode,
        sources=_sources({"tangled.py": BRANCHY}),
    )
    assert code == 0


def test_a_clean_tree_says_so_rather_than_printing_an_empty_listing() -> None:
    code, out, _err = _complexity({"calm.py": PLAIN})
    assert code == 0
    assert "no function scores" in out


def test_complexity_carries_the_rating_next_to_the_number() -> None:
    """The word is what most readers act on; the number is what they argue about."""
    _code, out, _err = _complexity({"t.py": BRANCHY})
    assert "high" in out


def test_top_limits_the_listing() -> None:
    many = {f"m{index}.py": BRANCHY for index in range(6)}
    _code, out, _err = _complexity(many, top=2)
    assert out.count("tangled") == 2


def test_complexity_as_json_is_a_list_of_records() -> None:
    _code, out, _err = _complexity({"t.py": BRANCHY}, as_json=True)
    payload = json.loads(out)
    assert [record["name"] for record in payload] == ["tangled"]
    assert payload[0]["score"] >= 10
    assert payload[0]["path"] == "t.py"


def test_complexity_is_in_the_subcommand_list() -> None:
    """So the typo message and `--help` both know about it."""
    assert "complexity" in SUBCOMMANDS


def test_complexity_parses_from_argv(tmp_path: Path) -> None:
    options = parse(["repo", "complexity", "--cwd", str(tmp_path)])
    assert isinstance(options, Options)
    assert options.command is Command.REPO
    assert options.repo is not None and options.repo.subcommand == "complexity"


def test_the_real_walk_reads_python_and_prunes_the_usual_directories(tmp_path: Path) -> None:
    """The one piece of `complexity` that touches a filesystem.

    `.venv` is in the list because a complexity report about somebody else's dependency
    is a report about somebody else's repository — and it would dominate the ranking.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(PLAIN, encoding="utf-8")
    (tmp_path / "notes.md").write_text("# not python\n", encoding="utf-8")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "dep.py").write_text(BRANCHY, encoding="utf-8")

    found = dict(_default_sources(tmp_path))
    assert set(found) == {str(Path("pkg/mod.py"))}
    assert ".venv" in SKIP_DIRS


def test_a_file_the_walk_cannot_decode_is_skipped_rather_than_raised(tmp_path: Path) -> None:
    """Advisory, not a gate: one undecodable file must not deny the reader the rest."""
    (tmp_path / "good.py").write_text(PLAIN, encoding="utf-8")
    (tmp_path / "bad.py").write_bytes(b"\xff\xfe\x00 not utf-8 \x80")

    found = dict(_default_sources(tmp_path))
    assert "good.py" in found
    assert "bad.py" not in found
