"""Post-answer grounding check: flag hallucinated symbols in the live coding agent.

This is Ronin's differentiator. After the coding agent produces an answer (and
its edits) in ``run_code_agent``, we check the symbols and file paths the answer
*claims* exist - functions it says it called, attributes it references, files it
says it edited - against what the agent ACTUALLY saw: the files it read during
the turn plus the files it wrote/edited (which now exist in the working tree).
Anything named in the answer that appears nowhere it observed is flagged as a
likely hallucination.

It reuses the existing faithfulness machinery and adds no model call:

- ``sources_from_trace`` (hardening package) already pulls the read-tool results
  out of the agent's trace - the exact evidence the agent observed.
- ``extract_symbols`` / the same substring + dotted-component grounding rules the
  edit guard uses decide whether a referenced symbol is supported.

The check is cheap and deterministic (content-word + symbol presence, all
lexical). It is ON by default for code mode and opt-out via the
``RONIN_GROUNDING_CHECK`` env var (set to ``0``/``off``/``false``/``no``) or a
``grounding_check=False`` config attribute. The result is a short ASCII note the
caller appends to ``res.output`` so it rides along anywhere the output is shown
(the Telegram bot already echoes ``res.output``).

A grounded answer produces no note; only a real flag adds text. Any internal
error degrades to "no note" so the check never breaks a real run.
"""
from __future__ import annotations

import os
import re
from typing import Any

from ronin_hardening import Source, extract_symbols, sources_from_trace

# Edit tools whose proposed new code counts as evidence too: a symbol the agent
# just wrote now exists in the working tree, so referencing it in the answer is
# grounded. Mirrors code_faithfulness.EDIT_TOOLS without importing it (kept local
# so this module has no dependency on the edit-guard wiring).
_EDIT_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "multi_edit"})


def _answer_symbols(answer: str) -> set[str]:
    """Symbols from the answer that we are willing to FLAG - unambiguously code.

    The harness's ``extract_symbols`` (used on the source side) is deliberately
    permissive: it accepts a bare Capitalized word like ``Session`` as a possible
    class name. That is right when scanning code, but in *prose* a lone
    Capitalized word is usually an English sentence-leader ("Done.", "Confirmed:",
    "Added a helper") and flagging it would be noise. So on the answer side we
    keep only the high-confidence shapes: a call ``foo(``, a dotted path
    ``a.b.c``, a file path, or a snake_case / internal-camelCase identifier. A
    bare single-Capitalized word is dropped. This makes the warning conservative
    (false negatives over false positives), which is the right bias for a note
    that appends to every answer.
    """
    keep: set[str] = set()
    for sym in extract_symbols(answer):
        if "/" in sym:                       # file path
            keep.add(sym)
        elif "." in sym:                     # dotted path or path with extension
            keep.add(sym)
        elif "_" in sym:                      # snake_case identifier
            keep.add(sym)
        elif any(c.isupper() for c in sym[1:]):  # internal capital: makeToken, fooBar
            keep.add(sym)
        elif f"{sym}(" in answer or f"{sym} (" in answer:  # written as a call
            keep.add(sym)
        # else: a bare lowercase word (already excluded by extract_symbols) or a
        # lone Capitalized prose word -> not flagged.
    return keep

# How many flagged symbols to name in the note. Keeps it short and ASCII.
_MAX_NAMED = 5

# A path-looking token in the answer (ends in a known code extension). Used to
# pull file paths the answer claims it touched so we can confirm them on disk.
_PATH_RE = re.compile(
    r"[\w./-]+\.(?:py|pyi|js|jsx|ts|tsx|go|rs|rb|java|kt|c|h|cpp|hpp|cs|php|"
    r"swift|json|yaml|yml|toml|cfg|ini|md|txt|sh|sql|html|css|tf)\b"
)


def grounding_enabled(config: Any | None = None) -> bool:
    """Whether the post-answer grounding check runs. ON by default for code mode.

    Opt out via the ``RONIN_GROUNDING_CHECK`` env var (``0``/``off``/``false``/
    ``no``/``disable``) or a falsey ``grounding_check`` attribute on the config.
    The env var wins so a single export disables it everywhere without editing
    config.
    """
    raw = os.environ.get("RONIN_GROUNDING_CHECK")
    if raw is not None:
        return raw.strip().lower() not in ("0", "off", "false", "no", "disable", "")
    flag = getattr(config, "grounding_check", True)
    return bool(flag)


def _new_code(name: str, args: dict) -> str:
    """The new text an edit tool proposes (its content becomes working-tree fact)."""
    if name == "write_file":
        return str(args.get("content", "") or "")
    if name == "edit_file":
        return str(args.get("new_string", "") or "")
    if name == "multi_edit":
        parts = [str(e.get("new_string", "") or "") for e in (args.get("edits") or [])]
        return "\n".join(p for p in parts if p)
    return ""


