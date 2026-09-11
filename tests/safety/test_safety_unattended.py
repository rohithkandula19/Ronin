"""The builtin allowlist, narrowed for a run with nobody attached.

`DEV_BINARIES` allows `npm`, `make`, `terraform`, `kubectl` and `helm` wholesale, which
is the right trade for somebody at a terminal — nobody wants a prompt before `npm test`
— and the wrong one for a Retainer working at 3am, because the same allowance covers
`npm publish`, `terraform destroy` and `kubectl delete`.

Three claims are worth pinning, and only the third is obvious:

1. **Each listed command moves `allow` → `ask`.** Both halves matter. Asserting only
   the `ask` would pass just as well if the command had always asked, and the table
   would then be documentation of a prohibition that already existed.
2. **The narrowings win by tying, not by being broader.** Precedence is specificity
   first, so a rule broader than the dev-binary allowance would *lose* to it, silently
   — the same trap a tool-wide `deny` falls into against a narrow `allow`. Equal
   specificity is load-bearing, so it is a test rather than a comment.
3. **Nothing safe moves.** An unattended agent that has to ask before running tests is
   an agent nobody deploys.
"""

from __future__ import annotations

from typing import Any

import pytest
from safety_harness import BASH, asker_of, engine, use

from ronin.safety.policy import (
    BUILTIN_SOURCE,
    UNATTENDED_ASK,
    UNATTENDED_SOURCE,
    Answer,
    CommandRegex,
    Decision,
    MatchTarget,
    Outcome,
    PolicyEngine,
    Rule,
    RuleSet,
    builtin_rules,
    unattended_rules,
)

#: Commands that resolve to `allow` as Ronin ships and to `ask` once the narrowings
#: apply. Every entry is a transition in both directions; `test_the_table_is_covered`
#: pins that the list exercises every pattern.
NARROWED = [
    "npm publish",
    "npm publish --access public",
    "npm unpublish --force",
    "yarn publish",
    "pnpm publish",
    "bun publish",
    "deno publish",
    "cargo publish",
    "poetry publish",
    "uv publish",
    "hatch publish",
    "gem push pkg-1.0.gem",
    "dotnet nuget push pkg.nupkg",
    "rake release",
    "bundle exec rake release",
    "mvn deploy",
    "mvn clean deploy -DskipTests",
    "gradle publish",
    "terraform apply",
    "terraform apply -auto-approve",
    "terraform destroy",
    "terraform import aws_s3_bucket.b b",
    "terraform taint aws_instance.web",
    "terraform state rm aws_s3_bucket.b",
    "terraform state mv a b",
    "terraform state push tf.state",
    "kubectl apply -f deploy.yaml",
    "kubectl delete pod web-1",
    "kubectl patch deploy web -p '{}'",
    "kubectl replace -f deploy.yaml",
    "kubectl scale deploy web --replicas=0",
    "kubectl drain node-1",
    "kubectl cordon node-1",
    "helm install rel ./chart",
    "helm upgrade rel ./chart",
    "helm uninstall rel",
    "helm rollback rel 1",
    "make deploy",
    "make release",
    "make publish",
    "make push",
    "make deploy-prod",
    "just deploy",
    "task release",
]

#: The work itself, which has to stay unprompted. One per binary the narrowings touch,
#: because the failure mode is a pattern that names the binary instead of the
#: subcommand and takes the test run down with the publish.
UNTOUCHED = [
    "npm test",
    "npm ci",
    "npm install",
    "npm run build",
    "npx tsc --noEmit",
    "yarn install",
    "pnpm install",
    "bun install",
    "deno test",
    "cargo test",
    "cargo build --release",
    "cargo fmt",
    "poetry install",
    "uv sync",
    "pip install -e .",
    "hatch env create",
    "pytest -q",
    "gem list",
    "dotnet build",
    "rake test",
    "bundle exec rake test",
    "bundle install",
    "mvn test",
    "mvn clean package",
    "gradle test",
    "terraform plan",
    "terraform validate",
    "terraform fmt",
    "terraform state list",
    "kubectl get pods",
    "kubectl describe pod web-1",
    "kubectl logs web-1",
    "helm list",
    "make",
    "make test",
    "make lint",
    "make coverage",
    "just test",
    "task build",
    "git status",
    "ls -la",
]


def narrowed_engine(*answers: Answer, **kwargs: Any) -> PolicyEngine:
    """A real engine carrying the shipped rules plus the narrowings, in that order."""
    return engine(
        *answers, rules=RuleSet(rules=builtin_rules()).with_rules(unattended_rules()), **kwargs
    )


def decide(policy: PolicyEngine, command: str) -> Decision:
    return policy.evaluate(BASH, use(command)).decision


# --------------------------------------------------------------------------- #
# What moves
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("command", NARROWED)
def test_a_narrowed_command_is_allowed_today_and_asked_unattended(command: str) -> None:
    """The `allow` half is not redundant: without it this test would still pass for a
    command that had always asked, and the table would be claiming credit for the
    default."""
    assert decide(engine(), command) is Decision.ALLOW, "not a narrowing — already asked"
    assert decide(narrowed_engine(), command) is Decision.ASK


@pytest.mark.parametrize("command", UNTOUCHED)
def test_the_work_itself_stays_unprompted(command: str) -> None:
    assert decide(engine(), command) is Decision.ALLOW
    assert decide(narrowed_engine(), command) is Decision.ALLOW


