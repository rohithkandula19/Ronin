"""One rollout: reset a task, run the policy, verify in a sandbox, score it.

This is the RL *environment* — the thing verl/SkyRL calls once per sample. It reuses the
eval harness's task shape (``fixture/`` + ``verify.sh``, the same
:mod:`ronin.evals.poolgen` writes and :mod:`ronin.evals.runner` runs) so a rollout task and
an eval task are the same on-disk object, and it reuses the eval harness's verify contract
(run ``verify.sh`` with the workspace as cwd) rather than inventing a second one.

The rollout is deliberately structured around the **hidden test**, the single most
effective anti-hack measure:

    reset → run policy (≤ max_turns) → snapshot the diff → run the VISIBLE verify.sh
    → NOW write the held-out hidden test and run it → the hidden result is the reward's
      pass signal.

The hidden test is never on disk while the policy runs, so the policy cannot read it,
overfit to it, or edit it — and because the diff is snapshotted *before* the hidden file
is written, the hidden file never counts as the agent's change. A policy that games the
visible suite (edits it, or writes just enough to satisfy the exact visible assertions)
still faces a suite it never saw, and the reward reflects that.

The two seams — the ``policy`` and the ``sandbox`` — are injected so the whole rollout runs
offline in a test with a scripted policy and a subprocess ``bash`` sandbox. In a real run
the policy runs the ronin agent loop (``ronin.evals`` adapters + a vLLM-served checkpoint)
and the sandbox is a container (``ronin.evals.poolgen.container_command``); the reward
arithmetic and the hidden-test discipline are identical either way.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .reward import RewardBreakdown, RewardWeights, RolloutOutcome, compute_reward

#: The default turn ceiling, matching the GRPO config. A rollout that reaches it is
#: ``ran_out_of_turns`` and hits the ``-0.5`` gate — flailing is a real cost.
DEFAULT_MAX_TURNS = 24


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """What the policy did, as the facts a reward needs — the transcript, digested.

    The policy has already mutated the workspace by the time it returns this. ``hit_turn_cap``
    is the policy's own report that it stopped because it ran out of turns, not because it
    finished; ``stated_done`` is whether it claimed the task was complete.
    """

    turns: int
    tool_validity: float = 1.0
    redundant_calls: int = 0
    stated_done: bool = False
    hit_turn_cap: bool = False


#: The policy seam: given a fresh workspace, do the work (mutating it) and report what
#: happened. A real policy runs the ronin loop with the served checkpoint; a test scripts it.
Policy = Callable[[Path], PolicyResult]


@dataclass(frozen=True, slots=True)
class HiddenTest:
    """The held-out check that produces the reward signal. Never on disk during the rollout.

    ``filename`` is where it is written *after* the policy finishes (and is passed to the
    reward as a protected path, so a rollout that somehow created a same-named file is
    gated). ``body`` is a ``verify.sh``-style script: exit 0 == the hidden suite passed.
    """

    filename: str
    body: str


#: The sandbox seam: run a script in a workspace, return True iff it exited 0. Default is a
#: subprocess ``bash`` with the network scrubbed from the environment; a real run swaps in a
#: container. Same script either way.
Sandbox = Callable[[Path, str], bool]


def bash_sandbox(workspace: Path, script_body: str, *, timeout: float = 120.0) -> bool:
    """Run ``script_body`` under ``bash`` with the workspace as cwd. Network-scrubbed env.

    The offline sandbox: a subprocess with a minimal ``PATH`` and no inherited proxy/token
    variables, so a verify that tries to reach the network cannot. It is the same script a
    container would run; the container adds the kernel-level isolation a real reward needs.
    """
    script = workspace / ".ronin_rl_verify.sh"
    script.write_text(script_body, encoding="utf-8", newline="\n")
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "RONIN_EVAL_PYTHON": _python()}
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=workspace,
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
    )
    script.unlink(missing_ok=True)
    return proc.returncode == 0


def _python() -> str:
    import sys

    return sys.executable


@dataclass(frozen=True, slots=True)
class Rollout:
    """One rollout's result: what happened, and the reward it earned."""

    outcome: RolloutOutcome
    reward: RewardBreakdown
    workspace: Path


