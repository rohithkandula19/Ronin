"""The repo map: what the codebase looks like, at signature resolution.

The model must never see the whole repo, and it must never be handed a random
sample of it either. This module builds the middle option — every important file's
*shape* (classes, functions, exports; no bodies) inside a hard token budget — and it
makes three choices that are worth stating plainly:

1. **Importance is pagerank over the import graph.** A file everything imports is
   the one worth showing; a file nothing imports is the one to drop first. Twenty
   lines of power iteration, no dependency. Ranking by size or by mtime both fail
   the same way: they surface the incidental and bury the load-bearing.
2. **Degradation drops leaves first, and says so.** Over budget, the lowest-ranked
   *leaf* (a file nothing in the repo imports) goes first, then the next, and only
   once every leaf is gone does a non-leaf go. The rendered map carries an explicit
   marker naming how many files were dropped and why, because a silently short map
   makes the model conclude the code does not exist.
3. **Rendering order is by path, not by rank.** Inclusion is by rank; *order* is
   alphabetical, because this text goes into the provider layer's stable prefix and
   a one-line edit that nudges pagerank must not reshuffle the whole block and
   throw away the prompt cache.

The gitignore matcher is implemented here rather than shelled out to ``git`` or
delegated to a dependency, and it is the part most heavily tested: a matcher that
over-includes leaks ``node_modules`` into the map and blows the budget, and one that
under-includes silently hides the file the user is asking about. Negations,
directory-only patterns (``foo/``), anchored patterns (``/foo``), ``**``, and nested
``.gitignore`` files with deeper-file precedence are all handled.

Parsing sits behind the :class:`Parser` protocol. :class:`PythonAstParser` uses
stdlib ``ast`` and is always available, so a bare install still produces a complete,
exact map for Python. :class:`TreeSitterParser` imports its grammar pack lazily
inside the call and raises :class:`ParserUnavailable` when it is not installed.

Token counts are ``len(text) / 4`` estimates — a rule of thumb, not a measurement.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from .budget import CHARS_PER_TOKEN, estimate_tokens

#: Default ceiling on the rendered map. Small on purpose: the map competes with the
#: user's actual code for context, and 2k tokens of signatures is already a lot of
#: structure.
DEFAULT_BUDGET_TOKENS = 2000

#: How many top-ranked files are considered for inclusion before budgeting.
DEFAULT_TOP_N = 50

#: Pagerank damping. The conventional 0.85; nothing here measured a better value.
DAMPING = 0.85

#: Power-iteration ceiling and the L1 delta below which it stops early.
MAX_ITERATIONS = 60
TOLERANCE = 1e-8

#: Never walked, whatever the ignore files say.
ALWAYS_IGNORED = frozenset({".git"})

#: The ignore files honoured, per directory, in precedence order (later wins).
IGNORE_FILENAMES: tuple[str, ...] = (".gitignore",)

#: Files larger than this are not read. A megabyte of generated source contributes
#: nothing to a signature map and costs the whole build's latency.
MAX_FILE_BYTES = 1_000_000

#: Suffixes considered source code at all. A file outside this set is walked (it
#: still counts for ignore-matching tests) but never read.
CODE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".go",
        ".rs",
        ".rb",
        ".java",
        ".kt",
        ".swift",
        ".c",
        ".h",
        ".cc",
        ".cpp",
        ".hpp",
        ".cs",
        ".php",
        ".scala",
        ".lua",
        ".sh",
    }
)

_PYTHON_SUFFIXES: frozenset[str] = frozenset({".py", ".pyi"})
_SCRIPT_SUFFIXES: tuple[str, ...] = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")

#: Directory names stripped when guessing a module's importable dotted name, so
#: ``src/ronin/core/types.py`` is reachable as ``ronin.core.types``.
_SOURCE_ROOTS: frozenset[str] = frozenset({"src", "lib"})

CACHE_VERSION = 2


class ParserUnavailable(RuntimeError):
    """A parser's optional dependency is not installed.

    Raised rather than silently returning no signatures: a map that is empty for a
    language the caller asked for is indistinguishable from a codebase with no
    definitions, and the caller can catch this and fall back explicitly.
    """


# --------------------------------------------------------------------------- #
# gitignore
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class IgnoreRule:
    """One gitignore line, compiled.

    ``base`` is the repo-relative directory of the ``.gitignore`` that declared it;
    a nested file's rules only apply beneath it, which is the whole point of
    supporting nested files rather than concatenating them.
    """

    raw: str
    regex: re.Pattern[str]
    negated: bool = False
    dir_only: bool = False
    base: str = ""

    def matches(self, rel_path: str, *, is_dir: bool) -> bool:
        if self.dir_only and not is_dir:
            return False
        subject = rel_path
        if self.base:
            prefix = f"{self.base}/"
            if not rel_path.startswith(prefix):
                return False
            subject = rel_path[len(prefix) :]
        return self.regex.fullmatch(subject) is not None


@dataclass(frozen=True, slots=True)
class GitIgnore:
    """An ordered rule set. Last matching rule wins, exactly like git."""

    rules: tuple[IgnoreRule, ...] = ()

    @classmethod
    def parse(cls, text: str, *, base: str = "") -> GitIgnore:
        rules: list[IgnoreRule] = []
        for line in text.splitlines():
            rule = _compile_rule(line, base=base)
            if rule is not None:
                rules.append(rule)
        return cls(rules=tuple(rules))

    @classmethod
    def from_patterns(cls, patterns: Iterable[str], *, base: str = "") -> GitIgnore:
        return cls.parse("\n".join(patterns), base=base)

    def extended(self, other: GitIgnore) -> GitIgnore:
        """``other``'s rules appended, so they take precedence. Deeper file wins."""
        return GitIgnore(rules=(*self.rules, *other.rules))

    def decide(self, rel_path: str, *, is_dir: bool = False) -> bool | None:
        """``True`` ignored, ``False`` explicitly re-included, ``None`` no rule matched."""
        verdict: bool | None = None
        for rule in self.rules:
            if rule.matches(rel_path, is_dir=is_dir):
                verdict = not rule.negated
        return verdict

    def ignores(self, rel_path: str, *, is_dir: bool = False) -> bool:
        return self.decide(rel_path, is_dir=is_dir) is True


