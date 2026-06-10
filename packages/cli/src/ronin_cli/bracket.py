"""Model tournament — a single-elimination bracket.

`ronin tournament "<question>" -m a,b,c,d` seeds the models into a bracket and
runs pairwise matchups: both answer, a judge picks the winner, the winner
advances — round by round until a champion. More dramatic (and more decisive)
than an all-at-once vote. The bracket math is pure and unit-tested.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


def make_matchups(players: list) -> list[tuple]:
    """Pair a round's players into matchups. An odd player out gets a bye
    (partner is None → auto-advances). Pure."""
    out: list[tuple] = []
    i = 0
    while i < len(players):
        a = players[i]
        b = players[i + 1] if i + 1 < len(players) else None
        out.append((a, b))
        i += 2
    return out


_PICK_RE = re.compile(r"WINNER:\s*([12])", re.IGNORECASE)

_JUDGE_SYS = (
    "You are judging a head-to-head: two answers to the same question. Pick the "
    "better one on correctness, completeness, and clarity. Justify briefly, then "
    "end with EXACTLY one line: WINNER: 1  or  WINNER: 2"
)


def pick_winner(text: str) -> int | None:
    """Parse 'WINNER: 1|2' from a judge reply → 0-based index, or None. Pure."""
    m = _PICK_RE.search(text or "")
    if not m:
        return None
    return int(m.group(1)) - 1


@dataclass
class Matchup:
    a: str
    b: str | None
    winner: str = ""
    note: str = ""


@dataclass
class TournamentResult:
    question: str
    rounds: list[list[Matchup]] = field(default_factory=list)
    champion: str = ""


def _judge_matchup(base, question: str, a_spec: dict, b_spec: dict, judge_spec, on_log=None) -> int:
    """Run a single matchup: ask both, judge picks. Returns 0 (a) or 1 (b)."""
    from .debate import _ask, _label

    qa = _ask(base, a_spec, "Answer the question rigorously and concisely.", question)
    qb = _ask(base, b_spec, "Answer the question rigorously and concisely.", question)
    if on_log:
        on_log(_label(base, a_spec), qa, _label(base, b_spec), qb)
    verdict = _ask(base, judge_spec or {}, _JUDGE_SYS,
                   f"QUESTION:\n{question}\n\n### Answer 1\n{qa}\n\n### Answer 2\n{qb}")
    pick = pick_winner(verdict)
    return 1 if pick == 1 else 0   # default to answer 1 if unparseable


def run_tournament(base, question: str, specs: list[dict[str, Any]], *,
                   judge_spec: dict | None = None, on_match=None) -> TournamentResult:
    """Run the full single-elimination bracket. ``on_match`` (optional) is called
    with each completed Matchup."""
    from .debate import _label
    result = TournamentResult(question=question)
    if not specs:
        return result
    field_ = list(specs)
    while len(field_) > 1:
        round_matches: list[Matchup] = []
        winners: list[dict] = []
        for a_spec, b_spec in make_matchups(field_):
            if b_spec is None:
                m = Matchup(a=_label(base, a_spec), b=None, winner=_label(base, a_spec), note="bye")
                winners.append(a_spec)
            else:
                idx = _judge_matchup(base, question, a_spec, b_spec, judge_spec)
                won = a_spec if idx == 0 else b_spec
                m = Matchup(a=_label(base, a_spec), b=_label(base, b_spec), winner=_label(base, won))
                winners.append(won)
            round_matches.append(m)
            if on_match:
                on_match(m)
        result.rounds.append(round_matches)
        field_ = winners
    if field_:
        result.champion = _label(base, field_[0])
    return result
