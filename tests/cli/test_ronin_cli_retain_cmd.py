"""``ronin retain`` — the Retainer's front door.

The test worth reading is :func:`test_a_signed_webhook_runs_a_summons_and_posts_a_reply`.
It sends real webhook bytes through the real receiver with a real signature, and
asserts on what came out the other side — no network, because the runner and the
poster are injected and the receiver's HTTP path is exercised by calling `route`
the way `tests/cli/test_ronin_cli_retain.py` does. That is the whole seam this
command exists to close, and until it existed nothing in the tree ran end to end.

The rest is mostly refusals, which is most of what this verb does. Routing is the
one decision here with more than one defensible answer, so each branch of it is
pinned with the reason in the docstring rather than left to be inferred.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import replace
from http import HTTPStatus
from pathlib import Path
from typing import Any

import pytest

from ronin.cli.main import Command, Options, Usage, parse
from ronin.cli.retain import DELIVERY_PATH, GITHUB_SCHEME, Delivery, route
from ronin.cli.retain_cmd import (
    HANDLE_ENV,
    SCHEME_FOR,
    SECRET_ENV,
    SUBCOMMANDS,
    Deps,
    RetainOptions,
    RetainRefused,
    act,
    open_stores,
    run_retain,
    run_tick,
    servable,
    state_dir,
    summons_from,
)
from ronin.cli.retainer_run import Outcome, RunRefused
from ronin.retainer.model import Capability, Channel, Summons, SummonsKind
from ronin.retainer.orders import authority_for
from ronin.retainer.registry import parse_registry
from ronin.retainer.routines import Routine, RoutineError, RoutineStore

SECRET = "topsecret"
HANDLE = "ronin-bot"

#: Slack's idea of a handle is a user *id*: a mention is a link, `<@U024BE7LH>`,
#: and the display name never appears in the message text at all. Which is why
#: `HANDLE_ENV` is three variables and not one.
SLACK_BOT = "U024BE7LH"

REGISTRY: dict[str, Any] = {
    "deployment": {"name": "laptop", "capabilities": ["shell", "network"]},
    "posts": {"identity": {"name": "Ronin Retainer", "email": "retainer@example.com"}},
    "retainers": [
        {
            "id": "triage",
            "name": "Triage",
            "repo": "acme/widgets",
            "channels": ["github", "slack"],
            "orders": {
                "brief": "Triage incoming issues.",
                "tools": ["read", "grep"],
                "wants": ["shell", "browser"],
                "grants": [{"tool": "bash", "decision": "allow", "command": "^pytest"}],
                "budgets": {"iterations": 12},
            },
        }
    ],
}


def _second(record: dict[str, Any] | None = None) -> dict[str, Any]:
    """A registry with two Retainers, on different repositories."""
    document: dict[str, Any] = json.loads(json.dumps(REGISTRY))
    extra = record or {
        "id": "docs",
        "name": "Docs",
        "repo": "acme/handbook",
        "channels": ["github", "slack"],
        "orders": {"tools": ["read"]},
    }
    document["retainers"].append(extra)
    return document


def _write(root: Path, document: dict[str, Any] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "retainers.json"
    path.write_text(json.dumps(document or REGISTRY, indent=2), encoding="utf-8")
    return root


def _registry(document: dict[str, Any] | None = None, home: Path = Path("/tmp/x")) -> Any:
    return parse_registry(document or REGISTRY, home=home)


def _env(**extra: str) -> dict[str, str]:
    return {SECRET_ENV: SECRET, HANDLE_ENV[Channel.GITHUB]: HANDLE, **extra}


def _issue_comment(*, repo: str = "acme/widgets", text: str = f"@{HANDLE} please look") -> bytes:
    return json.dumps(
        {
            "action": "created",
            "repository": {"full_name": repo},
            "issue": {"number": 41},
            "comment": {"body": text, "user": {"login": "someone"}, "node_id": "IC_1"},
        }
    ).encode()


def _delivery(body: bytes, *, event: str = "issue_comment") -> Delivery:
    return Delivery(
        scheme=GITHUB_SCHEME.name,
        headers={"X-GitHub-Event": event},
        body=json.loads(body),
        raw_body=body,
    )


# --------------------------------------------------------------------------- #
# check — the verb that makes the registry parser reachable at all
# --------------------------------------------------------------------------- #


def test_check_reports_what_the_deployment_holds(tmp_path: Path) -> None:
    home = _write(tmp_path)
    code, out, err = asyncio.run(run_retain(RetainOptions("check", home), environ={}))
    assert (code, err) == (0, "")
    assert "deployment: laptop" in out
    assert "triage  Triage" in out
    assert "acme/widgets" in out


def test_check_names_a_capability_that_was_asked_for_and_not_granted(tmp_path: Path) -> None:
    """The line an operator would never think to ask for, and most needs.

    `StandingOrders.granted` *intersects* rather than validates — deliberately, so
    moving a Retainer to a hosted deployment narrows what it can do instead of
    refusing to start. The cost of that choice is that a capability it asked for
    and cannot have is dropped in silence. This is what breaks the silence.
    """
    home = _write(tmp_path)
    _code, out, _err = asyncio.run(run_retain(RetainOptions("check", home), environ={}))
    assert "holds:    shell" in out
    assert "NOT held: browser" in out
    assert Capability.BROWSER not in _registry().deployment.capabilities


def test_check_needs_no_credentials_and_opens_nothing(tmp_path: Path) -> None:
    """Read-only: a question about a config file must not need a token to answer,
    and must not create the state directory as a side effect of answering."""
    home = _write(tmp_path)
    code, _out, _err = asyncio.run(run_retain(RetainOptions("check", home), environ={}))
    assert code == 0
    assert not state_dir(home).exists()


def test_check_as_json_is_parseable(tmp_path: Path) -> None:
    home = _write(tmp_path)
    _code, out, _err = asyncio.run(
        run_retain(RetainOptions("check", home, as_json=True), environ={})
    )
    payload = json.loads(out)
    assert payload["deployment"]["name"] == "laptop"
    assert payload["retainers"][0]["not_held"] == ["browser"]


def test_a_registry_error_reaches_the_operator_with_its_position(tmp_path: Path) -> None:
    """Why this verb exists.

    The parser's messages name the exact position in the file —
    `registry.retainers[0].orders.grants[0]` — and until there was a command to
    invoke it, the only way to see one was to start a daemon that did not exist.
    """
    broken = json.loads(json.dumps(REGISTRY))
    broken["retainers"][0]["orders"]["grants"] = [{"tool": "bash", "decision": "yes"}]
    home = _write(tmp_path, broken)

    code, out, err = asyncio.run(run_retain(RetainOptions("check", home), environ={}))
    assert code == 1 and out == ""
    assert "registry.retainers[0].orders.grants[0]" in err
    assert str(home / "retainers.json") in err


def test_a_missing_registry_names_where_it_looked(tmp_path: Path) -> None:
    code, _out, err = asyncio.run(run_retain(RetainOptions("check", tmp_path), environ={}))
    assert code == 1
    assert "no retainer registry at" in err


def test_an_unknown_subcommand_is_refused(tmp_path: Path) -> None:
    code, _out, err = asyncio.run(run_retain(RetainOptions("sevre", tmp_path), environ={}))
    assert code == 2
    assert "unknown subcommand 'sevre'" in err


# --------------------------------------------------------------------------- #
# routing — the one decision here with more than one defensible answer
# --------------------------------------------------------------------------- #


def test_a_named_retainer_is_served_alone() -> None:
    served = servable(_registry(_second()), Channel.GITHUB, "docs")
    assert [retainer.id for retainer in served] == ["docs"]


def test_naming_a_retainer_that_is_not_in_the_registry_lists_the_ones_that_are() -> None:
    with pytest.raises(RetainRefused, match="docs, triage"):
        servable(_registry(_second()), Channel.GITHUB, "nobody")


def test_naming_a_retainer_not_reachable_on_the_channel_is_refused() -> None:
    """Adding the channel to the record is the fix, and the message says so rather
    than serving it anyway — a Retainer answering on a channel its record does not
    list is exactly the kind of quiet widening standing orders exist to prevent."""
    document = json.loads(json.dumps(REGISTRY))
    document["retainers"][0]["channels"] = ["github"]
    with pytest.raises(RetainRefused, match="not reachable on slack"):
        servable(_registry(document), Channel.SLACK, "triage")


def test_a_channel_nobody_serves_is_refused_at_bind_rather_than_at_delivery() -> None:
    """Finding out at delivery time means finding out from a 500 in somebody
    else's webhook dashboard."""
    document = json.loads(json.dumps(REGISTRY))
    document["retainers"][0]["channels"] = ["github"]
    with pytest.raises(RetainRefused, match="reachable on slack"):
        servable(_registry(document), Channel.SLACK, "")


