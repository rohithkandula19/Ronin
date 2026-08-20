"""The repo map's degradation paths: unreadable files, malformed caches, bad globs.

The repo map is the one part of the context layer that has no *correct* answer when the
world misbehaves — it is a lossy summary by construction, so every failure here has an
obvious tempting shortcut: skip the file, drop the rule, ignore the cache. That is
mostly the right instinct, and it is exactly why these paths need tests. A map that
quietly omits a file looks identical to a map of a repo that does not contain it.

Four groups:

* **The gitignore compiler and glob translator.** A pattern the translator mishandles
  either eats a real source directory or lets `node_modules` in. Both are silent, and
  the second one costs the whole budget.
* **I/O that fails mid-walk.** An unreadable directory, a file that vanishes between
  the walk and the `stat`, a file too large to read. Each is skipped rather than fatal,
  because one bad file must not cost the user their whole map.
* **The cache.** Never load-bearing, in both directions: a corrupt cache file is
  discarded rather than trusted, and a cache that cannot be written is not an error.
  A cache that raises is worse than no cache at all.

* **The tree-sitter seam.** Neither grammar pack is installed here and neither ever will
  be in CI, so the node shapes come from stubs and the two-pack import ladder is staged
  through `sys.modules`. What is being tested is the *choosing* — which pack wins, what
  a bare install is told — not the grammars.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from context_harness import plant

from ronin.context import repomap
from ronin.context.repomap import (
    CACHE_VERSION,
    MAX_FILE_BYTES,
    MAX_SIGNATURE_CHARS,
    FileEntry,
    GitIgnore,
    ImportRef,
    ParserUnavailable,
    PythonAstParser,
    RefKind,
    Signature,
    SignatureKind,
    _class_end,
    _clip,
    _dotted_names,
    _fit_budget,
    _load_cache,
    _resolve_python,
    _resolve_relative_python,
    _scan_file,
    _translate,
    build_repo_map,
    cache_path,
    default_tree_sitter_loader,
    walk_repo,
)

# --------------------------------------------------------------------------- #
# the gitignore compiler: lines that compile to nothing
# --------------------------------------------------------------------------- #


def test_a_nested_rule_does_not_apply_to_a_sibling_directory() -> None:
    """`sub/.gitignore` saying `*.log` must not silence `other/a.log`.

    This is the entire reason rules carry a base rather than being concatenated into
    one list. Without the prefix check, one `.gitignore` deep in a vendored directory
    would apply its rules to the whole repo.
    """
    ignore = GitIgnore.parse("*.log\n", base="sub")
    assert ignore.ignores("sub/a.log")
    assert not ignore.ignores("other/a.log")
    assert not ignore.ignores("a.log"), "an unprefixed path is outside this file's scope"


@pytest.mark.parametrize(
    "line",
    ["/", "!/", "//", "!//"],
    ids=["bare-slash", "negated-slash", "double-slash", "negated-double-slash"],
)
def test_a_line_with_nothing_left_after_its_punctuation_compiles_to_no_rule(line: str) -> None:
    """`/` is anchoring with nothing to anchor and `!/` re-includes nothing.

    A rule compiled from these would carry an empty pattern, and an empty pattern is
    the dangerous kind of wrong: depending on the translation it matches either
    everything or nothing, and both are catastrophic for a `.gitignore`. Dropping the
    line is the only safe reading.
    """
    assert GitIgnore.parse(line + "\n").rules == ()


def test_a_comment_and_a_blank_line_are_still_dropped() -> None:
    """The control, so the test above is not passing because everything is dropped."""
    ignore = GitIgnore.parse("# a comment\n\n*.log\n")
    assert len(ignore.rules) == 1


# --------------------------------------------------------------------------- #
# the glob translator: character classes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("pattern", "matches", "misses"),
    [
        ("[ab].py", ["a.py", "b.py"], ["c.py", "ab.py"]),
        ("[!a].py", ["b.py"], ["a.py"]),
        ("[^a].py", ["b.py"], ["a.py"]),
        ("[]].py", ["].py"], ["a.py"]),
        ("[!]].py", ["a.py"], ["].py"]),
    ],
    ids=["plain", "bang-negated", "caret-negated", "literal-bracket", "negated-bracket"],
)
def test_a_character_class_translates_including_a_literal_closing_bracket(
    pattern: str, matches: list[str], misses: list[str]
) -> None:
    """`[]]` means "a literal `]`" — the first `]` after the opener does not close the
    class. Getting this wrong turns the pattern into `[]` followed by `]`, which is an
    empty class that matches nothing, so the rule silently stops working."""
    ignore = GitIgnore.parse(pattern + "\n")
    for path in matches:
        assert ignore.ignores(path), (pattern, path)
    for path in misses:
        assert not ignore.ignores(path), (pattern, path)


@pytest.mark.parametrize(
    ("pattern", "start"),
    [("[abc]", 0), ("[!abc]", 0), ("[]]", 0), ("[!]]", 0)],
    ids=["plain", "negated", "literal-bracket", "negated-bracket"],
)
def test_the_class_scanner_finds_the_real_closing_bracket(pattern: str, start: int) -> None:
    assert _class_end(pattern, start) == len(pattern) - 1


@pytest.mark.parametrize(
    "pattern",
    ["[abc", "[", "[!", "[]"],
    ids=["unterminated", "opener-only", "negated-opener", "empty-unterminated"],
)
def test_an_unterminated_character_class_has_no_end(pattern: str) -> None:
    assert _class_end(pattern, 0) is None


def test_an_unterminated_class_is_matched_as_a_literal_bracket() -> None:
    """`a[bc` is not a valid glob, and git treats the `[` as an ordinary character.

    The alternative — letting the malformed class through to `re.compile` — raises
    inside the walk, which would make one typo in a `.gitignore` fail the whole map.
    """
    translated = _translate("a[bc")
    assert translated == r"a\[bc", translated
    assert GitIgnore.parse("a[bc\n").ignores("a[bc")
    assert not GitIgnore.parse("a[bc\n").ignores("ab")


# --------------------------------------------------------------------------- #
# I/O that fails mid-walk
# --------------------------------------------------------------------------- #


def test_an_unreadable_ignore_file_is_skipped_and_the_walk_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `.gitignore` that cannot be read means its rules do not apply — the map is
    then too *inclusive*, which is the safe direction: a file that should have been
    ignored costs budget, while a lost rule file that aborted the walk costs the map."""
    plant(tmp_path, {".gitignore": "*.log\n", "app.py": "x = 1\n", "noise.log": "junk\n"})
    real_read = Path.read_bytes

    def flaky(self: Path) -> bytes:
        if self.name == ".gitignore":
            raise OSError("permission denied")
        return real_read(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    names = {path.name for path in walk_repo(tmp_path)}
    assert "app.py" in names, "the walk stopped at the unreadable ignore file"
    assert "noise.log" in names, "the rule was silently applied from a file that failed to read"


def test_an_unreadable_directory_is_skipped_rather_than_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory the process cannot list is one directory of loss, not a failed map.

    Reproduced by monkeypatching rather than by `chmod`, because the tests run as root
    in CI, where mode bits do not stop a read.
    """
    plant(tmp_path, {"app.py": "x = 1\n", "locked/inner.py": "y = 2\n"})
    real_iterdir = Path.iterdir

    def flaky(self: Path) -> Any:
        if self.name == "locked":
            raise PermissionError("not listable")
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", flaky)
    names = {path.name for path in walk_repo(tmp_path)}
    assert names == {"app.py"}, names


def test_a_file_that_vanishes_between_the_walk_and_the_stat_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The race is real: the walk lists names, then the scan stats each one, and a
    build against a live checkout has files removed in between.

    Staged by making the walk report a file that is not there, which is exactly what a
    walk that ran a moment ago reports. One missing file must not raise out of
    `build_repo_map` — the map is built from whatever survived to the `stat`.
    """
    plant(tmp_path, {"kept.py": "def kept() -> None: ...\n"})
    monkeypatch.setattr(
        repomap, "walk_repo", lambda root, **kwargs: (root / "kept.py", root / "gone.py")
    )

    result = build_repo_map(tmp_path)
    assert {entry.path for entry in result.entries} == {"kept.py"}


# --------------------------------------------------------------------------- #
# one file's scan: too big, unreadable, or a parser that misbehaves
# --------------------------------------------------------------------------- #


def scan(path: Path, *, parser: Any = None, size: int | None = None) -> Any:
    stat = path.stat()
    return _scan_file(
        path,
        path.name,
        stat.st_mtime_ns,
        stat.st_size if size is None else size,
        parser or PythonAstParser(),
    )


def test_a_file_over_the_size_limit_is_reported_rather_than_read(tmp_path: Path) -> None:
    """The limit is checked against the `stat` size *before* the read, so a
    hundred-megabyte generated file costs nothing. The reason is recorded on the entry
    so the map can say "not parsed" instead of showing an empty file."""
    path = tmp_path / "generated.py"
    path.write_text("x = 1\n", encoding="utf-8")
    scanned = scan(path, size=MAX_FILE_BYTES + 1)

    assert scanned.signatures == ()
    assert f"exceeds the {MAX_FILE_BYTES}-byte limit" in scanned.parse_error
    assert str(MAX_FILE_BYTES + 1) in scanned.parse_error, "say how big it actually was"


def test_an_unreadable_file_records_why_and_keeps_its_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The file still appears in the map, with the error where its signatures would
    be. Dropping it silently would make an unreadable file indistinguishable from a
    file with no definitions in it."""
    path = tmp_path / "secret.py"
    path.write_text("def f() -> None: ...\n", encoding="utf-8")

    def denied(self: Path) -> bytes:
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "read_bytes", denied)
    scanned = scan(path)
    assert scanned.signatures == ()
    assert scanned.parse_error.startswith("unreadable:")


class Raiser:
    """A parser that fails the way a third-party parser does: unpredictably."""

    name = "raiser"

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def supports(self, path: str) -> bool:
        return True

    def signatures(self, source: str, path: str) -> Any:
        raise self.error


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SyntaxError("bad token"), "syntax error at line"),
        (ParserUnavailable("no grammar"), "raiser unavailable: no grammar"),
        (RuntimeError("segfault-adjacent"), "raiser failed: RuntimeError: segfault-adjacent"),
        (KeyError("node"), "raiser failed: KeyError:"),
    ],
    ids=["syntax", "unavailable", "runtime", "key"],
)
def test_any_parser_failure_becomes_a_per_file_note_not_an_exception(
    tmp_path: Path, error: BaseException, expected: str
) -> None:
    """The bare `except Exception` here is deliberate and this is what justifies it.

    A tree-sitter grammar pack is third-party C code reached through a thin binding; it
    raises its own types, and new versions invent new ones. The map's contract is that
    one unparseable file costs that file's signatures and nothing else, and a contract
    that only holds for the exception types known when it was written is not a
    contract.
    """
    path = tmp_path / "thing.ts"
    path.write_text("export const x = 1;\n", encoding="utf-8")
    scanned = scan(path, parser=Raiser(error))

    assert scanned.signatures == ()
    assert expected in scanned.parse_error, scanned.parse_error


def test_a_syntax_error_names_the_line(tmp_path: Path) -> None:
    """`syntax error` alone sends the reader to the top of the file."""
    error = SyntaxError("unexpected indent")
    error.lineno = 42
    path = tmp_path / "x.ts"
    path.write_text("export const x = 1;\n", encoding="utf-8")

    assert "line 42" in scan(path, parser=Raiser(error)).parse_error


# --------------------------------------------------------------------------- #
# signatures: private names and over-long lines
# --------------------------------------------------------------------------- #


def test_a_private_class_is_omitted_along_with_its_methods() -> None:
    """The map is an interface summary. A private class's methods are twice removed
    from the interface, so descending into it would spend budget on the least useful
    lines in the file."""
    source = (
        "class Public:\n"
        "    def visible(self) -> None: ...\n"
        "class _Private:\n"
        "    def hidden(self) -> None: ...\n"
    )
    names = {s.name for s in PythonAstParser().signatures(source, "m.py")}
    assert "Public" in names
    assert "_Private" not in names
    assert "hidden" not in names, "a private class's methods came through anyway"


def test_a_private_class_is_included_when_private_names_are_requested() -> None:
    source = "class _Private:\n    def hidden(self) -> None: ...\n"
    names = {s.name for s in PythonAstParser(include_private=True).signatures(source, "m.py")}
    assert {"_Private", "hidden"} <= names


def test_an_enormous_signature_is_clipped_with_an_ellipsis() -> None:
    """A machine-generated function with sixty arguments would otherwise take the
    budget of twenty ordinary files by itself."""
    clipped = _clip("def f(" + ", ".join(f"arg{i}: int" for i in range(60)) + ") -> None")
    assert len(clipped) == MAX_SIGNATURE_CHARS
    assert clipped.endswith("…")


def test_a_signature_that_fits_is_left_exactly_alone() -> None:
    assert _clip("def f(x: int) -> None") == "def f(x: int) -> None"


def test_a_signature_is_collapsed_onto_one_line_before_clipping() -> None:
    """A multi-line `def` is one entry in the map, so the newlines have to go — and a
    map with a stray newline in it breaks the one-file-per-line reading."""
    assert _clip("def f(\n    x: int,\n) -> None") == "def f( x: int, ) -> None"


# --------------------------------------------------------------------------- #
# import resolution: the answers that are "no edge"
# --------------------------------------------------------------------------- #


def test_a_package_init_at_the_repo_root_has_no_dotted_name() -> None:
    """`__init__.py` at the root names the repo itself, which is not a module anything
    can import by name. Returning a name here would create an edge from every file
    that imports anything at all."""
    assert _dotted_names("__init__.py") == ()
    assert _dotted_names("pkg/__init__.py") == ("pkg",)


def test_an_import_with_no_target_resolves_to_nothing() -> None:
    """A malformed ref rather than a crash: the graph builder feeds this every ref it
    extracted, and one unparseable import must not lose the whole file's edges."""
    ref = ImportRef(kind=RefKind.PYTHON, target="", level=0)
    assert _resolve_python("app.py", ref, {"app.py": "app"}, {"app.py"}) is None


def test_a_dotted_import_tries_the_module_before_its_parent() -> None:
    """`from pkg.mod import thing` names the module `pkg.mod`, but `import pkg.mod.thing`
    is also legal — so the full path is tried first and the parent second. Trying the
    parent first would attribute the edge to `pkg` and lose which file was imported."""
    nodes = {"pkg/__init__.py", "pkg/mod.py"}
    index = {"pkg": "pkg/__init__.py", "pkg.mod": "pkg/mod.py"}

    exact = ImportRef(kind=RefKind.PYTHON, target="pkg.mod", level=0)
    assert _resolve_python("app.py", exact, index, nodes) == "pkg/mod.py"

    member = ImportRef(kind=RefKind.PYTHON, target="pkg.mod.thing", level=0)
    assert _resolve_python("app.py", member, index, nodes) == "pkg/mod.py"


def test_a_relative_import_that_lands_nowhere_produces_no_edge() -> None:
    """A relative import of a module that is not in the walked set — a namespace
    package, a C extension, a file the ignore rules excluded. An invented edge would
    change every rank in the map."""
    ref = ImportRef(kind=RefKind.PYTHON, target="missing", level=1)
    assert _resolve_relative_python("pkg/app.py", ref, {"pkg/app.py"}) is None


def test_a_relative_import_that_does_land_produces_the_edge() -> None:
    """The control: the resolver returning `None` for everything would pass the test
    above and delete the import graph."""
    ref = ImportRef(kind=RefKind.PYTHON, target="util", level=1)
    nodes = {"pkg/app.py", "pkg/util.py"}
    assert _resolve_relative_python("pkg/app.py", ref, nodes) == "pkg/util.py"


# --------------------------------------------------------------------------- #
# the budget floor
# --------------------------------------------------------------------------- #


def entry(path: str, *, rank: float = 0.1, inbound: int = 0) -> FileEntry:
    return FileEntry(
        path=path,
        rank=rank,
        inbound=inbound,
        signatures=(
            Signature(kind=SignatureKind.FUNCTION, name="f", text="def f() -> None", line=1),
        ),
    )


def test_a_budget_too_small_even_for_the_header_keeps_nothing_and_says_so() -> None:
    """The floor under degradation. Below the header's own cost there is no set of
    files whose listing fits, so the honest answer is an empty map with every file
    counted as dropped — not a map that overflows the budget it was given.

    Every other budget keeps at least one file; this is the only case that does not,
    and it must still return a well-formed pair rather than raising.
    """
    entries = [entry("a.py"), entry("b.py"), entry("c.py")]
    kept, dropped = _fit_budget(entries, budget_tokens=1, considered=3, parser_name="python-ast")

    assert kept == []
    assert len(dropped) == 3, "a dropped file the caller cannot count is a file that vanished"


def test_a_one_token_budget_through_the_public_entry_point_is_still_a_valid_map(
    tmp_path: Path,
) -> None:
    """The same floor from outside: `build_repo_map` returns a map, not an exception,
    and the marker admits the budget was exceeded."""
    plant(tmp_path, {"a.py": "def a() -> None: ...\n", "b.py": "def b() -> None: ...\n"})
    result = build_repo_map(tmp_path, budget_tokens=1)

    assert result.entries == ()
    assert result.dropped_count == 2
    assert "2" in result.marker(), result.marker()


# --------------------------------------------------------------------------- #
# the cache: never load-bearing, in either direction
# --------------------------------------------------------------------------- #


def write_cache(cache_dir: Path, root: Path, payload: object) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, root)
    path.write_bytes(json.dumps(payload).encode("utf-8"))
    return path


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ([1, 2, 3], "a list where an object was expected"),
        ("just a string", "a bare JSON string"),
        ({"version": CACHE_VERSION, "parser": "python-ast", "files": []}, "`files` as a list"),
        ({"version": CACHE_VERSION, "parser": "python-ast", "files": "nope"}, "`files` as text"),
        ({"version": CACHE_VERSION, "parser": "python-ast"}, "no `files` key at all"),
        ({"version": 0, "parser": "python-ast", "files": {}}, "a stale cache version"),
        ({"version": CACHE_VERSION, "parser": "other", "files": {}}, "another parser's cache"),
    ],
    ids=["list", "string", "files-list", "files-string", "files-missing", "version", "parser"],
)
def test_a_cache_of_the_wrong_shape_is_discarded_rather_than_trusted(
    tmp_path: Path, payload: object, why: str
) -> None:
    """A cache is an optimisation, so every unexpected shape means "re-scan" and never
    "raise". The alternative is a corrupt file on disk that makes the map permanently
    unbuildable until someone finds it by hand."""
    cache_dir = tmp_path / "cache"
    root = tmp_path / "repo"
    root.mkdir()
    write_cache(cache_dir, root, payload)

    assert _load_cache(cache_dir, root, "python-ast") == {}, why


