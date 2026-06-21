"""Draft a clean PR title + body from a branch's changes — `ronin pr`.

ronin compares the current branch against a base (default ``main``), gathers the
branch's commits + a diffstat, and the agent drafts a tight PR title + markdown
body grounded in what actually changed. The prompt building, output parsing, and
rendering are pure and unit-tested; the git reads and the provider call live in
``collect_branch_changes`` / ``draft``.

Every model call goes through ronin's OWN provider layer
(``build_single_provider(config).complete(...)``) — no vendor SDK. With no
provider auth, ``draft`` falls back to a heuristic title synthesized from the
commit subjects, so the command is still useful offline.
"""
from __future__ import annotations

from pathlib import Path

from .git_helper import _git


def pr_prompt(branch: str, base: str, diffstat: str, commits: list[str], *, max_chars: int = 14000) -> str:
    """Build the LLM prompt for a PR title + body. Pure.

    Grounds the model in the branch's commit subjects + the ``base..HEAD``
    diffstat so the description reflects the real change, not a guess."""
    log = "\n".join(f"- {c}" for c in commits) or "(no commits on this branch yet)"
    clipped = diffstat[:max_chars]
    note = "" if len(diffstat) <= max_chars else "\n\n[diffstat truncated]"
    return (
        "Write a pull-request title and description for merging branch "
        f"`{branch}` into `{base}`. Ground every claim in the commits and diffstat "
        "below — do not invent changes.\n\n"
        "Output format (no code fences, no preamble):\n"
        "  # <title>\n"
        "(a concise imperative title under ~72 chars)\n\n"
        "then a blank line, then a short markdown body: a one-line summary, then a "
        "`## Changes` list of 2-6 bullets covering what changed and why. Keep it "
        "tight and factual.\n\n"
        f"Commits ({len(commits)}):\n{log}\n\n"
        f"Diffstat ({base}..HEAD):\n```\n{clipped}\n```{note}"
    )


def parse_pr(text: str) -> tuple[str, str]:
    """Split model output into ``(title, body)``. Pure.

    The first non-empty line is the title (a leading ``# `` markdown heading or
    surrounding backticks are stripped); everything after it is the body. A
    title-only output yields an empty body."""
    lines = (text or "").strip().splitlines()
    # drop leading blank lines to find the real title line
    idx = 0
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    if idx >= len(lines):
        return "", ""
    title = lines[idx].strip()
    if title.startswith("#"):
        title = title.lstrip("#").strip()
    title = title.strip("`").strip()
    body = "\n".join(lines[idx + 1:]).strip()
    return title, body


def fallback_title(commits: list[str]) -> str:
    """A reasonable PR title without an LLM, from the commit subjects. Pure.

    A single commit becomes the title verbatim (minus any Conventional-Commit
    ``type(scope):`` prefix); several commits summarise as a count."""
    subjects = [c.strip() for c in commits if c.strip()]
    if not subjects:
        return "Update branch"
    if len(subjects) == 1:
        subj = subjects[0]
        # strip a leading "type(scope): " / "type: " prefix for a cleaner title
        head, sep, rest = subj.partition(": ")
        if sep and len(head) <= 24 and " " not in head:
            subj = rest
        return subj[:72].strip() or "Update branch"
    return f"{len(subjects)} changes from this branch"


def render_pr(title: str, body: str, console) -> None:
    """Show the drafted PR title + body in a rich panel. Impure (prints)."""
    from rich.panel import Panel

    inner = f"[bold]{title}[/bold]"
    if body:
        inner += f"\n\n{body}"
    console.print(Panel(inner, title="pull request", border_style="#6b7089", padding=(1, 2)))


def collect_branch_changes(root, *, base: str = "main") -> dict:
    """Gather the current branch's changes vs ``base``. Impure (runs git).

    Returns ``{branch, base, commits, diffstat, error}``. ``error`` is set (and
    the other fields empty) when ``root`` is not a git repo. ``commits`` is the
    list of ``base..HEAD`` subjects (newest first); ``diffstat`` is the
    ``git diff --stat base..HEAD`` text."""
    if _git(root, "rev-parse", "--is-inside-work-tree").returncode != 0:
        return {"branch": "", "base": base, "commits": [], "diffstat": "", "error": "not a git repository"}
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    rng = f"{base}..HEAD"
    log = _git(root, "log", rng, "--pretty=%s").stdout
    commits = [ln.strip() for ln in log.splitlines() if ln.strip()]
    diffstat = _git(root, "diff", "--stat", rng).stdout.strip()
    return {"branch": branch, "base": base, "commits": commits, "diffstat": diffstat, "error": ""}


def draft(config, root, *, base: str = "main") -> tuple[str, str]:
    """Draft ``(title, body)`` for the current branch vs ``base``.

    collect → build prompt → ronin provider ``.complete(...)`` → ``parse_pr``.
    Falls back to ``fallback_title`` (empty body) when there's no provider auth
    or the provider call fails — so the command always returns something usable.
    Raises ``ValueError`` only when ``root`` isn't a git repository."""
    changes = collect_branch_changes(root, base=base)
    if changes["error"]:
        raise ValueError(changes["error"])
    commits = changes["commits"]

    if not config.has_provider_auth():
        return fallback_title(commits), ""

    try:
        from ronin_agent_patterns import Message

        from .runner import build_single_provider
        provider = build_single_provider(config)
        prompt = pr_prompt(changes["branch"], base, changes["diffstat"], commits)
        resp = provider.complete(
            system="You write clear, accurate pull-request titles and descriptions.",
            messages=[Message(role="user", content=prompt)],
            tools=[],
            max_tokens=700,
        )
        title, body = parse_pr(resp.text or "")
        if not title:
            return fallback_title(commits), body
        return title, body
    except Exception:  # noqa: BLE001 — never fail the command on a provider/network error
        return fallback_title(commits), ""