def test_the_make_heuristic_over_asks_on_purpose() -> None:
    """`make` targets are freeform, so the only signal is the target's name. Reading it
    loosely costs one escalation on `make publish-docs-check`; reading it strictly
    misses `make publish-prod`, which is the trade this narrowing exists to make."""
    assert decide(engine(), "make publish-docs-check") is Decision.ALLOW
    assert decide(narrowed_engine(), "make publish-docs-check") is Decision.ASK
    assert decide(narrowed_engine(), "make docs") is Decision.ALLOW


# --------------------------------------------------------------------------- #
# How they win
# --------------------------------------------------------------------------- #


def dev_binary_rule() -> Rule:
    """The shipped rule these narrowings have to beat."""
    matches = [rule for rule in builtin_rules() if rule.reason == "build/test toolchain"]
    assert len(matches) == 1, "the dev-binary allowance moved; this suite tracks it"
    return matches[0]


def test_a_narrowing_ties_with_the_allowance_it_narrows() -> None:
    """Specificity first, then most-restrictive. Tying is how these take effect at all:
    win on specificity and they would be a different rule; lose on it and they would
    never apply."""
    allowance = dev_binary_rule()
    assert allowance.specificity == (2, 1)
    assert all(rule.specificity == allowance.specificity for rule in unattended_rules())


def test_a_broader_narrowing_would_have_lost_silently() -> None:
    """Why the table names subcommands instead of saying "ask about the shell". This is
    the same trap a tool-wide `deny` falls into against a narrow `allow`, and it fails
    open, so it is pinned rather than described."""
    broad = Rule(
        tool="*",
        matcher=CommandRegex(pattern="publish"),
        decision=Decision.ASK,
        source=UNATTENDED_SOURCE,
        reason="too broad to take effect",
    )
    ruleset = RuleSet(rules=builtin_rules()).with_rules([broad])
    assert (
        ruleset.resolve(MatchTarget(tool="bash", arguments={"command": "npm publish"})).decision
        is Decision.ALLOW
    )


def test_the_narrowings_carry_their_own_provenance() -> None:
    """ "How Ronin ships" and "this applied because nobody was watching" are different
    facts, and an operator reading an audit trail needs to tell them apart."""
    verdict = narrowed_engine().evaluate(BASH, use("terraform destroy"))
    assert UNATTENDED_SOURCE in verdict.sources
    assert BUILTIN_SOURCE not in verdict.sources, "the narrowing is what decided this"
    assert "not recoverable" in verdict.reason


# --------------------------------------------------------------------------- #
# What cannot be waived
# --------------------------------------------------------------------------- #


def test_every_narrowing_is_an_unwaivable_ask_on_the_shell() -> None:
    for rule in unattended_rules():
        assert rule.tool == "bash"
        assert rule.decision is Decision.ASK
        assert rule.unwaivable is True, "a remembered yes to publishing is not a thing"
        assert rule.source == UNATTENDED_SOURCE
        assert rule.reason, "the prompt has to say why"


async def test_a_yes_to_publishing_cannot_be_remembered() -> None:
    """Following `git push`: the human may say yes to the call in front of them and may
    not write down "yes, publish to npm from now on"."""
    written: list[Rule] = []
    policy = narrowed_engine(Answer(outcome=Outcome.YES_PERSIST), persist=written.append)
    decision = await policy.approve(BASH, use("npm publish"), rendered="npm publish")
    assert decision.approved is True
    assert decision.remember is False
    assert written == []
    assert policy.session_rules == ()

    asker_of(policy).answers.append(Answer(outcome=Outcome.YES_ONCE))
    await policy.approve(BASH, use("npm publish"), rendered="npm publish")
    assert len(asker_of(policy).requests) == 2, "it must ask again"


def test_a_fresh_tuple_every_call() -> None:
    """A function rather than a constant, for the reason `builtin_rules` is one: a
    mutable global that softens policy in place is a security hole with a short fuse."""
    assert unattended_rules() == unattended_rules()
    assert unattended_rules() is not unattended_rules()


# --------------------------------------------------------------------------- #
# The table itself
# --------------------------------------------------------------------------- #


def test_the_table_is_covered() -> None:
    """Every pattern is exercised by `NARROWED`. Without this, adding an entry and no
    test — or leaving one behind after a binary is dropped — passes quietly."""
    for pattern, _ in UNATTENDED_ASK:
        matcher = CommandRegex(pattern=pattern)
        assert any(
            matcher.matches(MatchTarget(tool="bash", arguments={"command": command}))
            for command in NARROWED
        ), f"no command in NARROWED exercises {pattern!r}"


@pytest.mark.parametrize(
    "command",
    [
        "flit publish",
        "./gradlew publish",
        "docker push registry.test/app:1",
        "twine upload dist/*",
        "gh pr merge 12 --squash",
        "git push origin main",
    ],
)
def test_only_transitions_are_listed(command: str) -> None:
    """These already ask, because their binaries are not in `DEV_BINARIES`. Listing
    them would make the table look like it were doing work it is not, and the next
    reader would trust the wrong part of it."""
    assert decide(engine(), command) is not Decision.ALLOW
    assert not any(
        CommandRegex(pattern=pattern).matches(
            MatchTarget(tool="bash", arguments={"command": command})
        )
        for pattern, _ in UNATTENDED_ASK
    )
