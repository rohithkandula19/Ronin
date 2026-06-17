"""Skills — ronin learns a reusable procedure from a task it just did.

A *skill* is a named, parameterized recipe: a short procedure (the steps to do
something) with ``{placeholder}`` slots you fill in when you run it. Once you
finish a task worth repeating ("ship a release", "scaffold a FastAPI route"),
you save it as a skill; next time you ``ronin skills run <name> k=v`` and get the
filled-in procedure back, and the agent is *offered* your skills as part of its
context so it can follow them.

Storage is local and dependency-free: one JSON file per skill under
``~/.ronin/skills/`` (user-global, like memory), so skills persist across
sessions and projects. No provider, no network.

The placeholder syntax is ``{name}`` (e.g. ``Cut a {level} release of {repo}``).
Running a skill substitutes the params you pass; any param the steps reference
but you don't supply is reported as missing rather than silently left blank.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ronin_agent_patterns import Tool

# A skill name: lowercase letters, digits, dash/underscore. Keeps file names sane
# and makes `run <name>` unambiguous.
_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]*$")
# A {placeholder} in the steps. Names follow the same rules as params below.
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
_MAX_STEPS_CHARS = 8000


def _skills_dir() -> Path:
    return Path.home() / ".ronin" / "skills"


def normalize_name(name: str) -> str:
    """Lowercase, trim, and turn spaces/odd chars into dashes — so "Ship Release"
    and "ship-release" land on the same skill."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return slug


def is_valid_name(name: str) -> bool:
    return bool(_NAME_RE.match(name or ""))


def extract_params(steps: str) -> list[str]:
    """The ``{placeholder}`` names referenced in ``steps``, in first-seen order
    (deduped). This is what makes a skill *parameterized* without a schema."""
    seen: list[str] = []
    for m in _PLACEHOLDER_RE.finditer(steps or ""):
        name = m.group(1)
        if name not in seen:
            seen.append(name)
    return seen


@dataclass
class Skill:
    name: str
    description: str
    steps: str
    params: list[str] = field(default_factory=list)
    created: float = 0.0
    source: str = ""  # optional: the session id / task this was learned from

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": self.steps,
            "params": list(self.params),
            "created": self.created,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Skill":
        steps = str(data.get("steps", ""))
        # Trust stored params if present, else recompute from the steps so a
        # hand-edited skill file still works.
        params = data.get("params")
        if not isinstance(params, list) or not params:
            params = extract_params(steps)
        return cls(
            name=str(data.get("name", "")),
            description=str(data.get("description", "")),
            steps=steps,
            params=[str(p) for p in params],
            created=float(data.get("created", 0.0) or 0.0),
            source=str(data.get("source", "")),
        )


def _path_for(name: str) -> Path:
    return _skills_dir() / f"{name}.json"


def save_skill(
    name: str,
    description: str,
    steps: str,
    *,
    source: str = "",
    overwrite: bool = True,
) -> Skill | None:
    """Create (or overwrite) a skill. Returns the saved ``Skill`` or ``None`` if
    the name is invalid, the steps are empty, or it exists and ``overwrite`` is
    False."""
    name = normalize_name(name)
    steps = (steps or "").strip()
    if not is_valid_name(name) or not steps:
        return None
    if len(steps) > _MAX_STEPS_CHARS:
        steps = steps[:_MAX_STEPS_CHARS] + "\n…[skill steps truncated]"
    path = _path_for(name)
    if path.exists() and not overwrite:
        return None
    skill = Skill(
        name=name,
        description=" ".join((description or "").split()).strip(),
        steps=steps,
        params=extract_params(steps),
        created=time.time(),
        source=source,
    )
    try:
        _skills_dir().mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(skill.to_dict(), indent=2), encoding="utf-8")
    except OSError:
        return None
    return skill


