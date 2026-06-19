"""Interactive choice picker — ronin's take on Claude Code's question UI.

A reusable single- or multi-select menu: arrow keys to move, space to toggle
(multi-select), Enter to confirm, Esc to cancel. Two consumers share it:

- the coding agent, via the ``ask_user`` tool — when ronin is genuinely unsure
  it asks a multiple-choice question instead of guessing, and
- the ``ronin play`` arcade, to choose which game to launch.

On a real terminal it uses prompt_toolkit's battle-tested radiolist/checkboxlist
dialogs (styled with ronin's teal). On anything that is not a TTY (pipes, tests)
it falls back to a numbered text prompt, so the picker stays scriptable and the
selection logic is unit-testable without a terminal.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from rich.console import Console

from .theme import ACCENT, MUTE, SOFT


@dataclass
class Choice:
    """One selectable option. ``value`` defaults to ``label`` when omitted."""
    label: str
    value: str | None = None
    description: str = ""

    def val(self) -> str:
        return self.value if self.value is not None else self.label


def normalize(options) -> list[Choice]:
    """Accept ``Choice``s, ``(label, description)`` tuples, or bare strings."""
    out: list[Choice] = []
    for o in options:
        if isinstance(o, Choice):
            out.append(o)
        elif isinstance(o, (tuple, list)):
            label = str(o[0])
            desc = str(o[1]) if len(o) > 1 else ""
            out.append(Choice(label, None, desc))
        else:
            out.append(Choice(str(o)))
    return out


def parse_reply(reply: str, n: int, *, multi: bool) -> list[int]:
    """Pure core of the text fallback: turn a typed reply (``"1"``, ``"1,3"``,
    ``"2 4"``) into a list of valid 0-based indexes — clamped to ``[0, n)``,
    de-duplicated, order preserved. Single-select keeps only the first pick.
    """
    picks: list[int] = []
    for tok in reply.replace(",", " ").split():
        if tok.isdigit():
            i = int(tok) - 1
            if 0 <= i < n and i not in picks:
                picks.append(i)
    return picks if multi else picks[:1]


def _interactive(console: Console) -> bool:
    try:
        return (getattr(console, "is_terminal", False)
                and sys.stdin.isatty() and sys.stdout.isatty())
    except Exception:  # noqa: BLE001
        return False


def _fallback(console: Console, question: str, choices: list[Choice], *, multi: bool):
    """Numbered text prompt used off-TTY (and as the safety net if the dialog
    raises). Returns the same shape as :func:`ask_choice`."""
    console.print(f"\n[bold]{question}[/bold]")
    for i, c in enumerate(choices, 1):
        tail = f"  [{MUTE}]— {c.description}[/{MUTE}]" if c.description else ""
        console.print(f"  [{ACCENT}]{i}[/{ACCENT}]. {c.label}{tail}")
    hint = "numbers, comma-separated" if multi else "a number"
    try:
        reply = console.input(f"\n[{SOFT}]Pick {hint}:[/{SOFT}] ")
    except (EOFError, KeyboardInterrupt):
        return [] if multi else None
    idxs = parse_reply(reply, len(choices), multi=multi)
    if multi:
        return [choices[i].val() for i in idxs]
    return choices[idxs[0]].val() if idxs else None


def ask_choice(question: str, options, *, multi: bool = False,
               console: Console | None = None):
    """Ask the user to choose. Returns the selected ``value`` (single-select,
    or ``None`` if cancelled) or a list of ``value``s (multi-select, possibly
    empty). ``options`` may be ``Choice``s, ``(label, desc)`` tuples, or strings.
    """
    console = console or Console()
    choices = normalize(options)
    if not choices:
        return [] if multi else None

    if not _interactive(console):
        return _fallback(console, question, choices, multi=multi)

    # Interactive path: prompt_toolkit dialogs (arrow keys + space + Enter).
    try:
        from prompt_toolkit.shortcuts import checkboxlist_dialog, radiolist_dialog
        from prompt_toolkit.styles import Style

        style = Style.from_dict({
            "dialog": "bg:default",
            "dialog frame.label": f"bg:default {ACCENT} bold",
            "dialog.body": "bg:default",
            "radio-checked": ACCENT,
            "checkbox-checked": ACCENT,
            "button.focused": f"bg:{ACCENT} #1a1a1a",
        })
        values = [(c.val(), f"{c.label}" + (f"  —  {c.description}" if c.description else ""))
                  for c in choices]
        if multi:
            result = checkboxlist_dialog(title="ronin", text=question,
                                         values=values, style=style).run()
            return list(result) if result else []
        result = radiolist_dialog(title="ronin", text=question,
                                  values=values, style=style).run()
        return result  # the chosen value, or None on cancel
    except Exception:  # noqa: BLE001 - never let the picker break the caller
        return _fallback(console, question, choices, multi=multi)
