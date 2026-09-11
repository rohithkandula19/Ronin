"""The seam: a summons in, an agent run, an answer out.

This is the integration test the retainer plane was missing — every earlier
module was tested alone. It runs a *real* `Agent` over a scripted provider in a
real temporary workspace, with real stores, and asserts the properties that only
appear once the pieces are wired:

* a gated call becomes an **escalation** and the run stops, rather than being
  flatly refused;
* the reply is posted **at most once** however many times the delivery arrives;
* `allow_tools` actually removes a tool from the registry the loop is handed,
  and what survives is still **gated**.

No network and no real provider: `scripted_router` is the model layer, and the
poster is a list.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import stream_harness as h

from ronin.cli.retainer_run import (
    ALWAYS_PUBLISHED,
    CAPABILITY_TOOLS,
    PAUSED_TEMPLATE,
    RunRefused,
    Stores,
    published_tools,
    run_summons,
)
from ronin.retainer.ask import EscalationStore
from ronin.retainer.ledger import EffectLedger, EffectStatus
from ronin.retainer.model import (
    Budgets,
    Capability,
    Channel,
    Deployment,
    Effect,
    EffectKind,
    Post,
    Retainer,
    StandingOrders,
    Summons,
    SummonsKind,
    summons_id,
)
from ronin.retainer.orders import parse_grants
from ronin.retainer.posts import Identity, PostStore
from ronin.retainer.threads import ThreadMap
from ronin.safety.policy import Decision
from ronin.verify.runner import CommandOutcome

BOT = Identity(name="ronin[bot]", email="ronin@users.noreply.github.com")
REPO = "rohithkandula19/Ronin"
THREAD = f"{REPO}#258"


class FakeGit:
    """Pretends to clone by making a checkout that already exists."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    async def __call__(
        self,
        argv: Any,
        *,
        cwd: Path,
        timeout: float = 0.0,
        env: Mapping[str, str] | None = None,
    ) -> CommandOutcome:
        (self.workspace / ".git").mkdir(parents=True, exist_ok=True)
        return CommandOutcome(argv=tuple(argv), exit_code=0)


@pytest.fixture
def bench(tmp_path: Path) -> Any:
    """A deployment, its four stores, and a workspace already checked out."""
    root = tmp_path / "posts"
    workspace = root / "sentry" / "rohithkandula19" / "Ronin"
    workspace.mkdir(parents=True)
    (workspace / ".git").mkdir()
    (workspace / "README.md").write_text("# Ronin\n")
    state = tmp_path / "state"
    home = tmp_path / "home"
    home.mkdir()
    stores = Stores(
        threads=ThreadMap.open(state),
        ledger=EffectLedger.open(state),
        escalations=EscalationStore.open(state),
        posts=PostStore(root=root, identity=BOT, run=FakeGit(workspace)),
    )
    return {"stores": stores, "home": home, "workspace": workspace}


def retainer(**kwargs: Any) -> Retainer:
    orders = kwargs.pop(
        "orders",
        StandingOrders(
            brief="keep CI green",
            tools=frozenset({"read", "bash"}),
            grants=parse_grants([{"tool": "read", "decision": "allow"}]),
            default=Decision.DENY,
            budgets=Budgets(iterations=4),
            wants=frozenset({Capability.SHELL}),
        ),
    )
    return Retainer(
        id=kwargs.pop("id", "sentry"),
        name=kwargs.pop("name", "Sentry"),
        post=Post(repo=REPO, workspace=Path("/unused"), branch="main"),
        orders=orders,
        channels=kwargs.pop("channels", frozenset({Channel.GITHUB})),
        **kwargs,
    )


def summons(**kwargs: Any) -> Summons:
    return Summons(
        retainer=kwargs.pop("retainer", "sentry"),
        kind=kwargs.pop("kind", SummonsKind.MENTION),
        channel=kwargs.pop("channel", Channel.GITHUB),
        thread=kwargs.pop("thread", THREAD),
        text=kwargs.pop("text", "is CI green?"),
        actor=kwargs.pop("actor", "rohithkandula19"),
        **kwargs,
    )


LOCAL = Deployment(name="laptop", capabilities=frozenset(Capability))
HOSTED = Deployment(
    name="fly", hosted=True, capabilities=frozenset({Capability.SHELL, Capability.NETWORK})
)


async def drive(bench: Any, *, says: Any, who: Retainer | None = None, **kwargs: Any) -> Any:
    """Run one summons against a scripted provider, recording what was posted."""
    router, _client = h.scripted_router(says)
    posted: list[tuple[str, str]] = []

    async def post(sent: Summons, text: str) -> str:
        posted.append((sent.thread, text))
        return f"https://github.com/{sent.thread}#issuecomment-1"

    who = who or retainer()
    outcome = await run_summons(
        kwargs.pop("summons", summons()),
        retainers={who.id: who},
        deployment=kwargs.pop("deployment", LOCAL),
        stores=bench["stores"],
        post=post,
        home=bench["home"],
        router=router,
        **kwargs,
    )
    return outcome, posted