def load_skill(name: str) -> Skill | None:
    """Load one skill by name (normalized). ``None`` if it doesn't exist."""
    name = normalize_name(name)
    path = _path_for(name)
    if not path.is_file():
        return None
    try:
        return Skill.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def list_skills() -> list[Skill]:
    """All saved skills, newest first."""
    d = _skills_dir()
    if not d.is_dir():
        return []
    out: list[Skill] = []
    for f in d.glob("*.json"):
        try:
            out.append(Skill.from_dict(json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda s: (s.created, s.name), reverse=True)
    return out


def forget_skill(name: str) -> bool:
    """Delete a skill. Returns True if it existed and was removed."""
    name = normalize_name(name)
    path = _path_for(name)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


@dataclass
class RenderResult:
    text: str
    missing: list[str]
    used: dict[str, str]


def render_skill(skill: Skill, params: dict[str, str]) -> RenderResult:
    """Fill the skill's ``{placeholder}`` slots with ``params``.

    Placeholders with no supplied value are left as ``{name}`` and reported in
    ``missing`` (so the caller can prompt for them) rather than blanked out.
    Pure — no side effects."""
    missing: list[str] = []
    used: dict[str, str] = {}

    def repl(m: re.Match) -> str:
        key = m.group(1)
        if key in params and str(params[key]).strip() != "":
            used[key] = str(params[key])
            return str(params[key])
        if key not in missing:
            missing.append(key)
        return m.group(0)  # leave {name} intact

    text = _PLACEHOLDER_RE.sub(repl, skill.steps)
    return RenderResult(text=text, missing=missing, used=used)


def parse_kv(pairs: list[str]) -> dict[str, str]:
    """Parse ``key=value`` CLI args into a dict. Values may contain ``=``; the
    first ``=`` splits. Args without ``=`` are ignored (the caller validates)."""
    out: dict[str, str] = {}
    for p in pairs or []:
        if "=" in p:
            k, v = p.split("=", 1)
            k = k.strip()
            if k:
                out[k] = v
    return out


def skills_prompt_block(limit: int = 30) -> str:
    """The '## Skills you can follow' block injected into the agent's system
    prompt, so saved procedures are *offered* to the agent each run. Empty string
    when there are no skills (zero overhead)."""
    skills = list_skills()
    if not skills:
        return ""
    lines = []
    for s in skills[:limit]:
        params = f" (params: {', '.join(s.params)})" if s.params else ""
        desc = f" — {s.description}" if s.description else ""
        lines.append(f"- {s.name}{desc}{params}")
    body = "\n".join(lines)
    return (
        "\n\n## Skills you can follow (learned procedures)\n"
        "These are reusable procedures the user saved from past tasks. When a "
        "request matches one, follow its steps. You can fetch a skill's full "
        "steps with the `use_skill` tool.\n"
        f"{body}"
    )


def build_skill_tools() -> list[Tool]:
    """Tools that let the agent use and create skills during a run.

    - ``use_skill`` returns a saved skill's steps (optionally rendered with
      params) so the agent can follow the learned procedure.
    - ``save_skill`` lets the agent capture a procedure it just worked out as a
      reusable skill for next time.
    """

    def use_skill(name: str, params: dict | None = None) -> str:
        skill = load_skill(name)
        if skill is None:
            available = ", ".join(s.name for s in list_skills()) or "(none saved)"
            return f"No skill named '{name}'. Available skills: {available}."
        rendered = render_skill(skill, {k: str(v) for k, v in (params or {}).items()})
        header = f"Skill: {skill.name}"
        if skill.description:
            header += f" — {skill.description}"
        out = f"{header}\n\nSteps:\n{rendered.text}"
        if rendered.missing:
            out += f"\n\n(Unfilled params, ask the user: {', '.join(rendered.missing)})"
        return out

    def save_skill_handler(name: str, description: str, steps: str) -> str:
        skill = save_skill(name, description, steps, source="agent")
        if skill is None:
            return "Could not save skill (invalid name or empty steps)."
        pinfo = f" Params: {', '.join(skill.params)}." if skill.params else ""
        return f"Saved skill '{skill.name}'.{pinfo} Run it later with `ronin skills run {skill.name}`."

    return [
        Tool(
            name="use_skill",
            description=(
                "Fetch the full steps of a saved skill (a learned procedure) so you "
                "can follow it. Pass `params` (a name->value object) to fill the "
                "skill's {placeholder} slots. Use this when the user's request "
                "matches one of the skills listed in your system prompt."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The skill name."},
                    "params": {
                        "type": "object",
                        "description": "Values for the skill's placeholders, e.g. {\"repo\": \"ronin\"}.",
                    },
                },
                "required": ["name"],
            },
            handler=use_skill,
        ),
        Tool(
            name="save_skill",
            description=(
                "Save a reusable, parameterized procedure as a skill so future "
                "sessions can follow it. Use {placeholder} slots in the steps for "
                "the parts that vary. Call this after you finish a task worth "
                "repeating, e.g. name='cut-release', steps='1. bump version in "
                "{file}\\n2. tag v{version}\\n3. push'."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short skill name, e.g. 'cut-release'."},
                    "description": {"type": "string", "description": "One line: what this skill does."},
                    "steps": {"type": "string", "description": "The procedure, with {placeholder} slots."},
                },
                "required": ["name", "steps"],
            },
            handler=save_skill_handler,
            sensitive=True,  # writes to the user's skill store
        ),
    ]
