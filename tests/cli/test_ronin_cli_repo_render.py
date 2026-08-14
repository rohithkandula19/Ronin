"""``ronin repo`` human rendering and the end-to-end dispatch path.

``test_ronin_cli_repo`` covers parsing and the JSON/error paths; this file drives the
*human* renderers for all four subcommands against a repo shaped to light up every branch
(entry points, orphans, a parse error, a large API surface, populated import edges), plus
a clean repo for the "nothing to report" branches, plus one real ``dispatch`` so the
``main.py`` executor is exercised the way argv actually reaches it.
"""

from __future__ import annotations

from pathlib import Path

from ronin.cli.main import Command, Options, Streams, dispatch, parse
from ronin.cli.repo import RepoOptions, run_repo
from ronin.repo import scan_repo


def _rich(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text("class Core:\n    pass\n", encoding="utf-8")
    (pkg / "app.py").write_text(
        "from pkg.core import Core\n\n\ndef main():\n    return Core()\n", encoding="utf-8"
    )
    (pkg / "__main__.py").write_text("from pkg.app import main\n\nmain()\n", encoding="utf-8")
    (pkg / "orphan.py").write_text("def unused():\n    return 1\n", encoding="utf-8")
    (pkg / "broken.py").write_text("def oops(:\n    pass\n", encoding="utf-8")
    big = "\n\n\n".join(f"def f{i}():\n    return {i}" for i in range(41))
    (pkg / "big.py").write_text(big + "\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_core.py").write_text(
        "from pkg.core import Core\n\n\ndef test_core():\n    assert Core()\n", encoding="utf-8"
    )
    return tmp_path


def _clean(tmp_path: Path) -> Path:
    """Every module imported by something, a test present — so health has nothing to say."""
    (tmp_path / "lib.py").write_text("def util():\n    return 1\n", encoding="utf-8")
    (tmp_path / "app.py").write_text(
        "from lib import util\n\n\ndef main():\n    return util()\n", encoding="utf-8"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_lib.py").write_text(
        "from lib import util\n\n\ndef test_util():\n    assert util()\n", encoding="utf-8"
    )
    return tmp_path


def _human(subcommand: str, root: Path, target: str = "") -> str:
    scan = scan_repo(root)
    code, out, err = run_repo(
        RepoOptions(subcommand=subcommand, root=root, target=target), scan=lambda _r: scan
    )
    assert code == 0 and err == "", (code, err)
    return out


def test_map_human_lists_entry_points_languages_and_top_modules(tmp_path: Path) -> None:
    out = _human("map", _rich(tmp_path))
    assert "repo map" in out
    assert "languages: Python" in out
    assert "entry points:" in out and "pkg/app.py" in out
    assert "most-imported modules" in out
    assert "parse errors" in out  # broken.py
    assert "orphan module" in out


def test_health_human_shows_warn_info_and_large_surface(tmp_path: Path) -> None:
    out = _human("health", _rich(tmp_path))
    assert "repo health" in out
    assert "[warn]" in out and "[info]" in out
    assert "large-surface" in out and "pkg/big.py" in out
    assert "orphan-module" in out and "parse-error" in out


def test_health_human_clean_repo_reports_no_signals(tmp_path: Path) -> None:
    out = _human("health", _clean(tmp_path))
    assert "no signals" in out


def test_explain_human_shows_defs_imports_and_importers(tmp_path: Path) -> None:
    root = _rich(tmp_path)
    core = _human("explain", root, "pkg/core.py")
    assert "class Core" in core and "imported by" in core and "pkg/app.py" in core
    app = _human("explain", root, "pkg/app.py")
    assert "tags: entry-point" in app and "imports (" in app and "pkg/core.py" in app
    test = _human("explain", root, "tests/test_core.py")
    assert "tags: test" in test  # the is_test tag branch


def test_explain_human_reports_a_parse_error(tmp_path: Path) -> None:
    out = _human("explain", _rich(tmp_path), "pkg/broken.py")
    assert "parse error:" in out


def test_deadcode_human_lists_candidates_and_the_caveat(tmp_path: Path) -> None:
    out = _human("deadcode", _rich(tmp_path))
    assert "candidate(s)" in out and "pkg/orphan.py" in out
    assert "note:" in out


def test_deadcode_human_clean_repo_has_no_candidates(tmp_path: Path) -> None:
    out = _human("deadcode", _clean(tmp_path))
    assert "no candidates" in out


async def test_dispatch_runs_repo_map_end_to_end(tmp_path: Path) -> None:
    out: list[str] = []
    err: list[str] = []
    streams = Streams(
        out=out.append, err=err.append, ask=lambda _q: "", flush=lambda: None, isatty=False
    )
    parsed = parse(["repo", "map", "--root", str(_rich(tmp_path))])
    assert isinstance(parsed, Options) and parsed.command is Command.REPO
    code = await dispatch(parsed, streams=streams, environ={})
    assert code == 0
    assert any("repo map" in line for line in out)
    assert err == []
