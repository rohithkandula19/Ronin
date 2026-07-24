"""The eval harness is honest: frozen set enforced, provenance stamped, no
fabricated numbers (a stub provider here produces REAL scored zeros)."""
from __future__ import annotations

import hashlib

from ronin_training.eval_runner import (
    EvalReport,
    ProviderResponse,
    assert_frozen_eval_set,
    run_evals,
    write_report,
)


def test_frozen_pin_accepts_pinned_bytes(tmp_path, monkeypatch):
    evals = tmp_path / "e.jsonl"
    evals.write_text('{"eval_id": "x", "category": "grounding"}\n')
    pin = tmp_path / "pin.sha256"
    monkeypatch.setattr("ronin_training.eval_runner._EVAL_PIN", pin)
    assert assert_frozen_eval_set(evals, repin=True) == []          # freeze
    assert assert_frozen_eval_set(evals) == []                      # clean pass
    evals.write_text('{"eval_id": "x", "category": "grounding", "tampered": 1}\n')
    errs = assert_frozen_eval_set(evals)                            # refusal
    assert errs and "sha256" in errs[0]


def test_missing_pin_is_a_refusal_not_a_pass(tmp_path, monkeypatch):
    evals = tmp_path / "e.jsonl"
    evals.write_text("{}\n")
    monkeypatch.setattr("ronin_training.eval_runner._EVAL_PIN", tmp_path / "nope")
    errs = assert_frozen_eval_set(evals)
    assert errs and "frozen pin" in errs[0]


def test_report_carries_provenance_and_per_task_rows(tmp_path):
    cases = [{"eval_id": "a", "category": "grounding", "must_include": ["yes"]},
             {"eval_id": "b", "category": "recovery", "must_include": ["impossible"]}]
    report = run_evals(cases, lambda c: ProviderResponse(text="yes"))
    assert isinstance(report, EvalReport) and report.passed == 1
    out = tmp_path / "r.md"
    write_report(report, out, title="T", provenance={
        "model": "m", "adapter": "(none)", "commit": "deadbeef",
        "eval_set_sha256": hashlib.sha256(b"x").hexdigest(), "timestamp": "t"})
    text = out.read_text()
    assert "## Provenance" in text and "deadbeef" in text
    assert "| a | grounding | PASS |" in text
    assert "| b | recovery | FAIL |" in text
