"""Incremental, markup-safe rendering for unified diffs.

The parser accepts chunks from a stream but only turns complete lines into diff
rows. Rendering uses Rich ``Text`` throughout, so file content is never treated
as terminal markup.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from rich.syntax import Syntax
from rich.text import Text

from .code_tools import diff_rows

_EXT_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript", ".tsx": "tsx",
    ".jsx": "jsx", ".json": "json", ".md": "markdown", ".sh": "bash", ".bash": "bash",
    ".toml": "toml", ".yaml": "yaml", ".yml": "yaml", ".html": "html",
    ".css": "css", ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".sql": "sql", ".php": "php", ".swift": "swift",
    ".kt": "kotlin",
}


def language_for(path: str | None) -> str:
    if not path:
        return "text"
    from os.path import splitext

    return _EXT_LANG.get(splitext(path)[1].lower(), "text")


def _fit(text: Text, width: int) -> Text:
    if text.cell_len <= width:
        return text
    if width <= 3:
        text.truncate(width, overflow="crop")
        return text
    text.truncate(width - 3, overflow="crop")
    text.append("...")
    return text


def render_unified_diff(
    diff: str,
    *,
    path: str | None = None,
    width: int = 100,
    max_rows: int | None = 14,
) -> list[Text]:
    """Render one unified diff into fixed-width terminal rows.

    ``max_rows=None`` renders every row. The returned Text objects are suitable
    for both live and non-live consoles; Rich honors ``NO_COLOR`` at print time.
    """
    width = max(24, min(width or 100, 120))
    rows = diff_rows(diff)
    if not rows:
        return [_fit(Text(line, style="dim"), width) for line in diff.splitlines()]

    hidden = 0
    if max_rows is not None and len(rows) > max_rows + 2:
        hidden = len(rows) - max_rows
        rows = rows[:max_rows]

    from .theme import CODE_THEME

    syntax = Syntax("", language_for(path), theme=CODE_THEME, background_color="default")
    out: list[Text] = []
    sign_style = {"add": "bold #73daca", "del": "bold #f7768e", "ctx": "#3b4261"}
    backgrounds = {"add": "#15291c", "del": "#2c161b"}

    for row_data in rows:
        if row_data["kind"] == "sep":
            out.append(Text("       |", style="#3b4261"))
            continue
        line_number = f"{row_data['lineno']:>4}" if row_data["lineno"] is not None else "    "
        kind = row_data["kind"]
        sign = {"add": "+", "del": "-", "ctx": " "}[kind]
        code = row_data["text"]
        highlighted = syntax.highlight(code) if code.strip() else Text(code)
        highlighted.rstrip()
        if kind == "del":
            highlighted.stylize("dim")
        row = Text(f" {line_number} ", style="#3b4261")
        row.append(f"{sign} ", style=sign_style[kind])
        row.append_text(highlighted)
        _fit(row, width)
        if kind in backgrounds:
            if row.cell_len < width:
                row.append(" " * (width - row.cell_len))
            row.stylize(f"on {backgrounds[kind]}")
        out.append(row)

    if hidden:
        out.append(_fit(Text(f"       ... {hidden} more lines", style="#3b4261"), width))
    return out


@dataclass
class StreamingDiffRenderer:
    """Collect a streamed unified diff while withholding incomplete lines."""

    path: str | None = None
    width: int = 100
    max_rows: int | None = 14
    _complete: list[str] = field(default_factory=list, init=False)
    _pending: str = field(default="", init=False)

    def feed(self, chunk: str) -> list[Text]:
        """Accept a chunk and return the current stable render of complete lines."""
        combined = self._pending + chunk
        parts = combined.splitlines(keepends=True)
        self._pending = ""
        if parts and not parts[-1].endswith(("\n", "\r")):
            self._pending = parts.pop()
        self._complete.extend(parts)
        return self.render()

    def finish(self) -> list[Text]:
        """Flush the final unterminated line and return the completed render."""
        if self._pending:
            self._complete.append(self._pending)
            self._pending = ""
        return self.render()

    def render(self) -> list[Text]:
        return render_unified_diff(
            "".join(self._complete), path=self.path, width=self.width, max_rows=self.max_rows,
        )
