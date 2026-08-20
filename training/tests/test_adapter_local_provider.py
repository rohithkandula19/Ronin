"""``ronin --model ronin-qwen-local``: the route, the config, and the failure messages.

No model is ever loaded here. Generation is injected, so what is under test is
everything around it — the route through the real router and registry, the adapter
directory check, the ChatML rendering, and the named errors the two heavy backends
degrade to. The messages are asserted, not the tracebacks: a user who sees an
``ImportError`` traceback out of a provider cannot tell it from a broken install.
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from ronin.core.types import Message, Text, ToolResultBlock, ToolUse
from ronin.core.types import Role as MessageRole
from ronin.providers.local_adapter import (
    ADAPTER_CONFIG_FILE,
    ADAPTER_PATH_ENV,
    BACKEND_ENV,
    BASE_MODEL_HF,
    BASE_MODEL_MLX,
    LOCAL_MODEL_NAME,
    LOCAL_PROVIDER,
    Backend,
    LocalAdapterClient,
    adapter_problems,
    build_local_adapter,
    build_local_client,
    describe_adapter,
    local_adapter_spec,
    local_router_config,
    render_chatml,
    resolve_backend,
    resolve_model_flag,
)
from ronin.providers.registry import ADAPTERS, build_client
from ronin.providers.resilience import RetryingClient
from ronin.providers.router import Role, Router
from ronin.providers.shim import CLOSE_TAG, OPEN_TAG, ShimClient
from ronin.providers.types import Completed, ModelRequest, ProviderError


def _fake_generator(text: str) -> object:
    def generate(prompt: str, max_tokens: int) -> Iterator[str]:
        del prompt, max_tokens
        for chunk in text.split(" "):
            yield chunk + " "

    return generate


def _adapter_dir(root: Path, *, complete: bool = True) -> Path:
    directory = root / "adapters"
    directory.mkdir(parents=True)
    (directory / ADAPTER_CONFIG_FILE).write_text("{}", encoding="utf-8")
    if complete:
        (directory / "adapters.safetensors").write_bytes(b"not really weights")
    return directory


# ------------------------------------------------------------------- the route


def test_the_local_model_name_resolves_to_a_router_config() -> None:
    config = resolve_model_flag(LOCAL_MODEL_NAME, env={})
    assert config is not None
    assert set(config.roles) == {Role.MAIN, Role.PLAN, Role.FAST}
    assert all(name == LOCAL_MODEL_NAME for name in config.roles.values())
    spec = config.spec_for(Role.MAIN)
    assert spec.provider == LOCAL_PROVIDER
    assert spec.model == BASE_MODEL_MLX


def test_every_role_points_at_the_local_model_so_subagents_still_work() -> None:
    """A config that routes ``fast`` elsewhere turns every subagent into an error."""
    config = local_router_config()
    assert config.spec_for(Role.FAST).name == LOCAL_MODEL_NAME
    assert config.spec_for(Role.PLAN).name == LOCAL_MODEL_NAME


def test_another_model_name_falls_through_rather_than_raising() -> None:
    """The flag is not owned by this module — only one of its values is."""
    assert resolve_model_flag("kimi-k2") is None
    assert resolve_model_flag("") is None


def test_the_provider_resolves_through_the_real_registry_and_router(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One line in ``registry.ADAPTERS`` is the whole wire-in; this proves it works."""
    monkeypatch.setitem(ADAPTERS, LOCAL_PROVIDER, build_local_adapter)
    router = Router(local_router_config(), env={})
    client = router.client_for(Role.MAIN)
    assert isinstance(client, RetryingClient)
    assert client.capabilities().max_context > 0
    # same client for every role, because they all name the same model
    assert router.client_for(Role.FAST).capabilities() == client.capabilities()


