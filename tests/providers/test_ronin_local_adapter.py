"""``--model ronin-qwen-local`` resolves through the real registry, with no ML stack.

The local adapter had a complete client, a spec builder and a whole-router config, and
was **not in** :data:`ronin.providers.registry.ADAPTERS` — so a config naming
``provider = "local-adapter"`` failed with "unknown provider", and the offline lane
existed only for a caller willing to pass ``build=build_local_client`` by hand. That is
the one lane that costs nothing to run, which makes it the only way to exercise the eval
suite under a $0 constraint, so "wired in" is the difference between the suite being
runnable and not.

No ML stack is imported anywhere below. ``LocalAdapterClient`` takes a ``generate``
seam, and construction is otherwise lazy, so the whole route → config → client path is
covered on a machine with neither ``mlx_lm`` nor ``transformers`` — which is what CI is.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ronin.providers import build_client, describe
from ronin.providers.local_adapter import (
    ADAPTER_PATH_ENV,
    BASE_MODEL_HF,
    BASE_MODEL_MLX,
    LOCAL_MODEL_NAME,
    LOCAL_PROVIDER,
    Backend,
    LocalAdapterClient,
    adapter_problems,
    local_adapter_spec,
    local_router_config,
)
from ronin.providers.mlx_local import Generator
from ronin.providers.registry import ADAPTERS
from ronin.providers.router import ModelSpec, Role, Router, RouterConfig, parse_config
from ronin.providers.shim import ShimClient
from ronin.providers.types import ProviderError


def generator(*chunks: str) -> Generator:
    """A ``Generator`` seam: text out, no model in.

    Typed as the real alias rather than ``object`` so the signature is checked here —
    a fake whose shape has drifted from the seam it stands in for tests nothing.
    """

    def generate(prompt: str, max_tokens: int) -> Iterator[str]:
        del prompt, max_tokens
        yield from chunks

    return generate


def unwrap(client: object) -> object:
    """The innermost real client, through however many decorators wrap it.

    A ``Router`` returns ``RetryingClient(ShimClient(LocalAdapterClient))`` — three
    layers, and the count is not something a test should hard-code, because adding a
    decorator is a legitimate change that must not break every provider test. What
    matters is which client is at the bottom.
    """
    node = client
    for _ in range(8):
        nxt = getattr(node, "_inner", None) or getattr(node, "_client", None)
        if nxt is None:
            return node
        node = nxt
    raise AssertionError("client decorators nest more than 8 deep; that is a bug")


def shim_layers(client: object) -> int:
    """How many ``ShimClient`` wrappers are in the chain. Must be exactly one."""
    count, node = 0, client
    for _ in range(8):
        if isinstance(node, ShimClient):
            count += 1
        nxt = getattr(node, "_inner", None) or getattr(node, "_client", None)
        if nxt is None:
            break
        node = nxt
    return count


# --------------------------------------------------------------------------- #
# The registration itself
# --------------------------------------------------------------------------- #


def test_the_provider_is_in_the_registry() -> None:
    """The one-line omission this file exists for."""
    assert LOCAL_PROVIDER in ADAPTERS
    assert LOCAL_PROVIDER == "local-adapter"


def test_the_doctor_line_names_the_provider_and_calls_it_unpriced() -> None:
    """``describe`` is what ``doctor`` prints for a wired role.

    "unpriced" is the honest word for a model that costs nothing, and it is the one
    a reader needs in order to trust a cost report that shows zero.
    """
    spec = local_adapter_spec(env={})
    line = describe(spec, build_client(spec, transport=None, env={}))
    assert LOCAL_PROVIDER in line
    assert LOCAL_MODEL_NAME in line
    assert "unpriced" in line
    assert "shimmed-tools" in line


def test_a_config_naming_the_provider_builds_a_client() -> None:
    """Before the registration this raised "unknown provider"."""
    spec = local_adapter_spec(env={})
    client = build_client(spec, transport=None, env={})
    assert client is not None


def test_the_built_client_is_shimmed_exactly_once() -> None:
    """Double-shimming emits the tool-call tags twice and parses neither.

    ``build_client`` applies the shim from capabilities, so the registry builder must
    return the *unshimmed* client — which is why the entry forwards to
    ``build_local_adapter`` and not to ``build_local_client``, whose whole job is to
    shim. Counted across the chain rather than checked one layer down, because the
    Router adds a ``RetryingClient`` on top and the layer count is not the point.
    """
    spec = local_adapter_spec(env={})
    client = build_client(spec, transport=None, env={})
    assert shim_layers(client) == 1, "a non-native-tools model is shimmed exactly once"
    assert getattr(unwrap(client), "provider_name", "") == LOCAL_PROVIDER


# --------------------------------------------------------------------------- #
# The spec
# --------------------------------------------------------------------------- #


def test_the_spec_is_free_and_that_is_not_a_placeholder() -> None:
    """Zero price is the truth here, which is why the lane exists at all.

    Everywhere else in this codebase an unknown price is refused rather than guessed;
    this is the one model where 0.0 is a measurement.
    """
    spec = local_adapter_spec(env={})
    assert spec.name == LOCAL_MODEL_NAME
    assert spec.provider == LOCAL_PROVIDER
    assert spec.price_in == 0.0
    assert spec.price_out == 0.0
    assert spec.priced is False


@pytest.mark.parametrize(
    ("backend", "expected"),
    [(Backend.MLX, BASE_MODEL_MLX), (Backend.HF, BASE_MODEL_HF)],
    ids=["mlx", "hf"],
)
def test_the_base_model_follows_the_backend(backend: Backend, expected: str) -> None:
    """A 4-bit MLX repo cannot be loaded by transformers, or the reverse."""
    assert local_adapter_spec(backend=backend, env={}).model == expected


def test_the_backend_choice_survives_into_the_client() -> None:
    """Carried in ``extra_body`` through the registry, which only sees a ``ModelSpec``.

    If it were dropped, a Linux box would silently try to load MLX weights.
    """
    spec = local_adapter_spec(backend=Backend.HF, env={})
    inner = unwrap(build_client(spec, transport=None, env={}))
    assert isinstance(inner, LocalAdapterClient)
    assert inner.backend is Backend.HF
    assert inner.model == BASE_MODEL_HF


def test_the_adapter_path_comes_from_the_environment_when_not_given() -> None:
    """``RONIN_ADAPTER=...`` is how a trained checkpoint is pointed at."""
    spec = local_adapter_spec(env={ADAPTER_PATH_ENV: "/checkpoints/run-3"})
    assert spec.adapter_path == "/checkpoints/run-3"


# --------------------------------------------------------------------------- #
# Routing, through the ordinary Router
# --------------------------------------------------------------------------- #


LOCAL_TOML = f"""
[roles]
main = "{LOCAL_MODEL_NAME}"