def _edit_sources_from_trace(trace: list[Any]) -> list[Source]:
    """Pull the agent's own edits out of the trace as evidence.

    ``sources_from_trace`` excludes write/edit *results* (they are the agent's
    actions, not observations). But a symbol the agent just wrote DOES exist
    afterwards, so for a post-answer hallucination check we fold the proposed new
    code back in. We read the edit's *call* step (which carries the new content),
    not its result.
    """
    sources: list[Source] = []
    for step in trace:
        if _attr(step, "kind") != "tool_call":
            continue
        content = _attr(step, "content")
        if not isinstance(content, dict):
            continue
        name = content.get("name")
        if name not in _EDIT_TOOLS:
            continue
        code = _new_code(str(name), content.get("input") or {})
        if code.strip():
            sources.append(Source(origin=str(name), text=code))
    return sources


def _attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _worktree_source(answer: str, root: str | os.PathLike) -> Source | None:
    """Confirm file paths the answer claims by reading them off the working tree.

    Only paths that actually resolve under ``root`` are folded in - a path the
    answer invents that is not on disk contributes nothing, so it stays flagged.
    Cheap: at most a handful of small files, read once, best-effort.
    """
    seen: set[str] = set()
    texts: list[str] = []
    base = os.path.abspath(str(root))
    for m in _PATH_RE.finditer(answer):
        rel = m.group(0)
        if rel in seen:
            continue
        seen.add(rel)
        if len(seen) > 20:  # bound the work; an answer naming 20+ files is rare
            break
        full = os.path.abspath(os.path.join(base, rel))
        # Stay inside the project root and only read regular files that exist.
        if not full.startswith(base) or not os.path.isfile(full):
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="ignore") as fh:
                texts.append(fh.read(200_000))
        except OSError:
            continue
        # Record that the path itself exists, so the bare path token is grounded.
        texts.append(rel)
    if not texts:
        return None
    return Source(origin="worktree", text="\n".join(texts))


def _grounded(sym: str, source_symbols: set[str], source_lower: str) -> bool:
    """True if ``sym`` is supported by the sources.

    Same rule the harness uses: an exact symbol match, a substring of the source
    text, or - for a dotted path - every component present. Inlined (one tiny
    function) so this module does not reach into the harness internals.
    """
    if sym in source_symbols or sym.lower() in source_lower:
        return True
    if "." in sym:
        parts = [p for p in sym.split(".") if p]
        return bool(parts) and all(p.lower() in source_lower for p in parts)
    return False


def flag_hallucinated_symbols(
    answer: str,
    trace: list[Any],
    *,
    root: str | os.PathLike = ".",
) -> list[str]:
    """Return symbols the ``answer`` claims that exist in NONE of the evidence.

    Evidence = files the agent read (from the trace) + code it wrote this turn +
    files it named that actually exist in the working tree. A returned list is
    the hallucinated symbols, sorted; an empty list means the answer is grounded
    (or there was nothing to check). Never raises.
    """
    try:
        sources = list(sources_from_trace(trace))
        sources.extend(_edit_sources_from_trace(trace))
        wt = _worktree_source(answer, root)
        if wt is not None:
            sources.append(wt)
        if not sources:
            # No observed evidence at all: we cannot tell grounded from invented,
            # so we stay silent rather than flag everything (abstain, not noise).
            return []

        source_text = "\n".join(s.text for s in sources)
        source_symbols = extract_symbols(source_text)
        source_lower = source_text.lower()

        flagged = sorted(
            s
            for s in _answer_symbols(answer)
            if not _grounded(s, source_symbols, source_lower)
        )
        return flagged
    except Exception:  # noqa: BLE001 - a grounding check must never break a run
        return []


def grounding_note(
    answer: str,
    trace: list[Any],
    *,
    root: str | os.PathLike = ".",
    config: Any | None = None,
) -> str:
    """The short ASCII note to append to the result, or "" when nothing is flagged.

    Returns "" when the check is disabled, when there is no evidence to check
    against, or when every referenced symbol is grounded. Otherwise a one-line
    warning naming up to a few of the flagged symbols.
    """
    if not grounding_enabled(config):
        return ""
    flagged = flag_hallucinated_symbols(answer, trace, root=root)
    if not flagged:
        return ""
    return render_note(flagged)


def render_note(flagged: list[str]) -> str:
    """Format a flagged-symbol list into the appended warning. ASCII only."""
    if not flagged:
        return ""
    named = ", ".join(f"{s}()" if _looks_callable(s) else s for s in flagged[:_MAX_NAMED])
    extra = len(flagged) - _MAX_NAMED
    tail = f" (and {extra} more)" if extra > 0 else ""
    return (
        "grounding check: referenced "
        + named
        + tail
        + " not found in the files I read. Verify before relying on this."
    )


def _looks_callable(sym: str) -> bool:
    """A bare snake_case / camelCase identifier reads better as ``name()``; a
    dotted path, a file path, or a Capitalized class-like name does not."""
    if "." in sym or "/" in sym:
        return False
    if sym[:1].isupper():
        return False
    return True


def append_grounding_note(output: str, note: str) -> str:
    """Append ``note`` to ``output`` on its own line. No-op when the note is empty."""
    if not note:
        return output
    if not output:
        return note
    return f"{output}\n\n{note}"
