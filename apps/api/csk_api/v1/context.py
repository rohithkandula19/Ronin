"""Shared v1 runtime context: the one place registries and the vault are built.

This binds the API to Ronin Core / the Industry SDK rather than reimplementing
any of it. Paths are configurable via env so tests get isolated temp dirs.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path

from ronin_industry_sdk import PackRegistry, discover_packs
from ronin_industry_sdk.eval_gate import SuiteRegistry
from ronin_industry_sdk.model_registry import (
    AdapterRegistry,
    ModelRegistry,
    seed_default_models,
    seed_known_adapters,
)
from ronin_vault import VaultStore


def _repo_root() -> Path:
    # apps/api/csk_api/v1/context.py -> repo root is parents[4]
    return Path(__file__).resolve().parents[4]


def packs_root() -> Path:
    return Path(os.environ.get("RONIN_PACKS_ROOT", _repo_root() / "industry-packs"))


def _data_dir() -> Path:
    d = Path(os.environ.get("RONIN_AIOS_DATA", _repo_root() / ".ronin" / "aios"))
    d.mkdir(parents=True, exist_ok=True)
    return d


@functools.lru_cache(maxsize=1)
def pack_registry() -> PackRegistry:
    return PackRegistry.from_discovery(discover_packs(packs_root()))


@functools.lru_cache(maxsize=1)
def suite_registry() -> SuiteRegistry:
    return SuiteRegistry.from_packs_root(packs_root())


@functools.lru_cache(maxsize=1)
def model_registry() -> ModelRegistry:
    reg = ModelRegistry(_data_dir() / "models.json")
    seed_default_models(reg)
    return reg


@functools.lru_cache(maxsize=1)
def adapter_registry() -> AdapterRegistry:
    reg = AdapterRegistry(_data_dir() / "adapters.json")
    seed_known_adapters(reg)
    return reg


@functools.lru_cache(maxsize=1)
def vault() -> VaultStore:
    return VaultStore(_data_dir() / "vault.json")


def reset_context_for_tests() -> None:
    """Clear the memoized singletons so a test's env/temp dirs take effect."""
    for fn in (pack_registry, suite_registry, model_registry, adapter_registry, vault):
        fn.cache_clear()
