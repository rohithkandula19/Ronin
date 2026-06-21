"""ronin loc — a code-stats dashboard: lines of code by language, file counts,
the biggest files, and a code/comment/blank breakdown, rendered the way the rest
of ronin feels (teal accents, rounded tables, a totals line).

Design — mirrors ``stats.py`` / ``gamify.py``: the classification + counting +
rollup are a **pure, deterministic core** (every function takes its text/rows as
parameters and never touches disk), so it's trivially unit-testable. A single
thin impure layer (``scan``) walks the tree. Stdlib + rich only, no network.
"""
from __future__ import annotations

import os
from collections import Counter, defaultdict
from pathlib import Path

# --------------------------------------------------------------------------- #
# palette (mirrors theme.py / stats.py so every ronin surface matches)
# --------------------------------------------------------------------------- #
TEAL = "#2dd4bf"   # ronin brand / accent
CYAN = "#22d3ee"
GOOD = "#9ece6a"   # soft green — code
WARN = "#e0af68"   # soft amber — comments
SKY = "#7dd3fc"    # soft sky — labels / language names
MUTE = "#6b7089"   # muted slate — secondary text (blanks)
SOFT = "#9aa0b8"   # soft foreground

# --------------------------------------------------------------------------- #
# language model: extension → language, and each language's line-comment prefix
# --------------------------------------------------------------------------- #
# Extension (lowercased, no dot) → friendly language name. Unknown → "Other".
EXT_LANG: dict[str, str] = {
    "py": "Python",
    "pyi": "Python",
    "ts": "TypeScript",
    "tsx": "TypeScript",
    "mts": "TypeScript",
    "cts": "TypeScript",
    "js": "JavaScript",
    "jsx": "JavaScript",
    "mjs": "JavaScript",
    "cjs": "JavaScript",
    "rs": "Rust",
    "go": "Go",
    "rb": "Ruby",
    "java": "Java",
    "kt": "Kotlin",
    "kts": "Kotlin",
    "swift": "Swift",
    "c": "C",
    "h": "C",
    "cc": "C++",
    "cpp": "C++",
    "cxx": "C++",
    "hpp": "C++",
    "cs": "C#",
    "php": "PHP",
    "scala": "Scala",
    "sh": "Shell",
    "bash": "Shell",
    "zsh": "Shell",
    "fish": "Shell",
    "lua": "Lua",
    "pl": "Perl",
    "r": "R",
    "ex": "Elixir",
    "exs": "Elixir",
    "erl": "Erlang",
    "hs": "Haskell",
    "clj": "Clojure",
    "dart": "Dart",
    "vue": "Vue",
    "svelte": "Svelte",
    "sql": "SQL",
    "md": "Markdown",
    "markdown": "Markdown",
    "rst": "reStructuredText",
    "html": "HTML",
    "htm": "HTML",
    "css": "CSS",
    "scss": "SCSS",
    "sass": "Sass",
    "less": "Less",
    "json": "JSON",
    "yaml": "YAML",
    "yml": "YAML",
    "toml": "TOML",
    "ini": "INI",
    "cfg": "INI",
    "xml": "XML",
    "proto": "Protobuf",
    "graphql": "GraphQL",
    "gql": "GraphQL",
    "tf": "Terraform",
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "mk": "Makefile",
    "txt": "Text",
}

# Some files have no useful extension but a well-known *name*. Matched on the
# lowercased full filename when the extension lookup misses.
NAME_LANG: dict[str, str] = {
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "rakefile": "Ruby",
    "gemfile": "Ruby",
}

# Line-comment prefix(es) per language. A line whose first non-whitespace run
# starts with one of these is counted as a comment. Languages absent here (JSON,
# Text, …) simply have no line comments — every non-blank line is "code".
LINE_COMMENTS: dict[str, tuple[str, ...]] = {
    "Python": ("#",),
    "Ruby": ("#",),
    "Shell": ("#",),
    "Perl": ("#",),
    "R": ("#",),
    "YAML": ("#",),
    "TOML": ("#",),
    "INI": ("#", ";"),
    "Makefile": ("#",),
    "Dockerfile": ("#",),
    "Terraform": ("#",),
    "Elixir": ("#",),
    "GraphQL": ("#",),
    "TypeScript": ("//",),
    "JavaScript": ("//",),
    "Rust": ("//",),
    "Go": ("//",),
    "Java": ("//",),
    "Kotlin": ("//",),
    "Swift": ("//",),
    "C": ("//",),
    "C++": ("//",),
    "C#": ("//",),
    "PHP": ("//", "#"),
    "Scala": ("//",),
    "Dart": ("//",),
    "Vue": ("//",),
    "Svelte": ("//",),
    "SQL": ("--",),
    "Haskell": ("--",),
    "Lua": ("--",),
    "Clojure": (";",),
    "Erlang": ("%",),
}

