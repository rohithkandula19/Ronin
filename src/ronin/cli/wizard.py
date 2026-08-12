"""First run: what would be created, then create it. Two functions, no prompting.

The split is the design. :func:`plan_first_run` inspects a workspace and returns a
:class:`WizardPlan` — every file it would write, its exact content, and *why* — and
:func:`apply_plan` writes it. Neither reads stdin, prints, or asks anything: the
question ("shall I?") belongs to the UI, and keeping it out of here is what makes the
wizard testable at all. A wizard that mixes the decision into the writing can only be
tested by driving a terminal.

Three rules the plan obeys:

1. **Nothing is overwritten.** An existing ``RONIN.md`` is somebody's standing
   instructions, and a first-run wizard that clobbers it is data loss with a friendly
   banner. A file that exists is reported as present and skipped.
2. **Content comes from evidence.** The ``RONIN.md`` draft is
   :func:`ronin.context.memory.propose_bootstrap`, which reads ``package.json``,
   ``pyproject.toml``, ``Makefile``, ``justfile``, ``Cargo.toml`` and the CI workflows,
   labels every command with the file it came from, and reports an absent test command
   as absent. A drafted-but-wrong ``make test`` is worse than a blank section.
3. **``~/.ronin`` only on request.** ``Paths.ensure()`` deliberately does not create
   the home layer, and neither does this unless ``include_user_layer`` is set: a first
   run that silently materialises a config directory in somebody's home is a surprise.

What the wizard deliberately does **not** do is edit ``.gitignore``. Adding lines to a
file the wizard did not write, in a repo whose history it does not own, is a different
kind of act from creating ``.ronin/settings.json`` — so the plan carries the exact
lines as a note, and ``ronin.cli.doctor`` reports the same thing as a check with the
patch attached. The user applies it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..context.memory import RONIN_FILENAME, discover_commands, propose_bootstrap
from ..safety.settings import RULES_KEY
from .detect import Detection
from .doctor import GITIGNORE_PATCH
from .spine import RONIN_DIRNAME, Paths

#: The committed project layer, as created. Every key is the builtin default, so the
#: file changes nothing on its own — it exists to be the place a team edits, and an
#: empty rule list is the shape a rule goes into.
STARTER_PROJECT_SETTINGS: str = (
    json.dumps({"mode": "ask", RULES_KEY: []}, indent=2, sort_keys=True) + "\n"
)


@dataclass(frozen=True, slots=True)
class PlannedFile:
    """One file the wizard would create, with its content and its justification."""

    path: Path
    content: str
    reason: str
    exists: bool = False

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError(f"planned file {self.path} has no content")
        if not self.reason:
            raise ValueError(
                f"planned file {self.path} has no reason — the user is being asked to "
                "accept a file, and 'why' is the only thing that makes that answerable"
            )

    @property
    def will_write(self) -> bool:
        """False for a file that already exists. The wizard never overwrites."""
        return not self.exists

    def line(self) -> str:
        mark = "skip " if self.exists else "write"
        tail = " (already exists)" if self.exists else ""
        return f"  {mark} {self.path}{tail}\n        {self.reason}"


@dataclass(frozen=True, slots=True)
class WizardPlan:
    """Exactly what a first run would do, as data a UI can print and a test can assert."""

    paths: Paths
    files: tuple[PlannedFile, ...] = ()
    directories: tuple[Path, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def writes(self) -> tuple[PlannedFile, ...]:
        return tuple(planned for planned in self.files if planned.will_write)

    @property
    def empty(self) -> bool:
        """Nothing to do: every file exists and every directory is present."""
        return not self.writes and not self.missing_directories

    @property
    def missing_directories(self) -> tuple[Path, ...]:
        return tuple(directory for directory in self.directories if not directory.is_dir())

    def render(self) -> str:
        parts = [f"first-run plan for {self.paths.workspace_root}", ""]
        if self.missing_directories:
            parts.append("directories")
            parts.extend(f"  create {directory}" for directory in self.missing_directories)
            parts.append("")
        parts.append("files")
        parts.extend(planned.line() for planned in self.files)
        if self.notes:
            parts.extend(["", "notes", *(f"  {note}" for note in self.notes)])
        if self.empty:
            parts.extend(["", "nothing to do — this workspace is already set up"])
        return "\n".join(parts) + "\n"


def plan_first_run(
    paths: Paths,
    *,
    include_user_layer: bool = False,
    memory: bool = True,
    project_settings: bool = True,
) -> WizardPlan:
    """Inspect ``paths`` and describe the first run. Touches nothing.

    ``memory`` and ``project_settings`` are separable because they are separable
    decisions: plenty of users want the drafted ``RONIN.md`` and no committed settings
    file, and the reverse happens in a repo that already has instructions.
    """
    root = paths.workspace_root
    directories: list[Path] = [paths.ronin_dir, paths.sessions_dir, paths.cache_dir]
    if include_user_layer:
        # `Paths.ensure()` will not create this, on purpose. Only an explicit request
        # puts a directory in somebody's home.
        directories.append(paths.home / ".ronin")

    files: list[PlannedFile] = []
    notes: list[str] = []

    if memory:
        target = root / RONIN_FILENAME
        found = discover_commands(root)
        detail = (
            f"drafted from {len(found)} command(s) found in this repo, each labelled "
            "with the file it came from"
            if found
            else (
                "no build/test/lint command could be found in this repo, so the draft "
                "says so per section rather than guessing one"
            )
        )
        files.append(
            PlannedFile(
                path=target,
                content=propose_bootstrap(root),
                reason=(f"the standing instructions the model reads on every turn — {detail}"),
                exists=target.exists(),
            )
        )

    if project_settings:
        target = paths.project_settings
        files.append(
            PlannedFile(
                path=target,
                content=STARTER_PROJECT_SETTINGS,
                reason=(
                    "the committed permission layer, shared with the team; every rule "
                    "here is visible in /doctor and attributable to this file"
                ),
                exists=target.exists(),
            )
        )

    notes.append(
        "this wizard does not edit .gitignore. add these lines yourself so the local "
        "layer stays out of git and the shared config stays in it:\n"
        + "\n".join(f"    {line}" for line in GITIGNORE_PATCH.splitlines())
    )
    if not include_user_layer:
        notes.append(
            f"{paths.home / '.ronin'} was not planned: the cross-project user layer is "
            "opt-in. re-run with the user layer enabled to create it"
        )

    return WizardPlan(
        paths=paths,
        files=tuple(files),
        directories=tuple(directories),
        notes=tuple(notes),
    )


def apply_plan(plan: WizardPlan) -> tuple[Path, ...]:
    """Create the plan's directories and write the files that do not exist yet.

    Returns the paths actually written, sorted, so a caller can report them without
    re-deriving which ones were skipped. Directories are created first: a planned file
    inside ``.ronin/`` has nowhere to go otherwise.

    Existing files are left alone even if their content differs from the plan — see
    rule 1 in this module's docstring. Re-running the wizard on a set-up workspace is
    therefore a no-op and returns ``()``.
    """
    for directory in plan.directories:
        directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for planned in plan.files:
        if not planned.will_write or planned.path.exists():
            continue
        planned.path.parent.mkdir(parents=True, exist_ok=True)
        # Explicit "\n" newline: these files are read back as bytes by tests and, more
        # to the point, a RONIN.md with CRLF endings renders a stray \r into the model's
        # prompt on every turn.
        planned.path.write_text(planned.content, encoding="utf-8", newline="\n")
        written.append(planned.path)
    return tuple(sorted(written))


# --------------------------------------------------------------------------- #
# Detection-driven model config
# --------------------------------------------------------------------------- #

#: The router config file the wizard writes, and the name ``load_router`` looks for
#: first (``.ronin/models.toml``). Kept as a bare name here so this module owns the
#: one place it is spelled, rather than importing it from ``cli.sdk`` and pulling the
#: whole SDK into the light first-run path.
MODELS_CONFIG_FILENAME = "models.toml"


@dataclass(frozen=True, slots=True)
class _ProviderStanza:
    """How to write a working ``[models.*]`` block for a detected provider key.

    ``provider`` must be a value ``ronin.providers.registry.ADAPTERS`` resolves —
    ``gemini`` and ``cerebras`` have no first-class adapter but speak the OpenAI wire
    protocol, so they are written as ``openai-compatible`` with an explicit ``base_url``.
    """

    provider: str
    model: str
    api_key_env: str
    base_url: str = ""


#: One stanza per provider :data:`ronin.cli.detect.PROVIDER_KEYS` can detect. The model
#: id is a current, sensible default and nothing more — the generated file names it on
#: its own line precisely so a first-run user changes it in one edit. No price is
#: written: Ronin invents none, and the ledger records an unpriced model as *unpriced*,
#: not as free, so an omitted price is honest where a guessed one would be a wrong
#: number in someone's cost report.
_PROVIDER_STANZAS: dict[str, _ProviderStanza] = {
    "anthropic": _ProviderStanza("anthropic", "claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
    "openai": _ProviderStanza("openai", "gpt-4o", "OPENAI_API_KEY"),
    "gemini": _ProviderStanza(
        "openai-compatible",
        "gemini-2.0-flash",
        "GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
    ),
    "groq": _ProviderStanza("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "cerebras": _ProviderStanza(
        "openai-compatible",
        "llama-3.3-70b",
        "CEREBRAS_API_KEY",
        base_url="https://api.cerebras.ai/v1",
    ),
    "openrouter": _ProviderStanza(
        "openrouter", "anthropic/claude-sonnet-4.5", "OPENROUTER_API_KEY"
    ),
}

#: The default local-server model, per server name. Every provider here is a first-class
#: adapter whose base URL comes from ``KNOWN_BASE_URLS``, so no ``base_url`` is needed.
#: ``native_tools = false`` routes the model through the tool-call shim automatically —
#: local models rarely have native tool calling, and the example config makes the same
#: choice for its ollama entry.
_LOCAL_STANZAS: dict[str, tuple[str, str]] = {
    "ollama": ("ollama", "qwen2.5-coder:7b"),
    "lmstudio": ("lmstudio", "qwen2.5-coder-7b-instruct"),
    "vllm": ("vllm", "Qwen/Qwen2.5-Coder-7B-Instruct"),
}


def _render_config(*, header: Sequence[str], model_name: str, model_lines: Sequence[str]) -> str:
    """Assemble a models.toml pointing all three roles at one model.

    All three because a first run has exactly one thing that works, and pointing ``plan``
    and ``fast`` at it too is what makes ``/doctor`` report a mapped config rather than
    warning that mechanical work will be billed at the main model's price. It is also the
    shape ``examples/models.toml`` calls the "fully-free configuration".
    """
    lines = [f"# {line}" for line in header]
    lines += [
        "",
        "[roles]",
        f'main = "{model_name}"',
        f'plan = "{model_name}"',
        f'fast = "{model_name}"',
        "",
        f"[models.{model_name}]",
        *model_lines,
        "",
    ]
    return "\n".join(lines)


def plan_config(detection: Detection) -> str | None:
    """Propose a ``models.toml`` body that works with what :func:`detect` found.

    The rule, from the "clean container with only ollama" case up: a provider whose key
    is set is preferred when there is one — a hosted key usually names a stronger model
    than a laptop runs — and a reachable local server is used when there is no key, so a
    container with nothing but ``ollama serve`` still gets a config that runs. Returns
    ``None`` when there is neither: a first run with no model at all cannot be configured
    into having one, and inventing a provider would write a file that 401s on turn one.

    Pure text. Nothing here opens a socket or writes a file — :func:`write_config` does
    the writing, and only when a caller asks.
    """
    for name in detection.keys:
        stanza = _PROVIDER_STANZAS.get(name)
        if stanza is None:
            continue
        model_lines = [
            f'provider = "{stanza.provider}"',
            f'model = "{stanza.model}"',
            f'api_key_env = "{stanza.api_key_env}"',
        ]
        if stanza.base_url:
            model_lines.append(f'base_url = "{stanza.base_url}"')
        return _render_config(
            header=[
                f"Generated by `ronin` first-run setup: ${stanza.api_key_env} is set.",
                "Check the model line names a model you can access, and add price_in /",
                "price_out if you want this model in the cost ledger — Ronin invents none.",
            ],
            model_name=name,
            model_lines=model_lines,
        )

    for name in detection.local_servers:
        local = _LOCAL_STANZAS.get(name)
        if local is None:
            continue
        provider, model = local
        return _render_config(
            header=[
                f"Generated by `ronin` first-run setup: {name} is reachable locally.",
                f"Change the model line to one you have pulled (e.g. `{name} list`).",
                "A local server needs no API key, so none is named — that is correct.",
            ],
            model_name=name,
            model_lines=[
                f'provider = "{provider}"',
                f'model = "{model}"',
                "native_tools = false",
                "max_context = 32768",
            ],
        )

    return None


def write_config(paths: Paths, body: str, *, user_layer: bool = False) -> Path | None:
    """Write a ``models.toml`` ``body`` and return its path, or ``None`` if one exists.

    Never overwrites, for the same reason the rest of the wizard does not: an existing
    ``models.toml`` is the user's chosen models, and clobbering it to point at a
    freshly-detected ollama is data loss with a friendly banner. A present file is left
    untouched and ``None`` is returned so the caller reports "kept" rather than "wrote".

    Writes the project layer (``<workspace>/.ronin/models.toml``) by default and the
    user layer (``~/.ronin/models.toml``) when ``user_layer`` is set — the cross-project
    home layer, opt-in like everything else the wizard puts in a home directory.
    """
    root = (paths.home / RONIN_DIRNAME) if user_layer else paths.ronin_dir
    target = root / MODELS_CONFIG_FILENAME
    if target.exists():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    # LF endings, like every other file the wizard writes: a config read back on a
    # machine that translated newlines is a needless diff, and tests read it as bytes.
    target.write_text(body, encoding="utf-8", newline="\n")
    return target


# --------------------------------------------------------------------------- #
# The 10-second smoke task
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SmokeResult:
    """Whether the first-run smoke task ran, whether it passed, and why.

    ``ran`` is false only when no smoke was wired: a smoke that was never supplied did
    not *fail*, and reporting it as a failure would tell a first-run user their model is
    broken when the truth is nobody asked it a question.
    """

    ran: bool
    ok: bool
    detail: str

    def line(self) -> str:
        if not self.ran:
            return f"smoke: skipped — {self.detail}"
        return f"smoke: {'passed' if self.ok else 'FAILED'} — {self.detail}"


async def run_smoke(
    smoke: Callable[[], Awaitable[bool]] | None,
    *,
    timeout: float = 10.0,
) -> SmokeResult:
    """Run the injected 10-second smoke task and report pass/fail. Never raises.

    The seam is a callable so the wizard has no opinion about *what* the smoke does — the
    real caller (``main`` on the first-run path) passes one that opens the freshly-written
    config and asks the model a trivial scripted question; a test passes one that returns
    ``True``. Either way this owns only the timing and the reporting, and turns any outcome
    — a false, a timeout, or a raised exception — into a :class:`SmokeResult`, because a
    setup step that crashes the wizard is worse than one that says "could not verify".

    ``smoke`` is ``None`` when no task was wired; the result is *skipped*, not failed.
    """
    if smoke is None:
        return SmokeResult(ran=False, ok=False, detail="no smoke task was supplied")
    try:
        ok = await asyncio.wait_for(smoke(), timeout)
    except TimeoutError:
        return SmokeResult(
            ran=True, ok=False, detail=f"the smoke task did not finish within {timeout:g}s"
        )
    # Deliberately broad: the smoke is caller-supplied and may reach a real provider, so
    # any failure it raises is a failed smoke to report, not a traceback out of setup.
    except Exception as exc:
        return SmokeResult(ran=True, ok=False, detail=f"the smoke task raised {exc!r}")
    return SmokeResult(
        ran=True,
        ok=ok,
        detail=(
            "the configured model completed a task" if ok else "the smoke task reported failure"
        ),
    )


__all__ = [
    "MODELS_CONFIG_FILENAME",
    "STARTER_PROJECT_SETTINGS",
    "PlannedFile",
    "SmokeResult",
    "WizardPlan",
    "apply_plan",
    "plan_config",
    "plan_first_run",
    "run_smoke",
    "write_config",
]
