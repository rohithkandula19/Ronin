"""``detect``: three injected probes, one ``Detection``, and nothing touched off-machine.

The property under test throughout is hermeticity. ``env`` is a mapping, the port prober
is a fake, and ``which`` is a fake — so the same assertions hold on a laptop with three
servers running and in CI with none. :func:`ronin.cli.detect.real_probe`, the one
function that opens a socket, is never *called* here; the whole reason the ``probe`` seam
exists is that a test describes a machine instead of being run on one.
"""

from __future__ import annotations

from collections.abc import Callable

from ronin.cli.detect import (
    LOCAL_SERVERS,
    PROVIDER_KEYS,
    Detection,
    detect,
    real_probe,
)


def _which(*present: str) -> Callable[[str], str | None]:
    """A ``shutil.which`` that finds exactly ``present`` and nothing else."""
    found = set(present)

    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in found else None

    return which


def _prober(*open_ports: int) -> Callable[[str, int], bool]:
    """A fake port prober: the given ports are open, everything else is closed."""
    ports = set(open_ports)

    def probe(host: str, port: int) -> bool:
        del host
        return port in ports

    return probe


def _no_ports(host: str, port: int) -> bool:
    del host, port
    return False


def test_a_clean_container_with_only_ollama_finds_ollama_and_no_keys() -> None:
    found = detect(env={}, probe=_prober(11434), which=_which("rg"))

    assert found.local_servers == ("ollama",)
    assert found.keys == ()
    assert found.ripgrep is True
    assert found.has_any_model() is True


def test_a_bare_machine_has_no_model() -> None:
    found = detect(env={}, probe=_no_ports, which=_which())

    assert found.keys == ()
    assert found.local_servers == ()
    assert found.ripgrep is False
    assert found.has_any_model() is False


def test_a_provider_key_in_the_env_is_detected_by_name() -> None:
    found = detect(env={"ANTHROPIC_API_KEY": "sk-x"}, probe=_no_ports, which=_which("rg"))

    assert found.keys == ("anthropic",)
    assert found.has_any_model() is True


def test_an_exported_but_blank_key_does_not_count() -> None:
    # `ANTHROPIC_API_KEY=` with nothing after it is what a half-finished shell profile
    # leaves behind; treating it as present is how a first run writes a config that 401s.
    found = detect(env={"OPENAI_API_KEY": "   "}, probe=_no_ports, which=_which())

    assert found.keys == ()
    assert found.has_any_model() is False


def test_keys_are_reported_in_a_stable_order() -> None:
    env = {var: "x" for var in PROVIDER_KEYS.values()}

    found = detect(env=env, probe=_no_ports, which=_which())

    assert found.keys == tuple(PROVIDER_KEYS)


def test_multiple_local_servers_are_reported_in_known_order() -> None:
    found = detect(env={}, probe=_prober(11434, 8000), which=_which())

    assert found.local_servers == ("ollama", "vllm")


def test_detect_probes_exactly_the_known_local_servers() -> None:
    seen: list[tuple[str, int]] = []

    def probe(host: str, port: int) -> bool:
        seen.append((host, port))
        return False

    detect(env={}, probe=probe, which=_which())

    assert seen == [(server.host, server.port) for server in LOCAL_SERVERS.values()]


def test_ripgrep_detection_looks_for_the_rg_binary() -> None:
    asked: list[str] = []

    def which(name: str) -> str | None:
        asked.append(name)
        return None

    detect(env={}, probe=_no_ports, which=which)

    assert "rg" in asked


def test_has_any_model_is_a_key_or_a_local_server() -> None:
    assert Detection().has_any_model() is False
    assert Detection(keys=("anthropic",)).has_any_model() is True
    assert Detection(local_servers=("ollama",)).has_any_model() is True


def test_real_probe_is_the_impure_seam_and_is_never_called_here() -> None:
    # Referenced, not invoked: calling it would open a socket, which is exactly what the
    # `probe` seam exists to keep out of the suite.
    assert callable(real_probe)
