"""``scan_repo`` — the un-budgeted scan extracted from ``build_repo_map``.

``build_repo_map``'s own suite proves the budgeting still works after the extraction;
this pins the *new* surface: ``scan_repo`` returns every code file (not the top-N slice a
budget leaves), with a signature entry per file and a real import graph. The repo-
intelligence commands depend on that completeness — a map that dropped the file you asked
about would make ``explain`` lie.
"""

from __future__ import annotations

from pathlib import Path

from ronin.context.repomap import build_repo_map, scan_repo


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text("import b\n\n\ndef fa():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def fb():\n    pass\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not code\n", encoding="utf-8")
    return tmp_path


def test_scan_repo_returns_every_code_file_with_signatures(tmp_path: Path) -> None:
    scan = scan_repo(_repo(tmp_path))
    assert scan.files == ("a.py", "b.py")  # notes.txt is not a code suffix
    assert "a.py" in scan.signatures and "b.py" in scan.signatures
    assert scan.parsed_files == 2 and scan.reused_files == 0


def test_scan_repo_builds_the_import_graph(tmp_path: Path) -> None:
    scan = scan_repo(_repo(tmp_path))
    assert ("a.py", "b.py") in scan.graph.edges  # a imports b
    assert scan.inbound["b.py"] == 1
    assert scan.inbound["a.py"] == 0
    assert "b.py" not in scan.graph.leaves()  # imported by a
    assert "a.py" in scan.graph.leaves()


def test_scan_repo_is_the_superset_build_repo_map_budgets(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    scan = scan_repo(root)
    mapped = build_repo_map(root)
    # Every path the budgeted map kept was in the full scan.
    assert set(mapped.paths).issubset(set(scan.files))
