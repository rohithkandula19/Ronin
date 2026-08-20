"""Persistent, incremental repo index — bounded retrieval for LARGE codebases.

``repo_map`` (BM25) and ``embeddings`` are great, but both rebuild their view of
the repo *in memory, per session*: fine for a few thousand files, wasteful on a
giant monorepo, and neither caps how much it hands the model. On a big repo the
real failure mode is the **context window**: front-load too many files and the
turn either errors or drowns the signal.

This module is the scalability lever. It keeps a **persistent SQLite index** at
``.ronin/index.db`` — one row per file (path, size, mtime, language, symbol list,
a compact head summary) — built once and then **incrementally refreshed**: only
files whose ``mtime`` changed are re-read, so a refresh on a 50k-file repo touches
just what you edited. Retrieval is then **retrieval-budgeted**: rank files by the
query, then greedily pick the highest-ranked ones that fit a token budget, so the
context injected stays bounded no matter how big the repo is.

Design goals (so it stays trustworthy and easy to test):
- **Pure, deterministic core.** ``rank_files`` and ``budget_select`` are plain
  functions over a ``list[dict]`` — no DB, no clock, no I/O — so they're trivially
  unit-testable and give identical output for identical input.
- **Stdlib only** (``sqlite3``) **+ rich** for the CLI table. No model, no network,
  no third-party dep — it runs anywhere ronin runs, fully offline.
- **Reuses the proven tokenizer & symbol extraction** from ``repo_map`` so the
  ranking matches the rest of ronin's retrieval surface (identifier-aware: splits
  ``snake_case`` / ``camelCase``).

The intended wiring (see ``CODE_MODE_INTEGRATION_NOTE``) is for ``code_mode``'s
context step to call ``query_index`` on large repos instead of holding a whole
in-memory map — front-loading the right files under a fixed token budget.
"""
from __future__ import annotations

import math
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

# Reuse ronin's identifier-aware tokenizer + symbol extractor so this index ranks
# the same way the rest of the retrieval surface does (one source of truth).
from .repo_map import (
    _IGNORE_DIRS,
    _MAX_BYTES,
    _TEXT_EXT,
    _extract_symbols,
    _tokenize,
)

# How much head text we keep as a per-file "summary" (first meaningful lines).
# Compact on purpose: enough signal to rank + show, cheap to store for 50k files.
_SUMMARY_CHARS = 400
_SUMMARY_LINES = 12
# Default ceiling on files walked in one build — a safety rail on pathological
# trees; a real monorepo source tree lands well under this once vendored dirs are
# pruned. Override per-call via ``build_index(..., max_files=...)``.
_MAX_FILES = 50_000
# Rough token estimate: ~4 chars/token (matches code_mode's gauge). Used only by
# the default ``size_of`` so a missing/zero file size never silently costs 0.
_CHARS_PER_TOKEN = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    path     TEXT PRIMARY KEY,
    size     INTEGER NOT NULL,
    mtime    REAL    NOT NULL,
    language TEXT    NOT NULL,
    symbols  TEXT    NOT NULL,   -- space-joined symbol names
    summary  TEXT    NOT NULL    -- compact head slice
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Map extension → a coarse language label (display + light ranking signal).
_LANG_BY_EXT = {
    ".py": "python", ".js": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust",
    ".java": "java", ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp", ".cs": "csharp",
    ".swift": "swift", ".kt": "kotlin", ".scala": "scala", ".sh": "shell",
    ".bash": "shell", ".zsh": "shell", ".sql": "sql", ".md": "markdown",
    ".rst": "rst", ".txt": "text", ".toml": "toml", ".yaml": "yaml",
    ".yml": "yaml", ".json": "json", ".cfg": "config", ".ini": "config",
    ".html": "html", ".css": "css", ".scss": "css", ".vue": "vue",
    ".svelte": "svelte", ".lua": "lua",
}


def _language_for(path: str) -> str:
    return _LANG_BY_EXT.get(Path(path).suffix.lower(), "text")