def test_a_cache_entry_of_the_wrong_shape_is_dropped_without_the_rest(
    tmp_path: Path,
) -> None:
    """Per-entry rather than all-or-nothing: one truncated record should cost one
    file's re-scan, not the whole cache. `mtime_ns` missing, a string where an int
    belongs, and a non-object entry all take the same lane."""
    cache_dir = tmp_path / "cache"
    root = tmp_path / "repo"
    root.mkdir()
    write_cache(
        cache_dir,
        root,
        {
            "version": CACHE_VERSION,
            "parser": "python-ast",
            "files": {
                "good.py": {"mtime_ns": 1, "size": 2, "signatures": [], "refs": []},
                "no-mtime.py": {"size": 2},
                "bad-int.py": {"mtime_ns": "not a number", "size": 2},
                "not-an-object.py": ["mtime_ns", 1],
            },
        },
    )

    loaded = _load_cache(cache_dir, root, "python-ast")
    assert set(loaded) == {"good.py"}, set(loaded)
    assert loaded["good.py"].mtime_ns == 1


def test_a_cache_directory_that_cannot_be_written_is_not_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read-only home, a full disk, a sandbox with no writable path outside the
    workspace. The map is already built by the time the cache is saved, so failing
    here would throw away completed work for the sake of an optimisation."""
    plant(tmp_path, {"a.py": "def a() -> None: ...\n"})

    def denied(self: Path, data: bytes) -> int:
        raise OSError("read-only file system")

    monkeypatch.setattr(Path, "write_bytes", denied)
    result = build_repo_map(tmp_path, cache_dir=tmp_path / "cache")

    assert [entry.path for entry in result.entries] == ["a.py"]


def test_a_cache_that_was_written_is_reused_on_the_second_build(tmp_path: Path) -> None:
    """The control for every discard above: a cache that is never loaded would pass
    all of them while making the whole mechanism dead weight."""
    plant(tmp_path, {"a.py": "def a() -> None: ...\n", "b.py": "def b() -> None: ...\n"})
    cache_dir = tmp_path / "cache"

    first = build_repo_map(tmp_path, cache_dir=cache_dir)
    assert (first.reused_files, first.parsed_files) == (0, 2)

    second = build_repo_map(tmp_path, cache_dir=cache_dir)
    assert (second.reused_files, second.parsed_files) == (2, 0)
    assert second.render() == first.render()


# --------------------------------------------------------------------------- #
# the tree-sitter seam: anonymous nodes, and which grammar pack is preferred
# --------------------------------------------------------------------------- #


class Node:
    """A tree-sitter node, shaped for the cases the extractor has to survive.

    Deliberately allows what the real bindings allow and the happy path never
    produces: no name child, and `text` of `None`.
    """

    def __init__(
        self,
        node_type: str,
        *,
        text: str | None = "",
        name: str | None = None,
        children: list[Node] | None = None,
    ) -> None:
        self.type = node_type
        self.text = None if text is None else text.encode("utf-8")
        self.start_point = (0, 0)
        self.children = children or []
        self._name = name

    def child_by_field_name(self, field: str) -> Node | None:
        if field != "name" or self._name is None:
            return None
        return Node("identifier", text=self._name)


class Tree:
    def __init__(self, root: Node) -> None:
        self.root_node = root


def parser_for(root: Node) -> repomap.TreeSitterParser:
    class Handle:
        def parse(self, source: bytes) -> Tree:
            return Tree(root)

    return repomap.TreeSitterParser(loader=lambda _language: Handle())


@pytest.mark.parametrize(
    ("node", "why"),
    [
        (Node("class_declaration", text="class {}", name=None), "no name child at all"),
        (Node("class_declaration", text="class {}", name=""), "an empty name"),
    ],
    ids=["nameless", "empty-name"],
)
def test_a_definition_with_no_name_is_skipped_rather_than_named_blank(node: Node, why: str) -> None:
    """An anonymous class expression or a default export has no name node.

    Emitting it would put a bare `class` in the map with nothing to look up, and the
    model reads the map to find *where a name is defined*. Its children are still
    descended into, because a method inside an anonymous class is still real.
    """
    signatures = parser_for(Node("program", children=[node])).signatures("x", "app.ts")
    assert signatures == (), why


def test_children_of_an_anonymous_definition_are_still_collected() -> None:
    """The skip must not prune the subtree — that would lose every method of an
    anonymous class, which is the one thing the file was worth reading for."""
    root = Node(
        "program",
        children=[
            Node(
                "class_declaration",
                text="export default class {}",
                name=None,
                children=[Node("method_definition", text="run() {}", name="run")],
            )
        ],
    )
    signatures = parser_for(root).signatures("x", "app.ts")
    assert [(s.name, s.parent) for s in signatures] == [("run", "")], signatures


def test_a_node_whose_text_the_binding_reports_as_none_yields_no_header() -> None:
    """`node.text` is `Optional` in the bindings. A `None` here must not raise out of
    the parser and take the whole file's signatures with it."""
    root = Node("program", children=[Node("function_declaration", text=None, name="f")])
    signatures = parser_for(root).signatures("x", "app.ts")
    assert [(s.name, s.text) for s in signatures] == [("f", "")]


