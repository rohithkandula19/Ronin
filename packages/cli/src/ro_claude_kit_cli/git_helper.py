"""In-session git helpers: ``/commit`` (LLM-drafted message, gated) and ``/pr``.

Both are mutations, so they always show what they'll do and wait for your y/N
before committing or pushing — nothing happens silently.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from rich.console import Console

from .config import CSKConfig


def _git(root, *args, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(Path(root).resolve()), *args],
        capture_output=True, text=True, timeout=timeout,
    )


def _confirm(console: Console, label: str) -> bool:
    console.print(f"  [yellow]{label}[/yellow] [grey50]y / N[/grey50] ", end="")
    try:
        return input().strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _draft(config: CSKConfig, system: str, prompt: str, max_tokens: int = 300) -> str:
    from ro_claude_kit_agent_patterns import Message

    from .runner import build_provider
    text = build_provider(config).complete(
        system=system, messages=[Message(role="user", content=prompt)],
        tools=[], max_tokens=max_tokens,
    ).text.strip()
    return text.strip().strip("`").strip()


def commit(console: Console, root, config: CSKConfig) -> None:
    """Draft a commit message from the diff and commit it (after approval)."""
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        console.print("[yellow]not a git repository[/yellow]")
        return
    diff = _git(root, "diff", "--cached").stdout
    stage_all = False
    if not diff.strip():
        # nothing staged — does the tree have any changes at all (incl. untracked)?
        if not _git(root, "status", "--porcelain").stdout.strip():
            console.print("[dim]nothing to commit — working tree clean[/dim]")
            return
        _git(root, "add", "-A")  # stage all so the diff (and commit) include new files
        diff = _git(root, "diff", "--cached").stdout
        stage_all = True
    if not diff.strip():
        console.print("[dim]nothing to commit — working tree clean[/dim]")
        return
    try:
        msg = _draft(
            config, "You write excellent, concise git commit messages.",
            "Write ONE git commit message (Conventional Commits style: imperative "
            "subject under ~72 chars, optional short body). Output only the message, "
            "no backticks.\n\n" + diff[:6000],
        )
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]couldn't draft a message:[/red] {e}")
        return
    console.print("[bold]proposed commit[/bold]" + ("  [dim](all changes staged)[/dim]" if stage_all else ""))
    for line in msg.splitlines():
        console.print(f"  [cyan]{line}[/cyan]")
    if not _confirm(console, "commit?"):
        if stage_all:
            _git(root, "reset")  # restore: unstage what we auto-staged
        console.print("[dim]aborted[/dim]")
        return
    r = _git(root, "commit", "-m", msg)
    console.print("[green]✓ committed[/green]" if r.returncode == 0
                  else f"[red]commit failed:[/red] {(r.stderr or r.stdout).strip()[:200]}")


def open_pr(console: Console, root, config: CSKConfig) -> None:
    """Push the branch and open a PR (title/body LLM-drafted), all gated."""
    if not shutil.which("gh"):
        console.print("[yellow]the GitHub CLI 'gh' isn't installed.[/yellow] "
                      "[dim]brew install gh && gh auth login[/dim]")
        return
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        console.print("[yellow]not a git repository[/yellow]")
        return
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch in ("main", "master"):
        console.print(f"[yellow]you're on '{branch}' — make a feature branch first "
                      "(git switch -c my-change).[/yellow]")
        return
    log = _git(root, "log", "--oneline", "-15").stdout
    try:
        draft = _draft(
            config, "You write clear pull-request titles and descriptions.",
            "From these commits, write a PR. First line = title (<72 chars). Then a "
            "blank line, then a short markdown body (what changed and why). No backticks.\n\n"
            + log, max_tokens=400,
        )
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]couldn't draft the PR:[/red] {e}")
        return
    title, _, body = draft.partition("\n")
    body = body.strip()
    console.print(f"[bold]PR[/bold] on branch [cyan]{branch}[/cyan]")
    console.print(f"  title: [cyan]{title}[/cyan]")
    if body:
        console.print(f"  [dim]{body[:300]}[/dim]")
    if not _confirm(console, "push branch & open PR?"):
        console.print("[dim]aborted[/dim]")
        return
    push = _git(root, "push", "-u", "origin", branch, timeout=60)
    if push.returncode != 0:
        console.print(f"[red]push failed:[/red] {(push.stderr or push.stdout).strip()[:200]}")
        return
    r = subprocess.run(["gh", "pr", "create", "--title", title, "--body", body or title],
                       cwd=str(Path(root).resolve()), capture_output=True, text=True, timeout=60)
    console.print((r.stdout or "").strip() if r.returncode == 0
                  else f"[red]gh pr create failed:[/red] {(r.stderr or '').strip()[:200]}")
