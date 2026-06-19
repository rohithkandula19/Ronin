"""Skill Registry — share ronin's repo-local muscle-memory skills, *safely*.

A **skill** is a markdown prompt template (``.ronin/commands/<name>.md``) — the
same thing :mod:`ronin_cli.muscle_memory` crystallizes and the custom-command
runner expands into the model's prompt. It is **not code**. When a skill is
expanded it becomes text that *influences* the agent; it can never itself run a
command, touch the filesystem, or call a tool. Every action the agent then
proposes still goes through the human y/n approval gate. That property is what
makes a public registry safe in a way that "install this plugin" (arbitrary
Python) never can be.

This module is the publish / install / search surface for that registry — think
agentskills.io / ClawHub, but where the installed artifact is a *template*, not
an executable:

* :func:`build_manifest` and :func:`validate_skill` are **pure** and fully unit
  tested. ``validate_skill`` is the SAFETY GATE: it rejects templates that try to
  smuggle an executable payload or break out of the prompt-template model
  (fenced ``bash``/``sh``/``python`` blocks dressed up as run-this instructions,
  ``!``-shell directives, classic prompt-injection like "ignore previous
  instructions / run the following").
* :func:`publish` packages a local skill + its manifest into a portable bundle
  and drops it in the local registry directory.
* :func:`install` validates **first** and only then writes the template into
  ``.ronin/commands/`` — a skill that fails the gate is never installed.
* :func:`search` queries the local registry index.

The registry backend is deliberately simple and offline-first: a directory of
``<name>.skill.json`` bundles under ``~/.ronin/skills/`` (override with
``RONIN_SKILL_REGISTRY``). No network is required for any of this; a git-repo
registry (clone-and-point-here) could back the same layout later without
changing this code.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .muscle_memory import normalize_name, skills_dir

MANIFEST_VERSION = "1"
# Bundle filename suffix in the registry dir, e.g. ``add-endpoint.skill.json``.
BUNDLE_SUFFIX = ".skill.json"


# --------------------------------------------------------------------------- #
# Registry location (offline, local-first).                                   #
# --------------------------------------------------------------------------- #
def default_registry_dir() -> Path:
    """The local skill registry directory.

    ``~/.ronin/skills/`` by default; override with the ``RONIN_SKILL_REGISTRY``
    env var (handy for tests and for pointing at a cloned git-repo registry)."""
    import os

    override = os.environ.get("RONIN_SKILL_REGISTRY")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".ronin" / "skills"


def _registry(registry: Path | str | None) -> Path:
    return Path(registry).expanduser() if registry is not None else default_registry_dir()


# --------------------------------------------------------------------------- #
# Pure helpers — manifest + the safety gate. No I/O, fully testable.          #
# --------------------------------------------------------------------------- #
def body_sha256(body: str) -> str:
    """SHA-256 of a skill body, over its stripped UTF-8 text. Stable + pure: the
    same template always hashes the same, so a bundle's integrity (and identity)
    can be checked offline without trusting the filename."""
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def summarize(body: str) -> str:
    """A one-line summary of a skill — its first non-empty line with leading
    markdown ``#`` heading and ``>`` blockquote markers stripped, clipped to 120
    chars. (A heading's text makes a perfectly good summary.)"""
    for raw in body.strip().splitlines():
        line = raw.strip().lstrip("#> ").strip()
        if line:
            return line[:120]
    return ""


def build_manifest(name: str, body: str, *, author: str | None = None) -> dict:
    """Build the manifest dict describing a skill. **Pure.**

    Keys: ``name`` (normalized to the kebab-case the runner expects),
    ``version``, ``sha256`` (of the body — stable, so identical bodies always
    produce an identical manifest hash), ``author`` (or ``None``), ``summary``
    (a one-line description), and ``manifest_version``. Raises ``ValueError`` on
    an empty body or a name that can't be normalized."""
    slug = normalize_name(name)
    if not slug:
        raise ValueError(f"invalid skill name {name!r} — use letters, digits, and dashes")
    if not body.strip():
        raise ValueError("skill body is empty")
    return {
        "name": slug,
        "version": MANIFEST_VERSION,
        "sha256": body_sha256(body),
        "author": author,
        "summary": summarize(body),
        "manifest_version": MANIFEST_VERSION,
    }


# Patterns the safety gate refuses. A skill is a *template*, so none of these
# belong in one — and each is a known way to try to turn "text the model reads"
# into "code that runs". Matching is case-insensitive over the raw body.
_FENCE_RUN = re.compile(
    r"```[ \t]*(?:bash|sh|shell|zsh|console|python|py|ruby|perl|powershell|ps1)\b",
    re.IGNORECASE,
)
# Inline ``!`` shell directive at the start of a line (Claude-Code's ! prefix /
# notebook shell escape), e.g. ``!rm -rf`` or ``! curl …``.
_SHELL_BANG = re.compile(r"(?m)^[ \t]*!\s*\S")
# Backtick-wrapped shell escape, e.g. ``!`rm -rf /``` smuggled inline.
_SHELL_BANG_INLINE = re.compile(r"!`[^`]+`")
# Prompt-injection / jailbreak phrasings: an instruction to disregard the
# surrounding rules, or to execute/eval an arbitrary payload.
_INJECTION = re.compile(
    r"ignore\s+(?:all\s+|any\s+)?(?:previous|prior|above|earlier|the\s+above)\s+"
    r"(?:instructions?|prompts?|rules?|context|messages?)"
    r"|disregard\s+(?:all\s+|the\s+)?(?:previous|prior|above|earlier)\b"
    r"|forget\s+(?:all\s+|everything\s+|your\s+)?(?:previous|prior|above)\b"
    r"|you\s+are\s+now\s+(?:in\s+)?(?:developer|jailbreak|dan|root|sudo|god)\s*mode"
    r"|disregard\s+(?:your\s+)?(?:system\s+prompt|safety|guidelines)",
    re.IGNORECASE,
)
# "run the following" / "execute this command" style auto-run lures — paired
# with an imperative to run/execute/eval a command or script.
_AUTORUN = re.compile(
    r"(?:run|execute|exec|eval|paste|pipe|curl)\b[^.\n]{0,40}?"
    r"(?:the\s+following|this|these|below)\b[^.\n]{0,40}?"
    r"(?:command|commands|code|script|shell|payload|line)"
    r"|(?:run|execute|exec)\s+(?:the\s+following|this)\b"
    r"|curl[^|\n]{0,80}\|\s*(?:bash|sh|zsh)\b"  # curl … | bash
    r"|(?:without|do\s*not|don'?t)\s+ask(?:ing)?\s+(?:for\s+)?"
    r"(?:permission|confirmation|approval)",
    re.IGNORECASE,
)
# Obvious destructive command literals — if a template *contains* one as text
# it's either a trap or a footgun; refuse and make the human paste it by hand.
_DANGER_LITERAL = re.compile(
    r"rm\s+-rf?\s+[~/]"  # rm -rf / , rm -rf ~
    r"|:\(\)\s*\{.*\};:"  # fork bomb
    r"|mkfs\.\w"  # format a filesystem
    r"|dd\s+if=.*of=/dev/"  # overwrite a device
    r"|>\s*/dev/sd[a-z]",  # clobber a disk
    re.IGNORECASE | re.DOTALL,
)


def validate_skill(body: str) -> tuple[bool, str]:
    """The SAFETY GATE. Return ``(ok, reason)``.

    Skills are prompt **templates** expanded into the model's prompt: they can
    *influence* the agent but cannot themselves run a command — and every action
    the agent subsequently proposes is still gated behind human approval. To keep
    that model honest, a publishable/installable skill must read like a template,
    not like an attempt to smuggle an executable payload or escape the sandbox.

    This rejects (case-insensitive):

    * empty bodies;
    * fenced ``bash``/``sh``/``python``/… code blocks that read as auto-run
      instructions (a template asks the agent to *figure out* the command — it
      doesn't ship one to paste);
    * ``!``-prefixed shell directives (Claude-Code's inline-shell escape);
    * prompt-injection / jailbreak phrasings ("ignore previous instructions",
      "you are now in developer mode", …);
    * "run the following command", ``curl … | bash``, "without asking for
      permission" style auto-run lures;
    * literal destructive commands (``rm -rf /``, fork bombs, ``mkfs``, …).

    A clean instructional template — "Summarize @file in 3 bullets", "Add a REST
    endpoint for $ARGUMENTS and write a test" — passes."""
    if not body or not body.strip():
        return False, "skill body is empty"
    if _FENCE_RUN.search(body):
        return (
            False,
            "contains a fenced shell/code block that reads like an auto-run payload; "
            "a skill is a prompt template — describe the steps, don't ship a script to run",
        )
    if _SHELL_BANG.search(body) or _SHELL_BANG_INLINE.search(body):
        return False, "contains a '!' shell directive; skills cannot execute commands"
    if _INJECTION.search(body):
        return False, "contains prompt-injection text (e.g. 'ignore previous instructions')"
    if _AUTORUN.search(body):
        return False, "contains an auto-run instruction (e.g. 'run the following command' / pipe-to-shell)"
    if _DANGER_LITERAL.search(body):
        return False, "contains a literal destructive command (e.g. 'rm -rf /')"
    return True, "ok"


# --------------------------------------------------------------------------- #
# Bundle model + (de)serialization.                                           #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SkillBundle:
    """A portable, self-describing skill package: its manifest + the template
    body. Serializes to a single ``<name>.skill.json`` file — no code, ever."""

    manifest: dict
    body: str

    @property
    def name(self) -> str:
        return self.manifest["name"]

    @property
    def summary(self) -> str:
        return self.manifest.get("summary", "")

    @property
    def author(self) -> str | None:
        return self.manifest.get("author")

    def to_json(self) -> str:
        return json.dumps({"manifest": self.manifest, "body": self.body},
                          indent=2, ensure_ascii=False)

    def verify_integrity(self) -> bool:
        """True iff the body matches the manifest's recorded sha256 — catches a
        bundle whose template was tampered with after it was built."""
        return body_sha256(self.body) == self.manifest.get("sha256")

    @classmethod
    def from_json(cls, text: str) -> "SkillBundle":
        data = json.loads(text)
        if not isinstance(data, dict) or "manifest" not in data or "body" not in data:
            raise ValueError("not a valid skill bundle (need 'manifest' and 'body')")
        if not isinstance(data["body"], str) or not isinstance(data["manifest"], dict):
            raise ValueError("malformed skill bundle")
        return cls(manifest=data["manifest"], body=data["body"])


def make_bundle(name: str, body: str, *, author: str | None = None) -> SkillBundle:
    """Build a :class:`SkillBundle` from a name + template body. Pure. The body
    is stored stripped so its hash is stable across trailing-whitespace churn."""
    return SkillBundle(manifest=build_manifest(name, body, author=author),
                       body=body.strip())


def load_bundle(path: Path | str) -> SkillBundle:
    """Load a ``.skill.json`` bundle from disk."""
    return SkillBundle.from_json(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# Registry I/O: publish / install / search.                                   #
# --------------------------------------------------------------------------- #
def _read_local_skill(name: str, root: Path | str) -> tuple[str, str]:
    """Return ``(slug, body)`` for a crystallized skill ``.ronin/commands/<name>.md``
    under ``root``. Raises ``FileNotFoundError`` / ``ValueError`` if it's missing
    or empty."""
    slug = normalize_name(name)
    path = skills_dir(root) / f"{slug}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no local skill '/{slug}' at {path}")
    body = path.read_text(encoding="utf-8").strip()
    if not body:
        raise ValueError(f"local skill '/{slug}' is empty")
    return slug, body


def publish(name: str, *, root: Path | str = ".", registry: Path | str | None = None,
            author: str | None = None) -> dict:
    """Package the local skill ``.ronin/commands/<name>.md`` into a portable
    bundle and write it to the registry directory.

    Validates the template through the safety gate **before** publishing — you
    can't push an unsafe skill into the registry for someone else to install.
    Returns a result dict ``{name, path, manifest, summary}``. Raises
    ``FileNotFoundError`` (no such local skill) or ``ValueError`` (empty / fails
    validation)."""
    slug, body = _read_local_skill(name, root)
    ok, reason = validate_skill(body)
    if not ok:
        raise ValueError(f"refusing to publish '/{slug}': {reason}")
    bundle = make_bundle(slug, body, author=author)
    reg = _registry(registry)
    reg.mkdir(parents=True, exist_ok=True)
    out = reg / f"{slug}{BUNDLE_SUFFIX}"
    out.write_text(bundle.to_json() + "\n", encoding="utf-8")
    return {"name": slug, "path": str(out), "manifest": bundle.manifest,
            "summary": bundle.summary}


def install(name_or_bundle: str, *, root: Path | str = ".",
            registry: Path | str | None = None) -> str:
    """Install a skill into ``<root>/.ronin/commands/`` so it's runnable as
    ``/<name>``.

    ``name_or_bundle`` is either a registry skill name (looked up in the registry
    dir) or a path to a ``.skill.json`` bundle file. The template is run through
    the safety gate **first**; if it fails, nothing is written and ``ValueError``
    is raised. On success the resolved path is returned.

    A skill can never shadow a builtin slash command (that check matches
    :func:`muscle_memory.crystallize`)."""
    bundle = _resolve_bundle(name_or_bundle, registry)
    if not bundle.verify_integrity():
        raise ValueError(f"bundle for '{bundle.name}' is corrupt (sha256 mismatch)")
    ok, reason = validate_skill(bundle.body)
    if not ok:
        raise ValueError(f"refusing to install '{bundle.name}': {reason}")

    slug = normalize_name(bundle.name)
    if not slug:
        raise ValueError(f"invalid skill name {bundle.name!r}")
    from .code_mode import SLASH_COMMANDS  # lazy: avoid an import cycle
    if slug in SLASH_COMMANDS:
        raise ValueError(f"'{slug}' is a builtin command — cannot install over it")

    d = skills_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slug}.md"
    body = bundle.body.strip()
    if "$ARGUMENTS" not in body:
        # keep parity with crystallize(): a skill always takes free-text input
        body += "\n\n$ARGUMENTS"
    path.write_text(body + "\n", encoding="utf-8")
    return str(path)


