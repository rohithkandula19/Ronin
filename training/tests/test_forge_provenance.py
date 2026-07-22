"""Forge: provenance eligibility, redaction+review, quality gates, bundle honesty."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ronin_training.forge import (
    BundleConfig,
    JobState,
    JobStateError,
    TrainingJob,
    generate_bundle,
)
from ronin_training.provenance import (
    Consent,
    DatasetItem,
    ProvenanceDataset,
    RedactionState,
    SourceType,
)
from ronin_training.quality import analyze_quality, contamination_check
from ronin_training.redaction import redact


def _item(**overrides) -> DatasetItem:
    base = dict(
        id="i1",
        instruction="Explain how to read a file in this repo.",
        output="Use the read_file tool with a path relative to the repo root.",
        source_type=SourceType.human_reviewed,
        license="owner",
        consent=Consent.owner_self,
        redaction=RedactionState.clean_reviewed,
        industry="coding",
    )
    base.update(overrides)
    return DatasetItem.model_validate(base)


# ------------------------------------------------------- provenance gates

def test_eligible_item_is_eligible():
    ok, reason = _item().training_eligibility()
    assert ok and reason == ""


def test_no_consent_excluded():
    ok, reason = _item(consent=Consent.none).training_eligibility()
    assert not ok and "consent" in reason


def test_proprietary_license_excluded():
    ok, reason = _item(license="proprietary").training_eligibility()
    assert not ok and "license" in reason


def test_unreviewed_redaction_excluded():
    ok, reason = _item(redaction=RedactionState.redacted_unreviewed).training_eligibility()
    assert not ok and "review" in reason


def test_health_and_minor_data_excluded_from_training():
    for sens in ("health", "minor", "credential"):
        ok, reason = _item(sensitivity=sens).training_eligibility()
        assert not ok and sens in reason


def test_synthetic_source_must_be_labeled():
    ok, reason = _item(source_type=SourceType.synthetic, synthetic=False).training_eligibility()
    assert not ok and "synthetic" in reason
    ok2, _ = _item(source_type=SourceType.synthetic, synthetic=True,
                   license="synthetic", consent=Consent.owner_self).training_eligibility()
    assert ok2


def test_explicit_consent_needs_ref():
    ok, reason = _item(consent=Consent.explicit, consent_ref="").training_eligibility()
    assert not ok and "consent_ref" in reason


def test_dataset_report_names_exclusions(tmp_path: Path):
    ds = ProvenanceDataset([
        _item(id="good"),
        _item(id="bad", consent=Consent.none),
    ])
    assert len(ds.eligible()) == 1
    report = ds.report()
    assert "bad" in report and "no consent recorded" in report


# ------------------------------------------------------------- redaction

def test_redaction_finds_and_replaces_common_pii():
    text = ("Contact me at jane.doe@example.com or 555-123-4567. "
            "SSN 123-45-6789, key sk-abcdefghijkl0123456789.")
    result = redact(text)
    kinds = {f.kind for f in result.findings}
    assert {"email", "ssn", "api_key"} <= kinds
    assert "jane.doe@example.com" not in result.text
    assert "123-45-6789" not in result.text
    assert not result.clean


def test_redaction_clean_is_not_a_guarantee():
    # a scanner returning "clean" only means no PATTERNS matched
    result = redact("The quick brown fox writes tests.")
    assert result.clean  # documented as pattern-negative, not a safety proof


def test_redaction_custom_and_disabled():
    r = redact("project CODENAME_ORION ships friday", extra_patterns={"codename": r"CODENAME_\w+"})
    assert any(f.kind == "codename" for f in r.findings)
    r2 = redact("ip 10.0.0.1", disabled_kinds={"ipv4"})
    assert r2.clean


# --------------------------------------------------------------- quality

def test_exact_and_near_duplicates():
    body = "it reads config dot toml first then falls back to environment variables and finally command line flags"
    items = [
        _item(id="a", instruction="explain the config loader", output=body),
        _item(id="b", instruction="explain the config loader", output=body),  # exact dup
        _item(id="c", instruction="explain the config loader", output=body + " carefully"),  # near dup
        _item(id="d", instruction="unrelated task about parsing csv", output="use the csv module to parse rows carefully"),
    ]
    report = analyze_quality(items)
    assert "b" in report.exact_duplicates
    assert any({"a", "c"} == {x, y} for x, y, _ in report.near_duplicates)
    assert report.balance["coding"] >= 1


def test_contamination_check():
    train = [_item(id="t1", instruction="explain the config loader", output="reads config.toml then env")]
    test = [_item(id="e1", instruction="explain the config loader", output="reads config.toml then env")]
    hits = contamination_check(train, test)
    assert hits == [("t1", "e1")]


# --------------------------------------------------------- bundle + jobs

def test_generate_bundle_writes_complete_kit_and_is_generated(tmp_path: Path):
    ds = ProvenanceDataset([_item(id=f"i{n}") for n in range(3)])
    job = generate_bundle(ds, tmp_path / "bundle", BundleConfig(industry="coding"))
    assert job.state is JobState.generated
    assert job.trained is False  # a generated bundle is NOT trained
    for name in ("dataset.jsonl", "config.json", "train.py", "requirements.txt",
                 "README.md", "MODEL_CARD.md", "Modelfile", "estimate.json"):
        assert (tmp_path / "bundle" / name).exists(), name
    readme = (tmp_path / "bundle" / "README.md").read_text()
    assert "Nothing has been trained" in readme
    # only eligible rows written
    rows = [json.loads(l) for l in (tmp_path / "bundle" / "dataset.jsonl").read_text().splitlines()]
    assert len(rows) == 3


def test_bundle_refuses_when_nothing_eligible(tmp_path: Path):
    ds = ProvenanceDataset([_item(id="x", consent=Consent.none)])
    with pytest.raises(ValueError, match="no training-eligible"):
        generate_bundle(ds, tmp_path / "bundle", BundleConfig())


def test_job_cannot_skip_from_generated_to_completed(tmp_path: Path):
    ds = ProvenanceDataset([_item()])
    job = generate_bundle(ds, tmp_path / "b", BundleConfig())
    with pytest.raises(JobStateError):
        job.advance(JobState.completed)  # must go through submitted->running
    job.advance(JobState.submitted)
    job.advance(JobState.running)
    job.advance(JobState.completed)
    assert job.trained is True
    assert [s for s, _ in job.history] == [JobState.submitted, JobState.running, JobState.completed]


def test_generated_bundle_never_reports_trained(tmp_path: Path):
    ds = ProvenanceDataset([_item()])
    job = generate_bundle(ds, tmp_path / "b", BundleConfig())
    # This is the core honesty property Forge must uphold.
    assert job.state is JobState.generated and not job.trained