class PackParser:
    """A parser handle tagged with the pack that produced it, so a test can tell."""

    def __init__(self, pack: str, language: str) -> None:
        self.pack = pack
        self.language = language

    def parse(self, source: bytes) -> Tree:
        return Tree(Node("program"))


def fake_pack(name: str) -> Any:
    """A module object standing in for an installed grammar pack."""
    module = types.ModuleType(name)
    module.get_parser = lambda language: PackParser(name, language)  # type: ignore[attr-defined]
    return module


def test_the_newer_grammar_pack_is_preferred_when_both_are_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`tree_sitter_language_pack` is the maintained one and `tree_sitter_languages` is
    its predecessor, so the order is not arbitrary — an install with both must not get
    the older grammars.

    Staged through `sys.modules` rather than by installing anything: the loader's job is
    to pick, and the packs are optional dependencies the offline suite cannot have.
    """
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", fake_pack("new"))
    monkeypatch.setitem(sys.modules, "tree_sitter_languages", fake_pack("old"))

    handle: Any = default_tree_sitter_loader("python")
    assert (handle.pack, handle.language) == ("new", "python")


def test_the_older_grammar_pack_is_used_when_the_newer_one_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback exists because the older pack is what is already installed on a
    lot of machines. `None` in `sys.modules` is how CPython represents "this import
    fails", which is what an uninstalled package looks like to the `import` statement.
    """
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_languages", fake_pack("old"))

    handle: Any = default_tree_sitter_loader("python")
    assert (handle.pack, handle.language) == ("old", "python")


def test_neither_pack_installed_names_both_and_the_way_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The message a bare install sees. It has to name the package to install *and*
    say that Python needs nothing — otherwise the obvious reading is that the repo map
    does not work without an optional dependency."""
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", None)
    monkeypatch.setitem(sys.modules, "tree_sitter_languages", None)

    with pytest.raises(ParserUnavailable) as caught:
        default_tree_sitter_loader("python")

    message = str(caught.value)
    assert "tree-sitter-language-pack" in message
    assert "tree-sitter-languages" in message
    assert "PythonAstParser" in message


def test_a_pack_that_has_no_grammar_for_the_language_is_reported_as_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grammar packs raise their own exception types — `LookupError`, `KeyError`,
    or something a future version invents — so the wrapper catches everything and
    restates it as the one error the map knows how to report per file."""
    module = types.ModuleType("tree_sitter_language_pack")

    def missing(language: str) -> Any:
        raise LookupError(f"no grammar for {language}")

    module.get_parser = missing  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", module)

    with pytest.raises(ParserUnavailable, match="no grammar for 'cobol'"):
        default_tree_sitter_loader("cobol")