def test_github_serves_every_retainer_because_a_mention_names_its_repository() -> None:
    served = servable(_registry(_second()), Channel.GITHUB, "")
    assert {retainer.id for retainer in served} == {"triage", "docs"}


def test_slack_refuses_two_retainers_because_a_message_names_no_repository() -> None:
    """Picking the first is not a routing rule, it is an accident that looks like
    one: a Slack message would wake whichever Retainer happened to sort earliest."""
    with pytest.raises(RetainRefused, match="--retainer"):
        servable(_registry(_second()), Channel.SLACK, "")


def test_a_mention_routes_to_the_retainer_that_serves_that_repository() -> None:
    registry = _registry(_second())
    reachable = servable(registry, Channel.GITHUB, "")

    summons = summons_from(
        _delivery(_issue_comment(repo="acme/handbook")),
        channel=Channel.GITHUB,
        reachable=reachable,
        handle=HANDLE,
    )
    assert summons is not None
    assert summons.retainer == "docs"
    assert summons.thread == "acme/handbook#41"


def test_a_mention_from_a_repository_nobody_serves_is_ignored() -> None:
    """`None`, not an error. The endpoint is one URL and an organisation can point
    every repository at it; a 4xx for "not mine" teaches GitHub the hook is broken.
    """
    reachable = servable(_registry(), Channel.GITHUB, "")
    assert (
        summons_from(
            _delivery(_issue_comment(repo="acme/unrelated")),
            channel=Channel.GITHUB,
            reachable=reachable,
            handle=HANDLE,
        )
        is None
    )


