"""``ronin retain`` — the Retainer's front door.

Every piece of the Retainer plane existed and none of it was reachable. The
registry parser had careful, positional error messages (``registry.retainers[2]
.orders.grants[1]``) that nothing could invoke; the receiver could be bound and
nothing bound it; ``run_summons`` could act on a summons and nothing produced one
from a webhook. An operator's only way to find out their ``retainers.json`` was
wrong was to run a daemon that did not exist. This is the missing edge: three
verbs, no new subsystem.

``check``  read the registry and report what it holds. No sockets, no git, no
           model. This is the one that makes the parser's messages reachable.
``serve``  bind the receiver and act on deliveries until interrupted.
``tick``   fire every routine that is due, once, and exit — for cron, or for a
           deployment that would rather not hold a process open.

Routing, which is the only decision here worth arguing about
------------------------------------------------------------
A delivery names a channel and a conversation; it does not name a Retainer. The
registry has no per-Retainer handle to match against and **this module does not
add one** — inventing a schema field to make one command shorter is the wrong
order, and the registry docstring is explicit that the file is the schema.

So routing uses what the records already carry:

* **GitHub** routes on the repository. A mention arrives with ``owner/name`` and
  each Retainer's ``post.repo`` is exactly that, so a deployment serving several
  Retainers across several repositories needs nothing configured.
* **Slack and Telegram** carry no repository, so they route to the single
  Retainer reachable on that channel. Two of them is refused at startup, with the
  fix named — ``--retainer`` — rather than resolved by picking one.

``--retainer`` overrides all of it and serves exactly that one.

What this module does not do
-----------------------------
It opens no socket itself and runs no model itself: the receiver, the poster and
the agent are all injected, defaulting to the real ones. That is what lets a test
drive a signed delivery end to end — webhook bytes in, posted reply out — with no
network, which is what this repository requires of every test.

It also does not read a token. Credentials come from the environment at post
time, in :mod:`ronin.retainer.adapters.outbound`, and the registry never holds
one. The gap named there is named here too: until an egress credential proxy
exists, a compromised post is a compromised token.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from ronin.retainer.adapters import github, slack, telegram
from ronin.retainer.adapters.outbound import http_poster
from ronin.retainer.ask import EscalationStore
from ronin.retainer.ledger import EffectLedger
from ronin.retainer.model import Channel, Retainer, Summons
from ronin.retainer.posts import PostStore
from ronin.retainer.registry import Registry, RegistryError, load_registry, registry_path
from ronin.retainer.routines import Routine, RoutineError, RoutineStore, fire
from ronin.retainer.threads import ThreadMap

from .retain import (
    GITHUB_SCHEME,
    SLACK_SCHEME,
    TELEGRAM_SCHEME,
    Delivery,
    ReceiverConfig,
    SigningScheme,
    header,
    serve_retainer,
)
from .retainer_run import Outcome, RunRefused, Stores, run_summons

#: The verbs, in help order.
SUBCOMMANDS: Final[tuple[str, ...]] = ("check", "serve", "tick")

#: Which signing scheme each channel's deliveries carry. The receiver is bound to
#: one channel because it is bound to one scheme: a single endpoint that accepted
#: three signature formats would have to decide which to demand from an unsigned
#: request, and the honest answer to that is a second port.
SCHEME_FOR: Final[Mapping[Channel, SigningScheme]] = {
    Channel.GITHUB: GITHUB_SCHEME,
    Channel.SLACK: SLACK_SCHEME,
    Channel.TELEGRAM: TELEGRAM_SCHEME,
}

#: The shared secret the receiver verifies against — GitHub's webhook secret,
#: Slack's signing secret, Telegram's ``X-Telegram-Bot-Api-Secret-Token``.
#: Refused when unset rather than defaulted: "unconfigured" meaning "unsigned is
#: fine" is how a webhook endpoint gets found by somebody else first.
SECRET_ENV: Final = "RONIN_RETAINER_SECRET"

#: What the Retainer is called on each channel, so a delivery can be recognised as
#: addressed to it — and, just as importantly, so its *own* posts do not wake it.
#:
#: Three variables rather than one because the three platforms mean different
#: things by "what it is called". GitHub wants the login a comment writes as
#: ``@name``; Telegram wants the bot's ``@username``; Slack wants the bot's **user
#: id** (``U024BE7LH``), because a Slack mention is a link — ``<@U024BE7LH>`` — and
#: the display name never appears in the message text at all. One shared variable
#: would work right up until a deployment's GitHub login and Telegram handle
#: differed, and then it would silently address the wrong one.
HANDLE_ENV: Final[Mapping[Channel, str]] = {
    Channel.GITHUB: "RONIN_RETAINER_HANDLE",
    Channel.SLACK: "SLACK_BOT_USER",
    Channel.TELEGRAM: "TELEGRAM_BOT_HANDLE",
}

#: Where the stores live, beside the registry. One directory so an operator
#: backing up a deployment has one thing to copy.
STATE_DIRNAME: Final = "retainer"


class RetainRefused(RuntimeError):
    """The verb could not run at all. Carries a reason worth printing."""


@dataclass(frozen=True, slots=True)
class RetainOptions:
    """A parsed ``ronin retain``. Pure data; every path is resolved in dispatch."""

    subcommand: str
    home: Path
    registry: Path | None = None
    channel: Channel = Channel.GITHUB
    retainer: str = ""
    host: str = "127.0.0.1"
    port: int = 8787
    as_json: bool = False


# --------------------------------------------------------------------------- #
# check — the verb that makes the registry's own error messages reachable
# --------------------------------------------------------------------------- #


def _describe(registry: Registry) -> str:
    lines = [
        f"deployment: {registry.deployment.name}"
        f"{' (hosted)' if registry.deployment.hosted else ''}",
        f"capabilities: {_names_of(registry.deployment.capabilities)}",
        f"identity: {registry.identity.name} <{registry.identity.email}>",
        f"posts: {registry.posts_root}",
        "",
        f"{len(registry.retainers)} retainer(s):",
    ]
    for name in registry.names:
        retainer = registry.retainers[name]
        orders = retainer.orders
        granted = orders.granted(registry.deployment)
        denied = orders.denied(registry.deployment)
        lines += [
            f"  {retainer.id}  {retainer.name}",
            f"    repo:     {retainer.post.repo}"
            f"{'@' + retainer.post.branch if retainer.post.branch else ''}",
            f"    channels: {_names_of(retainer.channels)}",
            f"    holds:    {_names_of(granted)}",
            f"    grants:   {len(orders.grants)} rule(s), default {orders.default.value}",
            f"    budget:   {orders.budgets.iterations} iteration(s)",
        ]
        if denied:
            # The line an operator most needs and would never think to ask for.
            # `granted` intersects rather than validates — deliberately, so moving
            # a Retainer to a hosted deployment narrows it instead of refusing to
            # start — which means a capability it asked for and cannot have is
            # dropped in silence unless something says so here.
            lines.append(
                f"    NOT held: {_names_of(denied)} — asked for, and "
                f"{registry.deployment.name} does not grant it"
            )
    return "\n".join(lines) + "\n"


def _names_of(values: frozenset[Any]) -> str:
    return ", ".join(sorted(str(value.value) for value in values)) or "—"


def _as_json(registry: Registry) -> str:
    payload: dict[str, Any] = {
        "deployment": {
            "name": registry.deployment.name,
            "hosted": registry.deployment.hosted,
            "capabilities": sorted(c.value for c in registry.deployment.capabilities),
        },
        "identity": {"name": registry.identity.name, "email": registry.identity.email},
        "posts_root": str(registry.posts_root),
        "retainers": [
            {
                "id": retainer.id,
                "name": retainer.name,
                "repo": retainer.post.repo,
                "branch": retainer.post.branch,
                "workspace": str(retainer.post.workspace),
                "channels": sorted(c.value for c in retainer.channels),
                "wants": sorted(c.value for c in retainer.orders.wants),
                "holds": sorted(c.value for c in retainer.orders.granted(registry.deployment)),
                "not_held": sorted(c.value for c in retainer.orders.denied(registry.deployment)),
                "default": retainer.orders.default.value,
                "grants": len(retainer.orders.grants),
                "iterations": retainer.orders.budgets.iterations,
            }
            for retainer in (registry.retainers[name] for name in registry.names)
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


# --------------------------------------------------------------------------- #
# routing — what turns a delivery into a summons
# --------------------------------------------------------------------------- #


def servable(registry: Registry, channel: Channel, retainer: str) -> tuple[Retainer, ...]:
    """The Retainers this receiver may wake, or a refusal naming the fix.

    Refusing at *bind* time and not at delivery time is the point: a deployment
    that cannot route is misconfigured, and finding that out when the first
    webhook arrives means finding it out from a 500 in somebody else's dashboard.
    """
    if retainer:
        found = registry.retainers.get(retainer)
        if found is None:
            raise RetainRefused(
                f"no retainer named {retainer!r} in the registry; it holds "
                f"{', '.join(registry.names) or 'none'}"
            )
        if not found.reachable_on(channel):
            raise RetainRefused(
                f"{found.id} is not reachable on {channel.value} — add the channel to "
                "its record rather than working around it here"
            )
        return (found,)

    reachable = tuple(
        registry.retainers[name]
        for name in registry.names
        if registry.retainers[name].reachable_on(channel)
    )
    if not reachable:
        raise RetainRefused(f"no retainer in this registry is reachable on {channel.value}")
    if channel is not Channel.GITHUB and len(reachable) > 1:
        # GitHub routes on the repository a mention arrived from; the other two
        # carry nothing to route on. Picking the first would mean a Slack message
        # waking whichever Retainer sorts earliest, which is not a routing rule,
        # it is an accident that looks like one.
        raise RetainRefused(
            f"{len(reachable)} retainers are reachable on {channel.value} and a "
            f"{channel.value} delivery names no repository to route on. Name one "
            "with --retainer, or serve them on separate ports."
        )
    return reachable


def _for_repo(reachable: Sequence[Retainer], repo: str) -> Retainer | None:
    matched = [retainer for retainer in reachable if retainer.post.repo == repo]
    return matched[0] if len(matched) == 1 else None


def summons_from(
    delivery: Delivery,
    *,
    channel: Channel,
    reachable: Sequence[Retainer],
    handle: str,
) -> Summons | None:
    """One delivery to one summons, or ``None`` when it was not for us.

    ``None`` is the answer for every uninteresting delivery — the wrong event, an
    unaddressed message, our own post — because a caller that has to tell those
    apart is a caller that forgets one, and each of them is answered the same way:
    200, nothing done. The adapters already collapse them; this only adds "and no
    Retainer of ours serves that repository".
    """
    body = delivery.body

    if channel is Channel.GITHUB:
        # The event name is a header, not a field — GitHub sends `issue_comment`
        # and `pull_request_review_comment` bodies that are otherwise hard to tell
        # apart, and `read_mention` keys its whole reading off it.
        mention = github.read_mention(
            body,
            event=header(delivery.headers, "X-GitHub-Event"),
            handle=handle,
            ourselves=handle,
        )
        if mention is None:
            return None
        target = _for_repo(reachable, mention.repo)
        return None if target is None else github.to_summons(mention, target.id)

    if channel is Channel.SLACK:
        slack_mention = slack.read_mention(body, bot_user=handle)
        if slack_mention is None:
            return None
        return slack.to_summons(slack_mention, reachable[0].id)

    telegram_mention = telegram.read_mention(body, handle=handle)
    if telegram_mention is None:
        return None
    return telegram.to_summons(telegram_mention, reachable[0].id)


# --------------------------------------------------------------------------- #
# the durable state one deployment holds
# --------------------------------------------------------------------------- #


def state_dir(home: Path) -> Path:
    return home / STATE_DIRNAME


def open_stores(registry: Registry, home: Path, *, environ: Mapping[str, str]) -> Stores:
    """The four stores, opened side by side under the Ronin home.

    ``PostStore`` gets the *registry's* identity rather than the operator's, which
    is what makes a Retainer's commits say plainly that they were the agent's —
    and revocable without revoking the operator.
    """
    directory = state_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    return Stores(
        threads=ThreadMap.open(directory),
        ledger=EffectLedger.open(directory),
        escalations=EscalationStore.open(directory),
        posts=PostStore(root=registry.posts_root, identity=registry.identity, environ=environ),
    )


@dataclass(frozen=True, slots=True)
class Deps:
    """What ``serve`` and ``tick`` reach the outside world through.

    One record rather than six keyword arguments repeated across three functions,
    and injected so the whole path is testable: a scripted poster and a scripted
    runner turn "a signed webhook produced this reply" into an assertion.
    """

    stores: Stores
    post: Callable[[Summons, str], Awaitable[str]]
    run: Callable[..., Awaitable[Outcome]] = run_summons
    log: Callable[[str], None] = field(default_factory=lambda: _stderr)


def _stderr(line: str) -> None:
    print(line, file=sys.stderr)


def _summary(outcome: Outcome) -> str:
    if outcome.paused:
        return (
            f"{outcome.summons.retainer}: paused on {len(outcome.escalations)} "
            f"escalation(s) in {outcome.summons.thread}"
        )
    state = "ok" if outcome.ok else "failed"
    posted = f", posted {outcome.posted}" if outcome.posted else ""
    return f"{outcome.summons.retainer}: {state} in {outcome.summons.thread}{posted}"


async def act(summons: Summons, registry: Registry, home: Path, deps: Deps, **extra: Any) -> str:
    """Run one summons and return the line worth logging about it.

    Every failure becomes a line rather than an exception. A daemon that dies on
    one bad summons stops serving every other Retainer, and the caller here is a
    webhook handler whose only other option is a 500 — which tells the platform to
    redeliver the same failing request.
    """
    try:
        outcome = await deps.run(
            summons,
            retainers=registry.retainers,
            deployment=registry.deployment,
            stores=deps.stores,
            post=deps.post,
            home=home,
            **extra,
        )
    except RunRefused as refused:
        return f"{summons.retainer}: refused — {refused}"
    except Exception as error:  # a daemon must survive one bad turn
        return f"{summons.retainer}: failed — {type(error).__name__}: {error}"
    for note in outcome.notes:
        deps.log(f"  note: {note}")
    return _summary(outcome)


# --------------------------------------------------------------------------- #
# tick — every due routine, once
# --------------------------------------------------------------------------- #


async def run_tick(
    registry: Registry,
    home: Path,
    deps: Deps,
    *,
    now: float,
    retainer: str = "",
    routines: RoutineStore | None = None,
) -> tuple[int, str]:
    """Fire what is due and report. Returns ``(exit_code, report)``.

    ``mark_fired`` happens *before* the run, not after. A routine whose run takes
    longer than its interval would otherwise be due again on the next tick and
    start a second copy of itself, which is how a five-minute routine becomes
    twelve concurrent ones over an hour.
    """
    store = routines if routines is not None else RoutineStore.open(state_dir(home))
    try:
        due: tuple[Routine, ...] = store.due(now, retainer=retainer)
    except RoutineError as error:
        return 1, f"ronin retain tick: {error}\n"

    if not due:
        return 0, "ronin retain tick: nothing due\n"

    lines: list[str] = []
    for routine in due:
        summons, _marked = fire(store, routine, now)
        lines.append(await act(summons, registry, home, deps))
    return 0, f"ronin retain tick: fired {len(due)} routine(s)\n" + "".join(
        f"  {line}\n" for line in lines
    )


# --------------------------------------------------------------------------- #
# the verb
# --------------------------------------------------------------------------- #


def _handle(channel: Channel, environ: Mapping[str, str]) -> str:
    name = HANDLE_ENV[channel]
    value = environ.get(name, "").strip()
    if not value:
        raise RetainRefused(
            f"set {name} to what this Retainer is called on {channel.value}. Without "
            "it a delivery cannot be recognised as addressed to it — and neither can "
            "its own posts, so it would answer itself."
        )
    return value


def _secret(environ: Mapping[str, str]) -> str:
    value = environ.get(SECRET_ENV, "").strip()
    if not value:
        raise RetainRefused(
            f"set {SECRET_ENV} to the shared secret this channel signs with. A "
            "receiver with nothing to verify against would accept every delivery, "
            "which is a public endpoint that runs an agent."
        )
    return value


async def run_retain(
    options: RetainOptions,
    *,
    environ: Mapping[str, str],
    serve: Callable[..., Any] = serve_retainer,
    deps: Deps | None = None,
    now: Callable[[], float] = time.time,
    wait: Callable[[], Awaitable[None]] | None = None,
) -> tuple[int, str, str]:
    """Run one ``retain`` verb. Returns ``(exit_code, stdout, stderr)``.

    ``check`` returns at once; ``tick`` runs one pass; ``serve`` waits until it is
    interrupted. Every failure that is the operator's — an unreadable registry, an
    unroutable channel, a missing secret — is a line on stderr and a non-zero code,
    never a traceback, because the reader of a daemon's first failure is somebody
    who has just written a config file.

    **Async, and the reason is a bug this had.** ``dispatch`` is already inside an
    event loop, so an ``asyncio.run`` in here raised ``cannot be called from a
    running event loop`` the first time ``tick`` was typed at a real terminal —
    and would have done the same for every delivery ``serve`` handled. The verb
    runs on the caller's loop instead. The receiver's own threads each open a
    fresh loop per delivery, which is `retain.py`'s business and unaffected.
    """
    if options.subcommand not in SUBCOMMANDS:
        return 2, "", f"ronin retain: unknown subcommand {options.subcommand!r}\n"

    try:
        registry = load_registry(options.home, path=options.registry)
    except RegistryError as error:
        where = options.registry or registry_path(options.home)
        return 1, "", f"ronin retain: {error}\n(reading {where})\n"

    if options.subcommand == "check":
        return 0, (_as_json(registry) if options.as_json else _describe(registry)), ""

    try:
        reachable = servable(registry, options.channel, options.retainer)
        handle = _handle(options.channel, environ)
        secret = _secret(environ)
    except RetainRefused as refused:
        return 1, "", f"ronin retain {options.subcommand}: {refused}\n"

    resolved = deps if deps is not None else _real_deps(registry, options, environ)

    if options.subcommand == "tick":
        code, report = await run_tick(
            registry,
            options.home,
            resolved,
            now=now(),
            retainer=options.retainer,
        )
        return code, report, ""

    return await _serve(options, registry, reachable, handle, secret, resolved, serve, wait)


def _real_deps(registry: Registry, options: RetainOptions, environ: Mapping[str, str]) -> Deps:
    return Deps(
        stores=open_stores(registry, options.home, environ=environ),
        post=http_poster(environ=environ),
    )


async def _serve(
    options: RetainOptions,
    registry: Registry,
    reachable: Sequence[Retainer],
    handle: str,
    secret: str,
    deps: Deps,
    serve: Callable[..., Any],
    wait: Callable[[], Awaitable[None]] | None,
) -> tuple[int, str, str]:
    async def receive(delivery: Delivery) -> Mapping[str, Any]:
        summons = summons_from(
            delivery, channel=options.channel, reachable=reachable, handle=handle
        )
        if summons is None:
            # 200 and "nothing to do". The alternative — a 4xx for a delivery that
            # was well-formed and simply not addressed to us — teaches the platform
            # that this endpoint is broken, and some of them stop delivering.
            return {"ok": True, "acted": False}
        deps.log(await act(summons, registry, options.home, deps))
        return {"ok": True, "acted": True, "retainer": summons.retainer}

    config = ReceiverConfig(
        scheme=SCHEME_FOR[options.channel],
        secret=secret,
        receive=receive,
        log=deps.log,
    )
    served = ", ".join(retainer.id for retainer in reachable)
    deps.log(f"serving {served} on {options.channel.value}")
    server = serve(options.host, options.port, config)
    try:
        # The server runs on its own threads; this only holds the process open.
        # An Event that is never set rather than a sleep loop: a daemon should
        # spend its idle time in a wait, not waking up to find nothing changed.
        await (wait() if wait is not None else asyncio.Event().wait())
    except (KeyboardInterrupt, asyncio.CancelledError):  # pragma: no cover - a signal
        pass
    finally:
        shutdown = getattr(server, "stop", None)
        if callable(shutdown):
            shutdown()
    return 0, "", ""


__all__ = [
    "HANDLE_ENV",
    "SCHEME_FOR",
    "SECRET_ENV",
    "STATE_DIRNAME",
    "SUBCOMMANDS",
    "Deps",
    "RetainOptions",
    "RetainRefused",
    "act",
    "open_stores",
    "run_retain",
    "run_tick",
    "servable",
    "state_dir",
    "summons_from",
]
