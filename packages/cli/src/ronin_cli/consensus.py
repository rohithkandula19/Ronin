"""Multi-model consensus — ask the *same* question of several models at once,
then cross-check and synthesize one answer.

A single-vendor agent only ever has one opinion. ronin is provider-agnostic, so
it can run a task on Claude **and** Gemini **and** a local model in parallel,
compare where they agree and disagree, and have a judge model fuse the best of
all of them. More robust on hard reasoning/design/review questions, and a
genuine capability a single-vendor tool can't offer.

This is read-only by design: each model explores the codebase and reasons, but
nothing is mutated — consensus is for analysis, review, and decisions, not for
N models racing to edit the same files (that's ``isolated_task``).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ronin_agent_patterns import Message

from .config import RoninConfig
from .runner import build_single_provider, config_for_spec


_CONSENSUS_SYSTEM = """You are one of several independent models answering the SAME
question. Reason carefully and give your best, self-contained answer. You may read
files to ground yourself, but do not edit anything. Be concrete and concise — your
answer will be compared against other models' answers."""

_JUDGE_SYSTEM = """You are the judge in a multi-model panel. You are given a question
and several independent answers from different models. Produce the single best
answer by synthesizing them: keep what they agree on, resolve disagreements on the
merits (say which you trust and why), and incorporate the strongest unique points.
Start with the synthesized answer. Then add a short '— panel notes:' section noting
where the models agreed and where they diverged."""


def parse_model_spec(spec: str) -> dict[str, str]:
    """Parse a ``provider`` or ``provider:model`` string into a spec dict."""
    spec = spec.strip()
    if ":" in spec:
        provider, model = spec.split(":", 1)
        return {"provider": provider.strip(), "model": model.strip()}
    return {"provider": spec}


@dataclass
class ModelAnswer:
    label: str
    answer: str
    ok: bool
    error: str | None = None


@dataclass
class ConsensusResult:
    task: str
    answers: list[ModelAnswer] = field(default_factory=list)
    consensus: str = ""

    @property
    def succeeded(self) -> list[ModelAnswer]:
        return [a for a in self.answers if a.ok]


def _run_one(base: RoninConfig, spec: dict[str, Any], task: str, root: Path | str,
             max_iterations: int) -> ModelAnswer:
    # Imported here so tests can monkeypatch consensus.run_code_agent.
    from .code_mode import run_code_agent

    sub = config_for_spec(base, spec)
    label = f"{sub.provider}:{sub.resolved_model()}"
    try:
        res = run_code_agent(
            sub, task, root=root, console=None, yolo=True, read_only=True,
            max_iterations=max_iterations, base_system=_CONSENSUS_SYSTEM,
            include_image_tool=False,
        )
    except Exception as e:  # noqa: BLE001 — one model failing shouldn't sink the panel
        return ModelAnswer(label, "", ok=False, error=str(e))
    if not res.success:
        return ModelAnswer(label, res.output or "", ok=False, error=res.error)
    return ModelAnswer(label, res.output or "(no answer)", ok=True)


def synthesize(base: RoninConfig, task: str, answers: list[ModelAnswer],
               judge_spec: dict[str, Any] | None) -> str:
    """Have a judge model fuse the panel's answers into one."""
    good = [a for a in answers if a.ok]
    if not good:
        return "no model produced an answer — check provider credentials."
    if len(good) == 1:
        return good[0].answer  # nothing to reconcile

    judge_cfg = config_for_spec(base, judge_spec) if judge_spec else base
    provider = build_single_provider(judge_cfg)
    panel = "\n\n".join(f"### Model {i + 1} ({a.label}) says:\n{a.answer}"
                        for i, a in enumerate(good))
    prompt = f"QUESTION:\n{task}\n\nANSWERS FROM THE PANEL:\n{panel}"
    resp = provider.complete(
        system=_JUDGE_SYSTEM,
        messages=[Message(role="user", content=prompt)],
        tools=[],
        max_tokens=2048,
    )
    return resp.text or "(judge returned no synthesis)"


def run_consensus(base: RoninConfig, task: str, specs: list[dict[str, Any]], *,
                  root: Path | str = ".", judge_spec: dict[str, Any] | None = None,
                  max_iterations: int = 12, max_workers: int = 4) -> ConsensusResult:
    """Run ``task`` on every model in ``specs`` concurrently, then synthesize."""
    result = ConsensusResult(task=task)
    if not specs:
        return result
    with ThreadPoolExecutor(max_workers=min(max_workers, len(specs))) as ex:
        result.answers = list(ex.map(
            lambda s: _run_one(base, s, task, root, max_iterations), specs))
    result.consensus = synthesize(base, task, result.answers, judge_spec)
    return result


def render_consensus(console, result: ConsensusResult) -> None:
    """Print each model's answer, then the synthesized consensus."""
    from rich.panel import Panel

    from .theme import ACCENT, ERR, MUTE, OK

    for a in result.answers:
        if a.ok:
            console.print(Panel(a.answer, title=f"[{OK}]●[/{OK}] {a.label}",
                                border_style=MUTE, padding=(0, 1)))
        else:
            console.print(Panel(f"[{ERR}]failed:[/{ERR}] {a.error}",
                                title=f"[{ERR}]✗[/{ERR}] {a.label}",
                                border_style=ERR, padding=(0, 1)))
    n = len(result.succeeded)
    console.print(Panel(result.consensus,
                        title=f"[bold {ACCENT}]⚖ consensus of {n} model(s)[/bold {ACCENT}]",
                        border_style=ACCENT, padding=(0, 1)))