def test_a_comment_that_does_not_mention_the_handle_is_ignored() -> None:
    reachable = servable(_registry(), Channel.GITHUB, "")
    assert (
        summons_from(
            _delivery(_issue_comment(text="looks fine to me")),
            channel=Channel.GITHUB,
            reachable=reachable,
            handle=HANDLE,
        )
        is None
    )


def test_the_retainers_own_comment_does_not_wake_it() -> None:
    """The loop this would otherwise be. `ourselves` is the same handle it answers
    to, so a reply that quotes the mention cannot summon another reply."""
    body = json.loads(_issue_comment())
    body["comment"]["user"]["login"] = HANDLE
    reachable = servable(_registry(), Channel.GITHUB, "")
    assert (
        summons_from(
            _delivery(json.dumps(body).encode()),
            channel=Channel.GITHUB,
            reachable=reachable,
            handle=HANDLE,
        )
        is None
    )


def test_the_github_event_is_read_from_the_header_not_the_body() -> None:
    """A bug this had: `Delivery` carries no event field, and `read_mention` keys
    its entire reading off the event name. Reading it from the body would have
    made every delivery look like the wrong event and woken nobody, silently."""
    reachable = servable(_registry(), Channel.GITHUB, "")
    delivery = _delivery(_issue_comment())
    assert (
        summons_from(delivery, channel=Channel.GITHUB, reachable=reachable, handle=HANDLE)
        is not None
    )
    assert (
        summons_from(
            replace(delivery, headers={}),
            channel=Channel.GITHUB,
            reachable=reachable,
            handle=HANDLE,
        )
        is None
    )


