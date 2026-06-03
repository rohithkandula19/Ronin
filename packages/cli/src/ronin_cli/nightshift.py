"""Nightshift — an autonomous teammate that works your backlog while you sleep.

You point ronin at a backlog (TODO markers, GitHub issues, or an explicit goal)
and it works each task UNATTENDED in an isolated git worktree: implement → prove
against the test suite (the fitness gate) → optionally a cross-vendor Duel review
→ and leaves a reviewable **patch** under .ronin/nightshift/. It never pushes,
never touches your working tree, and stops at a budget cap. In the morning you
review a stack of test-passing patches.

This module composes the pieces built elsewhere (worktree isolation, the Kaizen
fitness gate, Duel) into one autonomous loop. The backlog-gathering and report
logic are pure and unit-tested; execution runs sandboxed in throwaway worktrees.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_OUT_DIR = Path(".ronin") / "nightshift"
_REPORT = _OUT_DIR / "report.json"


@dataclass
class Task:
    source: str       # "goal" | "todo" | "issue"
    title: str
    detail: str
    ref: str = ""     # file:line, issue #, or ""

    def prompt(self) -> str:
        loc = f" (at {self.ref})" if self.ref else ""
        return (f"Task from the {self.source}{loc}: {self.title}\n\n{self.detail}\n\n"
                "Make the smallest correct change. Do NOT break existing tests; add "
                "a test if it makes sense. Keep the change focused on this one task.")


@dataclass
class TaskResult:
    task: Task
    status: str       # "patched" | "no-change" | "failed-tests" | "error" | "skipped-budget"
    note: str = ""
    patch_path: str = ""
    blockers: list[str] = field(default_factory=list)


def gather_tasks(root: Path | str, *, include_todos: bool = True,
                 include_issues: bool = False, goal: str | None = None,
                 limit: int = 20) -> list[Task]:
    """Build the backlog from the requested sources. Pure given the sources."""
    tasks: list[Task] = []
    if goal:
        tasks.append(Task("goal", goal.strip()[:80], goal.strip()))
    if include_todos:
        from .kaizen import find_targets
        for t in find_targets(root):
            tasks.append(Task("todo", f"{t.marker} in {t.file}",
                              t.text.strip() or f"Resolve the {t.marker}.",
                              ref=f"{t.file}:{t.line}"))
    if include_issues:
        from .gh_helper import open_issues
        for i in open_issues(root, limit=limit):
            tasks.append(Task("issue", i["title"], i["body"], ref=f"#{i['number']}"))
    return tasks[:limit]


def patch_path(root: Path | str, index: int, task: Task) -> Path:
    safe = "".join(c if c.isalnum() else "-" for c in task.title.lower())[:40].strip("-")
    return Path(root) / _OUT_DIR / f"{index:02d}-{safe or 'task'}.patch"


def save_report(root: Path | str, results: list[TaskResult]) -> None:
    """Persist a run's results to .ronin/nightshift/report.json. Silent on failure."""
    path = Path(root) / _REPORT
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [{"title": r.task.title, "source": r.task.source, "ref": r.task.ref,
                    "status": r.status, "note": r.note, "patch_path": r.patch_path,
                    "blockers": r.blockers} for r in results]
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_report(root: Path | str) -> list[dict]:
    """The last run's results (list of dicts), or [] if none."""
    path = Path(root) / _REPORT
    if not path.is_file():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return []


def apply_patch(root: Path | str, patch_file: str) -> bool:
    """Apply one patch to the working tree via ``git apply``. True on success."""
    if not patch_file or not Path(patch_file).is_file():
        return False
    try:
        proc = subprocess.run(["git", "-C", str(Path(root).resolve()), "apply",
                               "--whitespace=nowarn", patch_file],
                              capture_output=True, text=True, timeout=30)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def applicable(report: list[dict], *, clean_only: bool = False) -> list[dict]:
    """Patches from a report worth applying: status 'patched', existing patch
    file, and (if clean_only) no Duel blockers. Pure given the report."""
    out = []
    for r in report:
        if r.get("status") != "patched" or not r.get("patch_path"):
            continue
        if clean_only and r.get("blockers"):
            continue
        out.append(r)
    return out