def test_the_registry_path_shims_the_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Local weights have no tool-calling head, so the shim must be applied for us."""
    monkeypatch.setitem(ADAPTERS, LOCAL_PROVIDER, build_local_adapter)
    client = build_client(local_adapter_spec(), env={})
    assert isinstance(client, ShimClient)


def test_the_standalone_factory_shims_without_needing_a_registry_entry() -> None:
    client = build_local_client(local_adapter_spec(), env={})
    assert isinstance(client, ShimClient)


def test_the_router_reaches_the_local_lane_with_no_registry_edit() -> None:
    router = Router(local_router_config(), env={}, build=build_local_client)
    assert isinstance(router.client_for(Role.MAIN), RetryingClient)


# ---------------------------------------------------------------- backends and env


def test_the_backend_defaults_to_mlx_and_reads_an_injected_env() -> None:
    assert resolve_backend({}) is Backend.MLX
    assert resolve_backend(None) is Backend.MLX
    assert resolve_backend({BACKEND_ENV: "hf"}) is Backend.HF
    assert resolve_backend({BACKEND_ENV: " MLX "}) is Backend.MLX


def test_an_unknown_backend_fails_with_a_named_error() -> None:
    with pytest.raises(ProviderError) as caught:
        resolve_backend({BACKEND_ENV: "vllm"})
    message = str(caught.value)
    assert BACKEND_ENV in message
    assert "'mlx'" in message and "'hf'" in message


def test_the_hf_backend_picks_the_unquantized_base() -> None:
    spec = local_adapter_spec(backend=Backend.HF)
    assert spec.model == BASE_MODEL_HF
    assert spec.extra_body["backend"] == "hf"


def test_the_adapter_path_comes_from_the_injected_env(tmp_path: Path) -> None:
    directory = _adapter_dir(tmp_path)
    spec = local_adapter_spec(env={ADAPTER_PATH_ENV: str(directory)})
    assert spec.adapter_path == str(directory)
    client = build_local_adapter(spec, None, {})
    assert isinstance(client, LocalAdapterClient)
    assert client.adapter_path == str(directory)


def test_the_local_lane_is_unpriced_because_it_genuinely_costs_nothing() -> None:
    spec = local_adapter_spec()
    assert spec.priced is False
    assert spec.price_in == 0.0 and spec.price_out == 0.0


# ------------------------------------------------------- the adapter directory check


def test_a_missing_adapter_directory_is_named(tmp_path: Path) -> None:
    problems = adapter_problems(tmp_path / "nope")
    assert len(problems) == 1
    assert "does not exist" in problems[0]


def test_a_half_written_checkpoint_is_named_as_one(tmp_path: Path) -> None:
    directory = _adapter_dir(tmp_path, complete=False)
    problems = adapter_problems(directory)
    assert any("half-finished checkpoint" in problem for problem in problems)


def test_a_directory_with_weights_but_no_config_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "adapters"
    directory.mkdir()
    (directory / "adapter_model.safetensors").write_bytes(b"x")
    problems = adapter_problems(directory)
    assert any(ADAPTER_CONFIG_FILE in problem for problem in problems)


@pytest.mark.parametrize(
    "weights", ["adapters.safetensors", "adapter_model.safetensors", "adapter_model.bin"]
)
def test_either_training_lanes_weight_filename_is_accepted(
    tmp_path: Path, weights: str
) -> None:
    """mlx-lm and peft write different names; requiring one would reject our own output."""
    directory = tmp_path / "adapters"
    directory.mkdir()
    (directory / ADAPTER_CONFIG_FILE).write_text("{}", encoding="utf-8")
    (directory / weights).write_bytes(b"x")
    assert adapter_problems(directory) == ()


def test_a_broken_adapter_path_fails_closed_at_construction(tmp_path: Path) -> None:
    """Serving the base model instead would look like a measurement of the adapter."""
    with pytest.raises(ProviderError) as caught:
        LocalAdapterClient(adapter_path=str(tmp_path / "nope"))
    message = str(caught.value)
    assert "does not exist" in message
    assert ADAPTER_PATH_ENV in message
    assert "run the base model deliberately" in message


def test_no_adapter_path_is_allowed_and_says_so(tmp_path: Path) -> None:
    """The base model is a legitimate eval target — it is the comparison column."""
    client = LocalAdapterClient(generate=_fake_generator("hi"))
    assert client.adapter_path == ""
    assert "no adapter" in client.describe()
    assert describe_adapter(None).startswith("base model")


def test_describe_flags_an_unusable_adapter_for_the_doctor(tmp_path: Path) -> None:
    assert "UNUSABLE" in describe_adapter(tmp_path / "nope")
    assert describe_adapter(_adapter_dir(tmp_path)).endswith("valid")


def test_describe_names_the_model_and_the_backend(tmp_path: Path) -> None:
    client = LocalAdapterClient(
        adapter_path=str(_adapter_dir(tmp_path)),
        backend=Backend.HF,
        generate=_fake_generator("hi"),
    )
    described = client.describe()
    assert LOCAL_MODEL_NAME in described
    assert "hf:" in described
    assert "valid" in described


# ------------------------------------------------------------ the named degradations


def test_the_hf_backend_degrades_with_a_named_error_when_transformers_is_absent() -> None:
    """transformers is not installed here, so this is the real degradation path."""
    try:
        import transformers  # noqa: F401
    except ImportError:
        pass
    else:  # pragma: no cover - only on a machine that has the heavy stack
        pytest.skip("transformers is installed; the absent-backend path cannot be taken")

    client = LocalAdapterClient(backend=Backend.HF)
    with pytest.raises(ProviderError) as caught:
        list(client._hf_generate("prompt", 8))
    message = str(caught.value)
    assert "needs transformers and torch" in message
    assert "uv pip install transformers torch" in message
    assert f"{BACKEND_ENV}=mlx" in message
    assert caught.value.retryable is False


async def test_the_mlx_backend_degrades_with_a_named_error_when_mlx_lm_is_absent() -> None:
    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        pass
    else:  # pragma: no cover - Apple silicon only
        pytest.skip("mlx-lm is installed; the absent-backend path cannot be taken")

    client = LocalAdapterClient(backend=Backend.MLX)
    request = ModelRequest(model=BASE_MODEL_MLX, messages=(_user("hello"),))
    with pytest.raises(ProviderError) as caught:
        async for _delta in client.stream(request):
            pass
    assert "mlx-lm is not installed" in str(caught.value)
    assert caught.value.retryable is False


# --------------------------------------------------------------- prompt rendering


def _user(text: str) -> Message:
    return Message(role=MessageRole.USER, content_blocks=(Text(text),))


def test_chatml_rendering_wraps_every_turn_and_ends_open_for_the_assistant() -> None:
    request = ModelRequest(
        model="m",
        system="you are ronin",
        prefix="repo map here",
        messages=(
            _user("read a.py"),
            Message(
                role=MessageRole.ASSISTANT,
                content_blocks=(ToolUse(id="1", name="read_file", arguments={"path": "a.py"}),),
            ),
            Message(
                role=MessageRole.TOOL,
                content_blocks=(ToolResultBlock(tool_use_id="1", content="print(1)"),),
            ),
        ),
    )
    prompt = render_chatml(request)
    assert prompt.startswith("<|im_start|>system\nyou are ronin\n\nrepo map here<|im_end|>")
    assert "<|im_start|>user\nread a.py<|im_end|>" in prompt
    assert prompt.endswith("<|im_start|>assistant\n")
    # the past call is re-rendered in the shim's dialect, not some other shape
    assert OPEN_TAG in prompt and CLOSE_TAG in prompt
    assert '"name": "read_file"' in prompt
    # a tool result comes back as Qwen's tool_response wrapper
    assert "<tool_response>\nprint(1)\n</tool_response>" in prompt


def test_chatml_rendering_omits_an_empty_system_block() -> None:
    prompt = render_chatml(ModelRequest(model="m", messages=(_user("hi"),)))
    assert "<|im_start|>system" not in prompt


# ------------------------------------------------------------------- integration


async def test_the_local_client_behind_the_shim_produces_a_tool_use() -> None:
    """The whole offline path: ChatML in, dialect out, ``ToolUse`` at the boundary."""
    payload = '{"name": "read_file", "arguments": {"path": "a.py"}}'
    client = ShimClient(
        LocalAdapterClient(
            generate=_fake_generator(f"Looking. {OPEN_TAG}{payload}{CLOSE_TAG}")
        )
    )
    request = ModelRequest(model=BASE_MODEL_MLX, messages=(_user("read a.py"),))
    deltas = [delta async for delta in client.stream(request)]
    completed = [delta for delta in deltas if isinstance(delta, Completed)]
    assert len(completed) == 1
    uses = completed[0].tool_uses
    assert [use.name for use in uses] == ["read_file"]
    assert uses[0].arguments == {"path": "a.py"}
    # a local model reports no usage, and zero cost is the truth rather than a guess
    assert completed[0].usage.total_tokens == 0
    assert completed[0].usage.cost_usd == 0.0


async def test_the_local_client_streams_plain_text_when_no_call_is_made() -> None:
    client = LocalAdapterClient(generate=_fake_generator("no tools needed here"))
    request = ModelRequest(model=BASE_MODEL_MLX, messages=(_user("hi"),))
    text = "".join(
        delta.text
        for delta in [d async for d in client.stream(request)]
        if hasattr(delta, "text") and not isinstance(delta, Completed)
    )
    assert "no tools needed here" in text