[models.{LOCAL_MODEL_NAME}]
provider = "{LOCAL_PROVIDER}"
model = "{BASE_MODEL_HF}"
"""


def test_a_hand_written_toml_resolves_through_the_normal_router() -> None:
    """The end-to-end claim: a user's models.toml, the stock Router, no ``build=``.

    This is what `ronin eval --model ronin-qwen-local` depends on, and it is the thing
    that was impossible before the registry entry.
    """
    import tomllib

    config = parse_config(tomllib.loads(LOCAL_TOML))
    router = Router(config, env={})
    client = router.client_for(Role.MAIN)
    assert client is not None
    assert getattr(unwrap(client), "provider_name", "") == LOCAL_PROVIDER
    assert shim_layers(client) == 1


def test_no_api_key_is_required() -> None:
    """The whole point of the lane. An empty environment must be enough.

    Every other provider raises on a missing key; a local model that demanded one
    would be a contradiction, and this pins that it does not.
    """
    import tomllib

    router = Router(parse_config(tomllib.loads(LOCAL_TOML)), env={})
    assert router.client_for(Role.MAIN) is not None
    assert router.spec_for(Role.MAIN).api_key({}) == ""


def test_the_whole_router_config_points_every_role_at_the_local_model() -> None:
    """A config that routes ``fast`` elsewhere needs a key, defeating the purpose."""
    config = local_router_config(env={})
    assert isinstance(config, RouterConfig)
    for role in (Role.MAIN, Role.PLAN, Role.FAST):
        assert config.spec_for(role).provider == LOCAL_PROVIDER


# --------------------------------------------------------------------------- #
# Failing closed
# --------------------------------------------------------------------------- #


def test_a_half_finished_checkpoint_is_refused_at_construction() -> None:
    """Serving the base model when the adapter was asked for fakes a measurement.

    That number would read as the fine-tune's score and would not be one, which is the
    single most damaging thing this lane could do quietly.
    """
    with pytest.raises(ProviderError) as caught:
        LocalAdapterClient(adapter_path="/definitely/not/here", generate=generator("x"))
    assert "does not exist" in str(caught.value)
    assert ADAPTER_PATH_ENV in str(caught.value)


def test_a_config_without_weights_is_named_as_half_finished(tmp_path: Path) -> None:
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    problems = adapter_problems(tmp_path)
    assert problems
    assert "half-finished checkpoint" in " ".join(problems)


def test_a_complete_checkpoint_is_accepted(tmp_path: Path) -> None:
    """The positive case, so the guard above is not merely rejecting everything."""
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")
    (tmp_path / "adapters.safetensors").write_bytes(b"\x00")
    assert adapter_problems(tmp_path) == ()
    client = LocalAdapterClient(adapter_path=str(tmp_path), generate=generator("hi"))
    assert client.backend is not None


def test_an_unknown_backend_in_the_environment_is_reported_not_guessed() -> None:
    from ronin.providers.local_adapter import BACKEND_ENV, resolve_backend

    with pytest.raises(ProviderError) as caught:
        resolve_backend({BACKEND_ENV: "cuda-maybe"})
    assert "known backends" in str(caught.value)


# --------------------------------------------------------------------------- #
# Reaching it the way the CLI does
# --------------------------------------------------------------------------- #


def test_the_cli_resolves_the_model_name_against_a_local_config(tmp_path: Path) -> None:
    """``ronin eval --model ronin-qwen-local`` end to end through ``bench.router_for``.

    Written to disk rather than injected: the branch under test is the one that loads a
    config and promotes the named model, which is what a user actually hits.
    """
    from ronin.cli.bench import BenchOptions, Failed, router_for
    from ronin.cli.spine import Paths

    (tmp_path / ".git").mkdir()
    ronin_dir = tmp_path / ".ronin"
    ronin_dir.mkdir()
    (ronin_dir / "models.toml").write_text(LOCAL_TOML, encoding="utf-8")

    built = router_for(BenchOptions(suite=tmp_path), Paths.discover(tmp_path), {}, LOCAL_MODEL_NAME)
    assert not isinstance(built, Failed), built
    assert built.spec_for(Role.MAIN).provider == LOCAL_PROVIDER
    assert built.spec_for(Role.MAIN).priced is False


def test_the_shipped_example_config_parses_and_routes_keylessly() -> None:
    """``examples/models.local.toml`` is the file a user copies. It must work.

    A broken example is worse than none: the person following it has no way to tell
    whether they mistyped or the file was always wrong. Every role is checked, not just
    ``main`` — ``fast`` carries subagents and compaction, so a config that quietly
    needs a key there fails partway into a session rather than at startup.
    """
    import tomllib

    example = Path(__file__).resolve().parents[2] / "examples" / "models.local.toml"
    assert example.is_file(), f"{example} is referenced by the docs and must exist"

    config = parse_config(tomllib.loads(example.read_text(encoding="utf-8")))
    router = Router(config, env={})
    for role in (Role.MAIN, Role.PLAN, Role.FAST):
        spec = router.spec_for(role)
        assert spec.provider == LOCAL_PROVIDER, f"{role.value} is not on the local lane"
        assert spec.api_key({}) == "", f"{role.value} wants a key"
        assert router.client_for(role) is not None


def test_a_spec_the_registry_cannot_build_still_names_the_provider() -> None:
    """Regression guard for the failure this fixed: the message must name the provider."""
    spec = ModelSpec(name="x", provider="not-a-real-provider", model="m")
    with pytest.raises(ProviderError) as caught:
        build_client(spec, transport=None, env={})
    assert "not-a-real-provider" in str(caught.value)
