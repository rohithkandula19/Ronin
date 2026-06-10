"""Model debate — vendors argue across rounds, then converge.

Consensus asks several models once and reconciles. A *debate* is iterative: round
1 everyone answers; each later round, every model SEES the others' answers,
critiques them, and sharpens its own; a judge then synthesizes the converged
position and flags where they still disagree. Cross-examination across vendors —
something only a provider-agnostic agent can stage.

The round-prompt builders are pure and unit-tested; the model calls run in
parallel like consensus.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DEBATE_SYS = (
    "You are one expert on a panel debating a question. Be rigorous and concise. "
    "When shown other panelists' answers, engage with them directly: say where "
    "they're right, where they're wrong, and update your own answer accordingly. "
    "Change your mind when the argument is better; defend it when it isn't."
)
_JUDGE_SYS = (
    "You are the moderator. From the panelists' final positions, synthesize the "
    "single best answer. Note briefly where they converged and where they still "
    "disagree."
)


@dataclass
class Panelist:
    label: str
    rounds: list[str] = field(default_factory=list)   # answer text per round
    ok: bool = True
    error: str = ""

    @property
    def final(self) -> str:
        return self.rounds[-1] if self.rounds else ""


@dataclass
class DebateResult:
    question: str
    panelists: list[Panelist] = field(default_factory=list)
    synthesis: str = ""

    @property
    def participated(self) -> list[Panelist]:
        return [p for p in self.panelists if p.ok and p.final.strip()]


def opening_prompt(question: str) -> str:
    return f"QUESTION:\n{question}\n\nGive your answer with the key reasoning."


def critique_prompt(question: str, others: list[tuple[str, str]], your_last: str, round_num: int) -> str:
    """Prompt for a non-first round: the question, the other panelists' latest
    answers, and your own — asking you to critique and refine. Pure."""
    panel = "\n\n".join(f"### {label} said:\n{ans}" for label, ans in others) or "(no other answers)"
    return (f"QUESTION:\n{question}\n\nThis is debate round {round_num}. Here are the other "
            f"panelists' latest answers:\n\n{panel}\n\nYour previous answer was:\n{your_last}\n\n"
            "Critique the others where warranted, then give your REVISED answer.")


def _ask(base, spec: dict[str, Any], system: str, prompt: str, max_tokens: int = 1200) -> str:
    from ronin_agent_patterns import Message

    from .runner import build_single_provider, config_for_spec
    cfg = config_for_spec(base, spec) if spec else base
    provider = build_single_provider(cfg)
    resp = provider.complete(system=system, messages=[Message(role="user", content=prompt)],
                             tools=[], max_tokens=max_tokens)
    return (resp.text or "").strip()


def _label(base, spec: dict[str, Any]) -> str:
    from .runner import config_for_spec
    cfg = config_for_spec(base, spec) if spec else base
    return f"{cfg.provider}:{cfg.resolved_model()}"


def run_debate(base, question: str, specs: list[dict[str, Any]], *, rounds: int = 2,
               judge_spec: dict[str, Any] | None = None, max_workers: int = 4,
               on_round=None) -> DebateResult:
    """Run a ``rounds``-round debate among ``specs``, then synthesize. ``on_round``
    (optional) is called with (round_index, panelists) after each round."""
    result = DebateResult(question=question)
    if not specs:
        return result
    panelists = [Panelist(label=_label(base, s)) for s in specs]

    for r in range(max(1, rounds)):
        if r == 0:
            prompts = [opening_prompt(question)] * len(specs)
        else:
            prompts = []
            for i in range(len(specs)):
                others = [(panelists[j].label, panelists[j].rounds[-1])
                          for j in range(len(specs)) if j != i and panelists[j].rounds]
                prompts.append(critique_prompt(question, others, panelists[i].final, r + 1))

        def _one(i):
            try:
                return i, _ask(base, specs[i], _DEBATE_SYS, prompts[i])
            except Exception as e:  # noqa: BLE001
                return i, ("__ERR__", str(e))

        with ThreadPoolExecutor(max_workers=min(max_workers, len(specs))) as ex:
            for i, ans in ex.map(_one, range(len(specs))):
                if isinstance(ans, tuple) and ans and ans[0] == "__ERR__":
                    panelists[i].ok = False
                    panelists[i].error = ans[1]
                    panelists[i].rounds.append("")
                else:
                    panelists[i].rounds.append(ans or "")
        if on_round is not None:
            on_round(r, panelists)

    result.panelists = panelists
    good = [p for p in panelists if p.ok and p.final.strip()]
    if good:
        panel = "\n\n".join(f"### {p.label}:\n{p.final}" for p in good)
        try:
            result.synthesis = _ask(base, judge_spec or {}, _JUDGE_SYS,
                                    f"QUESTION:\n{question}\n\nFINAL POSITIONS:\n{panel}", max_tokens=1600)
        except Exception as e:  # noqa: BLE001
            result.synthesis = f"(couldn't synthesize: {e})"
    return result
