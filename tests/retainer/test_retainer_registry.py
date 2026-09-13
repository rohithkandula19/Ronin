"""Reading the file a human edits to say who serves here.

Two kinds of test, and the second kind is the point. The first checks the schema
parses. The second checks that a *malformed* file is refused loudly and in its
own terms — because this file compiles into a `PolicyEngine` ruleset, and the
failure mode of a permission config is never a crash, it is a rule that quietly
means something broader than it reads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ronin.retainer.model import Capability
from ronin.retainer.registry import (
    REGISTRY_FILENAME,
    Registry,
    RegistryError,
    load_registry,
    parse_registry,
    registry_path,
)
from ronin.safety.policy import Decision

HOME = Path("/home/dev/.ronin")


def document(**overrides: Any) -> dict[str, Any]:
    """A registry that parses, so each test can break exactly one thing."""
    base: dict[str, Any] = {
        "deployment": {"name": "laptop", "capabilities": ["shell", "network"]},
        "posts": {"identity": {"name": "Ronin Bot", "email": "bot@example.com"}},
        "retainers": [
            {
                "id": "ci-keeper",
                "name": "CI Keeper",
                "repo": "rohithkandula19/Ronin",
                "channels": ["github"],
                "acts_as": "ronin-bot",
                "orders": {
                    "brief": "keep CI green",
                    "tools": ["read", "bash"],
                    "grants": [{"tool": "bash", "decision": "allow", "command": "^pytest"}],
                    "wants": ["shell"],
                },
            }
        ],
    }
    return base | overrides


def parsed(**overrides: Any) -> Registry:
    return parse_registry(document(**overrides), home=HOME)


def refusal(**overrides: Any) -> str:
    with pytest.raises(RegistryError) as caught:
        parsed(**overrides)
    return str(caught.value)


# --------------------------------------------------------------------------- #
# What a good file means
# --------------------------------------------------------------------------- #


def test_a_registry_describes_one_deployment_and_who_serves_on_it() -> None:
    registry = parsed()
    assert registry.deployment.name == "laptop"
    assert registry.deployment.capabilities == frozenset({Capability.SHELL, Capability.NETWORK})
    assert registry.names == ("ci-keeper",)
    assert registry.retainers["ci-keeper"].acts_as == "ronin-bot"


def test_a_workspace_is_derived_rather_than_written_out() -> None:
    """`workspace_for` already answers this, and two answers drift. Writing the
    path by hand is still allowed, because a post adopted from elsewhere is real."""
    assert parsed().retainers["ci-keeper"].post.workspace == (
        HOME / "posts" / "ci-keeper" / "rohithkandula19" / "Ronin"
    )


def test_an_explicit_workspace_wins_and_expands_a_tilde() -> None:
    registry = parsed(
        retainers=[
            {
                "id": "ci-keeper",
                "name": "CI Keeper",
                "repo": "o/n",
                "workspace": "~/elsewhere/repo",
            }
        ]
    )
    workspace = registry.retainers["ci-keeper"].post.workspace
    assert workspace == Path.home() / "elsewhere" / "repo"


def test_grants_use_the_syntax_settings_json_already_uses() -> None:
    """One permission language. A second one is how the two drift apart."""
    grant = parsed().retainers["ci-keeper"].orders.grants[0]
    assert grant.specificity == (2, 1), "the narrow grant stayed narrow"
    assert grant.source == "standing orders"


def test_the_floor_is_ask_unless_the_file_says_otherwise() -> None:
    assert parsed().retainers["ci-keeper"].orders.default is Decision.ASK
    strict = parsed(
        retainers=[
            {"id": "r", "name": "R", "repo": "o/n", "orders": {"default": "deny"}},
        ]
    )
    assert strict.retainers["r"].orders.default is Decision.DENY


def test_budgets_default_and_override_one_field_at_a_time() -> None:
    registry = parsed(
        retainers=[
            {"id": "r", "name": "R", "repo": "o/n", "orders": {"budgets": {"iterations": 5}}},
        ]
    )
    budgets = registry.retainers["r"].orders.budgets
    assert budgets.iterations == 5
    assert budgets.tokens == 200_000, "an unnamed budget keeps its default"


def test_posts_root_defaults_under_the_ronin_home() -> None:
    """A daemon's working directory is not a durable place to check out a repo."""
    assert parsed().posts_root == HOME / "posts"
    assert parsed(posts={"root": "/srv/posts", **document()["posts"]}).posts_root == Path(
        "/srv/posts"
    )


