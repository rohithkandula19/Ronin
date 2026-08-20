"""The wizard's detection layer: a config that works with what is on the machine.

The headline property, straight from the spec: a clean container with no API key and
nothing but ``ollama serve`` should come out of first-run setup with a ``models.toml``
that parses, that the workspace resolver finds, and that the 10-second smoke reports
success on. Everything here is offline — the :class:`Detection` is constructed rather
than probed, and the smoke is a fake that returns ``True``.
"""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

from wire_harness import workspace

from ronin.cli.detect import Detection
from ronin.cli.sdk import find_router_config, load_router
from ronin.cli.wizard import plan_config, run_smoke, write_config
from ronin.providers.router import Role as ModelRole
from ronin.providers.router import load_config, parse_config

ONLY_OLLAMA = Detection(keys=(), local_servers=("ollama",), ripgrep=True)


async def test_a_clean_container_with_only_ollama_gets_a_config_a_task_runs_on(
    tmp_path: Path,
) -> None:
    paths = workspace(tmp_path)

    body = plan_config(ONLY_OLLAMA)
    assert body is not None
    # The free/local lane: a local ollama model and no API key named.
    assert 'provider = "ollama"' in body
    assert "api_key_env" not in body

    written = write_config(paths, body, user_layer=True)
    assert written == paths.home / ".ronin" / "models.toml"

    # The real config loader parses the file the wizard wrote...
    config = load_config(written)
    assert config.spec_for(ModelRole.MAIN).provider == "ollama"

    # ...and the workspace's own resolver finds it and builds a Router — no network,
    # because clients are built lazily and the smoke below is the only thing that runs.
    assert find_router_config(paths, environ={}) == written
    router = load_router(paths, environ={})
    assert router.spec_for(ModelRole.MAIN).provider == "ollama"

    # The 10-second smoke task, faked to succeed, reports success.
    async def smoke() -> bool:
        return True

    result = await run_smoke(smoke)
    assert result.ran is True
    assert result.ok is True
    assert "passed" in result.line()


def test_a_provider_key_is_preferred_over_a_local_server() -> None:
    both = Detection(keys=("anthropic",), local_servers=("ollama",), ripgrep=True)

    body = plan_config(both)

    assert body is not None
    assert 'provider = "anthropic"' in body
    assert 'api_key_env = "ANTHROPIC_API_KEY"' in body
    # A hosted key wins, so the detected local server is not what got written.
    assert 'provider = "ollama"' not in body


def test_a_key_without_a_native_adapter_is_written_as_openai_compatible() -> None:
    # Gemini has no first-class adapter but speaks the OpenAI wire protocol, so it is
    # written as `openai-compatible` with an explicit base_url rather than a provider
    # name that `build_client` would reject.
    body = plan_config(Detection(keys=("gemini",)))

    assert body is not None
    assert 'provider = "openai-compatible"' in body
    assert "base_url" in body
    assert 'api_key_env = "GEMINI_API_KEY"' in body


def test_a_bare_machine_cannot_be_configured() -> None:
    assert plan_config(Detection()) is None


def test_the_generated_config_maps_all_three_roles() -> None:
    # All three so /doctor reports a mapped config rather than warning that mechanical
    # work will be billed at the main model's price.
    body = plan_config(Detection(local_servers=("ollama",)))
    assert body is not None

    config = parse_config(tomllib.loads(body))

    assert set(config.roles) == {ModelRole.MAIN, ModelRole.PLAN, ModelRole.FAST}


def test_write_config_defaults_to_the_project_layer_with_lf_endings(tmp_path: Path) -> None:
    paths = workspace(tmp_path)

    written = write_config(paths, '# body\nprovider = "ollama"\n')

    assert written == paths.ronin_dir / "models.toml"
    assert b"\r\n" not in written.read_bytes()


def test_write_config_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    paths = workspace(tmp_path)

    first = write_config(paths, "# first\n")
    again = write_config(paths, "# second\n")

    assert first == paths.ronin_dir / "models.toml"
    assert again is None
    assert first.read_text(encoding="utf-8") == "# first\n"


async def test_a_missing_smoke_is_skipped_not_failed() -> None:
    result = await run_smoke(None)

    assert result.ran is False
    assert result.ok is False
    assert "skipped" in result.line()


async def test_a_smoke_that_returns_false_is_a_failure() -> None:
    async def smoke() -> bool:
        return False

    result = await run_smoke(smoke)

    assert result.ran is True
    assert result.ok is False
    assert "FAILED" in result.line()


async def test_a_smoke_that_raises_is_reported_not_propagated() -> None:
    async def smoke() -> bool:
        raise RuntimeError("model unreachable")

    result = await run_smoke(smoke)

    assert result.ran is True
    assert result.ok is False
    assert "model unreachable" in result.detail


async def test_a_smoke_that_hangs_past_the_timeout_fails() -> None:
    async def smoke() -> bool:
        await asyncio.sleep(1.0)
        return True

    result = await run_smoke(smoke, timeout=0.01)

    assert result.ran is True
    assert result.ok is False
    assert "did not finish" in result.detail
