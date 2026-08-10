"""The repo map, with most of the effort on the gitignore matcher.

That matcher is the part that fails silently: over-include and ``node_modules``
eats the budget, under-include and the file the user is asking about is invisible
while the map looks perfectly healthy. So the pattern cases are table-driven and
cover the four things a naive ``fnmatch`` implementation gets wrong — anchoring,
directory-only patterns, ``**``, and negation ordering — plus nested files.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from context_harness import plant

from ronin.context.repomap import (
    DEFAULT_BUDGET_TOKENS,
    GitIgnore,
    ImportRef,
    ParserUnavailable,
    PythonAstParser,
    RefKind,
    Signature,
    SignatureKind,
    TreeSitterParser,
    build_import_graph,
    build_repo_map,
    cache_path,
    pagerank,
    python_imports,
    script_imports,
    walk_repo,
)

# --------------------------------------------------------------------------- #
# gitignore matching
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("patterns", "path", "is_dir", "ignored"),
    [
        # plain names match at any depth
        (["build"], "build", True, True),
        (["build"], "deep/nested/build", True, True),
        (["*.log"], "a.log", False, True),
        (["*.log"], "deep/a.log", False, True),
        (["*.log"], "a.txt", False, False),
        # `*` must not cross a slash
        (["src/*.py"], "src/a.py", False, True),
        (["src/*.py"], "src/deep/a.py", False, False),
        # anchored: a leading slash pins to the root
        (["/build"], "build", True, True),
        (["/build"], "sub/build", True, False),
        # a pattern with an internal slash is anchored too, without needing one
        (["docs/build"], "docs/build", True, True),
        (["docs/build"], "sub/docs/build", True, False),
        # directory-only
        (["cache/"], "cache", True, True),
        (["cache/"], "cache", False, False),
        # `**`
        (["**/vendor"], "a/b/vendor", True, True),
        (["**/vendor"], "vendor", True, True),
        (["vendor/**"], "vendor/a/b.py", False, True),
        (["a/**/b"], "a/b", False, True),
        (["a/**/b"], "a/x/y/b", False, True),
        (["a/**/b"], "x/a/b", False, False),
        # `?` and character classes
        (["?.py"], "a.py", False, True),
        (["?.py"], "ab.py", False, False),
        (["[abc].py"], "b.py", False, True),
        (["[!abc].py"], "b.py", False, False),
        (["[!abc].py"], "d.py", False, True),
        # comments and blanks are not patterns
        (["# *.py", "", "  "], "a.py", False, False),
        # escaped hash is a literal
        ([r"\#real"], "#real", False, True),
    ],
)
def test_the_gitignore_matcher_handles_the_pattern_forms(
    patterns: list[str], path: str, is_dir: bool, ignored: bool
) -> None:
    assert GitIgnore.from_patterns(patterns).ignores(path, is_dir=is_dir) is ignored


def test_the_last_matching_rule_wins_so_negation_re_includes() -> None:
    ignore = GitIgnore.from_patterns(["*.log", "!keep.log"])
    assert ignore.ignores("debug.log")
    assert not ignore.ignores("keep.log")


def test_a_negation_before_the_exclusion_does_not_re_include() -> None:
    """Order matters, and the wrong order is a real user bug worth reproducing."""
    ignore = GitIgnore.from_patterns(["!keep.log", "*.log"])
    assert ignore.ignores("keep.log")


def test_no_matching_rule_is_distinguishable_from_an_explicit_re_include() -> None:
    ignore = GitIgnore.from_patterns(["*.log", "!keep.log"])
    assert ignore.decide("keep.log") is False
    assert ignore.decide("other.txt") is None


def test_a_nested_gitignore_only_applies_beneath_its_own_directory(tmp_path: Path) -> None:
    plant(
        tmp_path,
        {
            ".gitignore": "root_only.txt\n",
            "pkg/.gitignore": "local.txt\n",
            "pkg/local.txt": "x",
            "pkg/keep.py": "x = 1\n",
            "local.txt": "not ignored at the root",
            "root_only.txt": "x",
        },
    )
    names = {path.relative_to(tmp_path).as_posix() for path in walk_repo(tmp_path)}
    assert "pkg/local.txt" not in names
    assert "local.txt" in names
    assert "root_only.txt" not in names


def test_a_deeper_gitignore_can_re_include_what_the_root_excluded(tmp_path: Path) -> None:
    plant(
        tmp_path,
        {
            ".gitignore": "*.log\n",
            "logs/.gitignore": "!keep.log\n",
            "logs/keep.log": "x",
            "logs/drop.log": "x",
        },
    )
    names = {path.relative_to(tmp_path).as_posix() for path in walk_repo(tmp_path)}
    assert "logs/keep.log" in names
    assert "logs/drop.log" not in names


def test_an_ignored_directory_is_pruned_so_nothing_inside_is_re_included(
    tmp_path: Path,
) -> None:
    """git cannot re-include a file whose parent directory is excluded."""
    plant(
        tmp_path,
        {
            ".gitignore": "node_modules/\n!node_modules/important.js\n",
            "node_modules/important.js": "x",
            "app.js": "x",
        },
    )
    names = {path.relative_to(tmp_path).as_posix() for path in walk_repo(tmp_path)}
    assert names == {".gitignore", "app.js"}


def test_the_git_directory_is_never_walked(tmp_path: Path) -> None:
    plant(tmp_path, {"a.py": "x = 1\n"})
    (tmp_path / ".git" / "objects").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "blob").write_bytes(b"x")
    assert all(".git" not in path.parts for path in walk_repo(tmp_path))


def test_symlinks_are_skipped_rather_than_followed(tmp_path: Path) -> None:
    plant(tmp_path, {"real.py": "x = 1\n"})
    (tmp_path / "loop").symlink_to(tmp_path)
    names = {path.name for path in walk_repo(tmp_path)}
    assert names == {"real.py"}


def test_the_walk_is_sorted_and_reproducible(tmp_path: Path) -> None:
    plant(tmp_path, {f"dir{index}/file.py": "x = 1\n" for index in range(5)})
    assert walk_repo(tmp_path) == walk_repo(tmp_path)
    relative = [path.relative_to(tmp_path).as_posix() for path in walk_repo(tmp_path)]
    assert relative == sorted(relative)


# --------------------------------------------------------------------------- #
# signatures
# --------------------------------------------------------------------------- #

SOURCE = '''\
"""Module docstring."""
from __future__ import annotations

MAX = 12
NAME: str = "x"
_private_constant = 1


class Engine(Base, metaclass=Meta):
    """Doc."""

    def run(self, steps: int = 3, *args: int, **kw: str) -> str:
        secret = 1
        return "done"

    async def stop(self) -> None:
        pass

    def _hidden(self) -> None:
        pass

    def __post_init__(self) -> None:
        pass


def build(name: str, *, strict: bool = False) -> Engine:
    return Engine()


async def go() -> int:
    return 0


def _helper() -> None:
    pass
'''


def test_the_python_parser_emits_signatures_and_never_a_body() -> None:
    signatures = PythonAstParser().signatures(SOURCE, "engine.py")
    rendered = "\n".join(signature.text for signature in signatures)
    assert "def run(self, steps: int=3, *args: int, **kw: str) -> str" in rendered
    assert "async def stop(self) -> None" in rendered
    assert "class Engine(Base, metaclass=Meta)" in rendered
    assert "def build(name: str, *, strict: bool=False) -> Engine" in rendered
    assert "MAX = 12" in rendered
    assert "NAME: str" in rendered
    assert "secret" not in rendered
    assert "return" not in rendered


def test_private_names_are_omitted_but_dunders_are_kept() -> None:
    names = {s.name for s in PythonAstParser().signatures(SOURCE, "engine.py")}
    assert "_hidden" not in names
    assert "_helper" not in names
    assert "_private_constant" not in names
    assert "__post_init__" in names


def test_private_names_can_be_included_on_request() -> None:
    names = {s.name for s in PythonAstParser(include_private=True).signatures(SOURCE, "engine.py")}
    assert {"_hidden", "_helper"} <= names


def test_methods_are_attributed_to_their_class_and_indented() -> None:
    signatures = PythonAstParser().signatures(SOURCE, "engine.py")
    run = next(s for s in signatures if s.name == "run")
    assert run.kind is SignatureKind.METHOD
    assert run.parent == "Engine"
    assert run.render().startswith("    def run")
    build = next(s for s in signatures if s.name == "build")
    assert build.kind is SignatureKind.FUNCTION
    assert build.render().startswith("  def build")


def test_a_signature_survives_a_json_round_trip() -> None:
    original = Signature(
        kind=SignatureKind.METHOD, name="run", text="def run(self)", line=4, parent="Engine"
    )
    assert Signature.from_json(original.to_json()) == original


def test_a_syntax_error_raises_from_the_parser_and_is_reported_by_the_map(
    tmp_path: Path,
) -> None:
    with pytest.raises(SyntaxError):
        PythonAstParser().signatures("def broken(:\n", "broken.py")
    plant(tmp_path, {"broken.py": "def broken(:\n", "fine.py": "def ok() -> None: pass\n"})
    repo_map = build_repo_map(tmp_path)
    broken = next(entry for entry in repo_map.entries if entry.path == "broken.py")
    assert "syntax error" in broken.parse_error
    assert broken.signatures == ()


# --------------------------------------------------------------------------- #
# tree-sitter seam
# --------------------------------------------------------------------------- #


class FakeNode:
    """Shaped like a tree-sitter node, so the extraction logic is testable."""

    def __init__(
        self,
        node_type: str,
        *,
        text: str = "",
        name: str = "",
        children: list[FakeNode] | None = None,
        row: int = 0,
    ) -> None:
        self.type = node_type
        self.text = text.encode("utf-8")
        self.start_point = (row, 0)
        self.children = children or []
        self._name = FakeNode("identifier", text=name) if name else None

    def child_by_field_name(self, field: str) -> FakeNode | None:
        return self._name if field == "name" else None


class FakeTree:
    def __init__(self, root: FakeNode) -> None:
        self.root_node = root


class FakeTSParser:
    def __init__(self, root: FakeNode) -> None:
        self._root = root

    def parse(self, source: bytes) -> FakeTree:
        return FakeTree(self._root)


def test_tree_sitter_extraction_takes_the_declaration_line_only() -> None:
    root = FakeNode(
        "program",
        children=[
            FakeNode(
                "class_declaration",
                name="Engine",
                text="class Engine extends Base {\n  body();\n}",
                row=2,
                children=[
                    FakeNode(
                        "method_definition",
                        name="run",
                        text="run(steps) {\n  return 1;\n}",
                        row=3,
                    )
                ],
            ),
            FakeNode(
                "function_declaration",
                name="build",
                text="function build(name) {\n  return 1;\n}",
                row=9,
            ),
        ],
    )
    parser = TreeSitterParser(loader=lambda _: FakeTSParser(root))
    signatures = parser.signatures("ignored", "app.ts")
    assert [(s.kind, s.name, s.text, s.line) for s in signatures] == [
        (SignatureKind.CLASS, "Engine", "class Engine extends Base", 3),
        (SignatureKind.METHOD, "run", "run(steps)", 4),
        (SignatureKind.FUNCTION, "build", "function build(name)", 10),
    ]


def test_tree_sitter_raises_parser_unavailable_when_the_pack_is_missing() -> None:
    """A bare install must fail loudly here, not return an empty map."""

    def missing(language: str) -> FakeTSParser:
        raise ParserUnavailable("tree-sitter is not installed")

    with pytest.raises(ParserUnavailable, match="not installed"):
        TreeSitterParser(loader=missing).signatures("x", "app.ts")


def test_tree_sitter_refuses_a_language_it_has_no_mapping_for() -> None:
    with pytest.raises(ParserUnavailable, match="no tree-sitter language"):
        TreeSitterParser(loader=lambda _: FakeTSParser(FakeNode("program"))).signatures(
            "x", "notes.txt"
        )


def test_an_unavailable_parser_is_reported_per_file_not_fatal(tmp_path: Path) -> None:
    plant(tmp_path, {"app.ts": "export function a() {}\n"})

    def missing(language: str) -> FakeTSParser:
        raise ParserUnavailable("pack missing")

    repo_map = build_repo_map(tmp_path, parser=TreeSitterParser(loader=missing))
    entry = next(e for e in repo_map.entries if e.path == "app.ts")
    assert "unavailable" in entry.parse_error


# --------------------------------------------------------------------------- #
# import graph + pagerank
# --------------------------------------------------------------------------- #


def test_python_imports_records_absolute_and_relative_forms() -> None:
    import ast

    tree = ast.parse(
        "import os\nfrom pkg.mod import thing\nfrom ..core.types import X\nfrom . import y\n"
    )
    refs = python_imports(tree)
    assert ImportRef(RefKind.PYTHON, "os") in refs
    assert ImportRef(RefKind.PYTHON, "pkg.mod") in refs
    assert ImportRef(RefKind.PYTHON, "core.types", 2) in refs
    assert ImportRef(RefKind.PYTHON, "", 1) in refs


def test_script_imports_finds_relative_specifiers_only() -> None:
    refs = script_imports(
        "import x from 'react'\nimport y from './util'\nconst z = require('../lib/a')\n"
    )
    assert [ref.target for ref in refs] == ["./util", "../lib/a"]


def test_relative_python_imports_resolve_by_position_on_disk() -> None:
    refs = {
        "src/pkg/app.py": (ImportRef(RefKind.PYTHON, "core.types", 2),),
        "src/core/types.py": (),
        "src/pkg/__init__.py": (),
    }
    graph = build_import_graph(refs)
    assert ("src/pkg/app.py", "src/core/types.py") in graph.edges


def test_an_ambiguous_dotted_name_produces_no_edge_rather_than_a_guess() -> None:
    refs = {
        "one/util.py": (),
        "two/util.py": (),
        "app.py": (ImportRef(RefKind.PYTHON, "util"),),
    }
    assert build_import_graph(refs).edges == ()


def test_duplicate_imports_count_once() -> None:
    refs = {
        "app.py": (
            ImportRef(RefKind.PYTHON, "lib"),
            ImportRef(RefKind.PYTHON, "lib"),
            ImportRef(RefKind.PYTHON, "lib"),
        ),
        "lib.py": (),
    }
    assert build_import_graph(refs).edges == (("app.py", "lib.py"),)


def test_leaves_are_the_files_nothing_imports() -> None:
    refs = {
        "core.py": (),
        "a.py": (ImportRef(RefKind.PYTHON, "core"),),
        "b.py": (ImportRef(RefKind.PYTHON, "core"),),
    }
    graph = build_import_graph(refs)
    assert graph.leaves() == ("a.py", "b.py")
    assert graph.inbound_counts()["core.py"] == 2


def test_pagerank_ranks_the_most_imported_file_highest() -> None:
    nodes = ("core.py", "a.py", "b.py", "c.py")
    edges = (("a.py", "core.py"), ("b.py", "core.py"), ("c.py", "core.py"))
    ranks = pagerank(nodes, edges)
    assert max(ranks, key=lambda node: ranks[node]) == "core.py"
    assert abs(sum(ranks.values()) - 1.0) < 1e-9


def test_pagerank_mass_is_conserved_with_dangling_nodes() -> None:
    """Without dangling redistribution the whole vector decays toward zero."""
    nodes = tuple(f"n{index}.py" for index in range(6))
    ranks = pagerank(nodes, (("n0.py", "n1.py"),))
    assert abs(sum(ranks.values()) - 1.0) < 1e-9


def test_pagerank_on_an_empty_graph_is_empty() -> None:
    assert pagerank((), ()) == {}


def test_pagerank_ignores_self_edges_and_unknown_nodes() -> None:
    ranks = pagerank(("a.py",), (("a.py", "a.py"), ("a.py", "ghost.py")))
    assert ranks == {"a.py": 1.0}


# --------------------------------------------------------------------------- #
# the map, the budget, the cache
# --------------------------------------------------------------------------- #

TREE = {
    ".gitignore": "node_modules/\n*.log\n",
    "src/core.py": "class Engine:\n    def run(self) -> None:\n        pass\n",
    "src/app.py": "from .core import Engine\n\n\ndef main() -> int:\n    return 0\n",
    "src/leaf_a.py": "from .core import Engine\n\n\ndef a(x: int, y: str) -> None:\n    pass\n",
    "src/leaf_b.py": "from .core import Engine\n\n\ndef b(x: int, y: str) -> None:\n    pass\n",
    "node_modules/dep/index.js": "export function hidden() {}\n",
    "notes.log": "ignored\n",
}


def test_the_map_lists_only_walked_source_files(tmp_path: Path) -> None:
    plant(tmp_path, TREE)
    repo_map = build_repo_map(tmp_path)
    assert set(repo_map.paths) == {
        "src/app.py",
        "src/core.py",
        "src/leaf_a.py",
        "src/leaf_b.py",
    }
    assert "hidden" not in repo_map.render()


def test_entries_are_rendered_in_path_order_for_prompt_cache_stability(
    tmp_path: Path,
) -> None:
    plant(tmp_path, TREE)
    repo_map = build_repo_map(tmp_path)
    assert list(repo_map.paths) == sorted(repo_map.paths)


#: Big enough that the truncation marker is a small part of the budget, which is
#: the ordinary regime. ``TREE`` exercises the degenerate one.
BIG_TREE = {
    "core.py": "class Engine:\n    def run(self) -> None:\n        pass\n",
    **{
        f"leaf_{index}.py": (
            "from core import Engine\n\n\n"
            f"def handler_{index}(request: dict, *, timeout: float = 1.0) -> bytes:\n"
            "    return b''\n\n\n"
            f"def helper_{index}(value: int) -> str:\n"
            "    return ''\n"
        )
        for index in range(12)
    },
}


def test_the_map_fits_its_budget_and_names_what_it_dropped(tmp_path: Path) -> None:
    plant(tmp_path, BIG_TREE)
    repo_map = build_repo_map(tmp_path, budget_tokens=200)
    assert repo_map.token_estimate <= 200
    assert not repo_map.over_budget
    assert repo_map.dropped_count > 0
    marker = repo_map.marker()
    assert f"{repo_map.dropped_count} of {repo_map.considered}" in marker
    assert "leaf" in marker
    assert marker in repo_map.render()


def test_degradation_drops_leaves_before_the_file_everything_imports(
    tmp_path: Path,
) -> None:
    plant(tmp_path, BIG_TREE)
    repo_map = build_repo_map(tmp_path, budget_tokens=200)
    assert "core.py" in repo_map.paths
    assert all(path.startswith("leaf_") for path in repo_map.dropped_paths)
    assert repo_map.dropped_leaves == repo_map.dropped_count


def test_lower_budgets_drop_strictly_more(tmp_path: Path) -> None:
    plant(tmp_path, BIG_TREE)
    counts = [
        build_repo_map(tmp_path, budget_tokens=budget).dropped_count
        for budget in (900, 500, 300, 200)
    ]
    assert counts == sorted(counts)
    assert counts[0] == 0 and counts[-1] > 0


def test_a_smaller_budget_never_keeps_more_files(tmp_path: Path) -> None:
    """A regression: the total is not monotone in the number of files dropped.

    Dropping the first file *adds* the marker, so on a small repo an earlier
    implementation emptied the map at one budget while a smaller budget kept two
    files. A smaller budget showing more of the repo is indefensible whatever the
    arithmetic reason.
    """
    plant(tmp_path, TREE)
    counts = [
        len(build_repo_map(tmp_path, budget_tokens=budget).paths)
        for budget in (2000, 300, 200, 150, 120, 110, 100, 90, 80, 70, 60, 30)
    ]
    assert counts == sorted(counts, reverse=True), counts


def test_an_empty_map_is_not_returned_while_one_file_would_fit(tmp_path: Path) -> None:
    plant(tmp_path, TREE)
    for budget in (110, 100, 90):
        repo_map = build_repo_map(tmp_path, budget_tokens=budget)
        assert repo_map.paths, budget
        assert "src/core.py" in repo_map.paths, budget


def test_a_budget_too_small_for_the_marker_still_lists_files_and_says_so(
    tmp_path: Path,
) -> None:
    """The degenerate case: the explanation costs more than the budget allows.

    Dropping every file would be over budget *and* useless, so the listing is fitted
    without charging for the marker, and the map reports the overflow rather than
    pretending it fit.
    """
    plant(tmp_path, TREE)
    repo_map = build_repo_map(tmp_path, budget_tokens=60)
    assert repo_map.over_budget
    assert repo_map.paths
    assert "src/core.py" in repo_map.paths
    assert "not charged against it" in repo_map.marker()


def test_an_ample_budget_drops_nothing_and_emits_no_marker(tmp_path: Path) -> None:
    plant(tmp_path, TREE)
    repo_map = build_repo_map(tmp_path, budget_tokens=DEFAULT_BUDGET_TOKENS)
    assert repo_map.dropped_count == 0
    assert repo_map.marker() == ""
    assert "truncated" not in repo_map.render()


def test_the_token_estimate_matches_the_rendered_text(tmp_path: Path) -> None:
    from ronin.context.budget import estimate_tokens

    plant(tmp_path, TREE)
    repo_map = build_repo_map(tmp_path)
    assert repo_map.token_estimate == estimate_tokens(repo_map.render())


def test_top_n_bounds_how_many_files_are_considered(tmp_path: Path) -> None:
    plant(tmp_path, TREE)
    repo_map = build_repo_map(tmp_path, top_n=2)
    assert repo_map.considered == 2
    assert len(repo_map.paths) <= 2


@pytest.mark.parametrize(("budget", "top_n"), [(0, 10), (-1, 10), (100, 0), (100, -3)])
def test_a_nonsense_budget_or_top_n_is_rejected(tmp_path: Path, budget: int, top_n: int) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        build_repo_map(tmp_path, budget_tokens=budget, top_n=top_n)


def test_building_twice_is_byte_identical(tmp_path: Path) -> None:
    plant(tmp_path, TREE)
    first = build_repo_map(tmp_path).render()
    assert build_repo_map(tmp_path).render() == first


def test_the_cache_is_reused_when_nothing_changed(tmp_path: Path) -> None:
    root = plant(tmp_path / "repo", TREE)
    cache = tmp_path / "cache"
    first = build_repo_map(root, cache_dir=cache)
    assert first.parsed_files == 4
    assert first.reused_files == 0
    assert cache_path(cache, root).is_file()
    second = build_repo_map(root, cache_dir=cache)
    assert second.reused_files == 4
    assert second.parsed_files == 0
    assert second.render() == first.render()


def test_one_changed_file_re_parses_only_that_file(tmp_path: Path) -> None:
    root = plant(tmp_path / "repo", TREE)
    cache = tmp_path / "cache"
    build_repo_map(root, cache_dir=cache)
    (root / "src" / "leaf_a.py").write_bytes(
        b"from .core import Engine\n\n\ndef a(renamed: int) -> None:\n    pass\n"
    )
    updated = build_repo_map(root, cache_dir=cache)
    assert updated.parsed_files == 1
    assert updated.reused_files == 3
    assert "def a(renamed: int) -> None" in updated.render()


def test_a_new_import_re_ranks_everything_even_though_signatures_are_cached(
    tmp_path: Path,
) -> None:
    """Signatures are per-file cacheable; ranks are not — one import changes all of them."""
    root = plant(tmp_path / "repo", TREE)
    cache = tmp_path / "cache"
    before = {entry.path: entry.rank for entry in build_repo_map(root, cache_dir=cache).entries}
    (root / "src" / "core.py").write_bytes(
        b"from .leaf_a import a\n\n\nclass Engine:\n    def run(self) -> None:\n        pass\n"
    )
    after = {entry.path: entry.rank for entry in build_repo_map(root, cache_dir=cache).entries}
    assert after["src/leaf_a.py"] > before["src/leaf_a.py"]


def test_a_deleted_file_leaves_the_map(tmp_path: Path) -> None:
    root = plant(tmp_path / "repo", TREE)
    cache = tmp_path / "cache"
    build_repo_map(root, cache_dir=cache)
    (root / "src" / "leaf_b.py").unlink()
    assert "src/leaf_b.py" not in build_repo_map(root, cache_dir=cache).paths


def test_switching_parsers_invalidates_the_cached_signatures(tmp_path: Path) -> None:
    root = plant(tmp_path / "repo", TREE)
    cache = tmp_path / "cache"
    build_repo_map(root, cache_dir=cache)
    other = build_repo_map(
        root,
        cache_dir=cache,
        parser=TreeSitterParser(loader=lambda _: FakeTSParser(FakeNode("program"))),
    )
    assert other.reused_files == 0


def test_a_corrupt_cache_file_is_ignored_rather_than_fatal(tmp_path: Path) -> None:
    root = plant(tmp_path / "repo", TREE)
    cache = tmp_path / "cache"
    cache.mkdir()
    cache_path(cache, root).write_bytes(b"{not json")
    assert build_repo_map(root, cache_dir=cache).parsed_files == 4


def test_no_cache_dir_means_no_file_is_written(tmp_path: Path) -> None:
    root = plant(tmp_path / "repo", TREE)
    build_repo_map(root)
    assert not (tmp_path / "cache").exists()


def test_a_binary_file_with_a_source_suffix_is_skipped_with_a_reason(
    tmp_path: Path,
) -> None:
    plant(tmp_path, {"ok.py": "x = 1\n"})
    (tmp_path / "blob.py").write_bytes(b"\x00\x01\x02binary")
    entry = next(e for e in build_repo_map(tmp_path).entries if e.path == "blob.py")
    assert entry.parse_error == "skipped: binary"


def test_the_map_header_says_the_token_number_is_an_estimate(tmp_path: Path) -> None:
    plant(tmp_path, TREE)
    assert "estimated at 4 chars/token" in build_repo_map(tmp_path).render()
