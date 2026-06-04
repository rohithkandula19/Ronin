"""Tests for api_ref — extraction + Markdown rendering."""
from __future__ import annotations

from pathlib import Path

from ronin_cli.api_ref import build_api, extract_api, render_markdown


def test_extract_function_signature_and_summary() -> None:
    src = (
        'def add(a: int, b: int = 0) -> int:\n'
        '    """Add two ints.\n\n    More detail here.\n    """\n'
        '    return a + b\n'
        'def _hidden():\n    pass\n'
    )
    api = extract_api(src, "m.py")
    assert len(api) == 1                          # _hidden excluded
    s = api[0]
    assert s.signature == "def add(a: int, b: int=0) -> int"
    assert s.summary == "Add two ints."          # first line only


def test_extract_class_with_public_methods() -> None:
    src = (
        'class Widget:\n'
        '    """A widget."""\n'
        '    def draw(self, x): ...\n'
        '    def _internal(self): ...\n'
    )
    api = extract_api(src, "m.py")
    assert len(api) == 1
    cls = api[0]
    assert cls.kind == "class" and cls.signature == "class Widget"
    assert any("draw" in m for m in cls.methods)
    assert not any("_internal" in m for m in cls.methods)


def test_extract_async_function() -> None:
    api = extract_api("async def fetch(url):\n    ...\n", "m.py")
    assert api[0].signature.startswith("async def fetch(")


def test_extract_syntax_error_safe() -> None:
    assert extract_api("def (((", "m.py") == []


def test_render_markdown_structure() -> None:
    api = extract_api('def go() -> None:\n    """Do it."""\n', "pkg/m.py")
    md = render_markdown([("pkg/m.py", api)], title="My API")
    assert md.startswith("# My API")
    assert "## `pkg/m.py`" in md
    assert "### `def go() -> None`" in md
    assert "Do it." in md
    assert "1 public symbol" in md


def test_build_api_skips_tests_and_empty(tmp_path: Path) -> None:
    (tmp_path / "lib.py").write_text('def f():\n    """A fn."""\n', encoding="utf-8")
    (tmp_path / "test_lib.py").write_text("def test_f():\n    pass\n", encoding="utf-8")
    (tmp_path / "empty.py").write_text("x = 1\n", encoding="utf-8")  # no public defs
    mods = {m for m, _ in build_api(tmp_path)}
    assert "lib.py" in mods
    assert "test_lib.py" not in mods and "empty.py" not in mods
