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
    return [
        DatasetItem(
            id=f"{prefix}-{i:03d}", instruction=instr, output=ans,
            source_type=SourceType.human_reviewed, owner="ronin-maintainers",
            license="owner", consent=Consent.owner_self,
            redaction=RedactionState.clean_reviewed, industry="coding",
        )
        for i, (_cat, instr, ans) in enumerate(rows)
    ]


def test_corpus_present_and_sized():
    c = _load()
    assert len(c.TRAIN) >= 30
    assert len(c.LOCKED_EVAL) >= 6


def test_all_training_rows_are_training_eligible():
    c = _load()
    ds = ProvenanceDataset(_items(c.TRAIN, "t"))
    assert len(ds.eligible()) == len(c.TRAIN), ds.excluded()


def test_no_duplicates_or_empties():
    c = _load()
    q = analyze_quality(_items(c.TRAIN, "t"))
    assert q.exact_duplicates == []
    assert q.near_duplicates == []
    assert q.empty_outputs == [] and q.too_short == []


def test_locked_eval_not_contaminated_by_training():
    c = _load()
    contam = contamination_check(_items(c.TRAIN, "t"), _items(c.LOCKED_EVAL, "e"))
    assert contam == [], f"locked eval overlaps training: {contam}"


def test_category_coverage():
    c = _load()
    cats = {row[0] for row in c.TRAIN}
    # the program's core behavior targets must all be represented
    required = {"read_before_write", "refusal_unsafe", "approval_aware", "verification",
                "no_invention", "scope_control", "planning", "failure_recovery"}
    assert required <= cats, f"missing categories: {required - cats}"