def test_a_slack_message_routes_to_the_single_retainer_on_that_channel() -> None:
    """No repository to route on, so the routing was already done at bind time —
    `servable` refused a second Slack Retainer, which is what makes `reachable[0]`
    a decision rather than a coin toss."""
    reachable = servable(_registry(), Channel.SLACK, "")
    body = {
        "type": "event_callback",
        "team_id": "T1",
        "event": {
            "type": "app_mention",
            "user": "U9",
            "channel": "C1",
            "ts": "1757155200.000100",
            "text": f"<@{SLACK_BOT}> take a look",
        },
    }
    summons = summons_from(
        Delivery(scheme="slack", headers={}, body=body),
        channel=Channel.SLACK,
        reachable=reachable,
        handle=SLACK_BOT,
    )
    assert summons is not None
    assert summons.retainer == "triage"
    assert summons.channel is Channel.SLACK


def test_a_slack_delivery_that_is_not_a_mention_is_ignored() -> None:
    reachable = servable(_registry(), Channel.SLACK, "")
    assert (
        summons_from(
            Delivery(scheme="slack", headers={}, body={"type": "url_verification"}),
            channel=Channel.SLACK,
            reachable=reachable,
            handle=SLACK_BOT,
        )
        is None
    )


def test_a_telegram_update_routes_the_same_way() -> None:
    document = json.loads(json.dumps(REGISTRY))
    document["retainers"][0]["channels"] = ["telegram"]
    reachable = servable(_registry(document), Channel.TELEGRAM, "")
    text = f"@{HANDLE} status please"
    body = {
        "message": {
            "chat": {"id": -100},
            "message_id": 7,
            "from": {"id": 5, "is_bot": False, "username": "someone"},
            "text": text,
            "entities": [{"type": "mention", "offset": 0, "length": len(HANDLE) + 1}],
        }
    }
    summons = summons_from(
        Delivery(scheme="telegram", headers={}, body=body),
        channel=Channel.TELEGRAM,
        reachable=reachable,
        handle=HANDLE,
    )
    assert summons is not None
    assert summons.retainer == "triage"
    assert summons.channel is Channel.TELEGRAM


def test_a_telegram_update_that_is_not_a_message_is_ignored() -> None:
    document = json.loads(json.dumps(REGISTRY))
    document["retainers"][0]["channels"] = ["telegram"]
    reachable = servable(_registry(document), Channel.TELEGRAM, "")
    assert (
        summons_from(
            Delivery(scheme="telegram", headers={}, body={"poll": {"id": "1"}}),
            channel=Channel.TELEGRAM,
            reachable=reachable,
            handle=HANDLE,
        )
        is None
    )


@pytest.mark.parametrize("channel", list(Channel))
def test_every_channel_has_a_signing_scheme_and_a_handle_variable(channel: Channel) -> None:
    """Adding a fourth adapter without deciding either of these would bind a
    receiver that verifies nothing, or one that cannot tell its own posts apart."""
    assert channel in SCHEME_FOR
    assert channel in HANDLE_ENV


# --------------------------------------------------------------------------- #
# the end-to-end seam
# --------------------------------------------------------------------------- #


def _signed(body: bytes) -> dict[str, str]:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return {"X-GitHub-Event": "issue_comment", "X-Hub-Signature-256": f"sha256={digest}"}


def test_a_signed_webhook_runs_a_summons_and_posts_a_reply(tmp_path: Path) -> None:
    """Webhook bytes in, posted reply out, through the real receiver.

    This is the seam `ronin retain` exists to close. Every piece was already
    tested in isolation and nothing had ever put them in a line: `route` verified
    a signature against a receiver nobody bound, and `run_summons` acted on a
    summons nobody produced from a delivery.
    """
    registry = _registry(home=tmp_path)
    reachable = servable(registry, Channel.GITHUB, "")
    posted: list[tuple[str, str]] = []
    ran: list[Summons] = []

    async def poster(summons: Summons, text: str) -> str:
        posted.append((summons.thread, text))
        return "comment-1"

    async def runner(summons: Summons, **_kwargs: Any) -> Outcome:
        ran.append(summons)
        return Outcome(
            summons=summons,
            session="s1",
            authority=authority_for(registry.retainers["triage"], registry.deployment),
            posted=await poster(summons, "triaged: it is a duplicate of #12"),
        )

    logged: list[str] = []
    deps = Deps(stores=None, post=poster, run=runner, log=logged.append)  # type: ignore[arg-type]

    async def receive(delivery: Delivery) -> Mapping[str, Any]:
        summons = summons_from(delivery, channel=Channel.GITHUB, reachable=reachable, handle=HANDLE)
        if summons is None:
            return {"ok": True, "acted": False}
        logged.append(await act(summons, registry, tmp_path, deps))
        return {"ok": True, "acted": True, "retainer": summons.retainer}

    body = _issue_comment()
    response = asyncio.run(
        route(
            "POST",
            DELIVERY_PATH,
            _signed(body),
            body,
            scheme=GITHUB_SCHEME,
            secret=SECRET,
            receive=receive,
            now=lambda: 0.0,
            tolerance=300.0,
        )
    )

    assert response.status is HTTPStatus.OK
    assert response.body["acted"] is True
    assert [summons.thread for summons in ran] == ["acme/widgets#41"]
    assert ran[0].kind is SummonsKind.MENTION
    assert ran[0].text.strip() == "please look"
    assert posted == [("acme/widgets#41", "triaged: it is a duplicate of #12")]


