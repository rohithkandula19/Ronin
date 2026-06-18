"""Kaizen (改善, "continuous improvement") — ronin sharpens its own blade.

ronin's own source lives in a repo. Kaizen closes the loop: it finds a weakness
in that source, has a model (a *free* one, if you point it at one) draft a fix
**in an isolated git worktree**, then runs the project's own test suite as an
objective fitness gate. A change is only ever surfaced for your approval if the
tests *still pass* in the worktree — the agent must prove its work before you
even look. Nothing touches your working tree until you say so.

The fitness function is the test suite, not an LLM judge, so "did it improve?"
is an objective yes/no, not a vibe. The weakness finder and the fitness runner
are pure and fully unit-tested; the drafting step is the only part that needs a
model, and it runs sandboxed in a throwaway checkout.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console

from .config import RoninConfig
from .worktree import git_worktree, is_git_repo, worktree_diff

# Markers that flag self-improvement targets in source, strongest first.
_MARKERS = ("FIXME", "BUG", "XXX", "TODO", "HACK")
# A real marker lives in a COMMENT — after a #, //, /*, <!-- , or a ; — and is
# followed by ':' or whitespace+text. Requiring the comment lead-in is what stops
# false positives on the word appearing in a docstring or a string literal (e.g.
# the marker list in this very file, or prose like "TODO/FIXME markers").
_MARKER_RE = re.compile(
    r"(?:#|//|/\*|<!--|;)\s*(" + "|".join(_MARKERS) + r")\b[:\s]+(.*)")
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist",
              "build", ".pytest_cache", ".ronin"}
# pytest summary line, e.g. "734 passed, 5 warnings in 7.55s" / "2 failed, 730 passed"
_PYTEST_SUMMARY = re.compile(r"(\d+) (passed|failed|error[s]?)")


@dataclass
class Target:
    """A weakness in the source: a marker comment to resolve."""
    file: str
    line: int
    marker: str
    text: str

    def label(self) -> str:
        return f"{self.file}:{self.line}  [{self.marker}] {self.text.strip()[:80]}"


@dataclass
class Fitness:
    """Result of the test-suite fitness gate."""
    passed: bool
    total: int
    failed: int
    summary: str


@dataclass
class KaizenResult:
    target: Target | None
    fitness: Fitness | None
    diff: str
    applied: bool
    note: str
    verdict: Any = None   # DuelVerdict from a rival reviewer, if --duel was used


def find_targets(root: Path | str, *, limit: int = 25) -> list[Target]:
    """Scan source files for marker comments (FIXME/TODO/…), strongest first.

    Pure and deterministic — the objective 'what could improve' signal. Only
    looks at text source files and skips vendored / build dirs."""
    root_path = Path(root).resolve()
    hits: list[Target] = []
    for p in sorted(root_path.rglob("*")):
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if not p.is_file() or p.suffix not in {".py", ".md", ".toml", ".sh", ".js", ".ts"}:
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            m = _MARKER_RE.search(line)
            if m:
                hits.append(Target(file=str(p.relative_to(root_path)), line=n,
                                   marker=m.group(1), text=m.group(2)))
    # strongest markers first (FIXME before TODO), stable within a marker
    order = {mk: i for i, mk in enumerate(_MARKERS)}
    hits.sort(key=lambda t: order.get(t.marker, len(_MARKERS)))
    return hits[:limit]


def parse_pytest(output: str) -> Fitness:
    """Parse a pytest run's tail into a Fitness verdict. Pure."""
    counts = {kind: int(n) for n, kind in _PYTEST_SUMMARY.findall(output)}
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0)
    total = passed + failed
    # last non-empty line is usually pytest's own summary
    tail = [ln for ln in output.splitlines() if ln.strip()]
    summary = tail[-1][:200] if tail else "(no output)"
    return Fitness(passed=(failed == 0 and total > 0), total=total, failed=failed, summary=summary)


