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
from ronin_artifacts import ArtifactStore
from ronin_persistence import from_env as _doc_store_from_env
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


def _backend(name: str, filename: str):
    """Pick the persistence backend for a store. When RONIN_DATABASE_URL is
    set, all API state goes to PostgreSQL (one JSONB row per store); otherwise
    it stays on the local JSON file — the default, identical to before."""
    return _doc_store_from_env(name, path=_data_dir() / filename)


@functools.lru_cache(maxsize=1)
def model_registry() -> ModelRegistry:
    reg = ModelRegistry(backend=_backend("models", "models.json"))
    seed_default_models(reg)
    return reg


@functools.lru_cache(maxsize=1)
def adapter_registry() -> AdapterRegistry:
    reg = AdapterRegistry(backend=_backend("adapters", "adapters.json"))
    seed_known_adapters(reg)
    return reg


@functools.lru_cache(maxsize=1)
def vault() -> VaultStore:
    return VaultStore(backend=_backend("vault", "vault.json"))


@functools.lru_cache(maxsize=1)
def artifacts() -> ArtifactStore:
    return ArtifactStore(backend=_backend("artifacts", "artifacts.json"))


def reset_context_for_tests() -> None:
    """Clear the memoized singletons so a test's env/temp dirs take effect."""
    for fn in (pack_registry, suite_registry, model_registry, adapter_registry,
               vault, artifacts):
        fn.cache_clear()