def test_a_delivery_with_a_bad_signature_never_reaches_the_router(tmp_path: Path) -> None:
    """Authenticate, then parse, then route — in that order.

    A summons built from an unverified body is an agent run started by anybody who
    can find the URL, and the run has a repository checkout and a shell behind it.
    """
    seen: list[Delivery] = []

    async def receive(delivery: Delivery) -> Mapping[str, Any]:
        seen.append(delivery)
        return {"ok": True}

    body = _issue_comment()
    response = asyncio.run(
        route(
            "POST",
            DELIVERY_PATH,
            {"X-GitHub-Event": "issue_comment", "X-Hub-Signature-256": "sha256=" + "0" * 64},
            body,
            scheme=GITHUB_SCHEME,
            secret=SECRET,
            receive=receive,
            now=lambda: 0.0,
            tolerance=300.0,
        )
    )
    assert response.status is HTTPStatus.UNAUTHORIZED
    assert seen == []


# --------------------------------------------------------------------------- #
# one bad turn must not stop the daemon
# --------------------------------------------------------------------------- #


def _summons() -> Summons:
    return Summons(
        retainer="triage",
        kind=SummonsKind.MENTION,
        channel=Channel.GITHUB,
        thread="acme/widgets#1",
        text="hello",
    )


def test_a_refused_summons_becomes_a_line_not_an_exception(tmp_path: Path) -> None:
    async def refuse(_summons: Summons, **_kwargs: Any) -> Outcome:
        raise RunRefused("no post for that repository")

    deps = Deps(stores=None, post=_never, run=refuse, log=lambda _line: None)  # type: ignore[arg-type]
    line = asyncio.run(act(_summons(), _registry(), tmp_path, deps))
    assert "refused — no post for that repository" in line


def test_an_unexpected_error_becomes_a_line_too(tmp_path: Path) -> None:
    """A daemon that dies on one bad summons stops serving every other Retainer,
    and the caller is a webhook handler whose only other answer is a 500 — which
    tells the platform to redeliver the same failing request."""

    async def explode(_summons: Summons, **_kwargs: Any) -> Outcome:
        raise ZeroDivisionError("the model returned something impossible")

    deps = Deps(stores=None, post=_never, run=explode, log=lambda _line: None)  # type: ignore[arg-type]
    line = asyncio.run(act(_summons(), _registry(), tmp_path, deps))
    assert "failed — ZeroDivisionError" in line


def test_a_paused_run_says_so_rather_than_reading_as_a_failure(tmp_path: Path) -> None:
    registry = _registry()

    async def escalate(summons: Summons, **_kwargs: Any) -> Outcome:
        return Outcome(
            summons=summons,
            session="s1",
            authority=authority_for(registry.retainers["triage"], registry.deployment),
            escalations=("esc-1",),
        )

    deps = Deps(stores=None, post=_never, run=escalate, log=lambda _line: None)  # type: ignore[arg-type]
    line = asyncio.run(act(_summons(), registry, tmp_path, deps))
    assert "paused on 1 escalation(s)" in line


