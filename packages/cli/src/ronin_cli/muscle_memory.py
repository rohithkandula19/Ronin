"""Muscle Memory — ronin gets better at *your* repo over time.

When the agent works out how to do something non-trivial in this codebase, it can
**crystallize** that solution into a reusable skill: a ``.ronin/commands/NAME.md``
prompt template. Because that's the same format custom slash commands already
use, a crystallized skill is immediately re-runnable as ``/NAME`` — the agent's
hard-won approach becomes a one-liner you (or it) can replay.

Use it for a week and ronin has a custom playbook for *your* repo that compounds.
Skills live in the repo (committable, shareable with your team), not in some
opaque cloud memory. The crystallizer is pure and fully testable.
"""
from __future__ import annotations

import re
from pathlib import Path

_SKILLS_DIR = Path(".ronin") / "commands"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,40}$")


def skills_dir(root: Path | str) -> Path:
    return Path(root) / _SKILLS_DIR


def normalize_name(name: str) -> str:
    """Slugify a proposed skill name to the kebab-case the runner expects."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:41]


def list_skills(root: Path | str) -> list[str]:
    """Names of all crystallized skills in this repo (sorted)."""
    d = skills_dir(root)
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md") if p.is_file())


def crystallize(root: Path | str, name: str, template: str) -> tuple[Path, str]:
    """Save a reusable skill as ``.ronin/commands/<name>.md``. Returns
    ``(path, slug)``. Raises ``ValueError`` on a bad/reserved name or empty body.

    The slug is normalized to kebab-case; a name colliding with a builtin slash
    command is rejected so a skill can never shadow ``/help`` etc."""
    from .code_mode import SLASH_COMMANDS  # lazy: avoid an import cycle
    slug = normalize_name(name)
    if not _NAME_RE.match(slug):
        raise ValueError(f"invalid skill name {name!r} — use letters, digits, and dashes")
    if slug in SLASH_COMMANDS:
        raise ValueError(f"'{slug}' is a builtin command — pick another name")
    if not template.strip():
        raise ValueError("skill body is empty")
    d = skills_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.md"
    body = template.strip()
    if "$ARGUMENTS" not in body:
        # let the skill take a free-text argument even if the model forgot
        body += "\n\n$ARGUMENTS"
    path.write_text(body + "\n", encoding="utf-8")
    return path, slug


def build_muscle_tool(root: Path | str) -> list:
    """The ``crystallize_skill`` tool: the agent saves a reusable, repo-local
    skill once it's worked out how to do something here."""
    from ronin_agent_patterns import Tool

    def crystallize_skill(name: str, template: str) -> str:
        try:
            path, slug = crystallize(root, name, template)
        except ValueError as e:
            return f"ERROR: {e}"
        return (f"Crystallized skill '/{slug}' → {path}. It's re-runnable now as "
                f"`/{slug} <args>`. Use $ARGUMENTS in the template for the input.")

    return [
        Tool(
            name="crystallize_skill",
            description="Save a REUSABLE skill for this repo as a /command, after you've "
                        "worked out how to do something non-trivial here. 'template' is a "
                        "prompt the future agent will run; put $ARGUMENTS where the caller's "
                        "input should go. Use ONLY for genuinely repeatable workflows (e.g. "
                        "'add an endpoint', 'cut a release'), not one-off tasks. The skill "
                        "becomes `/name` immediately and is committed with the repo.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "short kebab-case name, e.g. add-endpoint"},
                    "template": {"type": "string", "description": "the reusable prompt; use $ARGUMENTS"},
                },
                "required": ["name", "template"],
            },
            handler=crystallize_skill,
        ),
    ]
