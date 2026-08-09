"""docforge: normalise text pulled out of uploaded documents."""

from docforge.textnorm import ensure_trailing_newline, normalize_bytes, normalize_newlines

__all__ = ["ensure_trailing_newline", "normalize_bytes", "normalize_newlines"]