OTHER = "Other"


# --------------------------------------------------------------------------- #
# pure core: classify a path to a language
# --------------------------------------------------------------------------- #
def classify_language(path: str) -> str:
    """Map a file path to a language by its extension (e.g. ``a.py`` → ``Python``,
    ``a.ts`` / ``a.tsx`` → ``TypeScript``). Falls back to a few well-known
    *filenames* (``Dockerfile``, ``Makefile``), and finally ``"Other"`` for
    anything unrecognised. Pure — only the string matters, nothing is read."""
    name = Path(str(path)).name
    lower = name.lower()
    # extension wins (the part after the last dot)
    if "." in name:
        ext = lower.rsplit(".", 1)[1]
        if ext in EXT_LANG:
            return EXT_LANG[ext]
    # extension-less / unknown-extension well-known names
    if lower in NAME_LANG:
        return NAME_LANG[lower]
    return OTHER


# --------------------------------------------------------------------------- #
# pure core: count code / comment / blank lines in a file's text
# --------------------------------------------------------------------------- #
def count_lines(text: str, lang: str) -> dict:
    """Split ``text`` into ``{code, comment, blank}`` line counts for ``lang``. Pure.

    * **blank** — a line that's empty or only whitespace.
    * **comment** — a non-blank line whose first non-whitespace characters are
      one of ``lang``'s line-comment prefixes (``#`` for Python, ``//`` for
      Rust/TS, ``--`` for SQL, …). Languages with no line-comment syntax never
      report comments. Block comments aren't tracked (kept honest + deterministic
      without a full parser); they count as code.
    * **code** — every other non-blank line.

    The three always sum to the file's line count. A trailing newline does not
    invent an extra blank line (``"a\\nb\\n"`` is two lines, not three)."""
    if not text:
        return {"code": 0, "comment": 0, "blank": 0}

    prefixes = LINE_COMMENTS.get(lang, ())
    lines = text.split("\n")
    # A trailing newline yields a final empty element we shouldn't count as a
    # blank line ("a\n" → ["a", ""]). Drop exactly that artifact.
    if lines and lines[-1] == "":
        lines.pop()

    code = comment = blank = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank += 1
        elif prefixes and stripped.startswith(prefixes):
            comment += 1
        else:
            code += 1
    return {"code": code, "comment": comment, "blank": blank}


# --------------------------------------------------------------------------- #
# pure core: aggregate per-file rows into totals + per-language rollup
# --------------------------------------------------------------------------- #
def aggregate(rows: list[dict]) -> dict:
    """Roll per-file rows up into the figures the dashboard shows. Pure.

    Each row is ``{path, language, code, comment, blank}`` (the shape ``scan``
    yields). Missing/malformed numeric fields are tolerated — coerced to 0 rather
    than raising. Returns::

        {
          "files": int, "code": int, "comment": int, "blank": int,
          "lines": int,                       # code+comment+blank
          "languages": [                      # one per language, busiest first
             {"language", "files", "code", "comment", "blank",
              "lines", "comment_ratio"}, …
          ],
          "biggest": [                        # files by code lines, biggest first
             {"path", "language", "code", "comment", "blank", "lines"}, …
          ],
          "comment_ratio": float,             # overall comment / (code+comment)
        }

    Per-language rows are ordered by code lines descending (ties → language name,
    for determinism). ``biggest`` is ordered by a file's code lines descending
    (ties → path)."""
    files = 0
    tot_code = tot_comment = tot_blank = 0
    by_lang_files: Counter[str] = Counter()
    by_lang_code: dict[str, int] = defaultdict(int)
    by_lang_comment: dict[str, int] = defaultdict(int)
    by_lang_blank: dict[str, int] = defaultdict(int)
    file_rows: list[dict] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        files += 1
        lang = (row.get("language") or OTHER) or OTHER
        code = _safe_int(row.get("code"))
        comment = _safe_int(row.get("comment"))
        blank = _safe_int(row.get("blank"))
        tot_code += code
        tot_comment += comment
        tot_blank += blank
        by_lang_files[lang] += 1
        by_lang_code[lang] += code
        by_lang_comment[lang] += comment
        by_lang_blank[lang] += blank
        file_rows.append(
            {
                "path": str(row.get("path") or ""),
                "language": lang,
                "code": code,
                "comment": comment,
                "blank": blank,
                "lines": code + comment + blank,
            }
        )

    languages: list[dict] = []
    for lang, n in by_lang_files.items():
        c, cm, b = by_lang_code[lang], by_lang_comment[lang], by_lang_blank[lang]
        languages.append(
            {
                "language": lang,
                "files": n,
                "code": c,
                "comment": cm,
                "blank": b,
                "lines": c + cm + b,
                "comment_ratio": _ratio(cm, c),
            }
        )
    # busiest language first; ties broken by name so the order is deterministic.
    languages.sort(key=lambda d: (-d["code"], d["language"]))

    biggest = sorted(file_rows, key=lambda d: (-d["code"], d["path"]))

    return {
        "files": files,
        "code": tot_code,
        "comment": tot_comment,
        "blank": tot_blank,
        "lines": tot_code + tot_comment + tot_blank,
        "languages": languages,
        "biggest": biggest,
        "comment_ratio": _ratio(tot_comment, tot_code),
    }


