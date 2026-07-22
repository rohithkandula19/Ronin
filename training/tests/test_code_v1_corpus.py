"""Guard the ronin-code-v1 corpus: eligible, deduped, and leak-free by construction."""
from __future__ import annotations

import importlib.util
from pathlib import Path

from ronin_training.provenance import (
    Consent,
    DatasetItem,
    ProvenanceDataset,
    RedactionState,
    SourceType,
)
from ronin_training.quality import analyze_quality, contamination_check

_CORPUS = Path(__file__).resolve().parents[1] / "datasets" / "ronin-code-v1" / "corpus.py"


def _load():
    spec = importlib.util.spec_from_file_location("code_v1_corpus", _CORPUS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _items(rows, prefix):
    """Handles 3-tuples (cat, instr, out) and 4-tuples (cat, context, instr, out)."""
    out = []
    for i, row in enumerate(rows):
        if len(row) == 4:
            _cat, context, instr, ans = row
        else:
            _cat, instr, ans = row
            context = ""
        out.append(DatasetItem(
            id=f"{prefix}-{i:03d}", instruction=instr, input=context, output=ans,
            source_type=SourceType.human_reviewed, owner="ronin-maintainers",
            license="owner", consent=Consent.owner_self,
            redaction=RedactionState.clean_reviewed, industry="coding",
        ))
    return out


def _all_train(c):
    return c.TRAIN + c.MULTI_TURN


def _all_eval(c):
    return c.LOCKED_EVAL + c.MULTI_TURN_EVAL


def test_corpus_present_and_sized():
    c = _load()
    assert len(c.TRAIN) >= 30
    assert len(c.MULTI_TURN) >= 8
    assert len(c.LOCKED_EVAL) >= 6
    assert len(c.MULTI_TURN_EVAL) >= 1


def test_all_training_rows_are_training_eligible():
    c = _load()
    rows = _all_train(c)
    ds = ProvenanceDataset(_items(rows, "t"))
    assert len(ds.eligible()) == len(rows), ds.excluded()


def test_no_duplicates_or_empties():
    c = _load()
    q = analyze_quality(_items(_all_train(c), "t"))
    assert q.exact_duplicates == []
    assert q.near_duplicates == []
    assert q.empty_outputs == [] and q.too_short == []


def test_locked_eval_not_contaminated_by_training():
    c = _load()
    contam = contamination_check(_items(_all_train(c), "t"), _items(_all_eval(c), "e"))
    assert contam == [], f"locked eval overlaps training: {contam}"


def test_multi_turn_rows_carry_prior_context():
    c = _load()
    for row in c.MULTI_TURN + c.MULTI_TURN_EVAL:
        assert len(row) == 4, f"multi-turn row must be 4-tuple: {row[:2]}"
        _cat, context, _instr, _out = row
        assert context.strip(), "multi-turn row must carry non-empty prior context"
    items = _items(c.MULTI_TURN, "mt")
    assert all(it.input for it in items)  # context lands in the input field


def test_multi_turn_categories_present():
    c = _load()
    cats = {row[0] for row in c.MULTI_TURN}
    assert any(x.startswith("multi_turn") for x in cats)
    assert {"multi_turn_constraint", "multi_turn_resume"} <= cats


def test_category_coverage():
    c = _load()
    cats = {row[0] for row in c.TRAIN}
    # the program's core behavior targets must all be represented
    required = {"read_before_write", "refusal_unsafe", "approval_aware", "verification",
                "no_invention", "scope_control", "planning", "failure_recovery"}
    assert required <= cats, f"missing categories: {required - cats}"
