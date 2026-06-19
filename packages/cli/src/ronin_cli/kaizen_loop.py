"""Continuous Kaizen — ronin's self-forging loop, on a schedule, into a PR.

The one-shot ``kaizen`` command finds a weakness in ronin's OWN source, fixes it
in an isolated worktree, and PROVES it against the test suite before surfacing
the diff for your approval (see ``kaizen.py``). This module closes the *autonomy*
loop on top of that: instead of waiting for a human at the y/N prompt, it takes a
*proven* (test-passing) diff and turns it into a reviewable **pull request** on a
fresh branch — so ronin can sharpen its own blade unattended, nightly, and leave
you a PR to glance at in the morning instead of a prompt to babysit.

Design constraints honored here:
  * We do NOT reimplement the fix logic. ``run_continuous`` calls the existing
    ``run_kaizen`` cycle (weakness → worktree fix → fitness gate) and only adds a
    thin "turn the proven diff into a PR" orchestration on top.
  * We NEVER mutate ``main``. The proven diff is committed on a *new* branch,
    created inside an isolated ``git_worktree_branch`` (the user's checkout is
    never touched), then pushed; a PR is opened via ``gh`` when available.
  * ``run_kaizen`` is invoked with ``auto_apply=False`` and its interactive y/N
    prompt is fed EOF, so it can never write to the working tree — it still
    returns the proven diff, which is all we consume.

The gate decision is factored out into a PURE, fully unit-tested helper,
``should_open_pr`` — "is this proven diff worth a PR?" is an objective yes/no
(tests passed AND the change is non-trivial), not a vibe.
"""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from rich.console import Console

from .config import RoninConfig

# A PR is only worth opening if at least this much actually changed. A diff that
# touches zero files (or a single stray line) is noise, not an improvement — the
# fitness gate can pass on a no-op, so we also require real substance here.
_MIN_FILES = 1
_MIN_LINES = 2


# --------------------------------------------------------------------------
# Pure decision helper — the objective "is this proven diff PR-worthy?" gate.
# --------------------------------------------------------------------------

@contextmanager
def _eof_stdin() -> Iterator[None]:
    """Swap ``sys.stdin`` for an empty stream so any ``input()`` raises EOFError
    immediately. ``run_kaizen(auto_apply=False)`` prompts y/N before applying to
    the working tree; with EOF it takes its 'aborted — tree untouched' path while
    still returning the proven diff, which is all we consume."""
    saved = sys.stdin
    sys.stdin = io.StringIO("")
    try:
        yield
    finally:
        sys.stdin = saved


def should_open_pr(tests_passed: bool, files_changed: int, lines_changed: int) -> bool:
    """Decide whether a proven Kaizen diff is worth opening a PR for. PURE.

    Two conditions, both objective:
      1. the test-suite fitness gate passed (no point shipping a red change), and
      2. the change is non-trivial — it touches at least one file and changes a
         few lines (a 0-line or 1-line "diff" is almost always a no-op the gate
         happened to pass, not a real improvement).

    Fully unit-tested; no I/O, no git, no model.
    """
    if not tests_passed:
        return False
    if files_changed < _MIN_FILES:
        return False
    if lines_changed < _MIN_LINES:
        return False
    return True


def _diff_stats(diff: str) -> tuple[int, int]:
    """(files_changed, lines_changed) for a unified diff. Pure.

    Files are counted from ``diff --git`` / ``+++`` headers; changed lines are the
    added/removed body lines (``+``/``-``) excluding the ``+++``/``---`` headers."""
    if not diff.strip():
        return 0, 0
    files = len(re.findall(r"^diff --git ", diff, flags=re.MULTILINE))
    if files == 0:
        # fall back to counting unique +++ targets for plain `git diff` output
        files = len({ln for ln in diff.splitlines() if ln.startswith("+++ ")})
    lines = 0
    for ln in diff.splitlines():
        if ln.startswith("+++") or ln.startswith("---"):
            continue
        if ln.startswith("+") or ln.startswith("-"):
            lines += 1
    return files, lines


# --------------------------------------------------------------------------
# Branch naming + PR drafting (thin reuse of the nightshift PR plumbing).
# --------------------------------------------------------------------------

def _branch_for(result_note: str, target_label: str | None) -> str:
    """A unique, safe branch name for this Kaizen improvement.

    Reuses ``nightshift_pr.branch_name`` for the slug rules, seeds it from the
    Kaizen target (the weakness being resolved), and appends a short timestamp so
    repeated nightly runs never collide on a branch that already exists."""
    from .nightshift_pr import branch_name
    seed = (target_label or result_note or "self-improvement").split("[", 1)[-1]
    seed = re.sub(r"^[A-Z]+\]\s*", "", seed)  # drop a leading "MARKER]" if present
    stamp = time.strftime("%m%d%H%M")
    base = branch_name(f"kaizen {seed}", 0)            # ronin/00-kaizen-...
    return f"{base}-{stamp}"