def _summarize(text: str) -> str:
    """A compact, high-signal head slice: the first non-blank, non-noise lines,
    capped to ``_SUMMARY_CHARS``. Skips decorators / lone braces so the summary
    leads with real content (a docstring, the first def, an import)."""
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s in ("{", "}", "(", ")", "[", "]") or s.startswith("@"):
            continue
        out.append(s)
        if len(out) >= _SUMMARY_LINES:
            break
    return " ".join(out)[:_SUMMARY_CHARS]


# --------------------------------------------------------------------------- #
# Ignore handling (gitignore-style, no dependency)
# --------------------------------------------------------------------------- #
def _load_ignore_names(root: Path) -> set[str]:
    """Directory/file *names* to prune, gathered from ``.gitignore`` /
    ``.roninignore`` on top of the built-in vendored-dir set. Kept deliberately
    simple (name-level, gitignore-ish) so the walk stays fast and dependency-free:
    a line like ``dist/`` or ``node_modules`` prunes that directory anywhere."""
    names: set[str] = set(_IGNORE_DIRS)
    for fname in (".gitignore", ".roninignore"):
        p = root / fname
        if not p.is_file():
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("!"):
                continue
            # Reduce a pattern to a prunable name: drop leading/trailing slashes and
            # globs. ``/dist/`` → ``dist``; ``**/build`` → ``build``. Patterns with
            # interior path separators (``a/b``) are too specific for name-pruning,
            # so we skip them (the built-in dir set covers the common cases).
            token = line.strip("/").split("/")[-1]
            if token and "*" not in token and "?" not in token:
                names.add(token)
    return names


def _walk(root: Path, ignore_names: set[str]):
    """Yield text files under ``root``, pruning ignored / hidden directories in
    place so we never descend into ``node_modules`` / ``.venv`` / ``dist`` etc."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ignore_names and not d.startswith(".")]
        for fn in filenames:
            if Path(fn).suffix.lower() in _TEXT_EXT:
                yield Path(dirpath) / fn


# --------------------------------------------------------------------------- #
# PURE retrieval core — no I/O, fully deterministic, unit-tested.
# --------------------------------------------------------------------------- #
def _doc_terms(entry: dict) -> list[str]:
    """The token bag for a file record, weighting path + symbol names over the
    body summary (×3) — a hit on a file/function *name* is a far stronger signal
    than an incidental mention in the head text. Mirrors ``repo_map``'s weighting
    so ranking is consistent across ronin's retrieval tools."""
    path = str(entry.get("path", ""))
    summary = str(entry.get("summary", ""))
    symbols = entry.get("symbols", [])
    if isinstance(symbols, str):                      # tolerate space-joined form
        symbols = symbols.split()

    terms: list[str] = _tokenize(path.replace("/", " ")) * 3
    for name in symbols:
        terms += _tokenize(str(name)) * 3
    terms += _tokenize(summary)
    # The coarse language label is a light extra signal ("python", "sql", …) so a
    # query that names a language nudges those files up.
    lang = entry.get("language")
    if lang:
        terms.append(str(lang).lower())
    return terms


def rank_files(query: str, file_index: list[dict]) -> list[tuple[str, float]]:
    """Rank index records against ``query`` with BM25 over path + symbols + summary.

    PURE and deterministic: given the same ``query`` and ``file_index`` it returns
    the same ordering every time — no DB, no clock, no filesystem. Each record is a
    dict with at least ``path`` (and ideally ``symbols`` / ``summary`` / ``language``).

    Returns ``[(path, score), ...]`` sorted by score descending; files with a
    zero score are dropped. Ties break by path (ascending) so the order is stable.
    """
    terms = set(_tokenize(query))
    if not terms or not file_index:
        return []

    # Build per-doc term frequencies + document frequencies (classic BM25 setup).
    doc_tfs: list[tuple[str, Counter, int]] = []
    df: dict[str, int] = {}
    total_len = 0
    for entry in file_index:
        path = str(entry.get("path", ""))
        if not path:
            continue
        tokens = _doc_terms(entry)
        tf = Counter(tokens)
        length = max(1, len(tokens))
        doc_tfs.append((path, tf, length))
        total_len += length
        for term in tf:
            df[term] = df.get(term, 0) + 1

    n = len(doc_tfs)
    if n == 0:
        return []
    avgdl = total_len / n
    k1, b = 1.5, 0.75

    scored: list[tuple[str, float]] = []
    for path, tf, length in doc_tfs:
        score = 0.0
        for term in terms:
            freq = tf.get(term)
            if not freq:
                continue
            d = df.get(term, 1)
            idf = math.log(1 + (n - d + 0.5) / (d + 0.5))
            denom = freq + k1 * (1 - b + b * length / avgdl)
            score += idf * (freq * (k1 + 1)) / denom
        if score > 0:
            scored.append((path, score))

    # Deterministic order: score desc, then path asc to break exact-score ties.
    scored.sort(key=lambda ps: (-ps[1], ps[0]))
    return scored