# --------------------------------------------------------------------------- #
# What a bad file means
# --------------------------------------------------------------------------- #


def test_a_grant_that_would_widen_silently_is_refused_here_too() -> None:
    """The registry inherits `parse_rule`'s narrowing, and says which grant.

    "a bad rule somewhere in this file" is not actionable when the file holds a
    fleet, so the index is part of the message.
    """
    message = refusal(
        retainers=[
            {
                "id": "r",
                "name": "R",
                "repo": "o/n",
                "orders": {
                    "grants": [
                        {"tool": "bash", "decision": "allow", "command": "^pytest"},
                        {"tool": "bash", "decision": "allow", "match": {"command": "^pytest"}},
                    ]
                },
            }
        ]
    )
    assert "grants[1]" in message
    assert "does not read" in message


def test_an_unknown_top_level_key_is_refused_rather_than_ignored() -> None:
    """A typo'd "retainer" that is silently dropped is a deployment that starts
    with nobody in it and no reason given."""
    assert "unknown key" in refusal(retainer=[])


def test_an_unknown_capability_names_the_ones_that_exist() -> None:
    message = refusal(deployment={"name": "laptop", "capabilities": ["net"]})
    assert "'net'" in message
    assert "network" in message, "the fix is one word; the error has to contain it"


def test_a_hosted_deployment_cannot_hold_the_browser() -> None:
    """The record's own invariant, surfaced verbatim rather than restated."""
    message = refusal(
        deployment={"name": "fly", "hosted": True, "capabilities": ["shell", "browser"]}
    )
    assert "browser" in message


def test_one_string_is_not_a_list_of_one_string() -> None:
    """`"tools": "bash"` would otherwise be the four tools b, a, s and h."""
    message = refusal(
        retainers=[{"id": "r", "name": "R", "repo": "o/n", "orders": {"tools": "bash"}}]
    )
    assert "not one string" in message


def test_two_records_claiming_one_id_is_refused() -> None:
    """Last-wins would silently drop the first one's orders."""
    twice = [
        {"id": "r", "name": "First", "repo": "o/n"},
        {"id": "r", "name": "Second", "repo": "o/n"},
    ]
    assert "already defined" in refusal(retainers=twice)


def test_a_blanket_allow_in_standing_orders_is_still_refused() -> None:
    message = refusal(
        retainers=[
            {
                "id": "r",
                "name": "R",
                "repo": "o/n",
                "orders": {"grants": [{"tool": "*", "decision": "allow"}]},
            }
        ]
    )
    assert "blanket allow" in message


def test_an_unusable_id_is_refused_down_both_paths() -> None:
    """Two different validators, depending on one optional key.

    Without a `workspace` the id becomes a directory name and `workspace_for`
    judges it; with one, nothing derives a path and `Retainer.__post_init__`'s
    slug check is the only thing left. A refusal that only held on one path
    would be a hole the config could walk through by naming a workspace.
    """
    derived = refusal(retainers=[{"id": "Not A Slug", "name": "R", "repo": "o/n"}])
    assert "'Not A Slug'" in derived
    assert "directory name" in derived

    explicit = refusal(
        retainers=[{"id": "Not A Slug", "name": "R", "repo": "o/n", "workspace": "/tmp/w"}]
    )
    assert "slug" in explicit