def _safe_int(value) -> int:
    """Coerce ``value`` to a non-negative int; anything unparseable → 0."""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def _ratio(comment: int, code: int) -> float:
    """Comment density = comment / (code + comment), in ``[0, 1]``. No comment or
    code lines → 0.0 (avoids a divide-by-zero and reads as "no comments")."""
    denom = comment + code
    return (comment / denom) if denom > 0 else 0.0


# --------------------------------------------------------------------------- #
# impure thin layer: walk the tree and count every text file
# --------------------------------------------------------------------------- #
# Directories never descended into (vendored / generated / VCS noise).
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "__pycache__",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".idea",
        ".next",
        "target",
        "coverage",
        ".ronin",
    }
)

# Read at most this many bytes per file to sniff text-ness + count lines — keeps
# a stray multi-GB asset from stalling the walk.
_MAX_BYTES = 2_000_000


def scan(root) -> list[dict]:
    """Walk ``root`` and return a per-file row ``{path, language, code, comment,
    blank}`` for every **text** file, skipping vendored/generated dirs
    (``.git``, ``node_modules``, ``.venv``, ``dist``, ``__pycache__``,
    ``build``, …). Impure (touches disk) but tolerant: unreadable files and
    binary files are simply skipped, never raised.

    ``path`` is relative to ``root`` (POSIX-style) so output is stable across
    machines. Binary detection is a NUL-byte sniff on the read bytes."""
    root_path = Path(root)
    rows: list[dict] = []
    if not root_path.exists():
        return rows
    # a single file is a degenerate "tree" — handle it directly
    if root_path.is_file():
        row = _row_for(root_path, root_path.parent)
        return [row] if row else rows

    for dirpath, dirnames, filenames in os.walk(root_path):
        # prune skip dirs in place so os.walk never descends into them
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not _is_skipped(d)]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            row = _row_for(fpath, root_path)
            if row:
                rows.append(row)
    return rows


def _is_skipped(name: str) -> bool:
    """Belt-and-suspenders: also skip any dir matching a skip name case-folded."""
    return name.lower() in SKIP_DIRS


def _row_for(fpath: Path, base: Path) -> dict | None:
    """Build one ``scan`` row for ``fpath`` (relative to ``base``), or ``None`` if
    it's unreadable, binary, or a symlink/non-regular file. Never raises."""
    try:
        if fpath.is_symlink() or not fpath.is_file():
            return None
        raw = fpath.read_bytes()[:_MAX_BYTES]
    except OSError:
        return None
    if b"\x00" in raw:  # NUL byte → treat as binary, skip
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("latin-1")
        except UnicodeDecodeError:
            return None
    lang = classify_language(fpath.name)
    counts = count_lines(text, lang)
    try:
        rel = fpath.relative_to(base).as_posix()
    except ValueError:
        rel = fpath.as_posix()
    return {"path": rel, "language": lang, **counts}


# --------------------------------------------------------------------------- #
# pure-ish render: draw the dashboard from an aggregate() snapshot
# --------------------------------------------------------------------------- #
def _fmt_int(n: int) -> str:
    """Human number formatting: 4 → '4', 12_345 → '12.3K', 6_400_000 → '6.4M'."""
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return f"{n:,}"