def _default_size_of(path: str) -> int:
    """Fallback token-cost for a path when no ``size_of`` is given: stat the file
    and estimate ~4 chars/token. Missing/unreadable → a small nonzero cost so it
    still consumes budget rather than appearing free."""
    try:
        return max(1, os.path.getsize(path) // _CHARS_PER_TOKEN)
    except OSError:
        return 1


def budget_select(
    ranked: Iterable[tuple[str, float]],
    *,
    token_budget: int,
    size_of: Callable[[str], int],
) -> list[str]:
    """Greedily pick the highest-ranked files that fit ``token_budget``.

    PURE and deterministic (the only impurity is whatever ``size_of`` does). Walks
    ``ranked`` **in the order given** (so callers pass ``rank_files`` output —
    highest first) and keeps each file whose estimated token cost still fits the
    remaining budget; a file too big to fit is skipped, but cheaper lower-ranked
    files after it can still be picked, so the budget is packed well rather than
    abandoned at the first oversized file.

    ``size_of(path) -> int`` returns a file's token cost (e.g. ``size // 4``).
    Returns the selected paths, still in rank order. A non-positive budget selects
    nothing.
    """
    if token_budget <= 0:
        return []
    chosen: list[str] = []
    remaining = token_budget
    for path, _score in ranked:
        cost = max(0, int(size_of(path)))
        if cost <= remaining:
            chosen.append(path)
            remaining -= cost
        if remaining <= 0:
            break
    return chosen


# --------------------------------------------------------------------------- #
# Persistent SQLite index — built once, incrementally refreshed.
# --------------------------------------------------------------------------- #
def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _load_existing(conn: sqlite3.Connection) -> dict[str, float]:
    """Map of ``path → mtime`` already in the index, so a refresh can tell which
    files are unchanged (skip), changed (re-read), or gone (delete)."""
    rows = conn.execute("SELECT path, mtime FROM files").fetchall()
    return {r["path"]: r["mtime"] for r in rows}


def build_index(root: Path | str, db_path: Path | str, *,
                max_files: int = _MAX_FILES) -> int:
    """Build or **incrementally refresh** the SQLite index at ``db_path`` for
    ``root``; return the number of files indexed (the live total after the refresh).

    Incremental by design: a file already in the index with an unchanged ``mtime``
    is left untouched (not re-read), a changed file is re-read and updated, and a
    file that has disappeared is removed. So the first build is a full scan and
    every later build only pays for what you actually edited — the property that
    keeps this cheap on a huge repo.
    """
    root = Path(root).resolve()
    db_path = Path(db_path)
    ignore_names = _load_ignore_names(root)

    conn = _connect(db_path)
    try:
        existing = _load_existing(conn)
        seen: set[str] = set()
        upserts: list[tuple] = []
        count = 0

        for path in _walk(root, ignore_names):
            try:
                st = path.stat()
            except OSError:
                continue
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                continue
            seen.add(rel)
            count += 1
            if count > max_files:
                break

            prior = existing.get(rel)
            # Unchanged file (same mtime) — skip the read entirely. This is the
            # incremental fast path: on a refresh almost everything lands here.
            if prior is not None and abs(prior - st.st_mtime) < 1e-6:
                continue
            if st.st_size > _MAX_BYTES:
                # Too big to summarize usefully (lockfile / data blob); still index
                # the metadata so it's rankable by path/symbols-less, but no body.
                upserts.append((rel, st.st_size, st.st_mtime,
                                _language_for(rel), "", ""))
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            symbols = [name for name, _ in _extract_symbols(text)]
            upserts.append((
                rel, st.st_size, st.st_mtime, _language_for(rel),
                " ".join(symbols), _summarize(text),
            ))

        if upserts:
            conn.executemany(
                "INSERT INTO files (path, size, mtime, language, symbols, summary) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(path) DO UPDATE SET "
                "size=excluded.size, mtime=excluded.mtime, language=excluded.language, "
                "symbols=excluded.symbols, summary=excluded.summary",
                upserts,
            )
        # Drop records for files that vanished since the last build.
        stale = [p for p in existing if p not in seen]
        if stale:
            conn.executemany("DELETE FROM files WHERE path = ?",
                             [(p,) for p in stale])

        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('root', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(root),))
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('built_at', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(time.time()),))
        conn.commit()

        return conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
    finally:
        conn.close()