def _compile_rule(line: str, *, base: str) -> IgnoreRule | None:
    """One line to a rule, or ``None`` for a blank/comment line."""
    raw = line
    if not line.strip() or line.lstrip().startswith("#"):
        return None
    # Trailing whitespace is not significant unless escaped; leading is.
    stripped = re.sub(r"(?<!\\)\s+$", "", line)
    if not stripped:
        return None  # pragma: no cover - `str.strip` and `\s` agree, so 197 caught this
    negated = stripped.startswith("!")
    # A leading `!` negates; a leading `\!` is a literal one. Both lose one char.
    if negated or stripped.startswith("\\!"):
        stripped = stripped[1:]
    dir_only = stripped.endswith("/")
    if dir_only:
        # Every trailing slash, not just one: `//` would otherwise compile to a rule
        # with an empty pattern, which matches nothing and looks configured.
        stripped = stripped.rstrip("/")
    if not stripped:
        return None
    anchored = stripped.startswith("/") or "/" in stripped.rstrip("/")
    body = stripped.removeprefix("/")
    regex = _translate(body)
    if not anchored:
        regex = f"(?:.*/)?{regex}"
    return IgnoreRule(
        raw=raw,
        regex=re.compile(regex),
        negated=negated,
        dir_only=dir_only,
        base=base,
    )


def _translate(pattern: str) -> str:
    """gitignore glob to regex.

    Hand-written rather than ``fnmatch.translate`` because ``fnmatch``'s ``*``
    happily crosses ``/``: with it, ``*.py`` matches ``vendor/deep/thing.py`` and
    an anchored pattern stops being anchored, which is precisely the over-matching
    that leaks a vendored tree into the map.
    """
    out: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        char = pattern[index]
        if char == "*":
            if pattern.startswith("**/", index):
                out.append("(?:.*/)?")
                index += 3
                continue
            if pattern.startswith("**", index):
                out.append(".*")
                index += 2
                continue
            out.append("[^/]*")
            index += 1
            continue
        if char == "?":
            out.append("[^/]")
            index += 1
            continue
        if char == "[":
            closing = _class_end(pattern, index)
            if closing is None:
                out.append(re.escape(char))
                index += 1
                continue
            body = pattern[index + 1 : closing]
            if body.startswith(("!", "^")):
                body = "^" + body[1:]
            out.append(f"[{body}]")
            index = closing + 1
            continue
        if char == "\\" and index + 1 < length:
            out.append(re.escape(pattern[index + 1]))
            index += 2
            continue
        out.append(re.escape(char))
        index += 1
    return "".join(out)


def _class_end(pattern: str, start: int) -> int | None:
    index = start + 1
    if index < len(pattern) and pattern[index] in "!^":
        index += 1
    if index < len(pattern) and pattern[index] == "]":
        index += 1
    while index < len(pattern):
        if pattern[index] == "]":
            return index
        index += 1
    return None


def walk_repo(root: Path, *, extra_ignores: GitIgnore | None = None) -> tuple[Path, ...]:
    """Every non-ignored file under ``root``, sorted by repo-relative posix path.

    Directories are pruned rather than filtered per-file, which is both faster and
    correct: git cannot re-include a file whose parent directory is excluded, so a
    ``!`` inside an ignored tree must not resurrect anything.

    Symlinks are skipped entirely. Following them turns a walk into a cycle hunt,
    and a repo map is not worth a hang.
    """
    found: list[tuple[str, Path]] = []
    stack: list[tuple[Path, str, GitIgnore]] = [(root, "", extra_ignores or GitIgnore())]
    while stack:
        directory, rel, inherited = stack.pop()
        ignore = inherited
        for name in IGNORE_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                try:
                    text = candidate.read_bytes().decode("utf-8", errors="replace")
                except OSError:
                    continue
                ignore = ignore.extended(GitIgnore.parse(text, base=rel))
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if entry.name in ALWAYS_IGNORED or entry.is_symlink():
                continue
            child_rel = f"{rel}/{entry.name}" if rel else entry.name
            if entry.is_dir():
                if not ignore.ignores(child_rel, is_dir=True):
                    stack.append((entry, child_rel, ignore))
            elif entry.is_file() and not ignore.ignores(child_rel, is_dir=False):
                found.append((child_rel, entry))
    return tuple(path for _, path in sorted(found))


# --------------------------------------------------------------------------- #
# signatures
# --------------------------------------------------------------------------- #


