"""Ambient pair programmer — `ronin pair`.

ronin watches your repo and, when you save, glances at *your* change (the git
diff) and offers a short, friendly suggestion — a bug it spots, a cleaner way, a
missing test — without you asking. A co-pilot sitting next to you, not a command
you invoke. Built on the Ghost's dependency-free file watcher; the prompt builder
is pure and unit-tested.
"""
from __future__ import annotations

import time
from pathlib import Path

_PAIR_SYS = (
    "You are a sharp, supportive pair programmer looking over your partner's "
    "shoulder. They just saved a change. In 1–3 short sentences, give the single "
    "most useful note: a bug you spot, a cleaner approach, or a missing test. If "
    "it looks good, say so briefly. Be concrete; reference names. No preamble."
)


def pair_prompt(changed: list[str], diff: str) -> str:
    """Build the co-pilot prompt from the changed files + their diff. Pure."""
    files = ", ".join(changed[:5]) or "(some files)"
    return (f"I just saved changes to {files}. Here's the diff:\n\n"
            f"```diff\n{diff[:6000]}\n```\n\nYour one note:")


def _suggest(config, root, console, changed: list[str]) -> None:
    """Glance at the working-tree diff and print one suggestion. Best-effort."""
    try:
        from ronin_agent_patterns import Message

        from .git_helper import _git
        from .runner import build_provider
        diff = _git(root, "diff").stdout
        if not diff.strip():
            return
        resp = build_provider(config).complete(
            system=_PAIR_SYS,
            messages=[Message(role="user", content=pair_prompt(changed, diff))],
            tools=[], max_tokens=200)
        if resp.text:
            console.print(f"[#bb9af7]ʕ•ᴥ•ʔ 💡 {resp.text.strip()}[/#bb9af7]")
    except Exception:  # noqa: BLE001 — the co-pilot is best-effort, never crashes the watch
        pass


def run_pair(config, root, console, *, interval: float = 2.0, max_ticks: int | None = None) -> None:
    """Watch ``root`` and offer a suggestion after each save (git-diff based)."""
    from .ghost import GhostState, react, snapshot

    state = GhostState(prev=snapshot(root))
    console.print(f"[dim]{react('idle')} · pairing on {Path(root).resolve()} "
                  "— save a file and I'll chime in (Ctrl+C to stop)[/dim]")
    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            time.sleep(interval)
            ticks += 1
            changed = state.tick(snapshot(root))
            if changed:
                _suggest(config, root, console, changed)
    except KeyboardInterrupt:
        console.print(f"\n[dim]{react('idle')} · pairing ended[/dim]")
