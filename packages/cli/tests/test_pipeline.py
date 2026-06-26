"""Tests for the Wave 4 role-handoff pipeline — pure core (state / parse / plan)."""
from __future__ import annotations

import pytest

from ronin_cli.config import RoninConfig
from ronin_cli.offline import apply_free, apply_offline
from ronin_cli.pipeline import (
    DEFAULT_ROLES,
    PipelineStage,
    PipelineState,
    parse_roles,
    plan_pipeline,
    stage_permission_label,
    stage_read_only,
)


# --- role parsing ------------------------------------------------------------

def test_default_role_sequence() -> None:
    assert DEFAULT_ROLES == ["architect", "implementer", "reviewer", "tester"]
    assert parse_roles(None) == DEFAULT_ROLES
    assert parse_roles("") == DEFAULT_ROLES
    assert parse_roles("  ") == DEFAULT_ROLES


def test_custom_role_sequence_parses() -> None:
    assert parse_roles("debugger,implementer,tester") == ["debugger", "implementer", "tester"]
    assert parse_roles(" reviewer , tester ") == ["reviewer", "tester"]


def test_invalid_role_raises_with_valid_set() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        parse_roles("architect,wizard,tester")


# --- per-stage permissions ---------------------------------------------------

def test_read_only_roles_always_read_only() -> None:
    for role in ("architect", "reviewer", "researcher"):
        assert stage_read_only(role, write_capable=True) is True
        assert stage_read_only(role, write_capable=False) is True


def test_doer_roles_read_only_unless_write_capable() -> None:
    for role in ("implementer", "tester", "debugger"):
        assert stage_read_only(role, write_capable=False) is True   # proposal mode
        assert stage_read_only(role, write_capable=True) is False    # may act (gated)


def test_permission_labels() -> None:
    assert stage_permission_label("architect", write_capable=True) == "read-only"
    assert stage_permission_label("implementer", write_capable=False) == "read-only (proposal)"
    assert stage_permission_label("implementer", write_capable=True) == "edits + commands (gated)"
    assert stage_permission_label("tester", write_capable=True) == "runs tests (gated)"


# --- state model -------------------------------------------------------------

def test_plan_pipeline_all_pending() -> None:
    st = plan_pipeline("do a thing", DEFAULT_ROLES, provider="cerebras", badge="FREE")
    assert [s.role for s in st.stages] == DEFAULT_ROLES
    assert all(s.status == "pending" for s in st.stages)
    assert st.badge == "FREE" and st.provider == "cerebras"
    assert st.stopped is False
    assert st.outcome() == "partial"


def test_state_stopped_and_outcome() -> None:
    st = plan_pipeline("t", ["architect", "implementer"])
    st.stages[0].status = "completed"
    st.stages[1].status = "failed"
    assert st.stopped is True
    assert st.outcome() == "failed"


def test_dry_run_outcome_is_planned() -> None:
    st = plan_pipeline("t", DEFAULT_ROLES, dry_run=True)
    assert st.outcome() == "planned"


def test_state_serializes_to_json() -> None:
    st = plan_pipeline("ship it", ["reviewer", "tester"], badge="LOCAL", offline=True)
    st.stages[0].status = "completed"
    st.stages[0].summary = "no blocking issues"
    st.stages[0].files_changed = []
    data = st.model_dump()
    assert data["task"] == "ship it"
    assert data["roles"] == ["reviewer", "tester"]
    assert data["badge"] == "LOCAL" and data["offline"] is True
    assert data["stages"][0]["status"] == "completed"
    # round-trips
    again = PipelineState.model_validate_json(st.model_dump_json())
    assert again.roles == st.roles
    assert again.stages[0].summary == "no blocking issues"


# --- shared free/offline resolution ------------------------------------------

def test_apply_free_keeps_already_free_provider() -> None:
    cfg = RoninConfig(provider="cerebras")
    assert apply_free(cfg).provider == "cerebras"


def _clear_shared_keys(monkeypatch) -> None:
    # free providers share the OPENAI_API_KEY slot; clear it so only explicit
    # per-provider keys count (deterministic across machines).
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
                "CEREBRAS_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_apply_free_falls_back_to_keyless_local(monkeypatch) -> None:
    _clear_shared_keys(monkeypatch)
    cfg = RoninConfig(provider="anthropic")  # paid, no free key
    assert apply_free(cfg).provider == "local"


def test_apply_free_prefers_keyed_free_tier(monkeypatch) -> None:
    _clear_shared_keys(monkeypatch)
    cfg = RoninConfig(provider="anthropic")
    # only groq has a per-provider key (set_key_for would mirror into the shared
    # OpenAI slot and unlock every free provider, so set it in isolation here)
    cfg.provider_keys["groq"] = "sk-test"
    assert apply_free(cfg).provider == "groq"


def test_apply_free_picks_first_preference_with_shared_key(monkeypatch) -> None:
    _clear_shared_keys(monkeypatch)
    cfg = RoninConfig(provider="anthropic")
    cfg.set_key_for("groq", "sk-test")  # mirrors to shared slot → all free tiers usable
    assert apply_free(cfg).provider == "cerebras"  # first in FREE_PREFERENCE


def test_apply_offline_forces_local_brain() -> None:
    cfg = RoninConfig(provider="anthropic", offline=True)
    assert apply_offline(cfg).provider == "ollama"
