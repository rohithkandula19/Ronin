"""Naming one capability in config must not silently reset the other five.

``_parse_capabilities`` in ``providers/router.py`` documented itself as merging
partial overrides against the adapter's defaults at build time. No merge existed.
It returned a fully-populated :class:`Capabilities` built from its *own* fallbacks
— ``prompt_cache=False``, ``thinking=False``, ``vision=False``,
``max_context=32_768`` — and the registry handed that whole object to the client,
which used it in place of everything the adapter knew about itself.

So a config that said one thing made the request do another, without a word:

* ``examples/models.toml`` sets ``thinking = true`` on DeepSeek-R1. That alone cut
  the window from the adapter's 128k to 32,768 — a quarter of the context, spent
  by a line that never mentioned context.
* ``max_context = 200000`` on an Anthropic model turned prompt caching, extended
  thinking and vision off. The one axis that *was* named is also the only one that
  survived.

The fix moves the merge to where the defaults are known. The spec now carries only
the keys the config named, as raw values, and
:func:`~ronin.providers.types.apply_capability_overrides` merges them over the
built client's own capabilities inside ``build_client`` — before the shim decision,
because ``native_tools = false`` is a request to shim.

These tests are written against the *built client*, not the parsed spec. The old
code parsed a spec that looked entirely reasonable in isolation; the damage only
appeared once it reached an adapter, which is where the assertions belong.
"""

from __future__ import annotations

import pytest

from ronin.providers.local_adapter import LOCAL_ADAPTER_DEFAULTS, build_local_client
from ronin.providers.registry import build_client
from ronin.providers.router import ModelSpec, parse_config
from ronin.providers.shim import ShimClient
from ronin.providers.types import (
    CAPABILITY_OVERRIDE_KEYS,
    Capabilities,
    ProviderError,
    apply_capability_overrides,
)

BASE = Capabilities(
    native_tools=True,
    parallel_tools=True,
    prompt_cache=True,
    thinking=True,
    max_context=200_000,
    vision=True,
)


def _spec(name: str = "m", provider: str = "anthropic", **kwargs: object) -> ModelSpec:
    return ModelSpec(name=name, provider=provider, model="x", **kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# the bug
# --------------------------------------------------------------------------- #


def test_setting_the_window_leaves_caching_thinking_and_vision_alone() -> None:
    """The reported symptom, at the only layer that can show it."""
    client = build_client(_spec(capability_overrides={"max_context": 120_000}))
    caps = client.capabilities()
    assert caps.max_context == 120_000
    assert caps.prompt_cache is True
    assert caps.thinking is True
    assert caps.vision is True


def test_the_deepseek_stanza_from_the_shipped_example_keeps_its_window() -> None:
    """``thinking = true`` used to cost three quarters of the context window.

    Pinned to the example config because that is where a user copies from: the
    entry is two lines long and neither of them is about context.
    """
    cfg = parse_config(
        {
            "roles": {"main": "deepseek-r1"},
            "models": {
                "deepseek-r1": {
                    "provider": "deepseek",
                    "model": "deepseek-reasoner",
                    "thinking": True,
                }
            },
        }
    )
    caps = build_client(cfg.models["deepseek-r1"]).capabilities()
    assert caps.thinking is True
    assert caps.max_context == 128_000  # the adapter's own, not the parser's 32,768


def test_declaring_nothing_changes_nothing() -> None:
    plain = build_client(_spec()).capabilities()
    empty = build_client(_spec(capability_overrides={})).capabilities()
    assert plain == empty


def test_every_capability_is_individually_settable() -> None:
    # Whatever the adapter says, each key can be moved on its own — otherwise the
    # override table is a list of things the config is allowed to *mention*.
    for key in CAPABILITY_OVERRIDE_KEYS:
        if key == "parallel_tools":
            continue  # not independent: Capabilities forbids it without native_tools
        value: object = 4096 if key == "max_context" else False
        caps = build_client(_spec(capability_overrides={key: value})).capabilities()
        assert getattr(caps, key) == value, key


# --------------------------------------------------------------------------- #
# the shim decision reads the merged value
# --------------------------------------------------------------------------- #


def test_turning_native_tools_off_in_config_shims_the_client() -> None:
    """The merge lands before the shim decision, or the config is ignored."""
    client = build_client(_spec(capability_overrides={"native_tools": False}))
    assert isinstance(client, ShimClient)


def test_turning_native_tools_off_also_turns_parallel_off() -> None:
    """An invariant the user never mentioned should not become their problem.

    :class:`Capabilities` refuses ``parallel_tools`` without ``native_tools`` — the
    format shim executes one tagged block at a time. Asking the config author to
    know that, and to restate a key they were not thinking about, would make the
    common override the one that errors.
    """
    caps = apply_capability_overrides(BASE, {"native_tools": False})
    assert caps.parallel_tools is False


def test_an_explicit_parallel_true_with_native_false_is_still_refused() -> None:
    # The courtesy above fills a *silence*. Saying both out loud is a contradiction,
    # and answering a contradiction by picking one side is worse than the error.
    with pytest.raises(ProviderError, match="contradictory"):
        apply_capability_overrides(BASE, {"native_tools": False, "parallel_tools": True})


# --------------------------------------------------------------------------- #
# bad values are refused, not coerced
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["false", "no", 0, 1, None])
def test_a_non_boolean_for_a_boolean_capability_is_refused(bad: object) -> None:
    """``bool("false")`` is ``True``.

    Coercing here would accept ``prompt_cache = "no"`` as *yes* — the exact class of
    silent disagreement between config and request that this module exists to end.
    """
    with pytest.raises(ProviderError, match="must be true or false"):
        apply_capability_overrides(BASE, {"prompt_cache": bad})


