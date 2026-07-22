"""Artifacts: structured content, immutable versioning, compare, restore, links."""
from __future__ import annotations

from pathlib import Path

import pytest

from ronin_artifacts import ArtifactKind, ArtifactLinks, ArtifactStore


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts.json")


def test_create_stores_structured_content_not_html(tmp_path: Path):
    store = _store(tmp_path)
    art = store.create(
        kind=ArtifactKind.study_plan, owner="alice", title="Calc I plan",
        content={"weeks": [{"topic": "limits"}]}, world="education", created="2026-07-22",
    )
    assert art.current.version == 1
    assert art.current.content == {"weeks": [{"topic": "limits"}]}
    assert isinstance(art.current.content, dict)  # structured, not a rendered string


def test_edit_appends_immutable_version(tmp_path: Path):
    store = _store(tmp_path)
    art = store.create(kind=ArtifactKind.plan, owner="a", title="v1",
                       content={"n": 1}, created="2026-07-22")
    v1_content = dict(art.current.content)
    store.edit(art.id, title="v2", content={"n": 2}, created="2026-07-23")
    got = store.get(art.id)
    assert [v.version for v in got.versions] == [1, 2]
    assert store.version(art.id, 1).content == v1_content  # v1 untouched
    assert got.current.title == "v2"


def test_compare_produces_unified_diff(tmp_path: Path):
    store = _store(tmp_path)
    art = store.create(kind=ArtifactKind.document, owner="a", title="doc",
                       content={"body": "first"}, created="2026-07-22")
    store.edit(art.id, content={"body": "second"}, created="2026-07-23")
    diff = store.compare(art.id, 1, 2)
    assert diff.from_version == 1 and diff.to_version == 2
    assert "first" in diff.unified and "second" in diff.unified


def test_restore_appends_new_version_never_rewrites(tmp_path: Path):
    store = _store(tmp_path)
    art = store.create(kind=ArtifactKind.plan, owner="a", title="v1",
                       content={"n": 1}, created="2026-07-22")
    store.edit(art.id, content={"n": 2}, created="2026-07-23")
    store.restore(art.id, 1, created="2026-07-24")
    got = store.get(art.id)
    assert [v.version for v in got.versions] == [1, 2, 3]
    assert got.current.content == {"n": 1}  # content restored
    assert "restored from v1" in got.current.note
    assert store.version(art.id, 2).content == {"n": 2}  # history intact


def test_links_capture_provenance(tmp_path: Path):
    store = _store(tmp_path)
    art = store.create(
        kind=ArtifactKind.medical_summary, owner="a", title="summary",
        content={"sections": []}, world="healthcare", created="2026-07-22",
        links=ArtifactLinks(source_ids=("doc1",), model_id="local:qwen2.5-coder-1.5b-4bit"),
    )
    assert art.current.links.source_ids == ("doc1",)
    assert art.current.links.model_id == "local:qwen2.5-coder-1.5b-4bit"


def test_list_filters_by_owner_world_kind(tmp_path: Path):
    store = _store(tmp_path)
    store.create(kind=ArtifactKind.plan, owner="a", title="p", content={}, world="coding", created="d")
    store.create(kind=ArtifactKind.study_plan, owner="a", title="s", content={}, world="education", created="d")
    store.create(kind=ArtifactKind.plan, owner="b", title="p2", content={}, world="coding", created="d")
    assert len(store.list_for("a")) == 2
    assert len(store.list_for("a", world="education")) == 1
    assert len(store.list_for("a", kind=ArtifactKind.plan)) == 1


def test_delete_is_owner_scoped(tmp_path: Path):
    store = _store(tmp_path)
    art = store.create(kind=ArtifactKind.plan, owner="a", title="p", content={}, created="d")
    assert store.delete(art.id, owner="b") is False  # not the owner
    assert store.get(art.id) is not None
    assert store.delete(art.id, owner="a") is True
    assert store.get(art.id) is None


def test_persistence_roundtrip(tmp_path: Path):
    store = _store(tmp_path)
    art = store.create(kind=ArtifactKind.report, owner="a", title="r",
                       content={"x": 1}, created="d")
    store.edit(art.id, content={"x": 2}, created="d2")
    store2 = ArtifactStore(tmp_path / "artifacts.json")
    got = store2.get(art.id)
    assert got is not None and len(got.versions) == 2 and got.current.content == {"x": 2}
