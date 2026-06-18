"""Supervised autonomy - `ronin auto "<task>"` works a longer task with guardrails.

This is NOT blind overnight magic. It is *supervised* autonomy: ronin runs the
coding agent step by step, but bounds it with four rails so an unattended run
can't quietly make a mess.

1. CHECKPOINT each step. If the working dir is a git repo, every step is snapshot
   into a ronin checkpoint (the whole tree, tracked + untracked) before and after
   the agent moves. Each step is revertible with `ronin rewind <id>`.
2. TEST-GATE. After a step changes files, the repo's own verify command (pytest /
   npm / cargo / go / make / a RONIN.md `verify:` line - see :mod:`verify_cmd`) is
   run. The loop only keeps going while the suite is not getting WORSE; it stops
   after N consecutive failing gates.
3. STALL-STOP. If the working tree's content does not change for K steps (the agent
   is looping or repeating itself), the loop stops and reports instead of burning
   iterations.
4. REPORT. At the end it prints what it did each step, what passed/failed, and the
   exact command to revert.

The loop keeps the agent's full read/edit/run powers; the rails only decide when to
STOP. The loop itself is pure given its injected callables, so it is fully testable
offline with a fake agent (no model, no network) - which is how the tests drive it.

Honest limits: the agent can still make a wrong-but-passing change, and the test
gate is only as good as the repo's tests. Supervised means you review the result
and revert with the printed checkpoint ids if you don't like it.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .verify_cmd import Verdict, run_verify

# Inject the agent so the loop is testable without a live model. Given a task
# instruction it does one bounded code-agent turn; the loop re-checks via the
# verify gate afterward. Return value is ignored.
AgentFn = Callable[[str], object]


@dataclass
class AutoStep:
    """One supervised step: what the agent was asked, and the rails' readings."""
    n: int
    checkpoint_id: int | None      # checkpoint taken AFTER this step (None if no git)
    changed: bool                  # did the working tree content move this step?
    verdict: Verdict | None        # test-gate result (None if not run / no tests)
    note: str = ""                 # why the loop stopped here, if it did


@dataclass
class AutoReport:
    """The end-of-run report: every step, the final state, and how to revert."""
    task: str
    steps: list[AutoStep] = field(default_factory=list)
    stopped_reason: str = ""
    final_passed: bool | None = None    # None -> no tests were ever run
    is_git: bool = True

    def first_checkpoint(self) -> int | None:
        for s in self.steps:
            if s.checkpoint_id is not None:
                return s.checkpoint_id
        return None

    def revert_hint(self) -> str:
        """Plain-text, honest instructions for undoing the run."""
        if not self.is_git:
            return ("not a git repo - no checkpoints were taken. "
                    "review the changes by hand before keeping them.")
        first = self.first_checkpoint()
        if first is None:
            return "no checkpoints were taken (the agent made no changes)."
        # rewinding to the checkpoint taken BEFORE the run undoes everything; the
        # per-step ids let you roll back to any single good step.
        ids = [str(s.checkpoint_id) for s in self.steps if s.checkpoint_id is not None]
        return (f"to undo the whole run: `ronin rewind {first - 1}` "
                f"(the checkpoint taken before step 1). "
                f"per-step checkpoints: {', '.join(ids)} - "
                f"`ronin rewind <id>` rolls back to any one of them.")


def working_tree_signature(root: Path | str) -> str:
    """A content signature of the working tree, for stall detection.

    Hash of `git diff HEAD` (tracked edits) plus the sorted list of untracked
    files. If two consecutive steps produce the same signature the agent made no
    new progress - it is looping or repeating identical actions. Pure given git.
    Falls back to a constant when git is unavailable (stall logic then relies on
    the agent's own no-op signalling)."""
    root = Path(root).resolve()
    try:
        diff = subprocess.run(["git", "-C", str(root), "diff", "HEAD"],
                              capture_output=True, text=True, timeout=30)
        others = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=30)
    except (subprocess.SubprocessError, OSError):
        return "no-git"
    blob = (diff.stdout or "") + "\n--untracked--\n" + "".join(
        sorted((others.stdout or "").splitlines()))
    return hashlib.sha256(blob.encode("utf-8", "ignore")).hexdigest()


