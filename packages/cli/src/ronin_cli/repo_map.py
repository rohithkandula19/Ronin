"""Semantic repo map — relevance-ranked code retrieval (ronin's "find the right
code" lever).

Ask for a *concept* — "how is auth handled", "where config is loaded" — and get
the most relevant files plus their symbol outlines, ranked. It's the
lightweight, dependency-free counterpart to embedding search: an
*identifier-aware* tokenizer (splits ``snake_case`` and ``camelCase``) feeding
classic **BM25**, so it runs anywhere ronin runs — no model, no API key, no heavy
dependency. It complements the exact-match ``search_files`` (grep) and the
symbol-precise LSP tools: use ``repo_map`` to *locate* the right files by meaning,
then ``read_file`` them.
"""
from __future__ import annotations

import math
import os
import re
from collections import Counter
from pathlib import Path

# Directories that never carry first-party source worth indexing.
_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".next", ".turbo", ".idea",
    ".tox", ".gradle", "target", "vendor", ".cache", "coverage", ".coverage",
}
# Text extensions we treat as code/docs (skip binaries, images, data blobs).
_TEXT_EXT = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".swift", ".kt", ".scala", ".sh",
    ".bash", ".zsh", ".sql", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml",
    ".json", ".cfg", ".ini", ".html", ".css", ".scss", ".vue", ".svelte", ".lua",
}
_MAX_FILES = 4000
_MAX_BYTES = 200_000   # skip files larger than this (lockfiles, data, build output)

# Symbol definitions per language family — capture (name, line). Order matters:
# the first pattern that matches a line wins.
_SYMBOL_RES = (
    re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"),                        # python
    re.compile(r"^\s*class\s+([A-Za-z_]\w*)"),                                   # py/ts/java class
    re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\*?\s*([A-Za-z_]\w*)"),  # js/ts fn
    re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\(?[^=]*=>"),  # arrow fn
    re.compile(r"^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_]\w*)"),   # ts types
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"),                   # go
    re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"),             # rust
)

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z0-9]+|[A-Z]+")


def _split_identifier(tok: str) -> list[str]:
    """``loadUserConfig`` → ['load', 'user', 'config']; ``HTTPServer`` → ['http', 'server']."""
    return [p.lower() for p in _CAMEL_RE.findall(tok) if p]


def _tokenize(text: str) -> list[str]:
    """Identifier-aware tokens: each word lowercased, plus its camelCase pieces."""
    toks: list[str] = []
    for word in _TOKEN_RE.findall(text):
        low = word.lower()
        toks.append(low)
        for sub in _split_identifier(word):
            if sub != low and len(sub) > 1:
                toks.append(sub)
    return toks


def _extract_symbols(text: str, *, limit: int = 50) -> list[tuple[str, int]]:
    """Top-level symbol outline: ``[(name, line_number), ...]``."""
    syms: list[tuple[str, int]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if len(line) > 400:
            continue
        for rx in _SYMBOL_RES:
            m = rx.match(line)
            if m:
                syms.append((m.group(1), i))
                break
        if len(syms) >= limit:
            break
    return syms


def _walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        # prune ignored / hidden directories in place
        dirnames[:] = [d for d in dirnames
                       if d not in _IGNORE_DIRS and not d.startswith(".")]
        for fn in filenames:
            if Path(fn).suffix.lower() in _TEXT_EXT:
                yield Path(dirpath) / fn


class _Doc:
    __slots__ = ("path", "tf", "length", "symbols")

    def __init__(self, path: str, tf: Counter, length: int,
                 symbols: list[tuple[str, int]]) -> None:
        self.path = path
        self.tf = tf
        self.length = length
        self.symbols = symbols


class RepoIndex:
    """A BM25 index over a repository's text files. Build once, search many."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.docs: list[_Doc] = []
        self.df: dict[str, int] = {}
        self.avgdl = 1.0

    def build(self) -> "RepoIndex":
        docs: list[_Doc] = []
        df: dict[str, int] = {}
        total_len = 0
        for path in _walk(self.root):
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            if len(raw) > _MAX_BYTES:
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue   # binary / non-UTF-8
            rel = str(path.relative_to(self.root))
            symbols = _extract_symbols(text)
            # Weight path + symbol names higher than body text (×3) — a hit on a
            # file/function name is a much stronger signal than a body mention.
            tokens = _tokenize(rel.replace("/", " ")) * 3
            for name, _ in symbols:
                tokens += _tokenize(name) * 3
            tokens += _tokenize(text)
            tf = Counter(tokens)
            docs.append(_Doc(rel, tf, max(1, len(tokens)), symbols))
            for term in tf:
                df[term] = df.get(term, 0) + 1
            total_len += len(tokens)
            if len(docs) >= _MAX_FILES:
                break
        self.docs = docs
        self.df = df
        self.avgdl = (total_len / len(docs)) if docs else 1.0
        return self

    def search(self, query: str, k: int = 8) -> list[tuple[float, _Doc]]:
        terms = set(_tokenize(query))
        if not terms or not self.docs:
            return []
        n = len(self.docs)
        k1, b = 1.5, 0.75
        scored: list[tuple[float, _Doc]] = []
        for doc in self.docs:
            score = 0.0
            for term in terms:
                tf = doc.tf.get(term)
                if not tf:
                    continue
                df = self.df.get(term, 1)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                denom = tf + k1 * (1 - b + b * doc.length / self.avgdl)
                score += idf * (tf * (k1 + 1)) / denom
            if score > 0:
                scored.append((score, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]


# Per-root index cache so the scan happens once per session, not per call.
_CACHE: dict[str, RepoIndex] = {}


def get_index(root: Path | str = ".", *, refresh: bool = False) -> RepoIndex:
    key = str(Path(root).resolve())
    if refresh or key not in _CACHE:
        _CACHE[key] = RepoIndex(root).build()
    return _CACHE[key]


def cached_index(root: Path | str = ".") -> RepoIndex | None:
    """The already-built index for ``root``, or ``None`` if it hasn't been built
    yet — non-blocking, so callers can decide whether to build in the background
    rather than stalling a turn on a cold index."""
    return _CACHE.get(str(Path(root).resolve()))


def repo_map(query: str, root: Path | str = ".", k: int = 8, *,
             refresh: bool = False) -> str:
    """Return a compact, ranked map of the files most relevant to ``query``."""
    idx = get_index(root, refresh=refresh)
    if not idx.docs:
        return "Repo map is empty (no indexable text files found)."
    results = idx.search(query, max(1, min(k, 25)))
    if not results:
        return f"No relevant files for {query!r} (indexed {len(idx.docs)} files)."
    lines = [f"Most relevant files for {query!r} "
             f"(ranked over {len(idx.docs)} indexed files):", ""]
    for _score, doc in results:
        lines.append(doc.path)
        if doc.symbols:
            outline = "  ".join(f"{name}:{ln}" for name, ln in doc.symbols[:12])
            lines.append(f"    {outline}")
    lines.append("")
    lines.append("→ read_file the ones that look right.")
    return "\n".join(lines)