def test_a_workspace_note_is_surfaced_rather_than_swallowed(tmp_path: Path) -> None:
    """`allow_tools` reports a name the workspace does not have as a Note, and a
    note nobody prints is a note nobody gets — which is the whole reason it was a
    note and not an exception."""
    registry = _registry()

    async def noted(summons: Summons, **_kwargs: Any) -> Outcome:
        return Outcome(
            summons=summons,
            session="s1",
            authority=authority_for(registry.retainers["triage"], registry.deployment),
            notes=("allow_tools named 'browse', which this workspace does not have",),
        )

    logged: list[str] = []
    deps = Deps(stores=None, post=_never, run=noted, log=logged.append)  # type: ignore[arg-type]
    asyncio.run(act(_summons(), registry, tmp_path, deps))
    assert any("does not have" in line for line in logged)


async def _never(_summons: Summons, _text: str) -> str:  # pragma: no cover - never called
    raise AssertionError("nothing should be posted in this test")


# --------------------------------------------------------------------------- #
# tick
# --------------------------------------------------------------------------- #


def test_tick_with_nothing_due_says_so(tmp_path: Path) -> None:
    deps = Deps(stores=None, post=_never, run=_never, log=lambda _l: None)  # type: ignore[arg-type]
    code, report = asyncio.run(
        run_tick(_registry(), tmp_path, deps, now=1000.0, routines=RoutineStore.open(tmp_path))
    )
    assert code == 0
    assert "nothing due" in report


def test_tick_fires_a_due_routine_and_runs_it(tmp_path: Path) -> None:
    store = RoutineStore.open(tmp_path)
    store.add(
        Routine(
            id="nightly",
            retainer="triage",
            channel=Channel.GITHUB,
            thread="acme/widgets#7",
            prompt="sweep the backlog",
            interval_s=3600,
        )
    )
    ran: list[Summons] = []
    registry = _registry()

    async def runner(summons: Summons, **_kwargs: Any) -> Outcome:
        ran.append(summons)
        return Outcome(
            summons=summons,
            session="s1",
            authority=authority_for(registry.retainers["triage"], registry.deployment),
            posted="c1",
        )

    deps = Deps(stores=None, post=_never, run=runner, log=lambda _l: None)  # type: ignore[arg-type]
    code, report = asyncio.run(run_tick(registry, tmp_path, deps, now=100_000.0, routines=store))

    assert code == 0
    assert "fired 1 routine(s)" in report
    assert [summons.kind for summons in ran] == [SummonsKind.ROUTINE]
    assert ran[0].text == "sweep the backlog"


def test_a_routine_is_marked_fired_before_it_runs(tmp_path: Path) -> None:
    """Otherwise a routine whose run outlasts its interval is due again on the next
    tick and starts a second copy of itself — which is how a five-minute routine
    becomes twelve concurrent ones over an hour."""
    store = RoutineStore.open(tmp_path)
    store.add(
        Routine(
            id="slow",
            retainer="triage",
            channel=Channel.GITHUB,
            thread="acme/widgets#7",
            prompt="a long one",
            interval_s=3600,
        )
    )
    registry = _registry()
    seen_while_running: list[tuple[Routine, ...]] = []

    async def runner(summons: Summons, **_kwargs: Any) -> Outcome:
        seen_while_running.append(store.due(100_000.0))
        return Outcome(
            summons=summons,
            session="s1",
            authority=authority_for(registry.retainers["triage"], registry.deployment),
        )

    deps = Deps(stores=None, post=_never, run=runner, log=lambda _l: None)  # type: ignore[arg-type]
    asyncio.run(run_tick(registry, tmp_path, deps, now=100_000.0, routines=store))

    assert seen_while_running == [()], "it must not still look due while it is running"


def test_tick_can_be_narrowed_to_one_retainer(tmp_path: Path) -> None:
    store = RoutineStore.open(tmp_path)
    for retainer in ("triage", "docs"):
        store.add(
            Routine(
                id=f"{retainer}-nightly",
                retainer=retainer,
                channel=Channel.GITHUB,
                thread=f"acme/{retainer}#1",
                prompt="sweep",
                interval_s=3600,
            )
        )
    ran: list[str] = []
    registry = _registry(_second())

    async def runner(summons: Summons, **_kwargs: Any) -> Outcome:
        ran.append(summons.retainer)
        return Outcome(
            summons=summons,
            session="s1",
            authority=authority_for(registry.retainers[summons.retainer], registry.deployment),
        )

    deps = Deps(stores=None, post=_never, run=runner, log=lambda _l: None)  # type: ignore[arg-type]
    asyncio.run(run_tick(registry, tmp_path, deps, now=100_000.0, retainer="docs", routines=store))
    assert ran == ["docs"]


