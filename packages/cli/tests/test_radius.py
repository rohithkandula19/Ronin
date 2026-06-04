"""Tests for `ronin radius` — import parsing, resolution, reverse dependents."""
from __future__ import annotations

from ronin_cli.radius import (
    build_import_graph,
    is_test_module,
    parse_imports,
    resolve_import,
    reverse_dependents,
    run_radius,
)


def test_parse_imports() -> None:
    src = "import a.b\nfrom c.d import e\nfrom . import f\nfrom ..g import h\n"
    assert parse_imports(src) == [("a.b", 0), ("c.d", 0), ("", 1), ("g", 2)]
    assert parse_imports("def broken(:\n") == []  # syntax error → empty, no raise


def test_resolve_import_absolute_and_relative() -> None:
    keys = {"pkg", "pkg/mod", "pkg/sub/leaf", "pkg/util"}
    # absolute, importing a symbol from a module → resolves to the module key
    assert resolve_import(keys, "pkg/mod", "pkg.util", 0) == "pkg/util"
    # absolute to a deeper symbol falls back to the longest existing prefix
    assert resolve_import(keys, "pkg/mod", "pkg.util.thing", 0) == "pkg/util"
    # external import → None
    assert resolve_import(keys, "pkg/mod", "numpy.linalg", 0) is None
    # relative `from . import util` inside pkg/mod → pkg/util
    assert resolve_import(keys, "pkg/mod", "util", 1) == "pkg/util"
    # relative climbing too far → None
    assert resolve_import(keys, "pkg/mod", "x", 5) is None


def test_reverse_dependents_is_transitive() -> None:
    # a → b → c  (a imports b, b imports c). Change c → blast radius {a, b}.
    graph = {"a": {"b"}, "b": {"c"}, "c": set(), "unrelated": set()}
    assert reverse_dependents(graph, {"c"}) == {"a", "b"}
    assert reverse_dependents(graph, {"a"}) == set()  # nothing imports a


def test_is_test_module() -> None:
    assert is_test_module("tests/test_foo")
    assert is_test_module("pkg/foo_test")
    assert not is_test_module("pkg/foo")


def test_build_import_graph_and_run_radius(tmp_path) -> None:
    (tmp_path / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "service.py").write_text("import core\n", encoding="utf-8")
    (tmp_path / "test_service.py").write_text("import service\n", encoding="utf-8")
    graph = build_import_graph(tmp_path)
    assert graph["service"] == {"core"}
    assert graph["test_service"] == {"service"}

    # a diff that changes core.py → radius reaches service + test_service
    diff = "--- a/core.py\n+++ b/core.py\n@@ -1 +1 @@\n-VALUE = 1\n+VALUE = 2\n"
    res = run_radius(tmp_path, diff=diff)
    assert res.changed == ["core"]
    assert set(res.radius) == {"service", "test_service"}
    assert res.affected_tests == ["test_service.py"]