def run_fitness(root: Path | str, *, target_tests: str | None = None,
                timeout: int = 600) -> Fitness:
    """Run the test suite in ``root`` and return the Fitness verdict.

    ``target_tests`` optionally narrows to a path/expression (faster pytest gate).
    With no narrowing, the repo's OWN verify command is detected (pytest, npm,
    cargo, go, make, or a RONIN.md ``verify:`` line) so the autonomy stack works
    on Node/Go/Rust repos, not just pytest."""
    if target_tests is None:
        from .verify_cmd import _has_python_tests, detect_verify_command, run_verify
        detected = detect_verify_command(root)
        # Only hand off to a non-pytest runner when this isn't a Python project.
        # Belt-and-suspenders: a Python repo with a stray package.json must never
        # have its real pytest gate silently swapped for `npm test`.
        if (detected is not None and detected[1] != "pytest"
                and not _has_python_tests(root)):
            v = run_verify(root, timeout=timeout)
            return Fitness(passed=v.passed, total=(1 if v.ran else 0),
                           failed=(0 if v.passed else 1), summary=v.summary)
    cmd = ["uv", "run", "pytest", "-q"]
    if target_tests:
        cmd.append(target_tests)
    try:
        proc = subprocess.run(cmd, cwd=str(Path(root).resolve()),
                              capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        return Fitness(passed=False, total=0, failed=0, summary=f"could not run tests: {e}")
    return parse_pytest((proc.stdout or "") + "\n" + (proc.stderr or ""))


def run_kaizen(config: RoninConfig, root: Path | str, console: Console, *,
               goal: str | None = None, target_tests: str | None = None,
               auto_apply: bool = False,
               duel_against: dict[str, Any] | None = None) -> KaizenResult:
    """One Kaizen cycle: pick a weakness → fix it in an isolated worktree →
    prove it with the test suite → (optionally) have a RIVAL model review the
    diff → surface it for approval.

    ``duel_against`` is a provider[:model] spec for a cross-vendor reviewer: the
    proven diff is red-teamed by a different model before you see it, so a self-
    improvement clears both an objective gate (tests) and an adversarial one.
    Returns a ``KaizenResult``. The working tree is only modified if approved
    (or ``auto_apply`` — but a rival BLOCK forces a confirm even then)."""
    root_path = Path(root).resolve()
    if not is_git_repo(root_path):
        return KaizenResult(None, None, "", False, "not a git repository — Kaizen needs git")

    # 1. find the weakness to forge against
    target: Target | None = None
    if goal is None:
        targets = find_targets(root_path)
        if not targets:
            return KaizenResult(None, None, "", False,
                                "no FIXME/TODO markers found — give an explicit goal instead")
        target = targets[0]
        goal = (f"Resolve this {target.marker} in {target.file} (line {target.line}): "
                f"{target.text.strip()}\n\nMake the smallest correct change. Do NOT break "
                f"any existing tests; add a test if it makes sense.")
        console.print(f"[bold #7aa2f7]改 kaizen[/bold #7aa2f7] target → [dim]{target.label()}[/dim]")
    else:
        console.print(f"[bold #7aa2f7]改 kaizen[/bold #7aa2f7] goal → [dim]{goal[:100]}[/dim]")

    # 2. forge the fix in an isolated worktree (auto-applied THERE; never here yet)
    from .code_mode import run_code_agent
    with git_worktree(root_path, label="kaizen") as wt:
        console.print("[dim]  forging in an isolated worktree…[/dim]")
        run_code_agent(config, goal, root=wt, console=None, yolo=True, read_only=False)

        # 3. fitness gate: the suite must still pass IN the worktree
        console.print("[dim]  running the test suite as the fitness gate…[/dim]")
        fitness = run_fitness(wt, target_tests=target_tests)
        diff = worktree_diff(wt)

    if not diff.strip():
        return KaizenResult(target, fitness, "", False, "the agent made no changes")
    if not fitness.passed:
        return KaizenResult(target, fitness, diff, False,
                            f"fitness gate FAILED ({fitness.summary}) — change discarded, "
                            "your tree is untouched")

    console.print(f"[green]  ✓ fitness passed[/green] [dim]({fitness.summary})[/dim]")

    # 4. cross-vendor review: a RIVAL model adversarially red-teams the proven
    # diff before you ever see it. Advisory — but a BLOCK overrides auto-apply.
    verdict = None
    if duel_against is not None:
        from .duel import render_verdict, review_diff
        console.print("[dim]  ⚔ a rival model is reviewing the change…[/dim]")
        verdict = review_diff(config, diff, duel_against)
        render_verdict(console, verdict)

    # 5. surface the proven diff for approval, then apply to the real tree
    forced_confirm = verdict is not None and not verdict.passed
    if not auto_apply or forced_confirm:
        if auto_apply and forced_confirm:
            console.print("[yellow]  rival flagged blocker(s) — confirming despite --yes[/yellow]")
        console.print("[bold]proposed self-improvement (proven against the suite):[/bold]")
        _print_diff(console, diff)
        console.print("  [yellow]apply to your working tree?[/yellow] [grey50]y / N[/grey50] ", end="")
        try:
            if input().strip().lower() not in ("y", "yes"):
                return KaizenResult(target, fitness, diff, False, "aborted — tree untouched", verdict)
        except (EOFError, KeyboardInterrupt):
            return KaizenResult(target, fitness, diff, False, "aborted — tree untouched", verdict)

    applied = _apply_diff(root_path, diff)
    return KaizenResult(target, fitness, diff, applied,
                        "applied ✓" if applied else "could not apply the patch cleanly",
                        verdict)


def _apply_diff(root: Path, diff: str) -> bool:
    """Apply a unified diff to the working tree via ``git apply``."""
    try:
        proc = subprocess.run(["git", "-C", str(root), "apply", "--whitespace=nowarn", "-"],
                              input=diff, capture_output=True, text=True, timeout=30)
        return proc.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _print_diff(console: Console, diff: str, max_lines: int = 120) -> None:
    from rich.markup import escape as _esc
    for i, line in enumerate(diff.splitlines()):
        if i >= max_lines:
            console.print(f"  [dim]… (diff truncated, {len(diff.splitlines())} lines)[/dim]")
            break
        safe = _esc(line)
        if line.startswith("+") and not line.startswith("+++"):
            console.print(f"  [green]{safe}[/green]")
        elif line.startswith("-") and not line.startswith("---"):
            console.print(f"  [red]{safe}[/red]")
        elif line.startswith("@@"):
            console.print(f"  [cyan]{safe}[/cyan]")
        else:
            console.print(f"  [dim]{safe}[/dim]")
