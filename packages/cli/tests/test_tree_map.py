"""Tests for tree_map — build, render, biggest_dirs."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.tree_map import biggest_dirs, build_tree, render_lines


def _make_repo(root: Path) -> None:
    (root / "pkg").mkdir()
    (root / "pkg" / "a.py").write_text("x = 1\ny = 2\n", encoding="utf-8")       # 2 lines
    (root / "pkg" / "b.py").write_text("z = 3\n", encoding="utf-8")             # 1 line
    (root / "tests").mkdir()
    (root / "tests" / "test_a.py").write_text("def t():\n    pass\n", encoding="utf-8")  # 2
    (root / "readme.md").write_text("# hi", encoding="utf-8")                   # not code
    noise = root / "node_modules"
    noise.mkdir()
    (noise / "dep.py").write_text("junk = 1\n", encoding="utf-8")               # skipped


def test_build_counts_code_files_and_lines(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    tree = build_tree(tmp_path)
    # 3 code files (a, b, test_a); readme.md and node_modules excluded
    assert tree.files == 3
    assert tree.lines == 5                       # 2 + 1 + 2
    names = {c.name for c in tree.children}
    assert "pkg" in names and "tests" in names
    assert "node_modules" not in names


def test_build_respects_max_depth(tmp_path: Path) -> None:
    deep = tmp_path / "a" / "b" / "c"
    deep.mkdir(parents=True)
    (deep / "x.py").write_text("v = 1\n", encoding="utf-8")
    tree = build_tree(tmp_path, max_depth=1)
    # 'a' is visible, but its grandchildren are pruned from the view…
    a = next(c for c in tree.children if c.name == "a")
    assert a.children == []
    # …yet the counts still roll up from the pruned depths
    assert a.files == 1 and a.lines == 1


def test_render_lines_has_root_and_connectors(tmp_path: Path) -> None:
    _make_repo(tmp_path)
    lines = render_lines(build_tree(tmp_path))
    assert lines[0].startswith(tmp_path.name) and "3 files" in lines[0]
    body = "\n".join(lines[1:])
    assert "pkg/" in body and "tests/" in body
    assert any(c in body for c in ("├──", "└──"))


def test_biggest_dirs_ranks_by_lines(tmp_path: Path) -> None:
    (tmp_path / "big").mkdir()
    (tmp_path / "big" / "f.py").write_text("\n".join(f"x{i}=1" for i in range(20)) + "\n",
                                           encoding="utf-8")
    (tmp_path / "small").mkdir()
    (tmp_path / "small" / "g.py").write_text("y = 1\n", encoding="utf-8")
    top = biggest_dirs(build_tree(tmp_path))
    assert top[0][0] == "big" and top[0][1] >= 20
