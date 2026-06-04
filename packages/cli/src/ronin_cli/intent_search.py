"""Search code by intent — `ronin grep <natural-language query>`.

Plain grep finds a string; this finds the *function that does the thing*. ronin
extracts every function/class (with its name + docstring + body), scores each one
against your query's terms (name matches weighted highest, then docstring, then
body), and returns the top-ranked locations. The agent can then read the winners
and answer precisely. All scoring is pure and unit-tested — no embeddings, no
network, works fully offline.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin", ".mypy_cache"}

# tiny stopword list so "where do we handle X" reduces to its content words
_STOP = {"the", "a", "an", "of", "to", "in", "on", "do", "we", "is", "are", "for",
         "and", "or", "how", "where", "what", "when", "does", "this", "that", "it",
         "with", "by", "from", "be", "our", "my", "i", "you", "code", "function"}

# a few stems so "retries"/"retrying"/"retry" collide
_SUFFIXES = ("ing", "ies", "ied", "es", "ed", "s")


def _stem(word: str) -> str:
    # strip one inflectional suffix, then a trailing 'e'/'y', so that variants
    # like retry / retries / retrying all collapse to the same root ("retr").
    for suf in _SUFFIXES:
        if len(word) > len(suf) + 2 and word.endswith(suf):
            word = word[: -len(suf)]
            break
    if len(word) > 3 and word[-1] in "ey":
        word = word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    """Lowercase content tokens (stemmed, stopwords + short words dropped). Pure."""
    raw = re.findall(r"[A-Za-z][A-Za-z0-9]+", (text or "").lower())
    return [_stem(w) for w in raw if len(w) >= 2 and w not in _STOP]


@dataclass
class CodeUnit:
    name: str
    kind: str
    file: str
    line: int
    name_tokens: set[str] = field(default_factory=set)
    doc_tokens: set[str] = field(default_factory=set)
    body_tokens: set[str] = field(default_factory=set)


def _split_identifier(name: str) -> list[str]:
    # snake_case + CamelCase → words
    parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace("_", " ")
    return tokenize(parts)


def extract_units(source: str, rel: str) -> list[CodeUnit]:
    """Top-level and method def/class units with tokenized name/doc/body. Pure."""
    out: list[CodeUnit] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        doc = ast.get_docstring(node) or ""
        try:
            body = ast.get_source_segment(source, node) or ""
        except Exception:  # noqa: BLE001
            body = ""
        out.append(CodeUnit(
            name=node.name, kind=kind, file=rel, line=node.lineno,
            name_tokens=set(_split_identifier(node.name)),
            doc_tokens=set(tokenize(doc)),
            body_tokens=set(tokenize(body)),
        ))
    return out


def score_unit(query_terms: list[str], unit: CodeUnit) -> float:
    """Relevance of a unit to the query terms. Name hits weigh most, then
    docstring, then body. Pure and deterministic."""
    terms = set(query_terms)
    if not terms:
        return 0.0
    score = 0.0
    for t in terms:
        if t in unit.name_tokens:
            score += 5.0
        elif t in unit.doc_tokens:
            score += 2.0
        elif t in unit.body_tokens:
            score += 1.0
    # small bonus for covering more of the query (breadth, not just one strong hit)
    covered = sum(1 for t in terms
                  if t in unit.name_tokens or t in unit.doc_tokens or t in unit.body_tokens)
    score += covered / len(terms)
    return score


def search(root: Path | str, query: str, *, limit: int = 12) -> list[tuple[CodeUnit, float]]:
    """Rank code units across the repo by relevance to ``query``. Pure given tree."""
    terms = tokenize(query)
    rp = Path(root).resolve()
    scored: list[tuple[CodeUnit, float]] = []
    for p in rp.rglob("*.py"):
        if any(part in _SKIP_DIRS for part in p.parts) or not p.is_file():
            continue
        rel = str(p.relative_to(rp))
        try:
            src = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for unit in extract_units(src, rel):
            s = score_unit(terms, unit)
            if s > 0:
                scored.append((unit, s))
    scored.sort(key=lambda us: (-us[1], us[0].file, us[0].line))
    return scored[:limit]


def search_prompt(query: str, hits: list[tuple[CodeUnit, float]]) -> str:
    locs = "\n".join(f"  {u.file}:{u.line}  {u.kind} {u.name}" for u, _ in hits[:10])
    return (
        f'Answer this about the codebase: "{query}". These functions/classes ranked '
        "most relevant — read the most promising ones and answer precisely, citing "
        f"file:line. If the real answer is elsewhere, follow the code to it.\n\n"
        f"Top candidates:\n{locs}"
    )