class SignatureKind(StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    EXPORT = "export"


@dataclass(frozen=True, slots=True)
class Signature:
    """One definition, rendered as a single line with no body."""

    kind: SignatureKind
    name: str
    text: str
    line: int
    parent: str = ""

    def render(self) -> str:
        indent = "    " if self.kind is SignatureKind.METHOD else "  "
        return f"{indent}{self.text}"

    def to_json(self) -> dict[str, str | int]:
        return {
            "kind": self.kind.value,
            "name": self.name,
            "text": self.text,
            "line": self.line,
            "parent": self.parent,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> Signature:
        return cls(
            kind=SignatureKind(str(data["kind"])),
            name=str(data["name"]),
            text=str(data["text"]),
            line=int(cast(int, data["line"])),
            parent=str(data.get("parent", "")),
        )


class Parser(Protocol):
    """The seam between the map and whatever can read a language.

    ``name`` is part of the cache key: switching parsers changes every signature,
    and a cache that did not know that would serve the old ones forever.
    """

    name: str

    def supports(self, path: str) -> bool: ...

    def signatures(self, source: str, path: str) -> Sequence[Signature]: ...


#: Longest rendered signature line before it is elided. A 400-character generic
#: signature is not more informative than its first 140 characters, and it is
#: twenty times the budget cost.
MAX_SIGNATURE_CHARS = 140


class PythonAstParser:
    """Signatures via stdlib ``ast``. Always available, and exact for Python.

    Private names (a single leading underscore) are omitted by default: the map is
    an interface summary, and a module's internals are what the model reads the file
    for. Dunders are kept — ``__init__`` is part of the interface.
    """

    name = "python-ast"

    def __init__(self, *, include_private: bool = False) -> None:
        self.include_private = include_private

    def supports(self, path: str) -> bool:
        return Path(path).suffix in _PYTHON_SUFFIXES

    def signatures(self, source: str, path: str) -> Sequence[Signature]:
        tree = ast.parse(source, filename=path)
        out: list[Signature] = []
        self._collect(tree.body, out, parent="")
        return tuple(out)

    def _collect(self, body: Sequence[ast.stmt], out: list[Signature], *, parent: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                if not self._visible(node.name):
                    continue
                out.append(
                    Signature(
                        kind=SignatureKind.CLASS,
                        name=node.name,
                        text=_clip(f"class {node.name}{_bases(node)}"),
                        line=node.lineno,
                        parent=parent,
                    )
                )
                self._collect(node.body, out, parent=node.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not self._visible(node.name):
                    continue
                prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                out.append(
                    Signature(
                        kind=SignatureKind.METHOD if parent else SignatureKind.FUNCTION,
                        name=node.name,
                        text=_clip(f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"),
                        line=node.lineno,
                        parent=parent,
                    )
                )
            elif not parent:
                export = _module_export(node)
                if export is not None and self._visible(export.name):
                    out.append(export)

    def _visible(self, name: str) -> bool:
        if self.include_private:
            return True
        return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _bases(node: ast.ClassDef) -> str:
    parts = [ast.unparse(base) for base in node.bases]
    parts.extend(f"{kw.arg}={ast.unparse(kw.value)}" for kw in node.keywords if kw.arg)
    return f"({', '.join(parts)})" if parts else ""


def _module_export(node: ast.stmt) -> Signature | None:
    """A module-level constant or annotated name, which is part of the interface."""
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return Signature(
            kind=SignatureKind.EXPORT,
            name=node.target.id,
            text=_clip(f"{node.target.id}: {ast.unparse(node.annotation)}"),
            line=node.lineno,
        )
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target = node.targets[0]
        if isinstance(target, ast.Name) and (target.id.isupper() or target.id == "__all__"):
            return Signature(
                kind=SignatureKind.EXPORT,
                name=target.id,
                text=_clip(f"{target.id} = {ast.unparse(node.value)}"),
                line=node.lineno,
            )
    return None


def _clip(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= MAX_SIGNATURE_CHARS:
        return collapsed
    return collapsed[: MAX_SIGNATURE_CHARS - 1] + "…"


# --------------------------------------------------------------------------- #
# tree-sitter (optional)
# --------------------------------------------------------------------------- #


class TSNode(Protocol):
    """The slice of a tree-sitter node this module uses."""

    @property
    def type(self) -> str: ...
    @property
    def start_point(self) -> tuple[int, int]: ...
    @property
    def children(self) -> Sequence[TSNode]: ...
    @property
    def text(self) -> bytes | None: ...
    def child_by_field_name(self, name: str) -> TSNode | None: ...


class TSTree(Protocol):
    @property
    def root_node(self) -> TSNode: ...


class TSParser(Protocol):
    def parse(self, source: bytes) -> TSTree: ...


#: Language name to a parser. Injectable so the extraction logic is testable
#: without the grammar pack installed — the default one does the lazy import.
TreeSitterLoader = Callable[[str], TSParser]

_TS_LANGUAGES: Mapping[str, str] = {
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".java": "java",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".php": "php",
    ".scala": "scala",
    ".lua": "lua",
    ".py": "python",
}

#: Node types that are a definition worth a line in the map. A superset across
#: languages: an unknown type is simply never matched.
_TS_DEFINITIONS: Mapping[str, SignatureKind] = {
    "function_declaration": SignatureKind.FUNCTION,
    "function_definition": SignatureKind.FUNCTION,
    "function_item": SignatureKind.FUNCTION,
    "method_definition": SignatureKind.METHOD,
    "method_declaration": SignatureKind.METHOD,
    "class_declaration": SignatureKind.CLASS,
    "class_definition": SignatureKind.CLASS,
    "class_specifier": SignatureKind.CLASS,
    "struct_item": SignatureKind.CLASS,
    "trait_item": SignatureKind.CLASS,
    "interface_declaration": SignatureKind.CLASS,
    "type_declaration": SignatureKind.EXPORT,
    "type_alias_declaration": SignatureKind.EXPORT,
    "enum_declaration": SignatureKind.EXPORT,
}

#: Definitions whose children are not descended into. Nested closures are noise.
_TS_OPAQUE: frozenset[SignatureKind] = frozenset({SignatureKind.FUNCTION, SignatureKind.METHOD})


def default_tree_sitter_loader(language: str) -> TSParser:
    """Import a grammar pack lazily and return its parser for ``language``.

    Lazy on purpose: nothing in a bare install may depend on this, and the import
    itself is what fails when the pack is missing.
    """
    # The narrow ignores below are for an optional dependency that is absent by
    # design: neither pack is installed in a bare checkout, so mypy cannot resolve
    # either import and there are no stubs to type against.
    try:
        from tree_sitter_language_pack import get_parser  # type: ignore[import-not-found]
    except ImportError:
        try:
            # The older pack, same `get_parser(language)` API.
            from tree_sitter_languages import get_parser  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ParserUnavailable(
                "tree-sitter is not installed. Install "
                "'tree-sitter-language-pack' (or the older 'tree-sitter-languages') "
                "for non-Python signatures, or use PythonAstParser, which needs "
                "nothing and is exact for Python."
            ) from exc
    try:
        return cast(TSParser, get_parser(language))
    except Exception as exc:  # the grammar pack raises its own exception types
        raise ParserUnavailable(f"tree-sitter has no grammar for {language!r}: {exc}") from exc


class TreeSitterParser:
    """Signatures for many languages, when the grammar pack is installed.

    The pack is not a hard dependency and this class is never the default, so a
    bare install still produces a full Python map. When the pack is missing,
    :meth:`signatures` raises :class:`ParserUnavailable` rather than returning
    nothing — see that class for why.
    """

    name = "tree-sitter"

    def __init__(self, *, loader: TreeSitterLoader | None = None) -> None:
        self._loader = loader or default_tree_sitter_loader

    def supports(self, path: str) -> bool:
        return Path(path).suffix in _TS_LANGUAGES

    def signatures(self, source: str, path: str) -> Sequence[Signature]:
        language = _TS_LANGUAGES.get(Path(path).suffix)
        if language is None:
            raise ParserUnavailable(f"no tree-sitter language for {path!r}")
        parser = self._loader(language)
        tree = parser.parse(source.encode("utf-8"))
        out: list[Signature] = []
        _ts_collect(tree.root_node, out, parent="")
        return tuple(out)


def _ts_collect(node: TSNode, out: list[Signature], *, parent: str) -> None:
    kind = _TS_DEFINITIONS.get(node.type)
    if kind is not None:
        name = _ts_name(node)
        if name:
            resolved = SignatureKind.METHOD if parent and kind is SignatureKind.FUNCTION else kind
            out.append(
                Signature(
                    kind=resolved,
                    name=name,
                    text=_clip(_ts_header(node)),
                    line=node.start_point[0] + 1,
                    parent=parent,
                )
            )
        if kind in _TS_OPAQUE:
            return
        parent = name or parent
    for child in node.children:
        _ts_collect(child, out, parent=parent)


def _ts_name(node: TSNode) -> str:
    named = node.child_by_field_name("name")
    if named is None or named.text is None:
        return ""
    return named.text.decode("utf-8", errors="replace")


def _ts_header(node: TSNode) -> str:
    """The declaration line, cut at the body opener so no body is ever emitted."""
    raw = node.text.decode("utf-8", errors="replace") if node.text is not None else ""
    header = raw.split("\n", 1)[0]
    for opener in ("{", "=>"):
        index = header.find(opener)
        if index > 0:
            header = header[:index]
    return header.strip().rstrip(":").strip()


# --------------------------------------------------------------------------- #
# import graph
# --------------------------------------------------------------------------- #


class RefKind(StrEnum):
    PYTHON = "python"
    PATH = "path"


@dataclass(frozen=True, slots=True)
class ImportRef:
    """One unresolved import, as written in the source.

    Cached unresolved on purpose: resolution depends on the whole file set, so a
    cache of resolved edges would be stale the moment a file is added or deleted —
    the one thing a repo map has to notice.
    """

    kind: RefKind
    target: str
    level: int = 0

    def to_json(self) -> dict[str, str | int]:
        return {"kind": self.kind.value, "target": self.target, "level": self.level}

    @classmethod
    def from_json(cls, data: Mapping[str, object]) -> ImportRef:
        return cls(
            kind=RefKind(str(data["kind"])),
            target=str(data["target"]),
            level=int(cast(int, data.get("level", 0))),
        )


@dataclass(frozen=True, slots=True)
class ImportGraph:
    """Repo-relative paths and the deduped edges between them.

    ``(importer, imported)``, so rank flows *toward* what is imported. Deduped:
    importing the same module on three lines is one dependency, and counting it
    three times would let an import-heavy file vote itself up.
    """

    nodes: tuple[str, ...] = ()
    edges: tuple[tuple[str, str], ...] = ()

    def inbound_counts(self) -> dict[str, int]:
        counts = dict.fromkeys(self.nodes, 0)
        for _, target in self.edges:
            if target in counts:
                counts[target] += 1
        return counts

    def leaves(self) -> tuple[str, ...]:
        """Nodes nothing imports. The first thing dropped when over budget."""
        counts = self.inbound_counts()
        return tuple(node for node in self.nodes if counts[node] == 0)


def python_imports(tree: ast.Module) -> tuple[ImportRef, ...]:
    """Every import in a parsed module, absolute and relative kept distinct."""
    refs: list[ImportRef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.extend(ImportRef(kind=RefKind.PYTHON, target=alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            refs.append(
                ImportRef(
                    kind=RefKind.PYTHON,
                    target=node.module or "",
                    level=node.level,
                )
            )
    return tuple(refs)


_SCRIPT_IMPORT = re.compile(
    r"""(?:from|import|require\(|import\()\s*['"](\.{1,2}/[^'"]+|\.{1,2})['"]"""
)


def script_imports(source: str) -> tuple[ImportRef, ...]:
    """Relative imports from JS/TS source, by regex.

    Only *relative* specifiers, because only those name a file in this repo. A bare
    ``react`` is a package, and resolving package specifiers means reading
    ``node_modules``, which is the thing the map exists to avoid.
    """
    return tuple(
        ImportRef(kind=RefKind.PATH, target=match.group(1))
        for match in _SCRIPT_IMPORT.finditer(source)
    )


def build_import_graph(refs: Mapping[str, Sequence[ImportRef]]) -> ImportGraph:
    """Resolve every reference against the file set and return the graph.

    A dotted name claimed by two files is ambiguous and produces **no** edge. A
    guessed edge is worse than a missing one: it moves rank onto the wrong file and
    the map then shows the wrong thing with total confidence.
    """
    nodes = tuple(sorted(refs))
    index = _module_index(nodes)
    known = set(nodes)
    edges: set[tuple[str, str]] = set()
    for source_path, references in refs.items():
        for ref in references:
            target = (
                _resolve_python(source_path, ref, index, known)
                if ref.kind is RefKind.PYTHON
                else _resolve_path(source_path, ref, known)
            )
            if target is not None and target != source_path:
                edges.add((source_path, target))
    return ImportGraph(nodes=nodes, edges=tuple(sorted(edges)))


def _module_index(nodes: Sequence[str]) -> Mapping[str, str | None]:
    """Dotted name to file, with ambiguous names mapped to ``None``."""
    index: dict[str, str | None] = {}
    for path in nodes:
        if Path(path).suffix not in _PYTHON_SUFFIXES:
            continue
        for name in _dotted_names(path):
            if name in index and index[name] != path:
                index[name] = None
            else:
                index.setdefault(name, path)
    return index


def _dotted_names(path: str) -> tuple[str, ...]:
    parts = list(Path(path).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    # Only the *directory* prefix is strippable. Without the length guard,
    # `lib.py` at the repo root loses its own name and becomes unresolvable.
    while len(parts) > 1 and parts[0] in _SOURCE_ROOTS:
        parts.pop(0)
    if not parts:
        return ()
    return tuple(".".join(parts[start:]) for start in range(len(parts)))


def _resolve_python(
    source_path: str,
    ref: ImportRef,
    index: Mapping[str, str | None],
    nodes: set[str],
) -> str | None:
    if ref.level:
        return _resolve_relative_python(source_path, ref, nodes)
    if not ref.target:
        return None
    # `from pkg.mod import name`: the module is `pkg.mod`, but `import pkg.mod.name`
    # names a module too, so try the full dotted path before its parent.
    candidates = [ref.target]
    if "." in ref.target:
        candidates.append(ref.target.rsplit(".", 1)[0])
    for candidate in candidates:
        found = index.get(candidate)
        if found is not None:
            return found
    return None


def _resolve_relative_python(source_path: str, ref: ImportRef, nodes: set[str]) -> str | None:
    """``from ..core.types import X`` against the importer's own directory.

    Exact, unlike the dotted-name lookup: a relative import is defined by position
    on disk, so there is nothing to guess — but the resolved file still has to be
    one we walked, or the edge points outside the map.
    """
    directory = Path(source_path).parent
    for _ in range(ref.level - 1):
        directory = directory.parent
    target = directory
    for part in ref.target.split(".") if ref.target else []:
        target = target / part
    for candidate in (
        target.with_suffix(".py"),
        target / "__init__.py",
        # `from ..types import X` where `types` is a symbol in `..__init__`
        target.parent / "__init__.py",
    ):
        as_posix = candidate.as_posix()
        if as_posix in nodes:
            return as_posix
    return None


def _resolve_path(source_path: str, ref: ImportRef, nodes: set[str]) -> str | None:
    base = (Path(source_path).parent / ref.target).as_posix()
    normalized = _normalize_relative(base)
    if normalized is None:
        return None
    for candidate in (
        normalized,
        *(f"{normalized}{suffix}" for suffix in _SCRIPT_SUFFIXES),
        *(f"{normalized}/index{suffix}" for suffix in _SCRIPT_SUFFIXES),
    ):
        if candidate in nodes:
            return candidate
    return None


def _normalize_relative(path: str) -> str | None:
    parts: list[str] = []
    for part in path.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts) if parts else None


def pagerank(
    nodes: Sequence[str],
    edges: Sequence[tuple[str, str]],
    *,
    damping: float = DAMPING,
    iterations: int = MAX_ITERATIONS,
    tolerance: float = TOLERANCE,
) -> dict[str, float]:
    """Plain power iteration. Ranks sum to 1.

    Dangling nodes (a file that imports nothing) have their mass redistributed
    uniformly instead of being dropped; without that the ranks leak away every
    iteration and the whole vector shrinks toward zero, which silently inverts the
    comparison against any fixed threshold.
    """
    if not nodes:
        return {}
    count = len(nodes)
    outgoing: dict[str, list[str]] = {node: [] for node in nodes}
    known = set(nodes)
    for source, target in edges:
        if source in known and target in known and source != target:
            outgoing[source].append(target)
    rank = dict.fromkeys(nodes, 1.0 / count)
    for _ in range(iterations):
        dangling = sum(rank[node] for node in nodes if not outgoing[node])
        base = (1.0 - damping) / count + damping * dangling / count
        updated = dict.fromkeys(nodes, base)
        for source, targets in outgoing.items():
            if not targets:
                continue
            share = damping * rank[source] / len(targets)
            for target in targets:
                updated[target] += share
        delta = sum(abs(updated[node] - rank[node]) for node in nodes)
        rank = updated
        if delta < tolerance:
            break
    return rank


# --------------------------------------------------------------------------- #
# the map
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class FileEntry:
    """One file's line in the map, plus what decided it was worth including."""

    path: str
    rank: float
    inbound: int
    signatures: tuple[Signature, ...] = ()
    parse_error: str = ""

    @property
    def is_leaf(self) -> bool:
        return self.inbound == 0

    def render(self) -> str:
        lines = [f"{self.path}"]
        if self.parse_error:
            lines.append(f"  # not parsed: {self.parse_error}")
        lines.extend(signature.render() for signature in self.signatures)
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class RepoMap:
    """The rendered map, and the accounting for everything it left out."""

    entries: tuple[FileEntry, ...] = ()
    dropped_count: int = 0
    token_estimate: int = 0
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    dropped_leaves: int = 0
    dropped_paths: tuple[str, ...] = ()
    considered: int = 0
    parser_name: str = ""
    #: Files whose signatures came from the cache, and files parsed this build.
    reused_files: int = 0
    parsed_files: int = 0

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)

    @property
    def over_budget(self) -> bool:
        """True when the truncation marker pushed the render past the budget.

        The listing itself is inside the budget; what does not fit is the
        explanation of what was cut. Dropping further files would show less and
        still overflow, so the marker is treated as a floor — the same rule
        :mod:`ronin.context.budget` applies to a clamp marker.
        """
        return self.token_estimate > self.budget_tokens

    def marker(self) -> str:
        """The explicit statement of what was dropped and why. ``""`` if nothing was."""
        if not self.dropped_count:
            return ""
        non_leaves = self.dropped_count - self.dropped_leaves
        detail = (
            f"{self.dropped_leaves} leaf file(s) — a leaf is imported by nothing "
            f"else in this repo — dropped lowest-pagerank first"
        )
        if non_leaves:
            detail += (
                f"; then {non_leaves} non-leaf file(s), also lowest-pagerank first, "
                "because every leaf was already gone"
            )
        if self.over_budget:
            detail += (
                "; note: the listing above fits the budget but this marker does not, "
                "so the marker was not charged against it — dropping more files would "
                "have shown less while still overflowing"
            )
        return (
            f"[repo map truncated to fit {self.budget_tokens} tokens: "
            f"{self.dropped_count} of {self.considered} file(s) dropped — {detail}]"
        )

    def render(self) -> str:
        """The text handed to ``StablePrefix.repo_map``. Deterministic, path-ordered."""
        header = [
            "# repo map — signatures only, no function bodies",
            f"# {len(self.entries)} of {self.considered} file(s), "
            f"budget {self.budget_tokens} tokens "
            f"(estimated at {CHARS_PER_TOKEN} chars/token)",
        ]
        body = [entry.render() for entry in self.entries]
        marker = self.marker()
        parts = ["\n".join(header), *body]
        if marker:
            parts.append(marker)
        return "\n\n".join(parts) + "\n"


@dataclass(frozen=True, slots=True)
class RepoScan:
    """The un-budgeted analysis of a repo: every code file's shape and how they link.

    This is the raw material :func:`build_repo_map` budgets down into a rendered map,
    exposed on its own because the repo-intelligence commands
    (:mod:`ronin.repo`) need the *whole* graph — every file, every rank, every
    inbound count — not the top-N slice a token budget leaves behind. One walk, one
    parse, shared by both consumers so there is a single definition of "scan the repo."

    Maps are keyed by repo-relative posix path. ``signatures`` and ``refs`` carry an
    entry for every file in ``files``; ``parse_errors`` carries one only for files
    that failed to parse.
    """

    root: Path
    files: tuple[str, ...]
    signatures: Mapping[str, tuple[Signature, ...]]
    refs: Mapping[str, tuple[ImportRef, ...]]
    parse_errors: Mapping[str, str]
    graph: ImportGraph
    ranks: Mapping[str, float]
    inbound: Mapping[str, int]
    parser_name: str
    reused_files: int = 0
    parsed_files: int = 0


def scan_repo(
    root: Path,
    *,
    parser: Parser | None = None,
    cache_dir: Path | None = None,
) -> RepoScan:
    """Walk, parse and rank ``root`` into a :class:`RepoScan` — no budget applied.

    The shared front half of :func:`build_repo_map`: it walks the tree (honouring
    ``.gitignore``), reads and parses every code file once (signatures + imports
    together), builds the import graph and runs pagerank over it. ``cache_dir``
    behaves exactly as it does for :func:`build_repo_map` — ``None`` reads and writes
    nothing; otherwise the per-file ``(path, mtime_ns, size)`` cache is reused.
    """
    active = parser or PythonAstParser()
    files = [path for path in walk_repo(root) if path.suffix in CODE_SUFFIXES]
    cache = _load_cache(cache_dir, root, active.name)
    scanned: dict[str, _Scanned] = {}
    reused = 0
    for path in files:
        rel = path.relative_to(root).as_posix()
        try:
            stat = path.stat()
        except OSError:
            continue
        cached = cache.get(rel)
        if (
            cached is not None
            and cached.mtime_ns == stat.st_mtime_ns
            and cached.size == stat.st_size
        ):
            scanned[rel] = cached
            reused += 1
            continue
        scanned[rel] = _scan_file(path, rel, stat.st_mtime_ns, stat.st_size, active)

    graph = build_import_graph({rel: item.refs for rel, item in scanned.items()})
    ranks = pagerank(graph.nodes, graph.edges)
    inbound = graph.inbound_counts()
    _save_cache(cache_dir, root, active.name, scanned)
    return RepoScan(
        root=root,
        files=tuple(sorted(scanned)),
        signatures={rel: item.signatures for rel, item in scanned.items()},
        refs={rel: item.refs for rel, item in scanned.items()},
        parse_errors={rel: item.parse_error for rel, item in scanned.items() if item.parse_error},
        graph=graph,
        ranks=ranks,
        inbound=inbound,
        parser_name=active.name,
        reused_files=reused,
        parsed_files=len(scanned) - reused,
    )


def build_repo_map(
    root: Path,
    *,
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    parser: Parser | None = None,
    cache_dir: Path | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> RepoMap:
    """Walk, rank, parse and budget ``root`` into a repo map.

    ``cache_dir`` is caller-supplied; with ``None`` nothing is written and nothing
    is read. The cache is keyed per file on ``(path, mtime_ns, size)`` plus the
    parser name, so one touched file re-parses one file and everything else is
    reused — then the graph is re-ranked from scratch, because one new import can
    change every rank.
    """
    if budget_tokens <= 0:
        raise ValueError("build_repo_map: budget_tokens must be positive")
    if top_n <= 0:
        raise ValueError("build_repo_map: top_n must be positive")
    scan = scan_repo(root, parser=parser, cache_dir=cache_dir)
    entries = tuple(
        FileEntry(
            path=rel,
            rank=scan.ranks.get(rel, 0.0),
            inbound=scan.inbound.get(rel, 0),
            signatures=scan.signatures.get(rel, ()),
            parse_error=scan.parse_errors.get(rel, ""),
        )
        for rel in scan.files
    )
    considered = sorted(entries, key=lambda e: (-e.rank, e.path))[:top_n]
    kept, dropped = _fit_budget(considered, budget_tokens, len(considered), scan.parser_name)
    return replace(
        _assemble(kept, dropped, budget_tokens, len(considered), scan.parser_name),
        reused_files=scan.reused_files,
        parsed_files=scan.parsed_files,
    )


def _assemble(
    kept: Sequence[FileEntry],
    dropped: Sequence[FileEntry],
    budget_tokens: int,
    considered: int,
    parser_name: str,
) -> RepoMap:
    ordered = tuple(sorted(kept, key=lambda e: e.path))
    draft = RepoMap(
        entries=ordered,
        dropped_count=len(dropped),
        dropped_leaves=sum(1 for entry in dropped if entry.is_leaf),
        dropped_paths=tuple(sorted(entry.path for entry in dropped)),
        budget_tokens=budget_tokens,
        considered=considered,
        parser_name=parser_name,
    )
    # The marker's wording depends on ``over_budget``, which depends on the
    # estimate, which depends on the marker. Two passes settle it (the note can
    # only ever make the text longer, so it cannot oscillate); the third is slack.
    estimate = estimate_tokens(draft.render())
    for _ in range(3):
        draft = replace(draft, token_estimate=estimate)
        settled = estimate_tokens(draft.render())
        if settled == estimate:
            break
        estimate = settled
    return replace(draft, token_estimate=estimate)


def _fit_budget(
    entries: Sequence[FileEntry], budget_tokens: int, considered: int, parser_name: str
) -> tuple[list[FileEntry], list[FileEntry]]:
    """Drop lowest-pagerank leaves, then lowest-pagerank non-leaves, until it fits.

    Leaves first because a file nothing imports is the one whose absence costs the
    model least: it cannot be the thing everything else is built on.

    Two candidates are considered at every drop count, and that is the whole
    subtlety. On a small repo with a small budget the truncation marker is a large
    fraction of the budget, so the total is *not* monotone in the number of files
    dropped — dropping the first file can make the rendered map bigger, because it
    adds the marker. A loop that only chased "total under budget" therefore emptied
    the map at some budgets while a *smaller* budget kept two files, which is
    indefensible. So:

    * prefer the largest set of files whose **whole** render fits the budget;
    * failing that (only the marker fits, or not even that), take the largest set
      whose **listing** fits and let the marker overflow, exactly as
      :mod:`ronin.context.budget` treats its own marker as a floor.

    An empty map is never the right answer while one file's signatures would fit:
    the tokens are better spent on the top-ranked file, and the marker says the
    budget was exceeded.
    """
    order = sorted(entries, key=lambda e: (0 if e.is_leaf else 1, e.rank, e.path))
    strict: tuple[list[FileEntry], list[FileEntry]] | None = None
    listing: tuple[list[FileEntry], list[FileEntry]] | None = None
    for count in range(len(order) + 1):
        dropped = order[:count]
        kept = [entry for entry in entries if entry not in dropped]
        candidate = _assemble(kept, dropped, budget_tokens, considered, parser_name)
        # Drop counts ascend, so the first candidate to satisfy a rule is the one
        # with the most files under it.
        if strict is None and kept and candidate.token_estimate <= budget_tokens:
            strict = (kept, list(dropped))
        listing_cost = candidate.token_estimate - estimate_tokens(candidate.marker())
        if listing is None and listing_cost <= budget_tokens:
            listing = (kept, list(dropped))
        if strict is not None:
            break
    chosen = strict or listing
    if chosen is None:
        return [], list(order)
    return chosen


# --------------------------------------------------------------------------- #
# scanning + cache
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Scanned:
    """One file's cacheable scan result."""

    mtime_ns: int
    size: int
    signatures: tuple[Signature, ...] = ()
    refs: tuple[ImportRef, ...] = ()
    parse_error: str = ""


def _scan_file(path: Path, rel: str, mtime_ns: int, size: int, parser: Parser) -> _Scanned:
    """Read once, extract signatures and imports together.

    One read and (for Python) one ``ast.parse`` serves both, so there is no reason
    to defer signature extraction to a second pass over the top-ranked files only.
    """
    if size > MAX_FILE_BYTES:
        return _Scanned(
            mtime_ns=mtime_ns,
            size=size,
            parse_error=f"skipped: {size} bytes exceeds the {MAX_FILE_BYTES}-byte limit",
        )
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return _Scanned(mtime_ns=mtime_ns, size=size, parse_error=f"unreadable: {exc}")
    if b"\x00" in raw[:1024]:
        return _Scanned(mtime_ns=mtime_ns, size=size, parse_error="skipped: binary")
    source = raw.decode("utf-8", errors="replace")

    refs: tuple[ImportRef, ...] = ()
    signatures: tuple[Signature, ...] = ()
    error = ""
    suffix = path.suffix
    if suffix in _PYTHON_SUFFIXES:
        try:
            tree = ast.parse(source, filename=rel)
        except SyntaxError as exc:
            error = f"python syntax error at line {exc.lineno}: {exc.msg}"
        else:
            refs = python_imports(tree)
    elif suffix in _SCRIPT_SUFFIXES:
        refs = script_imports(source)

    if not error and parser.supports(rel):
        try:
            signatures = tuple(parser.signatures(source, rel))
        except ParserUnavailable as exc:
            error = f"{parser.name} unavailable: {exc}"
        except SyntaxError as exc:
            error = f"syntax error at line {exc.lineno}: {exc.msg}"
        except Exception as exc:  # a third-party parser may raise anything at all
            error = f"{parser.name} failed: {type(exc).__name__}: {exc}"
    return _Scanned(
        mtime_ns=mtime_ns,
        size=size,
        signatures=signatures,
        refs=refs,
        parse_error=error,
    )


def cache_path(cache_dir: Path, root: Path) -> Path:
    """One JSON file per repo root, named by a digest of the absolute path."""
    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"repomap-{digest}.json"


def _load_cache(cache_dir: Path | None, root: Path, parser_name: str) -> dict[str, _Scanned]:
    if cache_dir is None:
        return {}
    path = cache_path(cache_dir, root)
    try:
        payload: object = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    if payload.get("version") != CACHE_VERSION or payload.get("parser") != parser_name:
        return {}
    files = payload.get("files")
    if not isinstance(files, dict):
        return {}
    out: dict[str, _Scanned] = {}
    for rel, entry in files.items():
        if not isinstance(rel, str) or not isinstance(entry, dict):
            continue
        try:
            out[rel] = _Scanned(
                mtime_ns=int(entry["mtime_ns"]),
                size=int(entry["size"]),
                signatures=tuple(Signature.from_json(item) for item in entry.get("signatures", [])),
                refs=tuple(ImportRef.from_json(item) for item in entry.get("refs", [])),
                parse_error=str(entry.get("parse_error", "")),
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _save_cache(
    cache_dir: Path | None,
    root: Path,
    parser_name: str,
    scanned: Mapping[str, _Scanned],
) -> None:
    """Write the whole scan. Failures are swallowed: a cache is never load-bearing."""
    if cache_dir is None:
        return
    payload = {
        "version": CACHE_VERSION,
        "parser": parser_name,
        "root": str(root.resolve()),
        "files": {
            rel: {
                "mtime_ns": item.mtime_ns,
                "size": item.size,
                "signatures": [signature.to_json() for signature in item.signatures],
                "refs": [ref.to_json() for ref in item.refs],
                "parse_error": item.parse_error,
            }
            for rel, item in sorted(scanned.items())
        },
    }
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path(cache_dir, root).write_bytes(
            json.dumps(payload, sort_keys=True, indent=1).encode("utf-8")
        )
    except OSError:
        return
