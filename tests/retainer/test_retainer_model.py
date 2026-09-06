"""The records a Retainer is made of, and the four invariants they enforce.

Every validator here exists because the thing it refuses is silent otherwise, so
each one is tested from both sides: the refusal, and the neighbouring case that
must still be allowed. A validator that only ever rejects is a validator nobody
can build a Retainer past.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ronin.retainer.model import (
    Budgets,
    Capability,
    Channel,
    Deployment,
    Effect,
    EffectKind,
    Escalation,
    EscalationState,
    Post,
    Retainer,
    StandingOrders,
    Summons,
    SummonsKind,
    by_id,
    summons_id,
)
from ronin.safety.policy import Decision, Outcome

WORKSPACE = Path("/srv/posts/ronin")


def post() -> Post:
    return Post(repo="rohithkandula19/Ronin", workspace=WORKSPACE)


def orders(**kwargs: object) -> StandingOrders:
    base: dict[str, object] = {"brief": "keep CI green", "tools": frozenset({"read"})}
    base.update(kwargs)
    return StandingOrders(**base)  # type: ignore[arg-type]


def retainer(**kwargs: object) -> Retainer:
    base: dict[str, object] = {
        "id": "sentry",
        "name": "Sentry",
        "post": post(),
        "orders": orders(),
    }
    base.update(kwargs)
    return Retainer(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Deployment — invariant 1, the capability wall
# --------------------------------------------------------------------------- #


def test_local_deployment_may_drive_a_browser() -> None:
    local = Deployment(name="laptop", capabilities=frozenset({Capability.BROWSER}))
    assert local.holds(Capability.BROWSER)


def test_hosted_deployment_may_not_drive_a_browser() -> None:
    with pytest.raises(ValueError, match="safe harbour"):
        Deployment(name="fly", hosted=True, capabilities=frozenset({Capability.BROWSER}))


def test_hosted_deployment_keeps_every_other_capability() -> None:
    hosted = Deployment(
        name="fly", hosted=True, capabilities=frozenset({Capability.SHELL, Capability.NETWORK})
    )
    assert hosted.holds(Capability.SHELL)
    assert not hosted.holds(Capability.BROWSER)


def test_deployment_name_is_a_slug() -> None:
    with pytest.raises(ValueError, match="slug"):
        Deployment(name="../etc")


def test_orders_can_only_request_a_capability_not_grant_it() -> None:
    hosted = Deployment(name="fly", hosted=True, capabilities=frozenset({Capability.SHELL}))
    wants = orders(wants=frozenset({Capability.SHELL, Capability.BROWSER}))
    assert wants.granted(hosted) == frozenset({Capability.SHELL})
    assert wants.denied(hosted) == frozenset({Capability.BROWSER})


def test_moving_to_a_narrower_deployment_narrows_rather_than_fails() -> None:
    local = Deployment(name="laptop", capabilities=frozenset(Capability))
    hosted = Deployment(name="fly", hosted=True, capabilities=frozenset({Capability.NETWORK}))
    wants = orders(wants=frozenset({Capability.NETWORK, Capability.BROWSER}))
    assert Capability.BROWSER in wants.granted(local)
    assert Capability.BROWSER not in wants.granted(hosted)


# --------------------------------------------------------------------------- #
# Post
# --------------------------------------------------------------------------- #


def test_post_requires_owner_and_name() -> None:
    for bad in ("Ronin", "rohithkandula19/", "/Ronin", "a/b/c"):
        with pytest.raises(ValueError, match="owner/name"):
            Post(repo=bad, workspace=WORKSPACE)


def test_post_workspace_must_be_absolute() -> None:
    with pytest.raises(ValueError, match="absolute"):
        Post(repo="o/n", workspace=Path("relative/checkout"))


def test_post_carries_an_optional_branch() -> None:
    assert Post(repo="o/n", workspace=WORKSPACE, branch="main").branch == "main"


# --------------------------------------------------------------------------- #
# Budgets
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["iterations", "tokens", "seconds"])
def test_budgets_must_be_positive(name: str) -> None:
    with pytest.raises(ValueError, match=name):
        Budgets(**{name: 0})


def test_notifications_may_be_zero_because_silence_is_a_valid_setting() -> None:
    assert Budgets(notifications=0).notifications == 0


def test_notifications_may_not_be_negative() -> None:
    with pytest.raises(ValueError, match="negative"):
        Budgets(notifications=-1)


def test_budget_defaults_are_deliberately_low() -> None:
    assert Budgets().iterations == 40
    assert Budgets().seconds == 900


# --------------------------------------------------------------------------- #
# StandingOrders
# --------------------------------------------------------------------------- #


def test_standing_orders_default_to_denying() -> None:
    assert StandingOrders().default is Decision.DENY


def test_prose_and_enforcement_are_separate_fields() -> None:
    """Orders may say anything; what is enforced is the rules and the default."""
    written = orders(brief="you may do anything at all")
    assert written.brief == "you may do anything at all"
    assert written.grants == ()
    assert written.default is Decision.DENY


# --------------------------------------------------------------------------- #
# Retainer
# --------------------------------------------------------------------------- #


def test_retainer_id_is_a_slug_because_it_is_a_directory_name() -> None:
    for bad in ("", "Sentry", "../escape", "a" * 65):
        with pytest.raises(ValueError, match="slug"):
            retainer(id=bad)


def test_retainer_needs_a_display_name() -> None:
    with pytest.raises(ValueError, match="name is required"):
        retainer(name="   ")


def test_retainer_is_reachable_only_where_it_was_given_a_channel() -> None:
    it = retainer(channels=frozenset({Channel.GITHUB}))
    assert it.reachable_on(Channel.GITHUB)
    assert not it.reachable_on(Channel.SLACK)


def test_acts_as_defaults_to_the_operator() -> None:
    assert retainer().acts_as == ""


def test_by_id_refuses_duplicates() -> None:
    assert set(by_id([retainer(), retainer(id="scout")])) == {"sentry", "scout"}
    with pytest.raises(ValueError, match="duplicate retainer id"):
        by_id([retainer(), retainer()])


# --------------------------------------------------------------------------- #
# Summons
# --------------------------------------------------------------------------- #


def test_summons_needs_a_thread_because_that_is_the_session_key() -> None:
    with pytest.raises(ValueError, match="thread is required"):
        Summons(retainer="sentry", kind=SummonsKind.MENTION, channel=Channel.GITHUB, thread="")


def test_summons_retainer_is_a_slug() -> None:
    with pytest.raises(ValueError, match="slug"):
        Summons(retainer="Sentry", kind=SummonsKind.MENTION, channel=Channel.GITHUB, thread="1")


def test_a_reply_must_name_the_escalation_it_answers() -> None:
    with pytest.raises(ValueError, match="must name the escalation"):
        Summons(retainer="sentry", kind=SummonsKind.REPLY, channel=Channel.GITHUB, thread="1")


def test_only_a_reply_carries_an_escalation() -> None:
    with pytest.raises(ValueError, match="only a reply summons"):
        Summons(
            retainer="sentry",
            kind=SummonsKind.ROUTINE,
            channel=Channel.GITHUB,
            thread="1",
            escalation="esc-1",
        )


def test_a_routine_and_a_mention_are_the_same_shape() -> None:
    common = {"retainer": "sentry", "channel": Channel.SLACK, "thread": "C1/17"}
    mention = Summons(kind=SummonsKind.MENTION, **common)  # type: ignore[arg-type]
    routine = Summons(kind=SummonsKind.ROUTINE, **common)  # type: ignore[arg-type]
    assert type(mention) is type(routine)
    assert (mention.retainer, mention.thread) == (routine.retainer, routine.thread)


def test_redelivering_the_same_summons_yields_the_same_id() -> None:
    one = Summons(
        retainer="sentry", kind=SummonsKind.MENTION, channel=Channel.GITHUB, thread="258", text="hi"
    )
    again = Summons(
        retainer="sentry", kind=SummonsKind.MENTION, channel=Channel.GITHUB, thread="258", text="hi"
    )
    assert summons_id(one) == summons_id(again)


def test_a_nonce_separates_two_genuinely_distinct_firings() -> None:
    fire = Summons(
        retainer="sentry", kind=SummonsKind.ROUTINE, channel=Channel.GITHUB, thread="nightly"
    )
    assert summons_id(fire, nonce="08:00") != summons_id(fire, nonce="09:00")


def test_summons_id_separates_channels_and_threads() -> None:
    base = {"retainer": "sentry", "kind": SummonsKind.MENTION, "text": "hi"}
    github = Summons(channel=Channel.GITHUB, thread="1", **base)  # type: ignore[arg-type]
    slack = Summons(channel=Channel.SLACK, thread="1", **base)  # type: ignore[arg-type]
    other = Summons(channel=Channel.GITHUB, thread="2", **base)  # type: ignore[arg-type]
    assert len({summons_id(github), summons_id(slack), summons_id(other)}) == 3


# --------------------------------------------------------------------------- #
# Escalation — invariant 3, state and answer agree
# --------------------------------------------------------------------------- #


def escalation(**kwargs: object) -> Escalation:
    base: dict[str, object] = {
        "id": "esc-1",
        "retainer": "sentry",
        "thread": "258",
        "tool": "bash",
        "request": "push the fix to the PR branch",
    }
    base.update(kwargs)
    return Escalation(**base)  # type: ignore[arg-type]


def test_an_escalation_must_explain_itself() -> None:
    with pytest.raises(ValueError, match="request is required"):
        escalation(request="  ")


def test_an_escalation_needs_an_id() -> None:
    with pytest.raises(ValueError, match="id is required"):
        escalation(id="")


def test_an_open_escalation_carries_no_answer() -> None:
    with pytest.raises(ValueError, match="cannot carry an answer"):
        escalation(answer=Outcome.YES_ONCE)


def test_an_answered_escalation_must_carry_one() -> None:
    with pytest.raises(ValueError, match="must carry the answer"):
        escalation(state=EscalationState.ANSWERED)


def test_resolving_returns_a_new_record_and_leaves_the_original_open() -> None:
    asked = escalation(session="s-1", checkpoint="c-1")
    answered = asked.resolved(Outcome.YES_ONCE, by="rohithkandula19")
    assert asked.open
    assert not answered.open
    assert answered.answer is Outcome.YES_ONCE
    assert (answered.session, answered.checkpoint) == ("s-1", "c-1")
    assert answered.answered_by == "rohithkandula19"


def test_yes_for_this_session_has_nothing_to_apply_to() -> None:
    """The run that asked has ended by the time anybody answers."""
    with pytest.raises(ValueError, match="nothing to apply to"):
        escalation().resolved(Outcome.YES_SESSION)
    with pytest.raises(ValueError, match="nothing to apply to"):
        escalation(state=EscalationState.ANSWERED, answer=Outcome.YES_SESSION)


def test_an_escalation_cannot_be_answered_twice() -> None:
    answered = escalation().resolved(Outcome.NO)
    with pytest.raises(ValueError, match="already answered"):
        answered.resolved(Outcome.YES_ONCE)


def test_an_expired_escalation_is_not_open_and_takes_no_answer() -> None:
    expired = escalation(state=EscalationState.EXPIRED)
    assert not expired.open
    with pytest.raises(ValueError, match="already expired"):
        expired.resolved(Outcome.YES_ONCE)


# --------------------------------------------------------------------------- #
# Effect — invariant 4, a key before a result
# --------------------------------------------------------------------------- #


def effect(**kwargs: object) -> Effect:
    base: dict[str, object] = {
        "retainer": "sentry",
        "summons": "sum-1",
        "step": "reply",
        "kind": EffectKind.COMMENT,
        "target": "rohithkandula19/Ronin#258",
    }
    base.update(kwargs)
    return Effect(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize("name", ["retainer", "summons", "step", "target"])
def test_every_part_of_the_ledger_key_is_required(name: str) -> None:
    with pytest.raises(ValueError, match=f"Effect.{name} is required"):
        effect(**{name: ""})


def test_a_retry_of_the_same_comment_is_the_same_effect() -> None:
    assert effect(body="green").key == effect(body="green").key


def test_an_edited_comment_is_a_different_effect() -> None:
    assert effect(body="green").key != effect(body="red").key


def test_the_same_body_from_a_different_step_is_a_different_effect() -> None:
    assert effect(step="reply").key != effect(step="summary").key


def test_the_kind_is_part_of_the_digest() -> None:
    assert effect(kind=EffectKind.COMMENT).digest != effect(kind=EffectKind.NOTIFY).digest


def test_the_key_names_who_acted_before_it_names_what_they_did() -> None:
    who, summons, step, digest = effect().key
    assert (who, summons, step) == ("sentry", "sum-1", "reply")
    assert digest == effect().digest
