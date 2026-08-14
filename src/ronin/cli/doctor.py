"""``/doctor``: what this workspace is, and what is actually wrong with it.

The rule that shapes this module: **a check that reports a problem without a remedy is
a log line.** Every :class:`Check` that is not ``ok`` carries the thing to do about it,
and :meth:`Check.__post_init__` refuses to construct one that does not — so the remedy
cannot be forgotten later, when whoever adds a check is in a hurry.

The second rule: **it reports, it does not re-derive.** ``Loaded.render()`` and
``Loaded.notes`` are already the account of what loaded and what degraded, and
computing any of that again here would let the two answers drift. What this module adds
is the set of questions only a diagnostic asks, because each of them costs something a
session start should not pay:

* does the configured model role resolve to a defined model, and is its key present;
* is the workspace a git repo, and is the shadow checkpoint repo usable (this shells
  out to ``git``, which is why it is not on the session's critical path);
* does every configured MCP server's command exist on ``PATH`` (a subprocess that does
  not exist is a server that will never answer, and the failure otherwise appears as a
  handshake timeout at the worst moment);
* is ``.ronin/`` gitignored in a way that hides the *shared* config as well as the
  secret (see :func:`gitignore_report` — this is a real misconfiguration in the wild
  and the fix is not the one people reach for first);
* did any settings layer fail to parse.

Nothing here opens a network connection. Reachability of a remote MCP server is
deliberately not probed: a diagnostic that hangs for thirty seconds is a diagnostic
people stop running.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from ..context.repomap import GitIgnore
from ..mcp.config import TransportKind
from ..providers.capability import PricingTier, capability_matrix, fallback_concerns
from ..providers.router import Role as ModelRole
from ..providers.router import Router
from ..safety.settings import LOCAL_SETTINGS
from ..verify.checkpoints import CheckpointReason, CheckpointStore
from .detect import Detection
from .spine import Loaded, Paths, Runtime

#: Paths inside ``.ronin/`` that a team is supposed to share, and that a blanket
#: ``.ronin/`` ignore rule silently hides. Checked as concrete paths rather than as
#: patterns so the real gitignore matcher decides, negations and all.
SHARED_CONFIG_PATHS: tuple[str, ...] = (
    ".ronin/settings.json",
    ".ronin/mcp.json",
    ".ronin/hooks.json",
    ".ronin/agents/reviewer.md",
    ".ronin/commands/ship.md",
)

#: The one path in ``.ronin/`` that must stay ignored: it holds plaintext API keys.
SECRET_CONFIG_PATH = LOCAL_SETTINGS.as_posix()

#: Session state that is regenerated and has no business in a diff.
GENERATED_PATHS: tuple[str, ...] = (
    ".ronin/cache/",
    ".ronin/sessions/",
    ".ronin/checkpoints/",
)

#: The replacement for a blanket ``.ronin/`` rule, and the shape this repo's own
#: ``.gitignore`` now uses. Default-deny with an explicit allowlist, because the
#: directory holds both plaintext API keys and the files a team must share; the crucial
#: character is the ``*`` in ``.ronin/*``, since git does not descend into an ignored
#: *directory* and no ``!`` negation under a blanket ``.ronin/`` can rescue anything.
GITIGNORE_PATCH = """\
# ronin workspace config. Default-deny with an explicit allowlist: this directory
# holds both plaintext API keys and the files a team is supposed to share. Note
# `.ronin/*`, not `.ronin/` — git does not descend into an ignored directory, so
# under a blanket rule no `!` negation inside it can rescue anything.
.ronin/*
!.ronin/settings.json
!.ronin/mcp.json
!.ronin/hooks.json
!.ronin/agents/
!.ronin/commands/
# ...but never the local layer, even though its parent is now walkable.
.ronin/settings.local.json
.ronin/*.local.json
"""

#: Where a ``models.toml`` is looked for, relative to the workspace root and the home
#: layer, plus the env var that overrides both. Mirrors ``cli.sdk.ROUTER_CONFIG_NAMES``
#: / ``ROUTER_CONFIG_ENV`` — duplicated rather than imported so importing this diagnostic
#: does not pull the whole SDK (and a model client) in behind it.
_MODELS_CONFIG_NAMES: tuple[str, ...] = (".ronin/models.toml", "models.toml")
_MODELS_CONFIG_ENV = "RONIN_MODELS"

#: The exact remedies the detection checks attach. Named constants because they are the
#: fix a first-run user acts on, and a diagnostic's whole worth is that the fix is right
#: — so they live in one place, are asserted against by the tests, and cannot drift into
#: a paraphrase that no longer matches the command someone has to type.
RIPGREP_REMEDY = (
    "no ripgrep found — install with `brew install ripgrep` / `apt install ripgrep`; "
    "the grep, glob and ls tools wrap it, so search is unavailable until it is on PATH"
)
NO_MODEL_REMEDY = (
    "no model configured — set ANTHROPIC_API_KEY (or another provider key) or run "
    "`ollama serve`, then copy examples/models.toml to .ronin/models.toml"
)
NO_CONFIG_REMEDY = (
    "copy examples/models.toml to .ronin/models.toml, or set RONIN_MODELS to a config "
    "path — Ronin ships no default model, so no session starts without one"
)


class CheckStatus(StrEnum):
    """Three outcomes, because "degraded" and "broken" need different exit codes."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class Check:
    """One question ``/doctor`` asked, its answer, and what to do about it."""

    name: str
    status: CheckStatus
    detail: str
    remedy: str = ""

    def __post_init__(self) -> None:
        if not self.name or not self.detail:
            raise ValueError("Check.name and Check.detail are both required")
        if self.status is not CheckStatus.OK and not self.remedy:
            raise ValueError(
                f"check {self.name!r} reports {self.status.value} with no remedy — a "
                "diagnostic that names a problem without saying what to do about it "
                "is a log line, not a diagnostic"
            )

    @property
    def failed(self) -> bool:
        return self.status is CheckStatus.FAIL

    def lines(self) -> tuple[str, ...]:
        head = f"  {self.status.value.upper():<4}  {self.name:<22} {self.detail}"
        if not self.remedy:
            return (head,)
        return (head, *(f"        → {line}" for line in self.remedy.splitlines()))


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """The whole diagnostic as a value, so a test can assert on it without parsing."""

    loaded: Loaded
    checks: tuple[Check, ...] = ()

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status is CheckStatus.FAIL)

    @property
    def warnings(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status is CheckStatus.WARN)

    @property
    def healthy(self) -> bool:
        return not self.failures

    def check(self, name: str) -> Check | None:
        for candidate in self.checks:
            if candidate.name == name:
                return candidate
        return None

    def exit_code(self) -> int:
        """``0`` when nothing is broken, ``1`` when something is.

        A warning does not set the exit code. Degradation is normal — a scratch
        directory is not a git repo, and most repos have no MCP servers — and a
        diagnostic that exits non-zero on the normal case gets ``|| true``'d into
        uselessness inside a week.
        """
        return 1 if self.failures else 0

    def summary(self) -> str:
        if self.failures:
            return (
                f"{len(self.failures)} problem(s) and {len(self.warnings)} warning(s): "
                + ", ".join(check.name for check in self.failures)
            )
        if self.warnings:
            return f"no problems, {len(self.warnings)} warning(s): " + ", ".join(
                check.name for check in self.warnings
            )
        return "everything checked out"

    def render(self) -> str:
        """The text a human reads. ``Loaded.render()`` is the body; checks follow it."""
        parts = [
            "ronin doctor",
            "",
            self.loaded.render(),
            "",
            "checks",
        ]
        for check in self.checks:
            parts.extend(check.lines())
        parts.extend(["", self.summary()])
        return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# The checks
# --------------------------------------------------------------------------- #


def _capability_check(router: Router) -> Check:
    """The provider x capability picture, and whether any fallback silently escalates cost.

    A fallback that climbs from a free model to a paid or unknown-price one is a ``warn``,
    never a failure: a user may have configured "pay to finish when the free model is down"
    on purpose. Surfacing it is the point — the swap is then a choice made here, not a
    surprise discovered on a bill. The remedy states the invariant plainly.
    """
    config = router.config
    matrix = capability_matrix(config)
    counts = dict.fromkeys(PricingTier, 0)
    for row in matrix:
        counts[row.tier] += 1
    breakdown = (
        f"{len(matrix)} model(s): {counts[PricingTier.FREE]} free, "
        f"{counts[PricingTier.PAID]} paid, {counts[PricingTier.UNKNOWN]} unknown-price"
    )
    concerns = fallback_concerns(config)
    if not concerns:
        return Check(
            name="provider pricing",
            status=CheckStatus.OK,
            detail=f"{breakdown}; no fallback escalates cost",
        )
    detail = f"{breakdown}; {len(concerns)} cost-escalating fallback(s): " + "; ".join(
        concern.message for concern in concerns
    )
    return Check(
        name="provider pricing",
        status=CheckStatus.WARN,
        detail=detail,
        remedy=(
            "reorder each role's fallback chain to stay on free/local models, or accept the "
            "cost knowingly — Ronin will not silently swap a free model for a paid one"
        ),
    )


async def run_doctor(
    loaded: Loaded,
    *,
    runtime: Runtime | None = None,
    router: Router | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    checkpoints: CheckpointStore | None = None,
    detection: Detection | None = None,
) -> DoctorReport:
    """Diagnose a loaded workspace, and a live runtime when there is one.

    ``runtime`` is optional because the whole point of splitting ``Loaded`` from
    ``Runtime`` is that ``/doctor`` works without starting a session; when one is
    supplied its router and checkpoint store are used rather than fresh ones, so the
    report describes the session that is actually running.

    ``environ`` is injected, and when it is absent the API-key half of the model check
    is skipped and says so. Reading ``os.environ`` from in here would make the answer
    depend on the shell the test ran in.

    ``detection`` is the offline probe of :func:`ronin.cli.detect.detect` — provider
    keys, reachable local servers, and ripgrep. When supplied it adds the first-run
    picture the SDK-free ``models`` check cannot give on its own: whether a config file
    resolves at all, whether *any* model is reachable, and whether search will work. It
    is a pre-built value rather than ``env``/``probe`` so this stays hermetic and so the
    caller (``main``) owns the one impure probe; omit it and the report is exactly what
    it was before, which is what keeps every existing call site unchanged.
    """
    store = checkpoints or (runtime.checkpoints if runtime is not None else None)
    store = store or CheckpointStore(loaded.paths.workspace_root)
    active_router = router or (runtime.session.router if runtime is not None else None)

    checks: list[Check] = []
    checks.extend(_settings_checks(loaded))
    checks.append(_model_check(active_router, environ))
    if active_router is not None:
        checks.append(_capability_check(active_router))
    checks.append(_git_check(loaded))
    checks.append(await _checkpoint_check(store))
    checks.extend(_mcp_checks(loaded, which))
    checks.append(_gitignore_check(loaded))
    if detection is not None:
        checks.append(_config_check(loaded.paths, environ))
        checks.append(_providers_check(detection))
        checks.append(_ripgrep_check(detection))
    checks.append(_telemetry_check(loaded))
    return DoctorReport(loaded=loaded, checks=tuple(checks))


def _resolve_config(paths: Paths, environ: Mapping[str, str] | None) -> Path | None:
    """The ``models.toml`` this workspace would use, or ``None``. Reads only ``environ``.

    Deliberately does *not* fall back to ``os.environ`` when ``environ`` is ``None`` —
    unlike ``cli.sdk.find_router_config``, whose caller wants the real environment. A
    diagnostic that read the real one would give an answer that depends on the shell it
    ran in, which is the thing this module refuses to do everywhere else.
    """
    env = environ or {}
    explicit = env.get(_MODELS_CONFIG_ENV, "")
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    for root in (paths.workspace_root, paths.home):
        for name in _MODELS_CONFIG_NAMES:
            candidate = root / name
            if candidate.is_file():
                return candidate
    return None


def _config_check(paths: Paths, environ: Mapping[str, str] | None) -> Check:
    """Is there a resolvable ``models.toml``? Ronin ships no default, so this decides.

    A warning, not a failure: the neighbouring ``providers`` check is where an absent
    model becomes a hard problem, and reporting the same thing as two failures would
    double-count one cause. This answers the narrower, file-level question — is the
    config the loader looks for actually on disk — because "no model" and "no config
    file" have different fixes and a first-run user needs to see which one they hit.
    """
    found = _resolve_config(paths, environ)
    if found is not None:
        return Check(name="config", status=CheckStatus.OK, detail=f"models config at {found}")
    return Check(
        name="config",
        status=CheckStatus.WARN,
        detail=(
            f"no models.toml found (searched {paths.workspace_root}/.ronin and {paths.home}/.ronin)"
        ),
        remedy=NO_CONFIG_REMEDY,
    )


def _providers_check(detection: Detection) -> Check:
    """Is any model reachable at all — a provider key, or a live local server?

    The one check that fails a first run outright, because with neither there is nothing
    to send a turn to and every other check is describing a session that cannot start.
    When something is reachable it lists what, so the report says *why* it is satisfied
    rather than only that it is.
    """
    if not detection.has_any_model():
        return Check(
            name="providers",
            status=CheckStatus.FAIL,
            detail="no provider API key is set and no local model server is reachable",
            remedy=NO_MODEL_REMEDY,
        )
    parts: list[str] = []
    if detection.keys:
        parts.append("provider keys: " + ", ".join(detection.keys))
    if detection.local_servers:
        parts.append("local servers: " + ", ".join(detection.local_servers))
    return Check(name="providers", status=CheckStatus.OK, detail="; ".join(parts))


def _ripgrep_check(detection: Detection) -> Check:
    """Is ripgrep on ``PATH``? The grep/glob/ls tools wrap it and cannot run without it.

    A warning rather than a failure: a session still starts, and the model can read files
    and run bash — it is search specifically that goes dark, which is degradation, the
    exact thing a warning is for.
    """
    if detection.ripgrep:
        return Check(name="ripgrep", status=CheckStatus.OK, detail="ripgrep (rg) is on PATH")
    return Check(
        name="ripgrep",
        status=CheckStatus.WARN,
        detail="ripgrep (rg) is not on PATH",
        remedy=RIPGREP_REMEDY,
    )


def _telemetry_check(loaded: Loaded) -> Check:
    """What consent state this machine is in, and where the evidence lives.

    Always reported, never a failure. Telemetry being off is the correct default and a
    diagnostic that nagged about it would be pressure dressed as advice — so ``OK``
    covers granted *and* refused, and the only thing that raises a warning is a consent
    file that could not be read, because then the user's actual choice is not being
    honoured and they cannot tell.
    """
    from ..telemetry import Consent, ConsentStore, default_consent_path, default_log_path

    home = loaded.paths.home
    record = ConsentStore(default_consent_path(home)).read()
    log_path = default_log_path(home)
    sent = 0
    if log_path.exists():
        try:
            sent = sum(
                1 for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        except OSError:
            sent = -1

    if record.problem:
        return Check(
            name="telemetry",
            status=CheckStatus.WARN,
            detail=(
                f"the consent file at {default_consent_path(home)} could not be read "
                f"({record.problem}), so nothing is sent — but your recorded choice is "
                "not the one being applied"
            ),
            remedy="`ronin telemetry off` or `ronin telemetry on` rewrites it cleanly",
        )

    if record.state is Consent.GRANTED:
        audited = "nothing logged yet" if sent == 0 else f"{sent} payload(s) logged"
        if sent < 0:
            audited = f"the log at {log_path} exists but could not be read"
        # Whether anything is actually transmitted is a separate question from consent,
        # and this build answers no: no sender is wired, so `record()` returns
        # no_sender. Reporting "on" alone would overstate what is happening.
        from ..telemetry import for_home

        wired = for_home(home).sender is not None
        state = (
            "on — aggregate task outcomes only, never prompts or code"
            if wired
            else "consent granted, but this build has no endpoint wired, so nothing is transmitted"
        )
        return Check(
            name="telemetry",
            status=CheckStatus.OK,
            detail=f"{state}. {audited}; `ronin telemetry show` prints them verbatim",
        )
    if record.state is Consent.REFUSED:
        return Check(
            name="telemetry",
            status=CheckStatus.OK,
            detail="off — you declined, and nothing is sent",
        )
    return Check(
        name="telemetry",
        status=CheckStatus.OK,
        detail=(
            "off — never asked, and off is the default. `ronin telemetry on` opts in; "
            "`ronin telemetry status` explains what it would send"
        ),
    )


def _settings_checks(loaded: Loaded) -> tuple[Check, ...]:
    """One failure per unreadable layer, plus one ok line when every layer parsed."""
    errors = loaded.settings.errors
    if not errors:
        present = [layer.name for layer in loaded.settings.layers if layer.present]
        return (
            Check(
                name="settings",
                status=CheckStatus.OK,
                detail=f"{len(loaded.settings.rules)} rule(s) from {', '.join(present)}",
            ),
        )
    return tuple(
        Check(
            name=f"settings:{error.layer}",
            status=CheckStatus.FAIL,
            detail=error.message,
            remedy=(
                f"fix {error.path if error.path is not None else 'the flags'} — until "
                "then that layer contributes nothing and any permission it granted is "
                "not in effect"
            ),
        )
        for error in errors
    )


def _model_check(router: Router | None, environ: Mapping[str, str] | None) -> Check:
    """Do the three roles resolve, and does each one have the key it names?

    A role that is not mapped falls back to ``main`` by design, which is a warning and
    not an error: a config that forgets ``fast`` should still work, and the cost — the
    main model doing search triage — is exactly what a warning is for.
    """
    if router is None:
        return Check(
            name="models",
            status=CheckStatus.WARN,
            detail="no router was supplied, so model roles were not checked",
            remedy=(
                "run /doctor from a started session, or pass the router — an "
                "unresolvable role surfaces as a 404 on the first turn otherwise"
            ),
        )
    config = router.config
    rows: list[str] = []
    unmapped: list[str] = []
    missing_keys: list[str] = []
    for role in ModelRole:
        spec = config.spec_for(role)
        rows.append(f"{role.value}={spec.name} ({spec.provider}/{spec.model})")
        if role not in config.roles:
            unmapped.append(role.value)
        if environ is not None and spec.api_key_env and not spec.api_key(environ):
            missing_keys.append(f"{spec.name} needs ${spec.api_key_env}")
    detail = "; ".join(rows)
    if environ is None:
        detail = f"{detail}; api keys not checked (no environment supplied)"
    if missing_keys:
        return Check(
            name="models",
            status=CheckStatus.FAIL,
            detail=f"{detail}; missing: {', '.join(missing_keys)}",
            remedy=(
                "export the variable, or point the role at a local model that needs no "
                "key — a request with an empty key fails as a 401 mid-turn"
            ),
        )
    if unmapped:
        return Check(
            name="models",
            status=CheckStatus.WARN,
            detail=f"{detail}; role(s) {', '.join(unmapped)} are not mapped",
            remedy=(
                "add them to [roles] in the provider config; until then they use the "
                "main model, so mechanical work is billed at the main model's price"
            ),
        )
    return Check(name="models", status=CheckStatus.OK, detail=detail)


def _git_check(loaded: Loaded) -> Check:
    root = loaded.paths.workspace_root
    if (root / ".git").exists():
        return Check(name="git", status=CheckStatus.OK, detail=f"{root} is a git repo")
    return Check(
        name="git",
        status=CheckStatus.WARN,
        detail=f"{root} is not a git repository",
        remedy=(
            "run `git init` — without it there are no checkpoints (/undo and /diff are "
            "off) and the repo map has no .gitignore to prune with"
        ),
    )


async def _checkpoint_check(store: CheckpointStore) -> Check:
    """Can the shadow repo actually be created and used?

    Shells out to ``git``. That is the reason this question belongs to ``/doctor`` and
    not to session start: the answer cannot change mid-session, and paying a subprocess
    for it on every launch is how startup gets slow.
    """
    availability = await store.ensure()
    if availability.available:
        return Check(
            name="checkpoints",
            status=CheckStatus.OK,
            detail=f"shadow repo ready at {store.git_dir}",
        )
    soft = {CheckpointReason.NOT_A_REPO, CheckpointReason.NO_GIT_BINARY}
    return Check(
        name="checkpoints",
        status=CheckStatus.WARN if availability.reason in soft else CheckStatus.FAIL,
        detail=f"unavailable ({availability.reason.value})",
        # `Availability` refuses to exist without a detail, and that detail is already
        # written as an instruction — so it is the remedy, verbatim.
        remedy=availability.detail,
    )


def _mcp_checks(loaded: Loaded, which: Callable[[str], str | None]) -> tuple[Check, ...]:
    """One check per configured server: does the thing we would launch exist?

    Only the stdio transports can be answered offline, and they are the ones that fail
    silently: a missing binary is indistinguishable from a slow server until the
    handshake times out. Remote servers are reported as unprobed rather than probed,
    because a diagnostic must not make a network call.
    """
    if not loaded.mcp_servers:
        return ()
    checks: list[Check] = []
    for config in loaded.mcp_servers:
        name = f"mcp:{config.name}"
        if not config.enabled:
            checks.append(Check(name=name, status=CheckStatus.OK, detail="disabled in mcp.json"))
            continue
        if config.transport is not TransportKind.STDIO:
            checks.append(
                Check(
                    name=name,
                    status=CheckStatus.OK,
                    detail=f"{config.transport.value} {config.url} (not contacted)",
                )
            )
            continue
        found = which(config.command)
        if found is None:
            checks.append(
                Check(
                    name=name,
                    status=CheckStatus.FAIL,
                    detail=f"command {config.command!r} is not on PATH",
                    remedy=(
                        f"install it, or correct the 'command' for server "
                        f"{config.name!r} in {loaded.paths.mcp_config} — as configured "
                        "the server can never start and its tools will not exist"
                    ),
                )
            )
            continue
        checks.append(Check(name=name, status=CheckStatus.OK, detail=f"stdio {found}"))
    return tuple(checks)


@dataclass(frozen=True, slots=True)
class GitignoreFinding:
    """What ``.gitignore`` does to ``.ronin/``, as data.

    Both halves matter and they pull in opposite directions, which is why one blanket
    rule cannot satisfy them: ``settings.local.json`` holds plaintext API keys and must
    be ignored, while ``settings.json``, ``mcp.json``, ``agents/*.md`` and
    ``commands/*.md`` are the config a team shares and must not be.
    """

    hidden_shared: tuple[str, ...] = ()
    secret_tracked: bool = False
    generated_tracked: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not self.hidden_shared and not self.secret_tracked


def hidden_by(ignore: GitIgnore, path: str, *, is_dir: bool = False) -> bool:
    """Whether git would ignore ``path``, counting a **pruned ancestor directory**.

    ``GitIgnore.ignores`` answers about one path against the rules that match it, which
    is all the repo-map walker needs — it prunes directories as it descends, so it never
    asks about a file beneath one. A diagnostic does have to ask, and the ancestor walk
    is the whole finding: a blanket ``.ronin/`` rule excludes the *directory*, and git
    will not re-include anything under an excluded directory, so ``settings.json`` is
    hidden and ``!.ronin/settings.json`` cannot bring it back.
    """
    parts = [part for part in path.strip("/").split("/") if part]
    for depth in range(1, len(parts)):
        if ignore.ignores("/".join(parts[:depth]), is_dir=True):
            return True
    return ignore.ignores("/".join(parts), is_dir=is_dir)


def gitignore_report(text: str) -> GitignoreFinding:
    """Read a ``.gitignore`` body and say what it does to ``.ronin/``.

    Uses the repo map's real gitignore matcher rather than looking for the string
    ``.ronin/``, so the answer is what git would do rather than what the file looks
    like — including the case where the author tried to negate their way out.
    """
    ignore = GitIgnore.parse(text)
    hidden = tuple(path for path in SHARED_CONFIG_PATHS if hidden_by(ignore, path))
    generated = tuple(path for path in GENERATED_PATHS if not hidden_by(ignore, path, is_dir=True))
    return GitignoreFinding(
        hidden_shared=hidden,
        secret_tracked=not hidden_by(ignore, SECRET_CONFIG_PATH),
        generated_tracked=generated,
    )


def _gitignore_check(loaded: Loaded) -> Check:
    path = loaded.paths.workspace_root / ".gitignore"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Check(
            name="gitignore",
            status=CheckStatus.WARN,
            detail=f"{path} does not exist, so .ronin/settings.local.json is tracked",
            remedy=("settings.local.json holds plaintext API keys. add:\n" + GITIGNORE_PATCH),
        )
    except OSError as exc:
        return Check(
            name="gitignore",
            status=CheckStatus.WARN,
            detail=f"{path} could not be read ({exc})",
            remedy=f"make it readable and check that it contains:\n{GITIGNORE_PATCH}",
        )
    finding = gitignore_report(text)
    if finding.clean:
        return Check(
            name="gitignore",
            status=CheckStatus.OK,
            detail="the local layer is ignored and the shared config is not",
        )
    problems: list[str] = []
    if finding.secret_tracked:
        problems.append(f"{SECRET_CONFIG_PATH} is NOT ignored (it holds API keys)")
    if finding.hidden_shared:
        problems.append(
            "these are ignored but are meant to be committed: " + ", ".join(finding.hidden_shared)
        )
    return Check(
        name="gitignore",
        status=CheckStatus.WARN,
        detail="; ".join(problems),
        remedy=(f"replace any blanket `.ronin/` rule in {path} with:\n{GITIGNORE_PATCH}"),
    )


def gitignore_patch(root: Path) -> str:
    """The exact patch to apply, addressed to ``<root>/.gitignore``."""
    return f"# --- {root / '.gitignore'} ---\n{GITIGNORE_PATCH}"


__all__ = [
    "GENERATED_PATHS",
    "GITIGNORE_PATCH",
    "NO_CONFIG_REMEDY",
    "NO_MODEL_REMEDY",
    "RIPGREP_REMEDY",
    "SECRET_CONFIG_PATH",
    "SHARED_CONFIG_PATHS",
    "Check",
    "CheckStatus",
    "DoctorReport",
    "GitignoreFinding",
    "gitignore_patch",
    "gitignore_report",
    "hidden_by",
    "run_doctor",
]