def summarize(results: list[TaskResult]) -> dict:
    """Aggregate counts by status. Pure."""
    out = {"patched": 0, "no-change": 0, "failed-tests": 0, "error": 0, "skipped-budget": 0}
    for r in results:
        out[r.status] = out.get(r.status, 0) + 1
    out["total"] = len(results)
    return out


def run_nightshift(config, root, console, tasks: list[Task], *, execute: bool = False,
                   duel_against: dict | None = None, budget: float | None = None,
                   roster=None, max_iterations: int = 25) -> list[TaskResult]:
    """Work each task in isolation. Dry-run (default) just returns the plan as
    'skipped' results; execute=True does the real autonomous loop, writing a
    patch per task that passes the test suite. Never pushes; never edits the
    working tree."""
    results: list[TaskResult] = []
    if not execute:
        return [TaskResult(t, "skipped-budget", note="dry-run") for t in tasks]

    from .cost import CostLedger, estimate_cost
    from .kaizen import run_fitness
    from .worktree import git_worktree, is_git_repo, worktree_diff

    if not is_git_repo(root):
        console.print("[yellow]nightshift needs a git repository.[/yellow]")
        return results

    out_dir = Path(root) / _OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger = CostLedger() if budget else None

    from .code_mode import run_code_agent
    for i, task in enumerate(tasks, 1):
        if budget and ledger and ledger.spent >= budget:
            results.append(TaskResult(task, "skipped-budget", note="budget cap reached"))
            continue
        console.print(f"[#7aa2f7]🌙 [{i}/{len(tasks)}] {task.title}[/#7aa2f7] [dim]({task.source})[/dim]")
        try:
            with git_worktree(root, label="nightshift") as wt:
                if roster is not None:
                    from .swarm import run_swarm
                    res = run_swarm(config, task.prompt(), root=wt, console=console,
                                    roster=roster, yolo=True, max_iterations=max_iterations)
                else:
                    res = run_code_agent(config, task.prompt(), root=wt, console=None,
                                         yolo=True, read_only=False, max_iterations=max_iterations)
                if ledger is not None:
                    u = res.usage or {}
                    ledger.record(config.provider, config.resolved_model(),
                                  u.get("input_tokens", 0), u.get("output_tokens", 0))
                fitness = run_fitness(wt)
                diff = worktree_diff(wt)
        except Exception as e:  # noqa: BLE001 — one task failing must not end the night
            results.append(TaskResult(task, "error", note=str(e)[:160]))
            console.print(f"[red]  ✗ error[/red] [dim]{str(e)[:80]}[/dim]")
            continue

        if not diff.strip():
            results.append(TaskResult(task, "no-change", note="agent made no changes"))
            console.print("[dim]  – no change[/dim]")
            continue
        if not fitness.passed:
            results.append(TaskResult(task, "failed-tests", note=fitness.summary))
            console.print(f"[red]  ✗ tests failed[/red] [dim]({fitness.summary}) — discarded[/dim]")
            continue

        blockers: list[str] = []
        if duel_against is not None:
            from .duel import review_diff
            verdict = review_diff(config, diff, duel_against)
            blockers = verdict.blockers

        pp = patch_path(root, i, task)
        try:
            pp.write_text(diff, encoding="utf-8")
        except OSError as e:
            results.append(TaskResult(task, "error", note=f"couldn't write patch: {e}"))
            continue
        results.append(TaskResult(task, "patched", note=fitness.summary,
                                  patch_path=str(pp), blockers=blockers))
        flag = f" [yellow]· {len(blockers)} blocker(s)[/yellow]" if blockers else ""
        console.print(f"[green]  ✓ patch ready[/green] [dim]({fitness.summary})[/dim]{flag}")

    save_report(root, results)
    return results
