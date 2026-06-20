"""Tests for the PURE pieces of session checkpoints (ronin undo).

Everything here is deterministic — id format, slugify, summarize, restore_plan,
and most_recent selection — so no real git repo is required. One small
create/restore round-trip exercises the file-copy fallback against tmp_path
(still no git) to prove the impure path snapshots + rewinds correctly.
"""
from __future__ import annotations

from ronin_cli import checkpoints as c


# ───────────────────────────── id format ─────────────────────────────────────

def test_id_is_deterministic_and_slugged() -> None:
    cid = c.new_checkpoint_id("before refactor", now=0.0)
    assert cid == c.new_checkpoint_id("before refactor", now=0.0)  # pure
    assert cid.endswith("-before-refactor")
    # base-36 of epoch 0, zero-padded to the fixed time-prefix width
    assert cid.split("-", 1)[0] == "0" * c._TIME_WIDTH


def test_id_time_prefix_sorts_chronologically() -> None:
    older = c.new_checkpoint_id("x", now=1000.0)
    newer = c.new_checkpoint_id("x", now=2_000_000.0)
    # base-36 time prefix must sort the same direction as real time, as strings
    assert older.split("-", 1)[0] < newer.split("-", 1)[0]


def test_id_handles_empty_and_messy_labels() -> None:
    assert c.new_checkpoint_id(None, now=10.0).endswith("-snapshot")
    assert c.new_checkpoint_id("", now=10.0).endswith("-snapshot")
    assert c.new_checkpoint_id("Fix: the THING!!!  ", now=10.0).endswith("-fix-the-thing")


def test_slugify_caps_length_and_strips_dashes() -> None:
    assert c.slugify("a b c") == "a-b-c"
    assert c.slugify("---weird---") == "weird"
    long = c.slugify("x" * 100)
    assert len(long) <= 24 and not long.startswith("-") and not long.endswith("-")
    assert c.slugify("***") == "snapshot"  # nothing alphanumeric → fallback


# ───────────────────────────── parse_checkpoint ──────────────────────────────

def test_parse_checkpoint_fills_defaults_and_coerces() -> None:
    p = c.parse_checkpoint({"id": "abc-x", "files": "7", "created": "12.5"})
    assert p["id"] == "abc-x"
    assert p["files"] == 7          # coerced str -> int
    assert p["created"] == 12.5     # coerced str -> float
    assert p["label"] == "snapshot" and p["kind"] == "git" and p["auto"] is False


def test_parse_checkpoint_tolerates_garbage() -> None:
    p = c.parse_checkpoint({"id": "z", "files": "not-a-number", "created": "nope"})
    assert p["files"] == 0 and p["created"] == 0.0  # degrades, never raises


# ───────────────────────────── most_recent ───────────────────────────────────

def test_most_recent_returns_none_when_empty() -> None:
    assert c.most_recent([]) is None


def test_most_recent_picks_latest_timestamp() -> None:
    recs = [
        {"id": c.new_checkpoint_id("a", now=100.0), "created": 100.0},
        {"id": c.new_checkpoint_id("b", now=300.0), "created": 300.0},
        {"id": c.new_checkpoint_id("c", now=200.0), "created": 200.0},
    ]
    assert c.most_recent(recs)["created"] == 300.0


def test_most_recent_tiebreaks_on_id_in_same_second() -> None:
    recs = [
        {"id": "0aa-first", "created": 50.0},
        {"id": "0zz-second", "created": 50.0},
    ]
    # equal timestamps → lexically-greatest id wins, deterministically
    assert c.most_recent(recs)["id"] == "0zz-second"


# ───────────────────────────── summarize ─────────────────────────────────────

def test_summarize_empty_is_friendly() -> None:
    out = c.summarize_checkpoints([])
    assert "no checkpoints" in out.lower()


def test_summarize_table_has_headers_and_rows_newest_first() -> None:
    recs = [
        {"id": "old1", "label": "first", "created": 1000.0, "files": 3},
        {"id": "new1", "label": "second", "created": 9000.0, "files": 5, "auto": True},
    ]
    out = c.summarize_checkpoints(recs, now=9000.0)
    lines = out.splitlines()
    assert lines[0].split()[:4] == ["id", "label", "when", "files"]
    body = "\n".join(lines[2:])  # skip header + separator
    # newest first: the row for new1 precedes old1
    assert body.index("new1") < body.index("old1")
    assert "(auto)" in out          # auto flag is surfaced
    assert "just now" in out        # created == now → "just now"


def test_summarize_is_deterministic_with_now() -> None:
    recs = [{"id": "x", "label": "L", "created": 100.0, "files": 1}]
    assert c.summarize_checkpoints(recs, now=200.0) == c.summarize_checkpoints(recs, now=200.0)


# ───────────────────────────── restore_plan ──────────────────────────────────

def test_restore_plan_splits_restore_and_remove() -> None:
    snap = {"a.py", "b.py"}
    current = {"b.py", "c.py"}  # c.py created since the snapshot
    plan = c.restore_plan(snap, current)
    assert plan["restore"] == ["a.py", "b.py"]      # sorted, full snapshot
    assert plan["would_remove"] == ["c.py"]         # only files not in snapshot


def test_restore_plan_never_removes_snapshot_files() -> None:
    plan = c.restore_plan({"x"}, {"x"})
    assert plan["would_remove"] == []


# ─────────────────── impure: file-copy fallback round-trip ────────────────────
# No git here — exercises the non-git path against tmp_path, per the brief.

def test_create_and_restore_filecopy_roundtrip(tmp_path, monkeypatch) -> None:
    # Redirect the user checkpoint store into tmp so we never touch ~/.ronin.
    monkeypatch.setattr(c, "USER_CHECKPOINT_DIR", tmp_path / "store")

    root = tmp_path / "proj"
    root.mkdir()
    (root / "keep.py").write_text("v1\n", encoding="utf-8")

    cp = c.create_checkpoint(root, "before changes", now=1000.0)
    assert cp.kind == "copy"
    assert cp.id.endswith("-before-changes")
    assert cp.files == 1

    # mutate: edit the tracked file + add a brand-new file
    (root / "keep.py").write_text("v2-broken\n", encoding="utf-8")
    (root / "oops.py").write_text("garbage\n", encoding="utf-8")

    res = c.restore_checkpoint(root, cp.id, now=2000.0)
    assert res.ok is True
    assert (root / "keep.py").read_text() == "v1\n"   # restored
    assert not (root / "oops.py").exists()            # created-since → removed
    assert res.removed == 1
    # reversible: a pre-restore safety snapshot was taken
    assert res.safety_id is not None
    assert res.safety_id != cp.id


def test_restore_unknown_id_is_safe_noop(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(c, "USER_CHECKPOINT_DIR", tmp_path / "store")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "f.py").write_text("hello\n", encoding="utf-8")

    res = c.restore_checkpoint(root, "does-not-exist", now=1.0)
    assert res.ok is False
    assert (root / "f.py").read_text() == "hello\n"   # nothing touched
