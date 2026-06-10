"""Understand a new codebase fast — `ronin onboard <repo>`.

Point it at a repo (a git URL → cloned to a temp dir, or a local path) and ronin
gathers deterministic facts (stack, size, entry points, architecture stats, docs)
then writes an onboarding guide: what it is, how it's laid out, where to start,
how to build/run/test. The URL detection + fact-gathering are pure-ish and
unit-tested; the guide is the agent's read-only synthesis.
"""
from __future__ import annotations

import re
from pathlib import Path

_GIT_URL_RE = re.compile(r"^(https?://|git@|ssh://|git://).+", re.IGNORECASE)


def is_git_url(s: str) -> bool:
    """True if ``s`` looks like a cloneable git URL (vs a local path). Pure."""
    return bool(_GIT_URL_RE.match(s.strip())) or s.strip().endswith(".git")


def gather_facts(root: Path | str) -> dict:
    """Deterministic facts about a repo for the onboarding prompt."""
    from .diagram import detect_package, import_graph, stats
    from .scaffold import detect_stack

    rp = Path(root).resolve()
    facts: dict = {"stack": detect_stack(rp)}

    # docs + config files present at the root
    notable = ["README.md", "README.rst", "CONTRIBUTING.md", "Makefile", "Dockerfile",
               "docker-compose.yml", ".github", "pyproject.toml", "package.json"]
    facts["notable"] = [n for n in notable if (rp / n).exists()]

    # entry points: pyproject [project.scripts], package.json scripts (best-effort)
    entries: list[str] = []
    pj = rp / "pyproject.toml"
    if pj.is_file():
        txt = pj.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"\[project\.scripts\](.*?)(\n\[|\Z)", txt, re.DOTALL)
        if m:
            entries += re.findall(r"^\s*([A-Za-z0-9_\-]+)\s*=", m.group(1), re.MULTILINE)
    facts["entry_points"] = entries[:10]

    # architecture stats for the biggest package
    pkg = detect_package(rp)
    if pkg:
        g = import_graph(pkg)
        s = stats(g)
        facts["package"] = str(pkg.relative_to(rp)) if pkg != rp else "."
        facts["modules"] = s["modules"]
        facts["core_modules"] = s["most_depended"]
    return facts


def facts_brief(facts: dict) -> str:
    """A compact text summary of the facts for the agent prompt. Pure."""
    lines = [f"Stack: {', '.join(facts.get('stack', []))}"]
    if facts.get("package"):
        lines.append(f"Main package: {facts['package']} ({facts.get('modules', 0)} modules)")
    if facts.get("core_modules"):
        lines.append(f"Most depended-on modules: {', '.join(facts['core_modules'])}")
    if facts.get("entry_points"):
        lines.append(f"Entry points: {', '.join(facts['entry_points'])}")
    if facts.get("notable"):
        lines.append(f"Notable files: {', '.join(facts['notable'])}")
    return "\n".join(lines)


def onboard_prompt(facts: dict) -> str:
    return (
        "You're onboarding a developer to this repository. Using your read-only "
        "tools and these gathered facts, write a tight onboarding guide as markdown:\n"
        "## What it is\n## How it's laid out\n## Where to start reading\n"
        "## Build / run / test\n## Gotchas\n\nBe concrete — name real files and "
        f"commands; don't pad.\n\nFacts:\n{facts_brief(facts)}"
    )
