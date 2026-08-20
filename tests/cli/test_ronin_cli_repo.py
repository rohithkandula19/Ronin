"""``ronin repo`` at the edge: argv parsing and the render/error paths of ``run_repo``.

The engine is tested in ``tests/repo``; here the concern is the CLI contract — that
``parse`` turns ``repo <sub>`` into the right :class:`RepoOptions`, that a bad invocation
becomes ``Usage`` rather than a traceback, and that ``run_repo`` renders both human and
JSON and reports the two user-facing failures (unknown subcommand, unknown explain target)
on stderr with a non-zero code.
"""

from __future__ import annotations

import json
from pathlib import Path

from ronin.cli.main import Command, Options, Usage, parse
from ronin.cli.repo import RepoOptions, run_repo
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
