"""Ronin Duel — a rival model red-teams the diff before you trust it.

A single agent grading its own homework has a blind spot: the same model that
wrote the code is the worst judge of it. Because ronin is provider-agnostic, it
can settle the matter the samurai way — hand the diff to a *different* provider
and have it adversarially hunt for what's wrong. Claude writes, Gemini cross-
examines (or any pairing you like). The verdict is advisory: blockers surfaced,
you still decide.

The review core takes an injected provider, so it's fully unit-testable with a
FakeProvider — no network in tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import CSKConfig

_DUEL_SYSTEM = (
    "You are an adversarial code reviewer from a RIVAL team. Another AI wrote the "
    "diff below; your job is to find what's WRONG with it before it ships — bugs, "
    "broken edge cases, security holes, race conditions, regressions, missing tests. "
    "Be skeptical and concrete. For each real problem output a line EXACTLY like:\n"
    "  BLOCKER: <file:line if known> — <the specific problem>\n"
    "Only flag genuine issues; do not invent nitpicks. End with EXACTLY one line:\n"
    "  VERDICT: PASS   (if you found no real blockers)\n"
    "  VERDICT: BLOCK  (if you found one or more)\n"
)

_BLOCKER_RE = re.compile(r"^\s*BLOCKER:\s*(.+)$", re.MULTILINE)
_VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(PASS|BLOCK)\s*$", re.MULTILINE | re.IGNORECASE)


@dataclass
class DuelVerdict:
    duelist: str               # provider:model that reviewed
    blockers: list[str] = field(default_factory=list)
    passed: bool = True        # True = no blockers / VERDICT: PASS
    raw: str = ""

    @property
    def blocker_count(self) -> int:
        return len(self.blockers)


def parse_verdict(text: str, duelist: str) -> DuelVerdict:
    """Parse a reviewer's reply into a structured verdict. Pure.

    A model that forgets the VERDICT line still 'blocks' if it listed blockers —
    we never silently pass real findings."""
    blockers = [b.strip() for b in _BLOCKER_RE.findall(text) if b.strip()]
    m = _VERDICT_RE.search(text)
    explicit = m.group(1).upper() if m else None
    if explicit == "BLOCK":
        passed = False
    elif explicit == "PASS":
        passed = len(blockers) == 0  # findings override a careless PASS
    else:
        passed = len(blockers) == 0
    return DuelVerdict(duelist=duelist, blockers=blockers, passed=passed, raw=text.strip())


def review_diff(base: CSKConfig, diff: str, duelist_spec: dict[str, Any] | None = None,
                *, provider: Any = None, max_tokens: int = 1200) -> DuelVerdict:
    """Have a rival provider adversarially review ``diff``.

    ``duelist_spec`` is a provider[:model] spec for the reviewer — pick one that
    differs from the author for a real second opinion. ``provider`` can be
    injected (tests). Returns a ``DuelVerdict``."""
    from ro_claude_kit_agent_patterns import Message

    if not diff.strip():
        return DuelVerdict(duelist="(none)", blockers=[], passed=True, raw="(empty diff)")

    if provider is None:
        from .runner import build_single_provider, config_for_spec
        cfg = config_for_spec(base, duelist_spec) if duelist_spec else base
        provider = build_single_provider(cfg)
        label = f"{cfg.provider}:{cfg.resolved_model()}"
    else:
        label = (f"{duelist_spec.get('provider')}:{duelist_spec.get('model')}"
                 if duelist_spec else getattr(provider, "model", "fake"))

    resp = provider.complete(
        system=_DUEL_SYSTEM,
        messages=[Message(role="user", content=f"Review this diff:\n\n{diff[:12000]}")],
        tools=[],
        max_tokens=max_tokens,
    )
    return parse_verdict(resp.text or "", label)


def render_verdict(console, verdict: DuelVerdict) -> None:
    """Pretty-print a duel verdict."""
    head = f"[bold]⚔ duel[/bold] · [dim]{verdict.duelist} reviewed the diff[/dim]"
    console.print(head)
    if verdict.passed and not verdict.blockers:
        console.print("  [green]✓ no blockers — the rival model signed off[/green]")
        return
    for b in verdict.blockers:
        console.print(f"  [red]✗[/red] {b}")
    tag = "[red]BLOCK[/red]" if not verdict.passed else "[yellow]advisory[/yellow]"
    console.print(f"  verdict: {tag} ([dim]{verdict.blocker_count} blocker(s)[/dim])")