@pytest.mark.parametrize("bad", [True, 0, -1, "200000", 1.5])
def test_a_window_that_is_not_a_positive_integer_is_refused(bad: object) -> None:
    with pytest.raises(ProviderError, match="must be a positive integer"):
        apply_capability_overrides(BASE, {"max_context": bad})


def test_an_unrecognized_key_names_itself_and_the_model() -> None:
    """Silently dropping it would leave the user believing they granted something."""
    with pytest.raises(ProviderError) as caught:
        apply_capability_overrides(BASE, {"promt_cache": True}, model="claude")
    message = str(caught.value)
    assert "promt_cache" in message
    assert "claude" in message
    assert "prompt_cache" in message  # the settable keys are listed, so the typo is visible


def test_the_config_parser_never_produces_an_unrecognized_key() -> None:
    # Which is why a stray key in a TOML model table stays non-fatal: the parser
    # filters to the closed set, and only a programmatic caller can trip the error.
    cfg = parse_config(
        {
            "roles": {"main": "m"},
            "models": {"m": {"provider": "anthropic", "model": "x", "not_a_capability": True}},
        }
    )
    assert cfg.models["m"].capability_overrides == {}
    build_client(cfg.models["m"])  # would raise if the whole table were forwarded


# --------------------------------------------------------------------------- #
# both entry points, and doing it twice
# --------------------------------------------------------------------------- #


def test_the_offline_lane_honours_overrides_without_going_through_the_registry() -> None:
    """``build_local_client`` bypasses ``build_client`` by design, so it merges too."""
    spec = _spec(provider="local-adapter", capability_overrides={"max_context": 8192})
    assert LOCAL_ADAPTER_DEFAULTS.max_context != 8192
    assert build_local_client(spec).capabilities().max_context == 8192


def test_the_local_lane_reaches_the_same_answer_through_the_registry() -> None:
    spec = _spec(provider="local-adapter", capability_overrides={"max_context": 8192})
    through_registry = build_client(spec)
    assert through_registry.capabilities() == build_local_client(spec).capabilities()


def test_merging_twice_is_merging_once() -> None:
    """What makes it safe for a builder to merge and the registry to merge again."""
    overrides = {"native_tools": False, "max_context": 4096}
    once = apply_capability_overrides(BASE, overrides)
    assert apply_capability_overrides(once, overrides) == once


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
