"""Bushido — ronin's code of honor, carried across every repo.

Project memory (RONIN.md / CLAUDE.md) is *per-repo*. Bushido is the **global**
counterpart: a single ``~/.ronin/bushido.md`` that travels with you into every
project. It captures *your* standing conventions — "pytest never unittest", "no
comments unless asked", "prefer httpx over requests" — so a masterless agent
that serves only you follows the same code wherever it goes.

It loads into the system prompt under your project's notes (project memory wins
on conflicts — a repo can always override your global default). The agent can
append a freshly-learned preference via the ``remember_preference`` tool, and
you can curate the file by hand any time.
"""
from __future__ import annotations

import os
from pathlib import Path

_MAX_CHARS = 6000

_TEMPLATE = """# Bushido — {name}'s code

ronin carries this into every repo. Keep it short, high-signal, and *personal* —
standing preferences that are true across all your projects. A specific repo's
RONIN.md / CLAUDE.md always overrides anything here.

## Code
- <a convention you always want, e.g. "tests: pytest, never unittest">
"""


def bushido_path() -> Path:
    """Location of the global code-of-honor file. Honors ``RONIN_HOME``."""
    home = os.environ.get("RONIN_HOME")
    base = Path(home) if home else Path.home() / ".ronin"
    return base / "bushido.md"


def load_bushido() -> str | None:
    """Return the bushido file's contents (capped), or ``None`` if absent/empty."""
    path = bushido_path()
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS] + "\n…[bushido truncated]"
    return text


def bushido_system_block() -> str | None:
    """System-prompt block for the global code of honor, or ``None``."""
    text = load_bushido()
    if text is None:
        return None
    return (
        "\n\n## Your code of honor (bushido — global, applies in every repo)\n"
        "These are the user's standing personal conventions. Follow them unless "
        "this repo's project notes say otherwise (the repo always wins on a "
        "conflict):\n\n"
        f"{text}"
    )


def remember_rule(rule: str) -> Path:
    """Append a one-line standing preference under ``## Code`` (newest first).

    Creates ``~/.ronin/bushido.md`` (and its parent) on first use. Idempotent:
    a rule already present (case-insensitively) is not added twice.
    """
    path = bushido_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    rule = rule.strip().lstrip("-").strip()
    bullet = f"- {rule}\n"

    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
    else:
        name = os.environ.get("USER") or "you"
        text = _TEMPLATE.format(name=name)

    # de-dup (case-insensitive) — don't pile up the same rule
    existing = {ln.strip().lstrip("-").strip().lower()
                for ln in text.splitlines() if ln.strip().startswith("-")}
    if rule.lower() in existing:
        path.write_text(text, encoding="utf-8")
        return path

    if "## Code" in text:
        out: list[str] = []
        inserted = False
        for line in text.splitlines(keepends=True):
            out.append(line)
            if not inserted and line.strip() == "## Code":
                if not line.endswith("\n"):
                    out.append("\n")
                out.append(bullet)
                inserted = True
        text = "".join(out)
    else:
        sep = "" if (not text or text.endswith("\n")) else "\n"
        text = f"{text}{sep}\n## Code\n\n{bullet}"
    path.write_text(text, encoding="utf-8")
    return path


def build_bushido_tool() -> list:
    """The ``remember_preference`` tool: the agent persists a learned, standing
    convention to the global code of honor so it sticks across every repo."""
    from ronin_agent_patterns import Tool

    def remember_preference(rule: str) -> str:
        path = remember_rule(rule)
        return f"Recorded in your code of honor ({path}): {rule.strip()}"

    return [
        Tool(
            name="remember_preference",
            description="Persist a STANDING personal coding preference to the user's "
                        "global code of honor (~/.ronin/bushido.md), so it applies in "
                        "every repo from now on. Use ONLY for durable, cross-project "
                        "preferences the user states or clearly implies (e.g. 'always "
                        "use pytest', 'never add comments unless asked') — not one-off, "
                        "repo-specific facts (those go to project memory).",
            input_schema={
                "type": "object",
                "properties": {
                    "rule": {"type": "string", "description": "one concise imperative line"},
                },
                "required": ["rule"],
            },
            handler=remember_preference,
        ),
    ]
