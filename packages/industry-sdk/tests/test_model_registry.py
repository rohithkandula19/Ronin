"""Model/Adapter registries: records, lifecycle honesty gates, persistence."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_industry_sdk.model_registry import (
    AdapterRecord,
    AdapterRegistry,
    AdapterState,
    EvalResultRef,
    ModelRecord,
    ModelRegistry,
    StateTransitionError,
    seed_default_models,
    seed_known_adapters,
)


def _model(**overrides) -> ModelRecord:
    base = dict(
        id="local:test-1b",
        provider="local",
        model_name="test-1b",
        display_name="Test 1B",
        open_weights=True,
        license="apache-2.0",
        local=True,
        supports_tool_use=True,
        price_in_per_mtok=0.0,
        price_out_per_mtok=0.0,
    )
    base.update(overrides)
    return ModelRecord.model_validate(base)


def _adapter(**overrides) -> AdapterRecord:
    base = dict(
        id="test-coding-v1",
        base_model="local:test-1b",
        industry="coding",
        version="1.0.0",
        created="2026-07-21",
    )
    base.update(overrides)
    return AdapterRecord.model_validate(base)


# ---------------------------------------------------------------- models

def test_model_register_get_and_persist(tmp_path: Path):
    reg = ModelRegistry(tmp_path / "models.json")
    reg.register(_model())
    # a fresh registry instance reads the same store
    reg2 = ModelRegistry(tmp_path / "models.json")
    got = reg2.get("local:test-1b")
    assert got is not None and got.local and got.open_weights


def test_model_duplicate_refused(tmp_path: Path):
    reg = ModelRegistry(tmp_path / "models.json")
    reg.register(_model())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(_model())


def test_find_capable_filters_by_capability_and_locality(tmp_path: Path):
    reg = ModelRegistry(tmp_path / "models.json")
    reg.register(_model())
    reg.register(_model(id="remote:big", provider="remote", local=False,
                        supports_structured_output=True))
    assert {m.id for m in reg.find_capable({"tool_use"})} == {"local:test-1b", "remote:big"}
    assert {m.id for m in reg.find_capable({"tool_use"}, local_only=True)} == {"local:test-1b"}
    assert {m.id for m in reg.find_capable({"structured_output"})} == {"remote:big"}


def test_unknown_price_is_none_not_zero(tmp_path: Path):
    m = _model(id="x:unpriced", price_in_per_mtok=None, price_out_per_mtok=None)
    assert m.price_in_per_mtok is None  # unknown is unknown, never a guessed number


# --------------------------------------------------------------- adapters

def test_adapter_lifecycle_happy_path(tmp_path: Path):
    reg = AdapterRegistry(tmp_path / "adapters.json")
    reg.register(_adapter())
    for state in (AdapterState.submitted, AdapterState.running, AdapterState.completed,
                  AdapterState.evaluating):
        reg.transition("test-coding-v1", state)
    rec = reg.transition(
        "test-coding-v1",
        AdapterState.approved,
        approved_by="rohith",
        eval_results=(EvalResultRef(suite="coding-protocol", passed=40, total=91),),
    )
    assert rec.state is AdapterState.approved and rec.approved_by == "rohith"
    rec = reg.transition("test-coding-v1", AdapterState.released)
    assert reg.released_for("coding").id == "test-coding-v1"
    rec = reg.transition("test-coding-v1", AdapterState.rolled_back)
    assert reg.released_for("coding") is None


def test_generated_cannot_jump_to_released(tmp_path: Path):
    """A generated training script is NOT a trained model — the core honesty gate."""
    reg = AdapterRegistry(tmp_path / "adapters.json")
    reg.register(_adapter())
    with pytest.raises(StateTransitionError, match="not a legal transition"):
        reg.transition("test-coding-v1", AdapterState.released)
    with pytest.raises(StateTransitionError):
        reg.transition("test-coding-v1", AdapterState.completed)


def test_approval_requires_evals_and_human(tmp_path: Path):
    reg = AdapterRegistry(tmp_path / "adapters.json")
    reg.register(_adapter())
    for state in (AdapterState.submitted, AdapterState.running, AdapterState.completed,
                  AdapterState.evaluating):
        reg.transition("test-coding-v1", state)
    with pytest.raises(StateTransitionError, match="no eval results"):
        reg.transition("test-coding-v1", AdapterState.approved, approved_by="rohith")
    with pytest.raises(StateTransitionError, match="approved_by"):
        reg.transition(
            "test-coding-v1", AdapterState.approved,
            eval_results=(EvalResultRef(suite="s", passed=1, total=2),),
        )


def test_adapter_persistence_roundtrip(tmp_path: Path):
    reg = AdapterRegistry(tmp_path / "adapters.json")
    reg.register(_adapter())
    reg.transition("test-coding-v1", AdapterState.submitted)
    reg2 = AdapterRegistry(tmp_path / "adapters.json")
    assert reg2.get("test-coding-v1").state is AdapterState.submitted


# ---------------------------------------------------------------- seeding

def test_seed_defaults_are_idempotent_and_honest(tmp_path: Path):
    models = ModelRegistry(tmp_path / "models.json")
    adapters = AdapterRegistry(tmp_path / "adapters.json")
    assert seed_default_models(models) >= 4
    assert seed_default_models(models) == 0  # idempotent
    assert seed_known_adapters(adapters) == 1
    assert seed_known_adapters(adapters) == 0

    v2 = adapters.get("ronin-coding-1.5b-v2-iter150")
    assert v2 is not None
    # honesty: the real adapter is completed+measured, NOT released
    assert v2.state is AdapterState.completed
    assert v2.eval_results[0].passed == 36 and v2.eval_results[0].total == 91
    assert adapters.released_for("coding") is None

    # every seeded local model is genuinely free and open-weights
    for m in models.find_capable({"text"}, local_only=True):
        assert m.price_in_per_mtok == 0.0 and m.open_weights
