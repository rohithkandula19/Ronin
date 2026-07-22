"""Vault: the boundary tests. These are the contract of the product."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_vault import (
    IsolationError,
    MemoryItem,
    MemoryScope,
    Sensitivity,
    VaultStore,
)


def _store(tmp_path: Path) -> VaultStore:
    return VaultStore(tmp_path / "vault.json")


def _item(**overrides) -> MemoryItem:
    base = dict(
        scope=MemoryScope.user,
        owner="rohith",
        text="prefers pytest over unittest for python testing",
        created="2026-07-21",
    )
    base.update(overrides)
    return MemoryItem.model_validate(base)


# ------------------------------------------------------- industry isolation

def test_healthcare_memory_never_surfaces_in_coding(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(
        id="health1",
        scope=MemoryScope.industry,
        world="healthcare",
        sensitivity=Sensitivity.health,
        text="patient takes medication for blood pressure daily",
    ))
    store.add(_item(
        id="code1",
        scope=MemoryScope.industry,
        world="coding",
        text="the medication tracker app uses pytest for its blood pressure module",
    ))

    coding_hits = store.recall(
        "medication blood pressure", owner="rohith", world="coding")
    assert [i.id for i in coding_hits] == ["code1"], (
        "healthcare memory leaked into the coding world"
    )
    health_hits = store.recall(
        "medication blood pressure", owner="rohith", world="healthcare")
    assert [i.id for i in health_hits] == ["health1"]


def test_org_memory_stays_out_of_personal_workspaces(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(
        id="org1", scope=MemoryScope.organization, owner="rohith",
        text="acme corp deploys releases every thursday afternoon",
    ))
    personal = store.recall("acme releases thursday", owner="rohith",
                            world="coding", workspace_kind="personal")
    assert personal == []
    org = store.recall("acme releases thursday", owner="rohith",
                       world="coding", workspace_kind="organization")
    assert [i.id for i in org] == ["org1"]


def test_tool_context_and_disabled_never_recalled(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(id="tc", scope=MemoryScope.tool_context,
                    text="temporary tool output about pytest runs"))
    store.add(_item(id="off", text="disabled note about pytest runs"))
    store.disable("off")
    assert store.recall("pytest runs", owner="rohith", world="coding",
                        scopes=tuple(MemoryScope)) == []


def test_other_owners_memory_invisible(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(id="mine", text="my pytest preference note"))
    store.add(_item(id="theirs", owner="someone-else", text="their pytest preference note"))
    hits = store.recall("pytest preference", owner="rohith", world="coding")
    assert [i.id for i in hits] == ["mine"]


# --------------------------------------------------- training-consent gates

def test_nothing_is_training_eligible_by_default(tmp_path: Path):
    store = _store(tmp_path)
    item = store.add(_item(id="n1"))
    assert item.training_eligible is False
    assert store.training_pool("rohith") == []


def test_cannot_create_item_pre_marked_eligible(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(IsolationError, match="mark_training_eligible"):
        store.add(_item(id="n1", training_eligible=True))


def test_eligibility_requires_consent_ref(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(id="n1"))
    with pytest.raises(IsolationError, match="consent_ref"):
        store.mark_training_eligible("n1", consent_ref="   ")
    marked = store.mark_training_eligible("n1", consent_ref="consent-2026-07-21-a")
    assert marked.training_eligible and marked.consent_ref
    assert [i.id for i in store.training_pool("rohith")] == ["n1"]


def test_credential_and_minor_data_can_never_be_training_eligible(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(id="cred", sensitivity=Sensitivity.credential,
                    text="the deploy key lives in the ops vault"))
    store.add(_item(id="kid", sensitivity=Sensitivity.minor,
                    text="student progress note for a minor"))
    for iid in ("cred", "kid"):
        with pytest.raises(IsolationError, match="never"):
            store.mark_training_eligible(iid, consent_ref="consent-x")


# -------------------------------------------------------- transfer preview

def test_transfer_requires_preview_style_confirmation(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(id="h1", scope=MemoryScope.industry, world="healthcare",
                    sensitivity=Sensitivity.health,
                    text="allergy to penicillin noted in 2024 records"))
    preview = store.preview_transfer("h1", to_world="coding")
    assert preview.from_world == "healthcare" and preview.to_world == "coding"
    assert preview.warnings, "sensitive transfer must warn"
    # preview moved nothing
    assert store.get("h1").world == "healthcare"
    with pytest.raises(IsolationError, match="confirmed=True"):
        store.transfer("h1", to_world="coding", confirmed=False)
    moved = store.transfer("h1", to_world="coding", confirmed=True)
    assert moved.world == "coding"


# --------------------------------------------------------- audit + control

def test_recall_is_audited(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(id="a1"))
    store.recall("pytest testing", owner="rohith", world="coding", when="2026-07-21T10:00Z")
    usage = store.usage_for("a1")
    assert len(usage) == 1
    assert usage[0]["query"] == "pytest testing" and usage[0]["world"] == "coding"


def test_view_edit_delete_export(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(id="e1"))
    store.edit("e1", text="prefers pytest fixtures over setUp methods", updated="2026-07-22")
    assert "fixtures" in store.get("e1").text
    exported = store.export_all("rohith")
    assert len(exported) == 1 and exported[0]["id"] == "e1"
    assert store.delete("e1") is True
    assert store.get("e1") is None
    assert store.delete("e1") is False


def test_retention_pruning(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(id="old", created="2026-01-01", retention_days=30))
    store.add(_item(id="keep", created="2026-07-01", retention_days=365))
    store.add(_item(id="forever", created="2026-01-01", retention_days=None))
    removed = store.prune_expired(now_iso="2026-07-21")
    assert removed == 1
    assert store.get("old") is None
    assert store.get("keep") is not None and store.get("forever") is not None


def test_industry_scope_requires_world(tmp_path: Path):
    store = _store(tmp_path)
    with pytest.raises(IsolationError, match="world"):
        store.add(_item(scope=MemoryScope.industry, world=""))


def test_persistence_roundtrip(tmp_path: Path):
    store = _store(tmp_path)
    store.add(_item(id="p1", scope=MemoryScope.industry, world="healthcare",
                    sensitivity=Sensitivity.health))
    store2 = VaultStore(tmp_path / "vault.json")
    item = store2.get("p1")
    assert item is not None
    assert item.sensitivity is Sensitivity.health and item.world == "healthcare"
