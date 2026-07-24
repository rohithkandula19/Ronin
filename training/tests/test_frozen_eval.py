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


def test_hf_provider_raises_cleanly_without_the_stack():
    import importlib.util
    import pytest
    from ronin_training.eval_runner import hf_provider
    if importlib.util.find_spec("transformers") is not None:
        pytest.skip("transformers installed here; the guard path can't fire")
    with pytest.raises(ImportError, match="Colab/Kaggle"):
        hf_provider("Qwen/Qwen2.5-Coder-1.5B-Instruct")


def test_provider_flag_parses(monkeypatch):
    # --provider hf reaches the right factory (stubbed; no model load)
    import ronin_training.eval_runner as er
    seen = {}
    def fake_hf(model, *, adapter_path=None, max_tokens=512):
        seen["provider"] = "hf"; seen["adapter"] = adapter_path
        return lambda case: er.ProviderResponse(text="")
    monkeypatch.setattr(er, "hf_provider", fake_hf)
    monkeypatch.setattr(er, "assert_frozen_eval_set", lambda p, repin=False: [])
    monkeypatch.setattr(er, "load_cases", lambda p: [])
    monkeypatch.setattr(er, "write_report", lambda *a, **k: None)
    # provenance hashes the eval FILE as a write_report argument — that file is
    # generated (gitignored), absent on a clean CI checkout; stub it too
    monkeypatch.setattr(er, "_sha256_file", lambda p: "stub")
    er.main(["--provider", "hf", "--model", "m", "--adapter", "x", "--out", "/dev/null"])
    assert seen == {"provider": "hf", "adapter": "x"}