# --------------------------------------------------------------------------- #
# The happy path, end to end
# --------------------------------------------------------------------------- #


async def test_a_mention_runs_and_the_answer_is_posted(bench: Any) -> None:
    outcome, posted = await drive(bench, says=[h.provider_says("CI is green.")])
    assert outcome.ok
    assert not outcome.paused
    assert posted == [(THREAD, "CI is green.")]
    assert outcome.posted.endswith("#issuecomment-1")


async def test_the_thread_is_bound_to_a_session_that_persists(bench: Any) -> None:
    outcome, _posted = await drive(bench, says=[h.provider_says("done")])
    found = bench["stores"].threads.lookup("sentry", Channel.GITHUB, THREAD)
    assert found is not None
    assert found.session == outcome.session
    assert found.workspace == bench["workspace"]


async def test_a_second_mention_lands_in_the_same_session(bench: Any) -> None:
    first, _ = await drive(bench, says=[h.provider_says("one")])
    second, _ = await drive(
        bench, says=[h.provider_says("two")], summons=summons(text="and again?")
    )
    assert first.session == second.session


async def test_an_empty_answer_still_says_something(bench: Any) -> None:
    """Silence would leave the thread looking like the Retainer never woke."""
    _outcome, posted = await drive(bench, says=[h.provider_says("")])
    assert posted
    assert "nothing to report" in posted[0][1]


# --------------------------------------------------------------------------- #
# Posted at most once, however many times the delivery arrives
# --------------------------------------------------------------------------- #


async def test_a_redelivered_webhook_posts_nothing_the_second_time(bench: Any) -> None:
    same = summons()
    _first, posted_first = await drive(bench, says=[h.provider_says("CI is green.")], summons=same)
    _second, posted_second = await drive(
        bench, says=[h.provider_says("CI is green.")], summons=same
    )
    assert posted_first == [(THREAD, "CI is green.")]
    assert posted_second == [], "the ledger must make the redelivery a no-op"


async def test_the_ledger_records_the_reply_as_done(bench: Any) -> None:
    outcome, _posted = await drive(bench, says=[h.provider_says("CI is green.")])
    effect = Effect(
        retainer="sentry",
        summons=summons_id(outcome.summons),
        step="reply",
        kind=EffectKind.COMMENT,
        target=THREAD,
        body="CI is green.",
    )
    assert bench["stores"].ledger.status(effect) is EffectStatus.DONE


async def test_a_different_answer_is_a_different_effect_and_does_post(bench: Any) -> None:
    same = summons()
    await drive(bench, says=[h.provider_says("green")], summons=same)
    _outcome, posted = await drive(bench, says=[h.provider_says("red")], summons=same)
    assert posted == [(THREAD, "red")]


# --------------------------------------------------------------------------- #
# A gated call escalates rather than being flatly refused
# --------------------------------------------------------------------------- #


async def test_a_gated_call_becomes_an_escalation_and_the_run_stops(bench: Any) -> None:
    outcome, posted = await drive(
        bench,
        says=[
            h.provider_calls("bash", {"command": "./deploy.sh production"}),
            h.provider_says("deployed"),
        ],
    )
    assert outcome.paused
    assert len(outcome.escalations) == 1
    waiting = bench["stores"].escalations.lookup(outcome.escalations[0])
    assert waiting is not None
    assert waiting.open
    assert waiting.tool == "bash"
    assert "./deploy.sh production" in waiting.request
    assert posted == [(THREAD, PAUSED_TEMPLATE.format(retainer="Sentry"))]


async def test_the_escalation_records_the_session_it_can_resume_from(bench: Any) -> None:
    outcome, _posted = await drive(
        bench,
        says=[
            h.provider_calls("bash", {"command": "systemctl restart nginx"}),
            h.provider_says("restarted"),
        ],
    )
    waiting = bench["stores"].escalations.lookup(outcome.escalations[0])
    assert waiting is not None
    assert waiting.session == outcome.session


async def test_a_paused_run_does_not_post_the_models_own_words(bench: Any) -> None:
    """It was refused, so whatever it said next is about the refusal."""
    _outcome, posted = await drive(
        bench,
        says=[
            h.provider_calls("bash", {"command": "./deploy.sh"}),
            h.provider_says("I could not deploy, sorry!"),
        ],
    )
    assert "sorry" not in posted[0][1]
    assert "Paused" in posted[0][1]


