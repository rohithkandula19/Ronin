"""Doctor — diagnose the install, config, and providers; suggest fixes.

A real diagnostics command: each check returns a ``Diagnosis`` with a status
(``ok`` / ``warn`` / ``fail``), a human message, and — when something is wrong —
a concrete ``fix`` the user can run. The check *functions* are pure: they take a
``RoninConfig`` (and small injected probes for anything that touches the network)
and return data, so the whole battery is unit-testable offline. The ``doctor``
CLI command renders them and decides the exit code (non-zero if anything failed),
which makes ``ronin doctor`` usable as a CI / setup gate.
"""
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from typing import Callable

from .config import PROVIDER_PRESETS, RoninConfig

# Status ordering for the overall verdict: fail dominates warn dominates ok.
OK = "ok"
WARN = "warn"
FAIL = "fail"
_RANK = {OK: 0, WARN: 1, FAIL: 2}


@dataclass
class Diagnosis:
    """One check's result."""

    name: str
    status: str  # OK | WARN | FAIL
    message: str
    fix: str = ""  # a command/instruction to resolve it (empty when status==OK)


def check_python(version: tuple[int, int] | None = None) -> Diagnosis:
    """ronin needs Python 3.11+. ``version`` overrides the live interpreter (tests)."""
    major, minor = version or (sys.version_info.major, sys.version_info.minor)
    if (major, minor) >= (3, 11):
        return Diagnosis("python", OK, f"Python {major}.{minor}")
    return Diagnosis(
        "python", FAIL, f"Python {major}.{minor} is too old (need >= 3.11)",
        fix="install Python 3.11+ (e.g. `uv python install 3.12`) and re-create the venv",
    )


def check_config(config: RoninConfig, config_path: object | None) -> Diagnosis:
    """A config file should exist; without one ronin runs on bare defaults."""
    if config_path is not None:
        return Diagnosis("config file", OK, str(config_path))
    return Diagnosis(
        "config file", WARN, "no config file found — running on defaults",
        fix="run `ronin init` (or `ronin init --demo` for a no-credentials demo)",
    )


def check_provider(config: RoninConfig) -> Diagnosis:
    """The configured provider must be one ronin knows how to drive."""
    if config.provider in PROVIDER_PRESETS or config.provider == "custom":
        return Diagnosis("provider", OK, config.provider)
    known = ", ".join(sorted(PROVIDER_PRESETS))
    return Diagnosis(
        "provider", FAIL, f"unknown provider '{config.provider}'",
        fix=f"set `provider` to one of: {known}, custom — e.g. `ronin set-key --provider anthropic`",
    )


def check_auth(config: RoninConfig) -> Diagnosis:
    """The active provider must have the credentials it needs (or be local/demo)."""
    if config.demo_mode:
        return Diagnosis("auth", OK, "demo mode — no credentials needed")
    if config.provider == "ollama":
        return Diagnosis("auth", OK, "ollama — local, no key needed")
    if config.has_provider_auth():
        return Diagnosis("auth", OK, f"{config.provider} key present")
    return Diagnosis(
        "auth", FAIL, f"no API key for provider '{config.provider}'",
        fix=f"run `ronin set-key --provider {config.provider}` "
            f"(or export {('ANTHROPIC_API_KEY' if config.provider == 'anthropic' else 'OPENAI_API_KEY')})",
    )


def check_base_url(config: RoninConfig) -> Diagnosis:
    """A 'custom' provider needs an explicit base_url; presets supply their own."""
    if config.provider == "custom" and not config.resolved_base_url():
        return Diagnosis(
            "base_url", FAIL, "custom provider has no base_url",
            fix="set `base_url` in config (the OpenAI-compatible endpoint, ending in /v1)",
        )
    url = config.resolved_base_url()
    return Diagnosis("base_url", OK, url or "(provider default)")


def check_offline_consistency(config: RoninConfig) -> Diagnosis:
    """Offline mode forbids network egress, so it only makes sense with a local
    brain. A cloud provider + offline is a misconfiguration worth flagging."""
    if config.offline and config.provider not in ("ollama", "custom") and not config.demo_mode:
        return Diagnosis(
            "offline", WARN,
            f"offline mode is on but provider '{config.provider}' is a cloud provider",
            fix="use `--provider ollama` (a local brain) with offline, or turn offline off",
        )
    return Diagnosis("offline", OK, "on" if config.offline else "off")


def check_ollama_binary(config: RoninConfig, which: Callable[[str], str | None] = shutil.which) -> Diagnosis:
    """If the provider is ollama, the `ollama` binary should be installed.
    ``which`` is injected so the check is testable without a real install."""
    if config.provider != "ollama":
        return Diagnosis("ollama", OK, "not using ollama")
    if which("ollama"):
        return Diagnosis("ollama", OK, "ollama binary found")
    return Diagnosis(
        "ollama", FAIL, "provider is ollama but the `ollama` binary is not on PATH",
        fix="install Ollama (https://ollama.com) and `ollama pull llama3.1`, or pick another provider",
    )


# The default battery, in render order. Each entry is (callable, needs_path).
def run_checks(config: RoninConfig, config_path: object | None) -> list[Diagnosis]:
    """Run the full battery and return the diagnoses in display order."""
    return [
        check_python(),
        check_config(config, config_path),
        check_provider(config),
        check_auth(config),
        check_base_url(config),
        check_ollama_binary(config),
        check_offline_consistency(config),
    ]


def overall_status(diagnoses: list[Diagnosis]) -> str:
    """The worst status across all diagnoses (ok < warn < fail)."""
    worst = OK
    for d in diagnoses:
        if _RANK[d.status] > _RANK[worst]:
            worst = d.status
    return worst


def exit_code(diagnoses: list[Diagnosis]) -> int:
    """0 if everything is ok/warn-free of failures, 1 if any check failed."""
    return 1 if overall_status(diagnoses) == FAIL else 0