# --------------------------------------------------------------------------- #
# serve, and what it refuses before binding anything
# --------------------------------------------------------------------------- #


def test_serving_without_a_secret_is_refused(tmp_path: Path) -> None:
    """ "Unconfigured" meaning "unsigned is fine" is how a webhook endpoint that
    runs an agent gets found by somebody else first."""
    home = _write(tmp_path)
    options = RetainOptions("serve", home)
    code, _out, err = asyncio.run(run_retain(options, environ={HANDLE_ENV[Channel.GITHUB]: HANDLE}))
    assert code == 1
    assert SECRET_ENV in err


def test_serving_without_a_handle_is_refused(tmp_path: Path) -> None:
    """Without it the Retainer cannot tell its own posts from anyone else's, so it
    would answer itself — forever."""
    home = _write(tmp_path)
    code, _out, err = asyncio.run(
        run_retain(RetainOptions("serve", home), environ={SECRET_ENV: SECRET})
    )
    assert code == 1
    assert HANDLE_ENV[Channel.GITHUB] in err
    assert "answer itself" in err


def test_serve_binds_announces_and_stops_cleanly(tmp_path: Path) -> None:
    """The verb's own shape, with the socket replaced: bind, say where, wait, stop.

    `run_retain` is async because `dispatch` already holds a loop — an
    `asyncio.run` in here raised "cannot be called from a running event loop" the
    first time `tick` was typed at a real terminal. Asserted here as a property of
    the interface rather than left to be rediscovered.
    """
    home = _write(tmp_path)
    bound: list[tuple[str, int]] = []
    stopped: list[bool] = []

    class FakeServer:
        def stop(self) -> None:
            stopped.append(True)

    def fake_serve(host: str, port: int, config: Any) -> FakeServer:
        bound.append((host, port))
        assert config.scheme is GITHUB_SCHEME
        assert config.secret == SECRET
        return FakeServer()

    async def once() -> None:
        return None

    logged: list[str] = []
    deps = Deps(stores=None, post=_never, log=logged.append)  # type: ignore[arg-type]
    code, _out, err = asyncio.run(
        run_retain(
            RetainOptions("serve", home, port=9999),
            environ=_env(),
            serve=fake_serve,
            deps=deps,
            wait=once,
        )
    )

    assert (code, err) == (0, "")
    assert bound == [("127.0.0.1", 9999)]
    assert stopped == [True], "the socket must be released on the way out"
    assert any("serving triage on github" in line for line in logged)


def test_the_bound_receiver_answers_a_delivery_that_is_not_for_us_with_a_200(
    tmp_path: Path,
) -> None:
    """The closure `serve` installs, driven through the config it was bound with.

    A 4xx for a delivery that was well-formed and simply not addressed to this
    deployment teaches the platform that the endpoint is broken, and some of them
    stop delivering after enough of those. 200 and "acted: false".
    """
    home = _write(tmp_path)
    answers: list[Mapping[str, Any]] = []

    class FakeServer:
        def stop(self) -> None:
            return None

    captured: list[Any] = []

    def capture(_host: str, _port: int, config: Any) -> FakeServer:
        captured.append(config)
        return FakeServer()

    async def drive() -> None:
        config = captured[0]
        answers.append(await config.receive(_delivery(_issue_comment(repo="acme/nope"))))
        answers.append(await config.receive(_delivery(_issue_comment())))

    ran: list[Summons] = []

    async def runner(summons: Summons, **_kwargs: Any) -> Outcome:
        ran.append(summons)
        registry = _registry(home=home)
        return Outcome(
            summons=summons,
            session="s1",
            authority=authority_for(registry.retainers["triage"], registry.deployment),
            posted="c1",
        )

    deps = Deps(stores=None, post=_never, run=runner, log=lambda _l: None)  # type: ignore[arg-type]
    code, _out, _err = asyncio.run(
        run_retain(
            RetainOptions("serve", home),
            environ=_env(),
            serve=capture,
            deps=deps,
            wait=drive,
        )
    )

    assert code == 0
    assert answers[0] == {"ok": True, "acted": False}
    assert answers[1] == {"ok": True, "acted": True, "retainer": "triage"}
    assert [summons.thread for summons in ran] == ["acme/widgets#41"]