async def test_an_unconditionally_denied_command_does_not_escalate(bench: Any) -> None:
    """The distinction a reader gets wrong, so it is pinned.

    `PolicyEngine.approve` returns on `deny` **without** consulting the asker, so
    the denylist floor — `git push --force`, verified `deny` against the real
    engine — is refused outright and there is nothing for a human to answer. That
    is correct: unconditional means unconditional. Escalation is for the `ask`
    band, which is why `StandingOrders.default` is `ask` and not `deny`.
    """
    outcome, posted = await drive(
        bench,
        says=[
            h.provider_calls("bash", {"command": "git push --force origin main"}),
            h.provider_says("I could not force-push."),
        ],
    )
    assert outcome.escalations == ()
    assert not outcome.paused
    assert bench["stores"].escalations.waiting() == ()
    assert posted == [(THREAD, "I could not force-push.")]


async def test_the_floor_is_ask_so_that_the_asker_decides_what_it_means() -> None:
    """`deny` would make escalation dead code; `ask` is exactly as safe."""
    assert StandingOrders().default is Decision.ASK


# --------------------------------------------------------------------------- #
# The allowlist really narrows the registry, and what survives is gated
# --------------------------------------------------------------------------- #


async def test_a_retainer_without_the_shell_capability_has_no_bash(bench: Any) -> None:
    """The capability wall, end to end: the tool is absent, not denied."""
    orders = StandingOrders(
        tools=frozenset({"read", "bash"}),
        grants=parse_grants([{"tool": "read", "decision": "allow"}]),
        budgets=Budgets(iterations=4),
        wants=frozenset({Capability.SHELL}),
    )
    without = Deployment(name="bare", capabilities=frozenset())
    outcome, _posted = await drive(
        bench, says=[h.provider_says("looked")], who=retainer(orders=orders), deployment=without
    )
    assert not outcome.authority.permits("bash")
    assert any("bash is not published" in note for note in outcome.notes)


async def test_a_hosted_deployment_takes_the_browser_away_end_to_end(bench: Any) -> None:
    orders = StandingOrders(
        tools=frozenset({"read", "browse"}),
        budgets=Budgets(iterations=4),
        wants=frozenset({Capability.BROWSER}),
    )
    outcome, _posted = await drive(
        bench, says=[h.provider_says("ok")], who=retainer(orders=orders), deployment=HOSTED
    )
    assert Capability.BROWSER in outcome.authority.withheld
    assert not outcome.authority.permits("browse")


async def test_reading_is_always_published_even_if_the_orders_forgot(bench: Any) -> None:
    """A Retainer that cannot look is one that guesses."""
    orders = StandingOrders(tools=frozenset(), budgets=Budgets(iterations=4))
    assert (
        published_tools(
            (await drive(bench, says=[h.provider_says("ok")], who=retainer(orders=orders)))[
                0
            ].authority
        )
        >= ALWAYS_PUBLISHED
    )


async def test_a_tool_the_workspace_lacks_is_a_note_not_a_crash(bench: Any) -> None:
    orders = StandingOrders(
        tools=frozenset({"read", "nonexistent_tool"}), budgets=Budgets(iterations=4)
    )
    outcome, _posted = await drive(bench, says=[h.provider_says("ok")], who=retainer(orders=orders))
    assert outcome.ok
    assert any("nonexistent_tool" in note for note in outcome.notes)


async def test_the_capability_map_lives_here_because_retainer_cannot_import_cli() -> None:
    from ronin.cli.gate import UNTRUSTED_TOOLS

    assert CAPABILITY_TOOLS[Capability.SHELL] == frozenset({"bash"})
    assert CAPABILITY_TOOLS[Capability.NETWORK] is UNTRUSTED_TOOLS
    assert set(CAPABILITY_TOOLS) == set(Capability)


# --------------------------------------------------------------------------- #
# Refusals that are not outcomes
# --------------------------------------------------------------------------- #


async def test_an_unknown_retainer_is_refused_before_anything_is_cloned(
    bench: Any,
) -> None:
    with pytest.raises(RunRefused, match="no retainer named"):
        await drive(bench, says=[h.provider_says("x")], summons=summons(retainer="ghost"))


async def test_a_channel_the_retainer_is_not_on_is_refused(bench: Any) -> None:
    with pytest.raises(RunRefused, match="not reachable on slack"):
        await drive(
            bench,
            says=[h.provider_says("x")],
            summons=summons(channel=Channel.SLACK, thread="C1/17"),
        )


async def test_the_refusal_names_the_fix_rather_than_just_the_problem(bench: Any) -> None:
    with pytest.raises(RunRefused, match="add the channel to its record"):
        await drive(
            bench,
            says=[h.provider_says("x")],
            summons=summons(channel=Channel.SLACK, thread="C1/17"),
        )