def test_a_repo_that_is_not_owner_slash_name_is_refused() -> None:
    assert "owner/name" in refusal(retainers=[{"id": "r", "name": "R", "repo": "justaname"}])


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"retainers": "nope"}, "must be a list"),
        ({"retainers": [{"name": "R", "repo": "o/n"}]}, "id"),
        ({"retainers": [{"id": "r", "repo": "o/n"}]}, "name"),
        ({"retainers": [{"id": "r", "name": "R"}]}, "repo"),
        ({"deployment": {"capabilities": []}}, "name"),
        ({"posts": {"identity": {"name": "x"}}}, "email"),
        (
            {"retainers": [{"id": "r", "name": "R", "repo": "o/n", "orders": {"default": "yes"}}]},
            "must be one of",
        ),
        (
            {
                "retainers": [
                    {
                        "id": "r",
                        "name": "R",
                        "repo": "o/n",
                        "orders": {"budgets": {"iterations": 0}},
                    }
                ]
            },
            "positive integer",
        ),
        (
            {
                "retainers": [
                    {
                        "id": "r",
                        "name": "R",
                        "repo": "o/n",
                        "orders": {"budgets": {"iterations": True}},
                    }
                ]
            },
            "positive integer",
        ),
        (
            {
                "retainers": [
                    {"id": "r", "name": "R", "repo": "o/n", "orders": {"budgets": {"x": 1}}}
                ]
            },
            "unknown budget",
        ),
        ({"retainers": [{"id": "r", "name": "R", "repo": "o/n", "channels": ["irc"]}]}, "unknown"),
    ],
)
def test_a_malformed_registry_says_where_it_went_wrong(
    overrides: dict[str, Any], fragment: str
) -> None:
    assert fragment in refusal(**overrides)


def test_a_bool_is_not_an_iteration_budget() -> None:
    """`bool` is an `int` in Python, so `"iterations": true` would be one."""
    assert "positive integer" in refusal(
        retainers=[
            {"id": "r", "name": "R", "repo": "o/n", "orders": {"budgets": {"iterations": True}}}
        ]
    )


# --------------------------------------------------------------------------- #
# Reading it off a disk
# --------------------------------------------------------------------------- #


def test_a_registry_loads_from_the_ronin_home(tmp_path: Path) -> None:
    (tmp_path / REGISTRY_FILENAME).write_text(json.dumps(document()), encoding="utf-8")
    assert load_registry(tmp_path).names == ("ci-keeper",)
    assert registry_path(tmp_path).name == REGISTRY_FILENAME


def test_an_explicit_path_overrides_the_home(tmp_path: Path) -> None:
    elsewhere = tmp_path / "fleet.json"
    elsewhere.write_text(json.dumps(document()), encoding="utf-8")
    assert load_registry(tmp_path / "unused", path=elsewhere).names == ("ci-keeper",)


def test_a_missing_registry_names_where_it_looked(tmp_path: Path) -> None:
    """Not an empty registry: a daemon that starts with nobody in it looks
    identical to one whose config did not deploy."""
    with pytest.raises(RegistryError, match="no retainer registry at"):
        load_registry(tmp_path)


def test_malformed_json_says_so_rather_than_raising_a_decode_error(tmp_path: Path) -> None:
    (tmp_path / REGISTRY_FILENAME).write_text("{ nope", encoding="utf-8")
    with pytest.raises(RegistryError, match="not valid JSON"):
        load_registry(tmp_path)


def test_a_registry_keyed_against_itself_is_refused() -> None:
    """`Registry` is constructible directly, and a mapping whose keys disagree
    with the records in it is a lookup that silently finds the wrong Retainer."""
    registry = parsed()
    record = registry.retainers["ci-keeper"]
    with pytest.raises(RegistryError, match="identifies as"):
        Registry(
            deployment=registry.deployment,
            retainers={"someone-else": record},
            posts_root=registry.posts_root,
            identity=registry.identity,
        )
