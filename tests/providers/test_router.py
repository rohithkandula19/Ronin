"""Config → clients, and the rule that mechanical work never gets the main model.

The most important test in this file is
``test_subagents_and_compaction_cannot_reach_the_main_model``. Everything else here
is config parsing; that one is the money.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest

from ronin.providers import (
    Capabilities,
    ModelSpec,
    ProviderError,
    Role,
    Router,
    RouterConfig,
    load_config,
    parse_config,
)
from ronin.providers.base import ModelClient
from ronin.providers.registry import ADAPTERS, build_client, describe, with_capabilities
from ronin.providers.types import ModelDelta, ModelRequest

CONFIG_TOML = """
[roles]
main = "kimi-k2"
plan = "deepseek-r1"
fast = "qwen-coder-7b"

[models.kimi-k2]
provider = "moonshot"
model = "kimi-k2-0711-preview"
api_key_env = "MOONSHOT_API_KEY"
price_in = 0.6
price_out = 2.5

[models.deepseek-r1]
provider = "deepseek"
model = "deepseek-reasoner"
api_key_env = "DEEPSEEK_API_KEY"
thinking = true

[models.qwen-coder-7b]
provider = "mlx"
model = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
native_tools = false
max_context = 32768
"""


class StubClient:
    """A client that records nothing and streams nothing. Enough for routing."""

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec

    def capabilities(self) -> Capabilities:
        return self.spec.capabilities or Capabilities(
            native_tools=True,
            parallel_tools=True,
            prompt_cache=False,
            thinking=False,
            max_context=1000,
            vision=False,
        )

    async def stream(self, req: ModelRequest) -> AsyncIterator[ModelDelta]:
        del req
        if False:  # pragma: no cover - never streams; satisfies the generator type
            yield  # type: ignore[unreachable]


def stub_factory(
    spec: ModelSpec,
    *,
    transport: object = None,
    env: Mapping[str, str] | None = None,
) -> ModelClient:
    del transport, env
    return StubClient(spec)


def config() -> RouterConfig:
    import tomllib

    return parse_config(tomllib.loads(CONFIG_TOML))


# --------------------------------------------------------------------------- #
# The routing rule
# --------------------------------------------------------------------------- #


def test_subagents_and_compaction_cannot_reach_the_main_model() -> None:
    """Neither helper takes a role argument, so neither can be called wrong."""
    router = Router(config(), build=stub_factory)
    fast = router.client_for(Role.FAST)
    assert router.for_subagent() is fast
    assert router.for_compaction() is fast
    assert router.client_for(Role.MAIN) is not fast


def test_each_role_resolves_to_its_configured_model() -> None:
    router = Router(config(), build=stub_factory)
    assert router.spec_for(Role.MAIN).model == "kimi-k2-0711-preview"
    assert router.spec_for(Role.PLAN).model == "deepseek-reasoner"
    assert router.spec_for(Role.FAST).model.endswith("Qwen2.5-Coder-7B-Instruct-4bit")


def test_a_client_is_built_once_and_cached() -> None:
    router = Router(config(), build=stub_factory)
    assert router.client_for(Role.MAIN) is router.client_for(Role.MAIN)


def test_two_roles_on_the_same_model_share_one_client() -> None:
    cfg = RouterConfig(
        roles={Role.MAIN: "m", Role.FAST: "m"},
        models={"m": ModelSpec(name="m", provider="openai", model="gpt")},
    )
    router = Router(cfg, build=stub_factory)
    assert router.client_for(Role.MAIN) is router.client_for(Role.FAST)


def test_a_missing_role_falls_back_to_main_rather_than_crashing_mid_session() -> None:
    cfg = RouterConfig(
        roles={Role.MAIN: "m"},
        models={"m": ModelSpec(name="m", provider="openai", model="gpt")},
    )
    router = Router(cfg, build=stub_factory)
    assert router.spec_for(Role.FAST).name == "m"
    assert router.for_subagent() is router.client_for(Role.MAIN)


def test_there_are_exactly_three_roles() -> None:
    """A fourth role is a fourth chance to route expensive work by accident."""
    assert [role.value for role in Role] == ["main", "plan", "fast"]


# --------------------------------------------------------------------------- #
# Config parsing
# --------------------------------------------------------------------------- #


def test_a_config_file_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "models.toml"
    path.write_text(CONFIG_TOML)
    cfg = load_config(path)
    assert cfg.roles[Role.MAIN] == "kimi-k2"
    assert cfg.models["kimi-k2"].price_in == 0.6


def test_capability_overrides_are_read_from_config() -> None:
    caps = config().models["qwen-coder-7b"].capabilities
    assert caps is not None
    assert caps.native_tools is False
    assert caps.max_context == 32768


def test_a_model_with_no_capability_keys_leaves_the_adapter_default() -> None:
    assert config().models["kimi-k2"].capabilities is None


def test_declaring_thinking_alone_does_not_reset_the_other_axes() -> None:
    caps = config().models["deepseek-r1"].capabilities
    assert caps is not None
    assert caps.thinking is True
    assert caps.native_tools is True


def test_a_role_pointing_at_an_undefined_model_is_rejected_with_both_names() -> None:
    with pytest.raises(ValueError, match="which is not defined"):
        RouterConfig(
            roles={Role.MAIN: "ghost"},
            models={"real": ModelSpec(name="real", provider="openai", model="gpt")},
        )


def test_a_config_with_no_main_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="must map the 'main' role"):
        RouterConfig(
            roles={Role.FAST: "m"},
            models={"m": ModelSpec(name="m", provider="openai", model="gpt")},
        )


def test_an_unknown_role_name_names_the_valid_roles() -> None:
    with pytest.raises(ProviderError, match="unknown role"):
        parse_config({"roles": {"turbo": "m"}, "models": {"m": {"provider": "x", "model": "y"}}})


def test_a_config_with_no_models_section_is_rejected() -> None:
    with pytest.raises(ProviderError, match="no \\[models\\] section"):
        parse_config({"roles": {"main": "m"}})


def test_a_config_with_no_roles_section_is_rejected() -> None:
    with pytest.raises(ProviderError, match="no \\[roles\\] section"):
        parse_config({"models": {"m": {"provider": "x", "model": "y"}}})


def test_a_model_table_that_is_not_a_table_is_rejected() -> None:
    with pytest.raises(ProviderError, match="must be a table"):
        parse_config({"roles": {"main": "m"}, "models": {"m": "just a string"}})


def test_a_model_missing_its_provider_is_rejected() -> None:
    with pytest.raises(ValueError, match="must name a provider"):
        parse_config({"roles": {"main": "m"}, "models": {"m": {"model": "y"}}})


def test_a_model_missing_its_model_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="must name a model id"):
        parse_config({"roles": {"main": "m"}, "models": {"m": {"provider": "x"}}})


def test_a_missing_config_file_reports_the_path(tmp_path: Path) -> None:
    with pytest.raises(ProviderError, match="cannot read provider config"):
        load_config(tmp_path / "nope.toml")


def test_invalid_toml_reports_that_it_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("[roles\nmain =")
    with pytest.raises(ProviderError, match="not valid TOML"):
        load_config(path)


# --------------------------------------------------------------------------- #
# Keys stay in the environment
# --------------------------------------------------------------------------- #


def test_the_api_key_is_read_from_the_named_environment_variable() -> None:
    spec = config().models["kimi-k2"]
    assert spec.api_key({"MOONSHOT_API_KEY": "sk-secret"}) == "sk-secret"


def test_a_model_with_no_key_env_needs_no_key() -> None:
    spec = config().models["qwen-coder-7b"]
    assert spec.api_key({"ANYTHING": "x"}) == ""


def test_a_missing_environment_variable_yields_an_empty_key_not_a_crash() -> None:
    """A local endpoint is keyless; failing here would break the offline path."""
    assert config().models["kimi-k2"].api_key({}) == ""


def test_no_config_field_is_named_like_a_secret() -> None:
    """A key in a config file ends up in a git history. Only the *name* is stored."""
    fields = {f for f in ModelSpec.__dataclass_fields__}
    assert "api_key" not in fields
    assert "api_key_env" in fields


# --------------------------------------------------------------------------- #
# Pricing metadata
# --------------------------------------------------------------------------- #


def test_a_model_with_prices_is_priced_and_one_without_is_not() -> None:
    cfg = config()
    assert cfg.models["kimi-k2"].priced
    assert not cfg.models["qwen-coder-7b"].priced


# --------------------------------------------------------------------------- #
# The registry
# --------------------------------------------------------------------------- #


def test_every_openai_compatible_host_shares_one_adapter() -> None:
    """Eight hosts, one implementation — the claim the package is built on."""
    from ronin.providers.registry import _build_openai_compat

    shared = [name for name, builder in ADAPTERS.items() if builder is _build_openai_compat]
    for host in ("openai", "deepseek", "together", "groq", "openrouter", "ollama", "vllm"):
        assert host in shared


def test_an_unknown_provider_lists_the_known_ones() -> None:
    spec = ModelSpec(name="x", provider="hal9000", model="m")
    with pytest.raises(ProviderError, match="known providers"):
        build_client(spec)


def test_an_openai_compatible_model_with_no_base_url_is_rejected() -> None:
    spec = ModelSpec(name="x", provider="openai-compatible", model="m")
    with pytest.raises(ProviderError, match="has to say where it is"):
        build_client(spec)


def test_a_model_without_native_tools_is_wrapped_in_the_shim_automatically() -> None:
    """The shim decision is made from capabilities, never by a human remembering."""
    from ronin.providers.shim import ShimClient

    spec = ModelSpec(
        name="local",
        provider="mlx",
        model="q",
        capabilities=Capabilities(
            native_tools=False,
            parallel_tools=False,
            prompt_cache=False,
            thinking=False,
            max_context=8192,
            vision=False,
        ),
    )
    assert isinstance(build_client(spec), ShimClient)


def test_a_model_with_native_tools_is_not_wrapped() -> None:
    from ronin.providers.shim import ShimClient

    spec = ModelSpec(name="claude", provider="anthropic", model="claude-x")
    assert not isinstance(build_client(spec), ShimClient)


def test_the_mlx_builder_needs_no_transport() -> None:
    client = build_client(ModelSpec(name="local", provider="mlx", model="q"))
    assert client.capabilities().native_tools is False


def test_describe_names_the_wiring_including_whether_it_is_priced() -> None:
    spec = config().models["kimi-k2"]
    line = describe(spec, StubClient(spec))
    assert "kimi-k2" in line
    assert "moonshot" in line
    assert "priced" in line


def test_describe_says_shimmed_for_a_model_without_native_tools() -> None:
    spec = config().models["qwen-coder-7b"]
    assert "shimmed-tools" in describe(spec, StubClient(spec))


def test_capabilities_cannot_be_re_declared_on_an_already_shimmed_client() -> None:
    spec = ModelSpec(name="local", provider="mlx", model="q")
    shimmed = build_client(spec)
    with pytest.raises(ProviderError, match="already-shimmed"):
        with_capabilities(shimmed, StubClient(spec).capabilities())


def test_with_capabilities_overrides_what_a_client_reports() -> None:
    spec = ModelSpec(name="x", provider="anthropic", model="claude-x")
    client = build_client(spec)
    narrowed = with_capabilities(
        client,
        Capabilities(
            native_tools=True,
            parallel_tools=False,
            prompt_cache=False,
            thinking=False,
            max_context=4096,
            vision=False,
        ),
    )
    assert narrowed.capabilities().max_context == 4096
    assert client.capabilities().max_context == 200_000


# --------------------------------------------------------------------------- #
# Capabilities invariants
# --------------------------------------------------------------------------- #


def test_parallel_tools_without_native_tools_is_rejected() -> None:
    """The shim is sequential; claiming parallel dispatch would licence a reorder."""
    with pytest.raises(ValueError, match="parallel_tools requires native_tools"):
        Capabilities(
            native_tools=False,
            parallel_tools=True,
            prompt_cache=False,
            thinking=False,
            max_context=1000,
            vision=False,
        )


def test_a_non_positive_context_window_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_context must be positive"):
        Capabilities(
            native_tools=True,
            parallel_tools=False,
            prompt_cache=False,
            thinking=False,
            max_context=0,
            vision=False,
        )


def test_the_conservative_default_promises_nothing() -> None:
    from ronin.providers import CONSERVATIVE

    assert not CONSERVATIVE.native_tools
    assert not CONSERVATIVE.parallel_tools
    assert not CONSERVATIVE.prompt_cache
    assert not CONSERVATIVE.vision