def _draft_pr_text(config: RoninConfig, diff: str, target_label: str | None,
                   fitness_summary: str) -> tuple[str, str]:
    """(title, body) for the PR. Tries to LLM-draft a tight title from the diff;
    always falls back to a deterministic, self-describing title/body so a missing
    or erroring provider never blocks opening the PR."""
    short = (target_label or "resolve a self-improvement target").strip()
    fallback_title = f"kaizen: {short[:64]}"
    body = (
        "Autonomous self-improvement opened by `ronin kaizen --continuous`.\n\n"
        f"- Target: {short}\n"
        f"- Fitness gate (test suite): **passed** — {fitness_summary}\n\n"
        "This change was forged in an isolated git worktree and only reached a "
        "branch *after* the full test suite passed against it. Review and merge "
        "if it holds up.\n"
    )
    try:
        from ronin_agent_patterns import Message

        from .runner import build_provider
        title = build_provider(config).complete(
            system="You write tight, imperative pull-request titles.",
            messages=[Message(role="user", content=(
                "Write ONE pull-request title (imperative, <72 chars, no backticks, "
                "no trailing period) for this diff:\n\n" + diff[:4000]))],
            tools=[], max_tokens=60,
        ).text.strip().splitlines()[0].strip().strip("`").strip()
        if title:
            title = title if title.lower().startswith("kaizen") else f"kaizen: {title}"
            return title[:72], body
    except Exception:  # noqa: BLE001 — drafting is best-effort; never block the PR
        pass
    return fallback_title, body


# --------------------------------------------------------------------------
# The continuous orchestration: one proven cycle → a PR branch.
# --------------------------------------------------------------------------

def run_continuous(
    config: RoninConfig,
    *,
    console: Console | None = None,
    root: Path | str = ".",
    open_pr: bool = True,
    dry_run: bool = False,
    goal: str | None = None,
    target_tests: str | None = None,
) -> dict[str, Any]:
    """Run ONE Kaizen cycle and, if it produced a proven non-trivial diff, turn it
    into a reviewable PR on a fresh branch — without ever touching the working
    tree or ``main``.

    Flow:
      1. Run the existing ``run_kaizen`` cycle (weakness → isolated-worktree fix →
         test-suite fitness gate). It is invoked with ``auto_apply=False`` and its
         y/N prompt is fed EOF, so it cannot write to the working tree; we consume
         only the proven diff it returns.
      2. Gate the result with the pure ``should_open_pr`` (tests passed + the diff
         is non-trivial).
      3. If ``dry_run``: print the proven diff and stop (no branch, no push).
      4. Otherwise create a fresh branch in an isolated worktree, apply+commit the
         proven diff there, push it, and — if ``open_pr`` and ``gh`` is available —
         open a PR. If ``gh`` is missing, the branch is still pushed (or kept
         locally if there's no remote) so nothing is lost.

    Returns a result dict:
        {improved, tests_passed, branch, pr_url, summary, applied, note}
    """
    console = console or Console()
    root_path = Path(root).resolve()

    result: dict[str, Any] = {
        "improved": False, "tests_passed": False, "branch": None,
        "pr_url": None, "summary": "", "applied": False, "note": "",
    }

    from .kaizen import run_kaizen
    from .worktree import is_git_repo

    if not is_git_repo(root_path):
        result["summary"] = "not a git repository — continuous Kaizen needs git"
        result["note"] = result["summary"]
        return result

    # 1. one proven cycle. auto_apply=False + EOF on stdin ⇒ run_kaizen never
    #    writes to the working tree; we only read result.diff / result.fitness.
    console.print("[bold #7aa2f7]改 kaizen --continuous[/bold #7aa2f7] [dim]forging one proven improvement…[/dim]")
    with _eof_stdin():
        kr = run_kaizen(config, root_path, console, goal=goal,
                        target_tests=target_tests, auto_apply=False)

    diff = kr.diff or ""
    tests_passed = bool(kr.fitness and kr.fitness.passed)
    fitness_summary = kr.fitness.summary if kr.fitness else "(no fitness result)"
    files, lines = _diff_stats(diff)
    target_label = kr.target.label() if kr.target else None

    result["tests_passed"] = tests_passed

    if not diff.strip():
        result["summary"] = kr.note or "the agent made no changes"
        result["note"] = result["summary"]
        console.print(f"[yellow]改 {result['summary']}[/yellow]")
        return result

    if not should_open_pr(tests_passed, files, lines):
        why = ("fitness gate failed" if not tests_passed
               else f"change too trivial ({files} file(s), {lines} line(s))")
        result["summary"] = f"no PR — {why} [dim]({fitness_summary})[/dim]"
        result["note"] = result["summary"]
        console.print(f"[yellow]改 {result['summary']}[/yellow]")
        return result

    result["improved"] = True
    console.print(f"[green]  ✓ proven improvement[/green] "
                  f"[dim]({files} file(s), {lines} line(s); {fitness_summary})[/dim]")

    # 3. dry-run: show the proven diff and stop — no branch, no push.
    if dry_run:
        from .kaizen import _print_diff
        console.print("[bold]proposed self-improvement (proven against the suite, dry-run):[/bold]")
        _print_diff(console, diff)
        result["summary"] = f"dry-run — proven diff NOT pushed ({files} file(s), {lines} line(s))"
        result["note"] = result["summary"]
        return result

    # 4. turn the proven diff into a PR branch (fresh branch, isolated worktree).
    branch = _branch_for(kr.note, target_label)
    result["branch"] = branch
    ok, note = _branch_commit_diff(root_path, diff, branch,
                                   f"kaizen: {(target_label or 'self-improvement')[:64]}")
    result["note"] = note
    if not ok:
        result["summary"] = f"could not stage the proven diff onto a branch: {note}"
        console.print(f"[yellow]改 {result['summary']}[/yellow]")
        return result

    console.print(f"[green]  ✓ committed on[/green] [cyan]{branch}[/cyan]")

    from .nightshift_pr import open_pr as _open_pr
    from .nightshift_pr import push_branch

    pushed = push_branch(root_path, branch)
    if not pushed:
        result["summary"] = (f"branch [cyan]{branch}[/cyan] committed locally but "
                             "could not push (no 'origin' remote?) — review it with "
                             f"`git log {branch}`")
        result["note"] = "committed locally; push failed"
        console.print(f"[yellow]改 {result['summary']}[/yellow]")
        return result

    if open_pr and shutil.which("gh"):
        title, body = _draft_pr_text(config, diff, target_label, fitness_summary)
        url = _open_pr(root_path, branch, title, body)
        result["pr_url"] = url
        result["applied"] = url is not None
        result["summary"] = (f"opened PR → {url}" if url
                             else f"pushed [cyan]{branch}[/cyan]; `gh pr create` failed — open it manually")
        console.print(f"[green]改 {result['summary']}[/green]" if url
                      else f"[yellow]改 {result['summary']}[/yellow]")
    else:
        result["applied"] = True
        hint = "" if shutil.which("gh") else " (install `gh` to auto-open a PR)"
        result["summary"] = f"pushed branch [cyan]{branch}[/cyan]{hint} — open a PR when ready"
        console.print(f"[green]改 {result['summary']}[/green]")

    return result


