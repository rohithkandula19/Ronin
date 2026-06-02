"""``ronin review`` — an AI code review of your changes.

Read-only: it diffs your working tree (or a branch vs a base), reads surrounding
files for context, and returns structured, severity-tagged findings — bugs,
security, then style. It never edits; pair it with ``/commit`` once you've acted.
"""
from __future__ import annotations

from pathlib import Path

from rich.console import Console

from .config import RoninConfig

REVIEW_SYSTEM = """You are a meticulous senior code reviewer. Review the diff the
user gives you. You may read other files in the project for context, but DO NOT
edit anything — this is a review.

Report findings grouped by severity, most serious first:
- 🔴 Critical — bugs, security holes, data loss, breaking changes
- 🟡 Medium — likely bugs, missing error handling, performance, unclear logic
- 🟢 Nit — style, naming, tests, docs

For each finding give: the file and line/area, what's wrong, and a concrete fix.
Be specific and skip praise/filler. If the diff is clean, say so plainly and
mention anything you'd still double-check. End with a one-line verdict
(e.g. "Ship it after fixing the 🔴 items")."""


def run_review(
    config: RoninConfig,
    *,
    root: Path | str = ".",
    base: str | None = None,
    staged: bool = False,
    pr: int | None = None,
    comment: bool = False,
    console: Console,
    max_iterations: int = 10,
) -> bool:
    """Collect the diff and stream an AI review. Returns False if there's nothing
    to review or it's not a git repo.

    ``pr`` reviews a GitHub PR (diff fetched via ``gh``); ``comment`` posts the
    review back onto that PR."""
    from .git_helper import _git

    if pr is not None:
        from .gh_helper import gh_available, pr_diff
        if not gh_available():
            console.print("[yellow]the GitHub CLI 'gh' isn't installed.[/yellow] "
                          "[dim]brew install gh && gh auth login[/dim]")
            return False
        diff = pr_diff(pr, root) or ""
        scope = f"PR #{pr}"
    else:
        if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
            console.print("[yellow]not a git repository[/yellow]")
            return False
        if base:
            diff = _git(root, "diff", f"{base}...HEAD").stdout
            scope = f"branch vs {base}"
        elif staged:
            diff = _git(root, "diff", "--cached").stdout
            scope = "staged changes"
        else:
            diff = _git(root, "diff").stdout
            scope = "working-tree changes"
    if not diff.strip():
        console.print(f"[dim]no {scope} to review.[/dim]")
        return False

    console.print(f"[#6b7089]reviewing {scope}…[/#6b7089]")
    from .code_mode import run_code_agent

    prompt = (f"Review the following {scope}. Read related files if you need context.\n\n"
              f"```diff\n{diff[:20000]}\n```")
    result = run_code_agent(
        config, prompt, root=root, console=console, read_only=True,
        base_system=REVIEW_SYSTEM, include_image_tool=False, max_iterations=max_iterations,
    )

    if pr is not None and comment and result.output:
        from .gh_helper import pr_comment
        body = f"## 🐼 ronin review\n\n{result.output}\n\n_Posted by ronin._"
        ok = pr_comment(pr, body, root)
        console.print("[green]✓ posted review to the PR[/green]" if ok
                      else "[yellow]couldn't post the comment (check gh auth)[/yellow]")
    return True
