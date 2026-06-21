"""Hunk-by-hunk diff review — approve/reject INDIVIDUAL changes in one edit,
like ``git add -p``.

When the coding agent proposes a file edit, ronin normally shows ONE diff and
asks to approve the whole change (see ``code_mode._selective_gate``). This module
lets the user instead accept some hunks and reject others, then writes back a
file that contains exactly the approved subset — every unapproved region keeps
its ORIGINAL content.

Design — almost everything here is PURE and unit-testable:

  * ``split_hunks(old, new)`` → ``[Hunk, …]`` — the unified-diff hunks between two
    versions (grouping matches :func:`difflib.unified_diff`).
  * ``apply_hunks(old, hunks, selected)`` → reconstructed file. Three LAWS hold
    byte-exactly: select ALL → ``new``; select NONE → ``old``; select a subset →
    exactly those changes (every other region stays ``old``).
  * ``render_hunk(hunk)`` → a colored unified-diff string.
  * ``review(old, new, *, console)`` is the ONE impure function: it prompts per
    hunk and returns the reconstructed content.

SAFETY: ``review`` is opt-in, and opting in must NEVER silently drop edits. On
EOF / a non-interactive stdin it returns ``new`` UNCHANGED, so behaviour matches
today's whole-file apply. The reconstruction is byte-exact and never corrupts a
file — ``split`` keeps line terminators, ``apply`` only ever re-joins original
lines, so non-newline-terminated files survive a round trip.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field


@dataclass
class Hunk:
    """One contiguous change between ``old`` and ``new``.

    Line ranges are 0-based, half-open indices into the *keepends* line lists
    (``old.splitlines(keepends=True)`` / ``new.splitlines(keepends=True)``):

      * ``old_start``/``old_end`` — the slice of OLD lines this hunk replaces.
        ``old_start == old_end`` means a pure insertion (nothing removed).
      * ``new_start``/``new_end`` — the slice of NEW lines it replaces them with.
        ``new_start == new_end`` means a pure deletion (nothing added).
      * ``old_lines``/``new_lines`` — those slices, captured so apply/render need
        no second pass over the source.
      * ``header`` — the ``@@ -l,s +l,s @@`` line (1-based, git-style).

    Selecting this hunk swaps ``old_lines`` for ``new_lines``; rejecting it keeps
    ``old_lines``. Context (unchanged) lines are NOT carried here — a Hunk is the
    minimal change span plus its header; context only appears in :func:`render_hunk`.
    """

    old_start: int
    old_end: int
    new_start: int
    new_end: int
    old_lines: list[str] = field(default_factory=list)
    new_lines: list[str] = field(default_factory=list)
    header: str = ""
    # Surrounding unchanged lines (kept only for rendering a contextual diff).
    before_ctx: list[str] = field(default_factory=list)
    after_ctx: list[str] = field(default_factory=list)


def _fmt_range(start: int, count: int) -> str:
    """Format a unified-diff range token (1-based). A zero-length range is
    rendered ``l,0`` anchored at the line *before* the change — git's convention
    for pure inserts/deletes."""
    if count == 0:
        return f"{start},0"
    if count == 1:
        return f"{start + 1}"
    return f"{start + 1},{count}"


def split_hunks(old: str, new: str, *, context: int = 3) -> list[Hunk]:
    """Compute the unified-diff hunks between ``old`` and ``new``.

    Uses :class:`difflib.SequenceMatcher` over the lines (keepends, so line
    terminators are preserved). Grouping mirrors :func:`difflib.unified_diff`:
    each group is the changed opcodes plus up to ``context`` unchanged lines on
    either side, and runs separated by more than ``2 * context`` unchanged lines
    become separate hunks. Pure.
    """
    a = old.splitlines(keepends=True)
    b = new.splitlines(keepends=True)
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    hunks: list[Hunk] = []
    for group in sm.get_grouped_opcodes(context):
        # The minimal CHANGE span (drop the leading/trailing 'equal' context the
        # grouping added) so old_start..old_end is exactly what this hunk swaps.
        changed = [op for op in group if op[0] != "equal"]
        if not changed:
            continue  # pure-context group (shouldn't occur, but never emit a no-op)
        old_start, old_end = changed[0][1], changed[-1][2]
        new_start, new_end = changed[0][3], changed[-1][4]

        # Context for rendering: the equal lines the grouping kept around the change.
        first, last = group[0], group[-1]
        before_ctx = a[first[1]:old_start] if first[0] == "equal" else []
        after_ctx = a[old_end:last[2]] if last[0] == "equal" else []

        # Header counts INCLUDE the surrounding context (git-style), so the @@
        # line matches what difflib.unified_diff would emit for this group.
        a_lo, a_hi = group[0][1], group[-1][2]
        b_lo, b_hi = group[0][3], group[-1][4]
        header = f"@@ -{_fmt_range(a_lo, a_hi - a_lo)} +{_fmt_range(b_lo, b_hi - b_lo)} @@"

        hunks.append(Hunk(
            old_start=old_start, old_end=old_end,
            new_start=new_start, new_end=new_end,
            old_lines=a[old_start:old_end],
            new_lines=b[new_start:new_end],
            header=header,
            before_ctx=list(before_ctx),
            after_ctx=list(after_ctx),
        ))
    return hunks


def apply_hunks(old: str, hunks: list[Hunk], selected: set[int]) -> str:
    """Reconstruct the file applying ONLY the hunks whose index is in ``selected``.

    Unselected regions keep their ORIGINAL (``old``) content. Pure + byte-exact.

    Laws (hold exactly):
      * ``selected`` == all indices  → reproduces ``new``.
      * ``selected`` == ``set()``     → reproduces ``old``.
      * a subset                      → applies exactly that subset.

    Works by walking the OLD line list, copying it verbatim except where a
    selected hunk's ``[old_start:old_end)`` span is reached — there the hunk's
    ``new_lines`` are emitted instead. Hunks are non-overlapping and ascending in
    ``old_start`` (``get_grouped_opcodes`` guarantees this), so a single forward
    pass is sufficient and never reorders or drops original bytes.
    """
    a = old.splitlines(keepends=True)
    # Apply in old-line order regardless of the caller's set/iteration order.
    chosen = sorted(
        (h for i, h in enumerate(hunks) if i in selected),
        key=lambda h: h.old_start,
    )
    out: list[str] = []
    cursor = 0
    for h in chosen:
        out.extend(a[cursor:h.old_start])   # unchanged gap before this hunk
        out.extend(h.new_lines)             # the approved replacement
        cursor = h.old_end
    out.extend(a[cursor:])                   # tail after the last applied hunk
    return "".join(out)


def render_hunk(hunk: Hunk, *, color: bool = True) -> str:
    """A unified-diff view of one hunk: teal header, green ``+`` adds, red ``-``
    removals, muted context. Pure. ``color=False`` yields plain text (for tests /
    no-color terminals). Colors match ``code_mode._render_diff`` (``#73daca`` add,
    ``#f7768e`` del) and the brand teal accent for the header.
    """
    HEADER, ADD, DEL, CTX, RESET = (
        ("\033[38;2;45;212;191m", "\033[38;2;115;218;202m",
         "\033[38;2;247;118;142m", "\033[38;2;107;112;137m", "\033[0m")
        if color else ("", "", "", "", "")
    )
    lines: list[str] = [f"{HEADER}{hunk.header}{RESET}"]
    for ln in hunk.before_ctx:
        lines.append(f"{CTX} {ln.rstrip(chr(10))}{RESET}")
    for ln in hunk.old_lines:
        lines.append(f"{DEL}-{ln.rstrip(chr(10))}{RESET}")
    for ln in hunk.new_lines:
        lines.append(f"{ADD}+{ln.rstrip(chr(10))}{RESET}")
    for ln in hunk.after_ctx:
        lines.append(f"{CTX} {ln.rstrip(chr(10))}{RESET}")
    return "\n".join(lines)


def _render_hunk_rich(hunk: Hunk, console) -> None:
    """Print a hunk via rich (matches ronin's diff styling). Falls back to the
    plain string renderer when rich isn't usable."""
    try:
        from rich.text import Text
    except Exception:  # noqa: BLE001 - degrade gracefully
        console.print(render_hunk(hunk, color=False))
        return
    nl = chr(10)
    console.print(Text(hunk.header, style="#2dd4bf"))           # teal header
    for ln in hunk.before_ctx:
        console.print(Text(f" {ln.rstrip(nl)}", style="#6b7089"))   # muted context
    for ln in hunk.old_lines:
        console.print(Text(f"-{ln.rstrip(nl)}", style="#f7768e"), style="on #2c161b")
    for ln in hunk.new_lines:
        console.print(Text(f"+{ln.rstrip(nl)}", style="#73daca"), style="on #15291c")
    for ln in hunk.after_ctx:
        console.print(Text(f" {ln.rstrip(nl)}", style="#6b7089"))


def review(old: str, new: str, *, console) -> str | None:
    """INTERACTIVE: walk the hunks between ``old`` and ``new``, prompting
    ``[y]es · [n]o · [a]ll · [q]uit`` per hunk, and return the file rebuilt from
    the approved hunks.

    Returns:
      * the reconstructed content (approved hunks applied) on a normal walk;
      * ``new`` UNCHANGED if there are no hunks (nothing to review), or if stdin
        is non-interactive / hits EOF — opting into review must never silently
        drop edits, so the fallback is exactly today's whole-file apply.

    This is the ONLY impure function in the module; everything it relies on
    (``split_hunks`` / ``apply_hunks`` / rendering) is pure.
    """
    hunks = split_hunks(old, new)
    if not hunks:
        return new  # identical, or only a trailing-newline tweak with no hunk → no-op

    # Non-interactive stdin (pipe, no TTY, headless): never drop the edit — apply
    # the whole change exactly as the non-review path would.
    import sys
    stdin = getattr(sys, "stdin", None)
    if stdin is None or not getattr(stdin, "isatty", lambda: False)():
        return new

    selected: set[int] = set()
    total = len(hunks)
    take_rest = False
    for i, h in enumerate(hunks):
        if take_rest:
            selected.add(i)
            continue
        _render_hunk_rich(h, console)
        console.print(
            f"    [yellow]apply this hunk?[/yellow] [grey50]({i + 1}/{total}) "
            "\\[y]es · \\[n]o · \\[a]ll · \\[q]uit[/grey50] ",
            end="",
        )
        try:
            # Pause ronin's type-ahead reader so its background thread can't steal
            # the keystroke (same guard the main approval prompt uses).
            try:
                from .input_queue import pause_capture
                with pause_capture():
                    raw = input().strip().lower()
            except Exception:  # noqa: BLE001 - pause_capture unavailable in tests
                raw = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Mid-review EOF/^C: apply what was approved so far (already a strict
            # subset of `new`) — never escalate to silently writing the whole file.
            break
        if raw in ("y", "yes"):
            selected.add(i)
        elif raw in ("a", "all"):
            selected.add(i)
            take_rest = True
        elif raw in ("q", "quit"):
            break
        # "n"/"no"/anything else → skip this hunk (keep the old content)

    return apply_hunks(old, hunks, selected)