def _branch_commit_diff(root: Path | str, diff: str, branch: str, message: str) -> tuple[bool, str]:
    """Create ``branch`` in an isolated worktree, apply the proven ``diff`` there
    and commit it. The worktree is torn down but the branch is kept; the user's
    checkout is never touched. Mirrors ``nightshift_pr.branch_commit_in_worktree``
    but takes the diff in-memory (Kaizen has no patch file on disk)."""
    from .worktree import git_worktree_branch
    if not diff.strip():
        return False, "empty diff"
    try:
        with git_worktree_branch(root, branch, label="kaizen-pr") as wt:
            ap = subprocess.run(["git", "-C", str(wt), "apply", "--whitespace=nowarn", "-"],
                                input=diff, capture_output=True, text=True, timeout=30)
            if ap.returncode != 0:
                return False, "proven diff did not apply cleanly: " + (ap.stderr or "").strip()[:120]
            subprocess.run(["git", "-C", str(wt), "add", "-A"],
                           capture_output=True, text=True, timeout=30)
            cm = subprocess.run(["git", "-C", str(wt), "commit", "-m", message],
                                capture_output=True, text=True, timeout=30)
            if cm.returncode != 0:
                return False, "nothing to commit"
        return True, "committed on " + branch
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:120]


# --------------------------------------------------------------------------
# Schedule hook — register with the scheduler so `ronin schedule` fires it.
# --------------------------------------------------------------------------

def nightly_kaizen(config: Any | None = None, root: Path | str = ".") -> dict[str, Any]:
    """Schedule entry point: run ONE continuous Kaizen cycle and open a PR.

    Shaped to be registered with ``scheduler.run_due`` as a task ``runner`` (or
    invoked directly by a cron line). Loads config itself when not given one, runs
    headless (a fresh ``Console``, never the interactive prompt), and returns the
    ``run_continuous`` result dict so the caller can log/notify on the outcome.

    Wire it up nightly with, e.g.::

        ronin schedule add nightly-kaizen "ronin kaizen --continuous" "0 3 * * *"

    or register ``nightly_kaizen`` as the runner for a stored task.
    """
    if config is None:
        from .config import load_config
        config = load_config()
    return run_continuous(config, console=Console(), root=root, open_pr=True, dry_run=False)