def test_a_routine_store_that_cannot_be_read_is_reported_not_raised(tmp_path: Path) -> None:
    """A tick that cannot open its store must say so and exit non-zero. Raising
    would put a traceback in a cron mail, which is where nobody reads it."""

    class Broken:
        def due(self, _now: float, *, retainer: str = "") -> tuple[Any, ...]:
            raise RoutineError("the routine store is locked")

    deps = Deps(stores=None, post=_never, run=_never, log=lambda _l: None)  # type: ignore[arg-type]
    code, report = asyncio.run(
        run_tick(_registry(), tmp_path, deps, now=0.0, routines=Broken())  # type: ignore[arg-type]
    )
    assert code == 1
    assert "the routine store is locked" in report


def test_serve_binds_to_loopback_by_default() -> None:
    """A webhook receiver that runs an agent is not something to expose by
    accident; a deployment that needs it reachable says so."""
    assert RetainOptions("serve", Path(".")).host == "127.0.0.1"


def test_the_stores_live_together_under_one_directory(tmp_path: Path) -> None:
    """One directory so an operator backing up a deployment has one thing to copy."""
    stores = open_stores(_registry(home=tmp_path), tmp_path, environ={})
    assert stores.threads.path.parent == state_dir(tmp_path)
    assert stores.ledger.path.parent == state_dir(tmp_path)
    assert stores.escalations.path.parent == state_dir(tmp_path)


def test_the_post_store_commits_as_the_registrys_identity(tmp_path: Path) -> None:
    """Its own identity, not the operator's: separate permissions, and an audit
    trail that says plainly which commits were the agent's."""
    stores = open_stores(_registry(home=tmp_path), tmp_path, environ={})
    assert stores.posts.identity.email == "retainer@example.com"
    assert "user.email=retainer@example.com" in stores.posts.identity.git_args


# --------------------------------------------------------------------------- #
# the command line
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("subcommand", SUBCOMMANDS)
def test_each_verb_parses(subcommand: str, tmp_path: Path) -> None:
    options = parse(["retain", subcommand, "--cwd", str(tmp_path)])
    assert isinstance(options, Options)
    assert options.command is Command.RETAIN
    assert options.retain is not None
    assert options.retain.subcommand == subcommand


def test_retain_without_a_subcommand_lists_them() -> None:
    refused = parse(["retain"])
    assert isinstance(refused, Usage)
    assert "check, serve, tick" in refused.message


def test_an_unknown_verb_is_refused_at_parse() -> None:
    refused = parse(["retain", "sevre"])
    assert isinstance(refused, Usage)
    assert "unknown subcommand 'sevre'" in refused.message


def test_a_positional_argument_is_refused_rather_than_ignored() -> None:
    """`ronin retain serve triage` reads like it names a Retainer. Sweeping it into
    the prompt instead would serve every one of them and say nothing."""
    refused = parse(["retain", "serve", "triage"])
    assert isinstance(refused, Usage)
    assert "--retainer" in refused.message


def test_the_flags_reach_the_options(tmp_path: Path) -> None:
    options = parse(
        [
            "retain",
            "serve",
            "--channel",
            "slack",
            "--retainer",
            "triage",
            "--registry",
            str(tmp_path / "other.json"),
            "--port",
            "9000",
            "--cwd",
            str(tmp_path),
        ]
    )
    assert isinstance(options, Options)
    assert options.retain == RetainOptions(
        subcommand="serve",
        home=tmp_path,
        registry=tmp_path / "other.json",
        channel=Channel.SLACK,
        retainer="triage",
        port=9000,
    )


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
