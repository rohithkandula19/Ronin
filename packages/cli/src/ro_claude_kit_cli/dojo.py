"""The Dojo — rival models fight over your code; the best diff wins.

Consensus reconciles *answers*. The Dojo settles *code*: give it a task and a
roster of providers, and each one attempts the change in its own isolated git
worktree, in parallel. Their diffs come back, a judge model scores them, and the
winning diff is surfaced for your approval. Claude vs Gemini vs DeepSeek, all
swinging at the same problem — then you (or the judge) crown one.

Only ronin can stage this: it needs N providers AND parallel mutating isolation.
The judge-parse + roster logic are pure and unit-tested; the fights themselves
run sandboxed in throwaway worktrees and never touch your tree until you accept.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import CSKConfig
from .worktree import git_worktree, is_git_repo, worktree_diff

_JUDGE_SYSTEM = (
    "You are judging several candidate diffs that each attempt the SAME coding "
    "task. Pick the single best one on correctness, completeness, and simplicity. "
    "Briefly justify, then end with EXACTLY one line:\n  WINNER: <number>\n"
    "where <number> is the candidate's index (1-based)."
)
_WINNER_RE = re.compile(r"WINNER:\s*(\d+)", re.IGNORECASE)
_DUEL_SYSTEM_HINT = "Make the change directly. Keep it minimal and correct."


@dataclass
class Fighter:
    label: str
    diff: str = ""
    ok: bool = True
    error: str = ""

    @property
    def changed(self) -> bool:
        return self.ok and bool(self.diff.strip())


@dataclass
class DojoResult:
    task: str
    fighters: list[Fighter] = field(default_factory=list)
    winner_index: int | None = None   # 0-based into `fighters`
    rationale: str = ""

    @property
    def winner(self) -> Fighter | None:
        if self.winner_index is None:
            return None
        return self.fighters[self.winner_index]

    @property
    def contenders(self) -> list[Fighter]:
        return [f for f in self.fighters if f.changed]


def pick_winner(text: str, n: int) -> int | None:
    """Parse a judge reply → 0-based winner index in [0, n). Pure.

    Returns ``None`` if no valid 'WINNER: k' (1-based, in range) is found."""
    m = _WINNER_RE.search(text or "")
    if not m:
        return None
    k = int(m.group(1))
    return k - 1 if 1 <= k <= n else None


def _fight_one(base: CSKConfig, spec: dict[str, Any], task: str, root: Path | str,
               max_iterations: int) -> Fighter:
    from .code_mode import run_code_agent
    from .runner import config_for_spec

    sub = config_for_spec(base, spec)
    label = f"{sub.provider}:{sub.resolved_model()}"
    try:
        with git_worktree(root, label="dojo") as wt:
            run_code_agent(sub, f"{task}\n\n{_DUEL_SYSTEM_HINT}", root=wt, console=None,
                           yolo=True, read_only=False, max_iterations=max_iterations,
                           include_image_tool=False)
            diff = worktree_diff(wt)
        return Fighter(label=label, diff=diff, ok=True)
    except Exception as e:  # noqa: BLE001 — one fighter failing shouldn't end the match
        return Fighter(label=label, ok=False, error=str(e))


def judge_diffs(base: CSKConfig, task: str, fighters: list[Fighter],
                judge_spec: dict[str, Any] | None) -> tuple[int | None, str]:
    """Have a judge model pick the winning diff. Returns (0-based index, rationale)."""
    from ro_claude_kit_agent_patterns import Message

    from .runner import build_single_provider, config_for_spec

    contenders = [f for f in fighters if f.changed]
    if not contenders:
        return None, "no fighter produced a diff"
    if len(contenders) == 1:
        return fighters.index(contenders[0]), "only one fighter produced a diff"

    cfg = config_for_spec(base, judge_spec) if judge_spec else base
    provider = build_single_provider(cfg)
    board = "\n\n".join(f"### Candidate {i + 1} ({f.label})\n```diff\n{f.diff[:6000]}\n```"
                        for i, f in enumerate(contenders))
    resp = provider.complete(
        system=_JUDGE_SYSTEM,
        messages=[Message(role="user", content=f"TASK:\n{task}\n\nCANDIDATES:\n{board}")],
        tools=[], max_tokens=800,
    )
    text = resp.text or ""
    local = pick_winner(text, len(contenders))
    if local is None:
        return fighters.index(contenders[0]), text  # fall back to first contender
    return fighters.index(contenders[local]), text


def run_dojo(base: CSKConfig, task: str, specs: list[dict[str, Any]], *,
             root: Path | str = ".", judge_spec: dict[str, Any] | None = None,
             max_iterations: int = 20, max_workers: int = 4) -> DojoResult:
    """Run ``task`` on every model in ``specs`` in parallel isolated worktrees,
    then judge their diffs. Raises ValueError outside a git repo."""
    if not is_git_repo(root):
        raise ValueError("the Dojo needs a git repository (parallel worktree isolation)")
    result = DojoResult(task=task)
    if not specs:
        return result
    with ThreadPoolExecutor(max_workers=min(max_workers, len(specs))) as ex:
        result.fighters = list(ex.map(
            lambda s: _fight_one(base, s, task, root, max_iterations), specs))
    result.winner_index, result.rationale = judge_diffs(base, task, result.fighters, judge_spec)
    return result