def _resolve_bundle(name_or_bundle: str, registry: Path | str | None) -> SkillBundle:
    """Resolve a name-or-path into a :class:`SkillBundle`. Tries, in order: an
    explicit ``.skill.json`` file path, then ``<registry>/<name>.skill.json``."""
    p = Path(name_or_bundle).expanduser()
    if p.is_file() and p.name.endswith(BUNDLE_SUFFIX):
        return load_bundle(p)
    if p.is_file() and p.suffix == ".json":
        return load_bundle(p)
    slug = normalize_name(name_or_bundle)
    candidate = _registry(registry) / f"{slug}{BUNDLE_SUFFIX}"
    if candidate.is_file():
        return load_bundle(candidate)
    raise FileNotFoundError(
        f"no skill '{name_or_bundle}' in registry {_registry(registry)} "
        f"(and not a .skill.json path)"
    )


def index(registry: Path | str | None = None) -> list[SkillBundle]:
    """Every skill bundle in the registry directory (sorted by name). Skips
    files that don't parse as bundles. Pure-ish (reads the registry dir only)."""
    reg = _registry(registry)
    if not reg.is_dir():
        return []
    out: list[SkillBundle] = []
    for path in sorted(reg.glob(f"*{BUNDLE_SUFFIX}")):
        try:
            out.append(load_bundle(path))
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    return sorted(out, key=lambda b: b.name)


def search(query: str, registry: Path | str | None = None) -> list[SkillBundle]:
    """Registry skills whose name, summary, or author matches ``query``
    (substring, case-insensitive). Empty query returns the whole index. Pure-ish
    over the registry directory."""
    items = index(registry)
    q = (query or "").strip().lower()
    if not q:
        return items
    return [
        b for b in items
        if q in b.name.lower()
        or q in (b.summary or "").lower()
        or q in (b.author or "").lower()
    ]


def registry_rows(registry: Path | str | None = None) -> list[tuple[str, str, str]]:
    """``(name, author, summary)`` for every registry skill, for display. Pure-ish."""
    return [(b.name, b.author or "—", b.summary) for b in index(registry)]
