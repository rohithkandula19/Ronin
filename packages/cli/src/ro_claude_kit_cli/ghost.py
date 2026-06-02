"""The Ghost — an ambient pair that's just *there*, watching your back.

Most agents wait to be asked. The Ghost watches your repo: when you save a source
file it notices, and (optionally) runs your tests and reacts — the panda cheers
when they're green and winces when they break, before you ask it anything. With
``--watchful`` it goes a step further and has a model glance at what just broke
and tell you what it suspects.

The watch loop is a dependency-free mtime poll. The snapshot/diff and the panda
reactions are pure and unit-tested; the loop just wires them to a clock.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

# Panda faces for each thing the Ghost can notice.
PANDA: dict[str, str] = {
    "idle": "ʕ•ᴥ•ʔ",
    "saved": "ʕ•ᴥ•ʔ✎",
    "thinking": "ʕ￫ᴥ￩ʔ…",
    "pass": "ʕ•ᴥ•ʔ✨",
    "fail": "ʕ#ᴥ#ʔ💥",
}

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".csk", ".ronin"}
_WATCH_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
                   ".rb", ".md", ".toml", ".json", ".css", ".html"}


def react(event: str, detail: str = "") -> str:
    """A panda reaction line for an event. Pure."""
    face = PANDA.get(event, PANDA["idle"])
    messages = {
        "saved": "saw that save",
        "thinking": "taking a look…",
        "pass": "tests green — nice",
        "fail": "something broke",
        "idle": "watching your back",
    }
    msg = messages.get(event, "")
    tail = f" — {detail}" if detail else ""
    return f"{face}  {msg}{tail}"


def snapshot(root: Path | str) -> dict[str, float]:
    """Map watched source files → mtime. Pure (a point-in-time read)."""
    root_path = Path(root).resolve()
    out: dict[str, float] = {}
    for p in root_path.rglob("*"):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in _WATCH_SUFFIXES:
            try:
                out[str(p.relative_to(root_path))] = p.stat().st_mtime
            except OSError:
                continue
    return out


def changed_files(old: dict[str, float], new: dict[str, float]) -> list[str]:
    """Files added or modified between two snapshots (sorted). Pure."""
    changed = [f for f, m in new.items() if f not in old or m > old[f]]
    return sorted(changed)


@dataclass
class GhostState:
    """The Ghost's running memory across poll ticks."""
    prev: dict[str, float] = field(default_factory=dict)
    saves: int = 0
    last_change: list[str] = field(default_factory=list)

    def tick(self, current: dict[str, float]) -> list[str]:
        """Fold a fresh snapshot in; return the files that changed since last tick."""
        changed = changed_files(self.prev, current) if self.prev else []
        self.prev = current
        if changed:
            self.saves += len(changed)
            self.last_change = changed
        return changed


def run_ghost(config, root, console, *, interval: float = 1.5,
              run_tests: bool = False, watchful: bool = False,
              max_ticks: int | None = None) -> None:
    """Watch ``root`` and react to saves (dependency-free mtime poll).

    ``run_tests`` runs the suite after a save and reacts to the result;
    ``watchful`` additionally asks a model what likely broke on failure.
    ``max_ticks`` bounds the loop (tests pass it; real use leaves it None)."""
    state = GhostState(prev=snapshot(root))
    console.print(f"[dim]{react('idle')} · ghost watching {Path(root).resolve()} "
                  f"(Ctrl+C to dismiss)[/dim]")
    ticks = 0
    try:
        while max_ticks is None or ticks < max_ticks:
            time.sleep(interval)
            ticks += 1
            changed = state.tick(snapshot(root))
            if not changed:
                continue
            shown = ", ".join(changed[:3]) + (f" +{len(changed) - 3} more" if len(changed) > 3 else "")
            console.print(f"[#7dcfff]{react('saved', shown)}[/#7dcfff]")
            if run_tests:
                _react_to_tests(config, root, console, changed, watchful)
    except KeyboardInterrupt:
        console.print(f"\n[dim]{react('idle')} · ghost dismissed ({state.saves} saves seen)[/dim]")


def _react_to_tests(config, root, console, changed: list[str], watchful: bool) -> None:
    from .kaizen import run_fitness

    console.print(f"[dim]{react('thinking')}[/dim]")
    fitness = run_fitness(root)
    if fitness.passed:
        console.print(f"[green]{react('pass', fitness.summary)}[/green]")
        return
    console.print(f"[red]{react('fail', fitness.summary)}[/red]")
    if watchful:
        _watchful_hint(config, root, console, changed)


def _watchful_hint(config, root, console, changed: list[str]) -> None:
    """Have a model glance at the just-changed files and the failure, and guess."""
    try:
        from ro_claude_kit_agent_patterns import Message

        from .git_helper import _git
        from .runner import build_single_provider
        diff = _git(root, "diff").stdout[:6000]
        if not diff.strip():
            return
        provider = build_single_provider(config)
        resp = provider.complete(
            system="You are a sharp pair programmer. Tests just broke after an edit. "
                   "In ONE or TWO sentences, name the most likely cause. Be specific.",
            messages=[Message(role="user", content=f"Changed: {', '.join(changed)}\n\nDiff:\n{diff}")],
            tools=[], max_tokens=160,
        )
        if resp.text:
            console.print(f"[#bb9af7]ʕ•ᴥ•ʔ💡 {resp.text.strip()}[/#bb9af7]")
    except Exception:  # noqa: BLE001 — the hint is best-effort, never crash the watch
        pass
