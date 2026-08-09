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
from ..providers.router import Role as ModelRole
from ..providers.router import Router
from ..safety.settings import LOCAL_SETTINGS
from ..verify.checkpoints import CheckpointReason, CheckpointStore
from .spine import Loaded, Runtime

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

#: The replacement for a blanket ``.ronin/`` rule. Enumerating what to ignore is the
#: *only* correct shape: git cannot re-include a file whose parent directory is
#: excluded, so the obvious fix — keep ``.ronin/`` and add ``!.ronin/settings.json`` —
#: does not work, and looks like it does.
GITIGNORE_PATCH = """\
# ronin — ignore the local layer (plaintext API keys) and generated state, and
# COMMIT the shared config. Note: a blanket `.ronin/` rule cannot be undone with
# `!.ronin/settings.json` — git will not re-include a file whose parent directory
# is excluded — so the entries have to be enumerated.
.ronin/settings.local.json
.ronin/cache/
.ronin/sessions/
.ronin/checkpoints/
"""


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
            return (
                f"no problems, {len(self.warnings)} warning(s): "
                + ", ".join(check.name for check in self.warnings)
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


async def run_doctor(
    loaded: Loaded,
    *,
    runtime: Runtime | None = None,
    router: Router | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    checkpoints: CheckpointStore | None = None,
) -> DoctorReport:
    """Diagnose a loaded workspace, and a live runtime when there is one.

    ``runtime`` is optional because the whole point of splitting ``Loaded`` from
    ``Runtime`` is that ``/doctor`` works without starting a session; when one is
    supplied its router and checkpoint store are used rather than fresh ones, so the
    report describes the session that is actually running.

    ``environ`` is injected, and when it is absent the API-key half of the model check
    is skipped and says so. Reading ``os.environ`` from in here would make the answer
    depend on the shell the test ran in.
    """
    store = checkpoints or (runtime.checkpoints if runtime is not None else None)
    store = store or CheckpointStore(loaded.paths.workspace_root)
    active_router = router or (runtime.session.router if runtime is not None else None)

    checks: list[Check] = []
    checks.extend(_settings_checks(loaded))
    checks.append(_model_check(active_router, environ))
    checks.append(_git_check(loaded))
    checks.append(await _checkpoint_check(store))
    checks.extend(_mcp_checks(loaded, which))
    checks.append(_gitignore_check(loaded))
    return DoctorReport(loaded=loaded, checks=tuple(checks))


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


def _mcp_checks(
    loaded: Loaded, which: Callable[[str], str | None]
) -> tuple[Check, ...]:
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
            checks.append(
                Check(name=name, status=CheckStatus.OK, detail="disabled in mcp.json")
            )
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
    return checks


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
    generated = tuple(
        path
        for path in GENERATED_PATHS
        if not hidden_by(ignore, path, is_dir=True)
    )
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
            remedy=(
                "settings.local.json holds plaintext API keys. add:\n" + GITIGNORE_PATCH
            ),
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
            "these are ignored but are meant to be committed: "
            + ", ".join(finding.hidden_shared)
        )
    return Check(
        name="gitignore",
        status=CheckStatus.WARN,
        detail="; ".join(problems),
        remedy=(
            f"replace any blanket `.ronin/` rule in {path} with:\n{GITIGNORE_PATCH}"
        ),
    )


def gitignore_patch(root: Path) -> str:
    """The exact patch to apply, addressed to ``<root>/.gitignore``."""
    return f"# --- {root / '.gitignore'} ---\n{GITIGNORE_PATCH}"


__all__ = [
    "GENERATED_PATHS",
    "GITIGNORE_PATCH",
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
