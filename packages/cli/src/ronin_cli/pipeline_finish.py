"""Optional, gated commit/PR finish for the role pipeline.

Strictly opt-in (``--commit`` / ``--pr``) and **never silent**: it reuses the
existing gated git helpers, only proceeds after a passing verifier (or asks for
an explicit confirmation otherwise), always shows the diff summary first, and
makes ZERO git changes in ``--dry-run``. It never invents a remote/branch.

``commit_gate`` is the pure decision; ``finish_pipeline`` does the IO.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .git_helper import _confirm, _draft, _git


def commit_gate(state, *, force: bool = False) -> tuple[bool, str]:
    """May the pipeline commit? Returns ``(allowed, reason)`` — pure.

    Allowed only when the verifier (else tester) verdict is ``passed``, or — with
    no verifier in the run — when every stage completed. A failed/blocked/unknown
    verdict, or a halted pipeline, is allowed only with ``force`` (the CLI turns
    that into an explicit y/N confirmation, never an automatic commit)."""
    if state.dry_run:
        return False, "dry run — nothing to commit"
    if state.stopped:
        stopper = next((s for s in state.stages
                        if s.status in ("failed", "blocked")), None)
        where = f"{stopper.role} ({stopper.status})" if stopper else "a stage"
        return force, f"pipeline stopped at {where}"
    # Prefer the combined Wave-6 verdict (verifier + independent verify + contract)
    # when it's been computed; fall back to the verifier-stage verdict.
    final = state.final_verdict or state.verdict
    if state.verification() is not None or state.final_verdict:
        v = final or "unknown"
        if v == "passed":
            return True, "final verdict: passed"
        return force, f"final verdict is '{v}', not 'passed'"
    if state.outcome() == "completed":
        return True, "all stages completed (no verifier ran)"
    return force, "pipeline did not complete"


def _diff_stat(root) -> str:
    out = _git(root, "diff", "--stat").stdout.strip()
    if out:
        return out
    porcelain = _git(root, "status", "--porcelain").stdout.strip()
    return porcelain or "(working tree clean)"


def _do_commit(console, root, config, message: str | None) -> bool:
    if not _git(root, "status", "--porcelain").stdout.strip():
        console.print("[dim]nothing to commit — working tree clean[/dim]")
        return False
    _git(root, "add", "-A")
    diff = _git(root, "diff", "--cached").stdout
    msg = message
    if not msg:
        try:
            msg = _draft(
                config, "You write excellent, concise git commit messages.",
                "Write ONE Conventional-Commits message (imperative subject < ~72 "
                "chars). Output only the message.\n\n" + diff[:6000])
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]couldn't draft a message:[/red] {exc}")
            _git(root, "reset")
            return False
    console.print("[bold]proposed commit[/bold]")
    for line in msg.splitlines():
        console.print(f"  [cyan]{line}[/cyan]")
    if not _confirm(console, "commit?"):
        _git(root, "reset")
        console.print("[dim]aborted[/dim]")
        return False
    r = _git(root, "commit", "-m", msg)
    ok = r.returncode == 0
    console.print("[green]✓ committed[/green]" if ok
                  else f"[red]commit failed:[/red] {(r.stderr or r.stdout).strip()[:200]}")
    return ok


def _do_pr(console, root, config, title: str | None, body: str | None) -> bool:
    if not shutil.which("gh"):
        console.print("[yellow]'gh' not installed — skipping PR.[/yellow] "
                      "[dim]brew install gh && gh auth login[/dim]")
        return False
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch in ("main", "master"):
        console.print(f"[yellow]on '{branch}' — make a feature branch first "
                      "(or pass --branch).[/yellow]")
        return False
    if not title:
        log = _git(root, "log", "--oneline", "-15").stdout
        try:
            draft = _draft(config, "You write clear pull-request titles and descriptions.",
                           "From these commits, write a PR: first line title (<72 chars), "
                           "blank line, then a short markdown body.\n\n" + log, max_tokens=400)
            title, _, drafted_body = draft.partition("\n")
            body = body or drafted_body.strip()
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]couldn't draft the PR:[/red] {exc}")
            return False
    console.print(f"[bold]PR[/bold] on branch [cyan]{branch}[/cyan] · title: [cyan]{title}[/cyan]")
    if not _confirm(console, "push branch & open PR?"):
        console.print("[dim]aborted[/dim]")
        return False
    push = _git(root, "push", "-u", "origin", branch, timeout=60)
    if push.returncode != 0:
        console.print(f"[red]push failed:[/red] {(push.stderr or push.stdout).strip()[:200]}")
        return False
    r = subprocess.run(["gh", "pr", "create", "--title", title, "--body", body or title],
                       cwd=str(Path(root).resolve()), capture_output=True, text=True, timeout=60)
    ok = r.returncode == 0
    console.print((r.stdout or "").strip() if ok
                  else f"[red]gh pr create failed:[/red] {(r.stderr or '').strip()[:200]}")
    return ok


def finish_pipeline(console, root, config, state, *, do_commit: bool = False,
                    do_pr: bool = False, branch: str | None = None,
                    message: str | None = None, title: str | None = None,
                    body: str | None = None, dry_run: bool = False,
                    force: bool = False) -> dict:
    """Run the opt-in commit/PR finish. Returns a small result dict. Makes no git
    changes when ``dry_run`` (it only describes what it would do)."""
    result = {"committed": False, "pr_opened": False, "skipped_reason": None,
              "would_commit": False, "would_pr": False}
    if not (do_commit or do_pr):
        return result

    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        if console is not None:
            console.print("[yellow]not a git repository — skipping commit/PR[/yellow]")
        result["skipped_reason"] = "not a git repository"
        return result

    allowed, reason = commit_gate(state, force=force)
    if console is not None:
        console.print("[bold]changes[/bold]")
        console.print(f"[dim]{_diff_stat(root)}[/dim]")
        console.print(f"[dim]commit gate: {reason}[/dim]")

    if dry_run:
        result["would_commit"] = do_commit
        result["would_pr"] = do_pr
        if console is not None:
            steps = []
            if branch:
                steps.append(f"create branch '{branch}'")
            if do_commit:
                steps.append("commit (approval-gated)")
            if do_pr:
                steps.append("push + open PR (approval-gated)")
            console.print(f"  [dim]would: {', '.join(steps)} — dry run, nothing done[/dim]")
        return result

    if not allowed:
        if console is None or not _confirm(console, f"{reason} — proceed anyway?"):
            if console is not None:
                console.print("[dim]skipped commit/PR (verifier did not pass)[/dim]")
            result["skipped_reason"] = reason
            return result

    if branch:
        r = _git(root, "checkout", "-b", branch)
        if r.returncode != 0 and console is not None:
            console.print(f"[yellow]couldn't create branch '{branch}':[/yellow] "
                          f"{(r.stderr or '').strip()[:160]}")

    if do_commit:
        result["committed"] = _do_commit(console, root, config, message)
        if not result["committed"]:
            return result
    if do_pr:
        result["pr_opened"] = _do_pr(console, root, config, title, body)
    return result