def load_index(db_path: Path | str) -> list[dict]:
    """Read all file records from the index into the plain ``list[dict]`` shape the
    pure ``rank_files`` consumes. Returns ``[]`` if the index doesn't exist yet."""
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT path, size, mtime, language, symbols, summary FROM files"
        ).fetchall()
    finally:
        conn.close()
    out: list[dict] = []
    for r in rows:
        out.append({
            "path": r["path"],
            "size": r["size"],
            "mtime": r["mtime"],
            "language": r["language"],
            "symbols": r["symbols"].split() if r["symbols"] else [],
            "summary": r["summary"],
        })
    return out


def query_index(query: str, db_path: Path | str, *,
                token_budget: int = 8000) -> list[str]:
    """Rank the persistent index against ``query`` and **budget-select** the top
    files that fit ``token_budget`` — the retrieval-budgeted entry point.

    Returns the chosen paths (rank order, highest first). Combines the pure
    ``rank_files`` + ``budget_select`` over records loaded from ``db_path``; each
    file's token cost is estimated from its stored ``size`` (~4 chars/token), so
    no filesystem access is needed at query time. Empty if the index is empty.
    """
    index = load_index(db_path)
    if not index:
        return []
    ranked = rank_files(query, index)
    # Token cost straight from the stored size — keeps query time pure of stat()s.
    sizes = {e["path"]: max(1, int(e.get("size", 0)) // _CHARS_PER_TOKEN)
             for e in index}
    return budget_select(ranked, token_budget=token_budget,
                         size_of=lambda p: sizes.get(p, 1))


def index_db_path(root: Path | str = ".") -> Path:
    """Canonical index location for a repo: ``<root>/.ronin/index.db``."""
    return Path(root).resolve() / ".ronin" / "index.db"


def index_stats(db_path: Path | str) -> dict:
    """Quick summary of the index: file count and a per-language breakdown — what
    the ``ronin index`` command reports after a build. ``{}`` if absent."""
    db_path = Path(db_path)
    if not db_path.exists():
        return {}
    conn = _connect(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM files").fetchone()["c"]
        langs = conn.execute(
            "SELECT language, COUNT(*) AS c FROM files "
            "GROUP BY language ORDER BY c DESC"
        ).fetchall()
        built = conn.execute(
            "SELECT value FROM meta WHERE key = 'built_at'").fetchone()
    finally:
        conn.close()
    return {
        "files": total,
        "languages": [(r["language"], r["c"]) for r in langs],
        "built_at": float(built["value"]) if built else None,
    }


# --------------------------------------------------------------------------- #
# CLI entry — the `ronin index` command.
#
# Lives here (not in main.py) so the whole feature is one module. To wire it,
# main.py registers this on its Typer app once:
#
#     from .repo_index import index as _index_cmd
#     app.command(name="index")(_index_cmd)
#
# Signature (the requested entry point):
#     ronin index [--root PATH] [--query TEXT] [--budget INT]
# --------------------------------------------------------------------------- #
def index(
    root: "Path" = None,                 # type: ignore[assignment]
    query: "str | None" = None,
    budget: int = 8000,
) -> None:
    """🗂  Build / refresh ronin's persistent repo index (``.ronin/index.db``) so the
    coding agent can work on LARGE repos without blowing the context window. The
    index is incremental — a refresh only re-reads files whose mtime changed.

    --query "<task>" previews the budget-selected files this index would front-load
    for that task (rank + token-budget select), without running the agent.
    --budget sets the token budget for that preview (default 8000).
    """
    # Typer is only imported here so importing this module (for the pure functions
    # / tests) never requires the CLI dependency stack.
    import typer
    from rich.console import Console
    from rich.table import Table

    console = Console()
    root_path = Path(root) if root is not None else Path(".")
    db_path = index_db_path(root_path)

    if query:
        # Preview mode: don't rebuild — rank against whatever is indexed and show
        # the budget-selected set, so the user sees exactly what context a task
        # would pull. (If the index is missing, point them at a build first.)
        if not db_path.exists():
            console.print("[yellow]No index yet — run `ronin index` first to build "
                          f"it.[/yellow] [dim]({db_path})[/dim]")
            raise typer.Exit(1)
        picked = query_index(query, db_path, token_budget=budget)
        if not picked:
            console.print(f"[dim]No files matched {query!r} within "
                          f"{budget} tokens.[/dim]")
            return
        table = Table(title=f"Context preview for {query!r}  "
                            f"(budget {budget} tokens)",
                      show_header=True, header_style="bold cyan")
        table.add_column("#", justify="right", style="dim")
        table.add_column("file", style="cyan")
        for i, path in enumerate(picked, 1):
            table.add_row(str(i), path)
        console.print(table)
        console.print(f"[dim]→ {len(picked)} file(s) would be front-loaded; "
                      "the agent reads the rest on demand.[/dim]")
        return

    # Build / refresh mode.
    console.print(f"[#6b7089]indexing[/#6b7089] [bold]{root_path}[/bold] "
                  f"[dim]→ {db_path}[/dim]")
    t0 = time.time()
    n = build_index(root_path, db_path)
    elapsed = time.time() - t0

    stats = index_stats(db_path)
    console.print(f"[green]✓[/green] indexed [bold]{n}[/bold] files "
                  f"[dim]in {elapsed:.2f}s[/dim]")
    langs = stats.get("languages", [])
    if langs:
        table = Table(show_header=True, header_style="bold cyan", box=None)
        table.add_column("language", style="cyan")
        table.add_column("files", justify="right")
        for lang, c in langs[:12]:
            table.add_row(lang, str(c))
        console.print(table)
    console.print("[dim]preview what a task would pull: "
                  "[/dim][cyan]ronin index --query \"<task>\"[/cyan]")


# --------------------------------------------------------------------------- #
# Integration note for the code-mode context policy.
# --------------------------------------------------------------------------- #
CODE_MODE_INTEGRATION_NOTE = """\
How code_mode uses this on LARGE repos
======================================

On the first interactive turn, `code_mode.py` prefers the persistent index when
it exists and falls back to the in-memory repo map when it does not. The shared
`context_policy.resolve_context_policy(config)` supplies the retrieval budget, so
the indexed set stays aligned with the selected model's available input window:

    from .repo_index import index_db_path, query_index, build_index
    from .context_policy import resolve_context_policy

    db = index_db_path(root)
    policy = resolve_context_policy(config)
    paths = query_index(task, db, token_budget=policy.retrieval_budget_tokens)
    if paths:
        block = "## Relevant code (ronin repo index)\\n" + \\
                "\\n".join(f"- {p}" for p in paths)
        system += "\\n\\n" + block

Why this scales where the in-memory map strains:
- **Bounded context.** `query_index` runs `rank_files` then `budget_select`, so the
  injected set never exceeds `token_budget` — context stays constant-ish whether
  the repo is 500 files or 500k. That's the core win for the context window.
- **Persistent + incremental.** The DB survives across runs and refreshes only
  changed files (mtime check), so there's no per-session full re-walk — cold-start
  cost is paid once, not every turn.
- **Same cache-safety contract.** Inject only on the first turn (no
  `message_history`) exactly as today, so the system prefix stays stable across a
  turn and the prompt cache isn't invalidated.

Keep it non-blocking like the current path: if the DB is cold, kick `build_index`
off in a background thread (mirror `context_engine._ensure_background_build`) and
skip injection for that one turn rather than stalling it.
"""