def _tree_files(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(root))
            try:
                out[rel] = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:  # pragma: no cover - binary fixture files
                out[rel] = "<binary>"
    return out


def _changed_paths(before: dict[str, str], after: dict[str, str]) -> tuple[str, ...]:
    changed = {p for p, text in after.items() if before.get(p) != text}
    changed |= set(before) - set(after)  # deletions count too
    return tuple(sorted(changed))


@dataclass(frozen=True, slots=True)
class Environment:
    """Runs one rollout end to end and scores it. Seams injected; deterministic given them."""

    max_turns: int = DEFAULT_MAX_TURNS
    sandbox: Sandbox = bash_sandbox
    weights: RewardWeights = field(default_factory=RewardWeights)

    def rollout(
        self,
        task_dir: Path,
        policy: Policy,
        *,
        hidden_test: HiddenTest,
        median_turns: float,
        workspace: Path,
    ) -> Rollout:
        """Reset ``task_dir`` into ``workspace``, run ``policy``, verify, and score.

        ``task_dir`` is an eval/pool task directory (``fixture/`` + ``verify.sh``).
        ``workspace`` is where the clean reset lands and the policy works — a fresh copy of
        ``fixture/`` every time, which is the "reset to a clean sha" for a fixture task.
        """
        if workspace.exists():
            shutil.rmtree(workspace)
        shutil.copytree(task_dir / "fixture", workspace)
        clean = _tree_files(workspace)

        result = policy(workspace)
        turns = result.turns
        ran_out = result.hit_turn_cap or turns >= self.max_turns

        # The diff is snapshotted BEFORE the hidden test is written, so the hidden file is
        # never counted as the agent's change.
        diff_paths = _changed_paths(clean, _tree_files(workspace))

        visible_pass = self.sandbox(workspace, (task_dir / "verify.sh").read_text(encoding="utf-8"))

        # The hidden test runs only now — after the policy has finished and the diff is
        # taken — so it was never readable, editable, or part of the fixture. Its name is
        # passed to the reward as a protected path, so a rollout that itself created a
        # same-named file (an attempt to pre-empt it) is gated.
        hidden_pass = self.sandbox(workspace, hidden_test.body)

        outcome = RolloutOutcome(
            turns=turns,
            ran_out_of_turns=ran_out,
            verify_passed_hidden=hidden_pass,
            verify_passed_visible=visible_pass,
            tool_validity=result.tool_validity,
            redundant_calls=result.redundant_calls,
            stated_done_while_failing=result.stated_done and not hidden_pass,
            diff_paths=diff_paths,
            median_turns=median_turns,
        )
        reward = compute_reward(
            outcome, self.weights, protected_extra=(hidden_test.filename,)
        )
        return Rollout(outcome=outcome, reward=reward, workspace=workspace)


def solution_policy(task_dir: Path, *, turns: int = 3, tool_validity: float = 1.0) -> Policy:
    """A policy that applies the task's reference ``solution/`` — the oracle rollout.

    The control for the environment, exactly as ``oracle_patch_runner`` is for SWE-bench:
    the reference solution should earn a passing reward. One that does not indicts the
    task or the environment, not a model.
    """

    def run(workspace: Path) -> PolicyResult:
        for path in sorted((task_dir / "solution").rglob("*")):
            if path.is_file():
                target = workspace / path.relative_to(task_dir / "solution")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        return PolicyResult(turns=turns, tool_validity=tool_validity, stated_done=True)

    return run


__all__ = [
    "DEFAULT_MAX_TURNS",
    "Environment",
    "HiddenTest",
    "Policy",
    "PolicyResult",
    "Rollout",
    "Sandbox",
    "bash_sandbox",
    "solution_policy",
]