def render_loc(stats: dict, console) -> None:
    """Draw the code-stats dashboard from an :func:`aggregate` snapshot. Impure
    (prints), but takes the ready stats dict so it's deterministic to drive in a
    test. Renders a per-language table, a totals line, and the top-5 biggest
    files — teal accents throughout, matching ``ronin stats``.

    Empty input is a friendly nudge, never a wall of zeros."""
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    from .theme import gradient_text

    head = gradient_text("ronin loc")
    head.append("   lines of code, by the numbers", style=f"bold {MUTE}")
    console.print()
    console.print(head)
    console.print()

    languages = stats.get("languages") or []
    if not languages:
        empty = Text()
        empty.append("ʕ•ᴥ•ʔ\n\n", style=f"bold {WARN}")
        empty.append("no source files found here — point me at a repo with ", style=SOFT)
        empty.append("--root", style=f"bold {TEAL}")
        empty.append(".", style=SOFT)
        console.print(Panel(empty, border_style=TEAL, padding=(1, 4), title=f"[{TEAL}]nothing to count[/{TEAL}]"))
        console.print()
        return

    # ----- per-language table --------------------------------------------- #
    table = Table(
        title=f"[bold {SKY}]by language[/bold {SKY}]",
        border_style=MUTE,
        header_style=f"bold {TEAL}",
        title_justify="left",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("Language", style=SKY, no_wrap=True)
    table.add_column("Files", justify="right", style=SOFT)
    table.add_column("Code", justify="right", style=GOOD)
    table.add_column("Comment", justify="right", style=WARN)
    table.add_column("Blank", justify="right", style=MUTE)
    table.add_column("Comment %", justify="right", style=CYAN)

    for lang in languages:
        table.add_row(
            lang["language"],
            _fmt_int(lang["files"]),
            _fmt_int(lang["code"]),
            _fmt_int(lang["comment"]),
            _fmt_int(lang["blank"]),
            f"{lang['comment_ratio'] * 100:.0f}%",
        )

    # totals row — visually separated, teal-bold so it reads as the bottom line.
    table.add_section()
    table.add_row(
        Text("TOTAL", style=f"bold {TEAL}"),
        Text(_fmt_int(stats.get("files", 0)), style=f"bold {TEAL}"),
        Text(_fmt_int(stats.get("code", 0)), style=f"bold {GOOD}"),
        Text(_fmt_int(stats.get("comment", 0)), style=f"bold {WARN}"),
        Text(_fmt_int(stats.get("blank", 0)), style=f"bold {MUTE}"),
        Text(f"{stats.get('comment_ratio', 0.0) * 100:.0f}%", style=f"bold {CYAN}"),
    )
    console.print(table)
    console.print()

    # ----- totals one-liner ----------------------------------------------- #
    summary = Text()
    summary.append("📦 ", style=WARN)
    summary.append(_fmt_int(stats.get("code", 0)) + " lines of code", style=f"bold {TEAL}")
    summary.append(" across ", style=SOFT)
    summary.append(_fmt_int(stats.get("files", 0)) + " files", style=f"bold {SKY}")
    summary.append(" in ", style=SOFT)
    summary.append(f"{len(languages)} languages", style=f"bold {GOOD}")
    summary.append("  ·  ", style=MUTE)
    summary.append(f"{stats.get('comment_ratio', 0.0) * 100:.0f}% comments", style=f"italic {CYAN}")
    console.print(summary)
    console.print()

    # ----- top-5 biggest files -------------------------------------------- #
    biggest = (stats.get("biggest") or [])[:5]
    if biggest:
        big = Table(
            title=f"[bold {SKY}]biggest files[/bold {SKY}]",
            border_style=MUTE,
            header_style=f"bold {TEAL}",
            title_justify="left",
            expand=True,
            padding=(0, 1),
        )
        big.add_column("File", style=SKY, overflow="fold")
        big.add_column("Lang", style=SOFT, no_wrap=True)
        big.add_column("Code", justify="right", style=GOOD)
        big.add_column("Lines", justify="right", style=SOFT)
        for f in biggest:
            big.add_row(
                f.get("path", ""),
                f.get("language", OTHER),
                _fmt_int(f.get("code", 0)),
                _fmt_int(f.get("lines", 0)),
            )
        console.print(big)
        console.print()


# --------------------------------------------------------------------------- #
# entry point: `ronin loc`
# --------------------------------------------------------------------------- #
def run(root="." , console=None) -> None:
    """The ``ronin loc`` command body: scan ``root``, aggregate, and draw the
    code-stats dashboard. Read-only — it never writes, and only reads text files
    (binaries and vendored/generated dirs are skipped).

    main.py wires this with a thin Typer command (kept out of this module so the
    constraint "only create loc.py" holds)::

        @app.command()
        def loc(
            root: Path = typer.Option(Path("."), "--root", help="Project root to scan."),
        ) -> None:
            \"\"\"A code-stats dashboard: lines of code by language, file counts, the
            biggest files, and comment ratio. Read-only; text files only.\"\"\"
            from .loc import run as run_loc
            run_loc(root=str(root), console=console)
    """
    if console is None:
        from rich.console import Console

        console = Console()
    rows = scan(root)
    stats = aggregate(rows)
    render_loc(stats, console)
