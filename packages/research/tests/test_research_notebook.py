"""Research/citations: honesty invariants — no fabrication, no dangling cites."""
from __future__ import annotations

import pytest

from ronin_research import (
    ClaimStatus,
    Notebook,
    Source,
    SupportStrength,
    UnknownSourceError,
)


def _nb() -> Notebook:
    return Notebook(question="What is the capital of France?")


def test_claim_cannot_cite_a_nonexistent_source():
    nb = _nb()
    with pytest.raises(UnknownSourceError, match="not in the notebook"):
        nb.add_claim("Paris is the capital.", source_ids=("src_fake",))


def test_supported_claim_maps_to_real_source():
    nb = _nb()
    s = nb.add_source(Source(title="Encyclopedia entry: France", url="https://example.org/fr",
                             published_date="2025-01-01", retrieved_date="2026-07-22"))
    c = nb.add_claim("Paris is the capital.", source_ids=(s.id,),
                     support=SupportStrength.strong)
    assert nb.status_of(c) is ClaimStatus.supported


def test_unsourced_claim_is_labeled_inference_not_cited():
    nb = _nb()
    c = nb.add_claim("Paris is probably the largest city too.")
    assert nb.status_of(c) is ClaimStatus.inference
    assert c.source_ids == ()
    assert "INFERENCE" in nb.report()


def test_deleting_source_drops_citations_no_dangling():
    nb = _nb()
    s = nb.add_source(Source(title="entry"))
    c = nb.add_claim("Paris is the capital.", source_ids=(s.id,))
    assert nb.status_of(c) is ClaimStatus.supported
    assert nb.delete_source(s.id) is True
    # the claim now has no live sources — reported as UNSUPPORTED, never dangling
    updated = next(cl for cl in nb.claims() if cl.id == c.id)
    assert updated.source_ids == ()
    assert nb.status_of(updated) is ClaimStatus.unsupported
    assert s.id not in nb.report()  # no dangling citation id


def test_contradiction_is_visible():
    nb = _nb()
    a = nb.add_source(Source(title="says X"))
    b = nb.add_source(Source(title="says not X"))
    c = nb.add_claim("X is true.", source_ids=(a.id, b.id), contradicted=True)
    assert nb.status_of(c) is ClaimStatus.contradicted
    assert "CONTRADICTED" in nb.report()


def test_no_sources_report_states_model_knowledge():
    nb = _nb()
    nb.add_claim("Answer based on what the model knows.")
    report = nb.report()
    assert "No sources were retrieved" in report
    assert "model knowledge" in report


def test_published_and_retrieved_dates_are_separate():
    nb = _nb()
    s = nb.add_source(Source(title="t", published_date="2020-05-01",
                             retrieved_date="2026-07-22"))
    assert s.published_date != s.retrieved_date
    assert "2020-05-01" in nb.report() and "2026-07-22" in nb.report()


def test_rejected_sources_are_shown():
    nb = _nb()
    nb.reject_source(Source(title="low-quality blog"), reason="unreliable")
    assert "Rejected sources" in nb.report()
    assert len(nb.rejected_sources()) == 1


def test_coverage_stats():
    nb = _nb()
    s = nb.add_source(Source(title="t"))
    nb.add_claim("supported one", source_ids=(s.id,))
    nb.add_claim("inference one")
    cov = nb.coverage()
    assert cov["claims"] == 2 and cov["supported"] == 1 and cov["inference"] == 1
    assert cov["coverage"] == 0.5


def test_url_is_never_auto_generated():
    """A file source legitimately has no URL; nothing invents one."""
    nb = _nb()
    s = nb.add_source(Source(title="uploaded.pdf", kind="file"))
    assert s.url == ""