def run_auto_loop(
    root: Path | str,
    task: str,
    agent_fn: AgentFn,
    *,
    max_steps: int = 6,
    max_consecutive_failures: int = 2,
    stall_after: int = 2,
    verify_fn: Callable[[Path | str], Verdict] = run_verify,
    signature_fn: Callable[[Path | str], str] = working_tree_signature,
    checkpoint_fn: Callable[[Path | str, str], int | None] | None = None,
    on_step: Callable[[AutoStep], None] | None = None,
) -> AutoReport:
    """Run ``task`` under supervision: agent step -> checkpoint -> test-gate, with
    a consecutive-failure cap and a stall guard. Returns an :class:`AutoReport`.

    Rails:
    * After each agent step the working tree is checkpointed (revertible).
    * The verify gate runs; ``max_consecutive_failures`` failing gates in a row
      stop the loop (the change is getting worse, not better).
    * ``stall_after`` steps with no working-tree change stop the loop (a loop /
      repeated identical action makes no progress).
    * ``max_steps`` is the hard ceiling.

    All side-effecting pieces (agent / verify / signature / checkpoint) are
    injected so the loop runs fully offline in tests.
    """
    root = Path(root).resolve()
    report = AutoReport(task=task)

    # A checkpoint BEFORE step 1, so the whole run is revertible as a unit. Its id
    # is (first per-step id - 1); recorded implicitly via revert_hint.
    if checkpoint_fn is not None:
        try:
            checkpoint_fn(root, "auto: before step 1")
        except Exception:  # noqa: BLE001 - never let snapshotting abort the run
            report.is_git = False

    consecutive_failures = 0
    stalled = 0
    prev_sig = signature_fn(root)

    for n in range(1, max_steps + 1):
        # one bounded agent turn on the task (or the previous gate's failure)
        instruction = task if n == 1 else _continue_instruction(task, report)
        agent_fn(instruction)

        sig = signature_fn(root)
        changed = sig != prev_sig
        prev_sig = sig

        # checkpoint AFTER the step so this exact state is revertible
        cp_id: int | None = None
        if checkpoint_fn is not None and report.is_git:
            try:
                cp_id = checkpoint_fn(root, f"auto: after step {n}")
            except Exception:  # noqa: BLE001
                cp_id = None

        step = AutoStep(n=n, checkpoint_id=cp_id, changed=changed, verdict=None)

        # STALL: no new file changes / repeated identical action
        if not changed:
            stalled += 1
            if stalled >= stall_after:
                step.note = f"stalled - no progress for {stalled} step(s)"
                report.steps.append(step)
                if on_step is not None:
                    on_step(step)
                report.stopped_reason = step.note
                break
            report.steps.append(step)
            if on_step is not None:
                on_step(step)
            continue
        stalled = 0

        # TEST-GATE: run the repo's own verify command after a real change
        verdict = verify_fn(root)
        step.verdict = verdict
        if verdict.ran:
            report.final_passed = verdict.passed
            if verdict.passed:
                consecutive_failures = 0
            else:
                consecutive_failures += 1
        # a repo with no detectable tests (ran=False) can't be gated - don't count
        # it as a failure, just proceed (the checkpoint trail is the safety net).

        if consecutive_failures >= max_consecutive_failures:
            step.note = (f"stopped - test gate failed {consecutive_failures} "
                         f"step(s) in a row ({verdict.summary})")
            report.steps.append(step)
            if on_step is not None:
                on_step(step)
            report.stopped_reason = step.note
            break

        report.steps.append(step)
        if on_step is not None:
            on_step(step)
    else:
        report.stopped_reason = f"reached the step ceiling ({max_steps})"

    return report


def _continue_instruction(task: str, report: AutoReport) -> str:
    """The follow-up instruction for step 2+, carrying the last gate's verdict so
    the agent can course-correct instead of repeating itself."""
    last = report.steps[-1] if report.steps else None
    if last is not None and last.verdict is not None and last.verdict.ran and not last.verdict.passed:
        return (
            f"Continue this task: {task}\n\n"
            f"The verify command `{last.verdict.command}` is currently FAILING: "
            f"{last.verdict.summary}\n\n"
            f"--- failing output (tail) ---\n{last.verdict.output or '(none)'}\n"
            f"--- end output ---\n\n"
            "Make the smallest change that moves the suite toward green. Do NOT "
            "weaken or delete tests to make them pass. If you are already done, "
            "make no further edits."
        )
    return (
        f"Continue this task: {task}\n\n"
        "If the task is already complete, make no further edits (the loop will "
        "detect there is no more progress and stop). Otherwise take the next "
        "small step."
    )
