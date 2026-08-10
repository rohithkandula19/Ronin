"""The ship gate is enforced in code: a sub-gate score cannot reach `hf
upload`, and the model card can only be filled from a measured, gate-clearing
run. Criteria come from the committed pin, never from the operator."""
from __future__ import annotations

import json
from pathlib import Path

from ronin_training.publish_gate import (
    DEFAULT_GATE,
    evaluate_gate,
    fill_model_card,
    load_gate,
    main,
)

ROOT = Path(__file__).resolve().parents[2]


def _run(passed=61, total=91, vtj=(4, 4), extra_cats=None):
    cats = {"valid_tool_json": list(vtj)}
    cats.update(extra_cats or {})
    return {"passed": passed, "total": total, "by_category": cats,
            "provenance": {"model": "m", "adapter": "a", "commit": "cafebabe",
                           "eval_set_sha256": "e" * 64, "timestamp": "t"}}


def test_the_committed_pin_carries_the_real_criteria():
    gate = load_gate()
    assert DEFAULT_GATE.is_file()
    assert gate["require_all_pass_categories"] == ["valid_tool_json"]
    assert gate["min_passed_exclusive"] == 60
    assert gate["expected_total"] == 91


def test_sub_gate_scores_are_refused():
    gate = load_gate()
    # valid_tool_json 3/4 refuses even with a high overall score
    errs = evaluate_gate(_run(passed=80, vtj=(3, 4)), gate)
    assert errs and "valid_tool_json 3/4" in errs[0]
    # 60/91 is NOT >60 — the boundary refuses
    assert evaluate_gate(_run(passed=60), gate)
    # a partial run can't clear the gate no matter the ratio
    assert evaluate_gate(_run(passed=50, total=50), gate)
    # a run that never exercised the category cannot be verified → refused
    errs = evaluate_gate({"passed": 91, "total": 91, "by_category": {}}, gate)
    assert errs and "absent" in errs[0]


def test_gate_clears_only_on_full_measured_pass():
    assert evaluate_gate(_run(passed=61, vtj=(4, 4)), load_gate()) == []


def test_check_cli_exits_nonzero_on_sub_gate(tmp_path, capsys):
    scores = tmp_path / "s.json"
    scores.write_text(json.dumps({"adapter": _run(passed=55, vtj=(2, 4))}))
    assert main(["check", "--scores", str(scores)]) == 1
    out = capsys.readouterr().out
    assert "PUBLISH REFUSED" in out and "valid_tool_json 2/4" in out
    scores.write_text(json.dumps({"adapter": _run()}))
    assert main(["check", "--scores", str(scores)]) == 0


def test_fill_card_writes_measured_numbers_with_stamp(tmp_path):
    card = (ROOT / "training" / "MODEL_CARD_ronin-code-1.5b.md").read_text()
    scores = {"adapter": _run(passed=63), "baseline": _run(passed=31, vtj=(0, 4))}
    filled = fill_model_card(card, scores, adapter_path="adapters/v5")
    assert "PENDING" not in [l for l in filled.splitlines() if l.startswith("|")][0]
    assert "| protocol eval (of 91) | 31 | 63 | +32 |" in filled
    assert "| valid_tool_json (of 4) | 0/4 | 4/4 |" in filled   # baseline | adapter
    assert "commit `cafebabe`" in filled and "adapter `adapters/v5`" in filled


def test_fill_card_cli_refuses_sub_gate_and_leaves_card_untouched(tmp_path):
    card = tmp_path / "card.md"
    original = "| protocol eval (of 91) | PENDING | PENDING | PENDING |\n"
    card.write_text(original)
    scores = tmp_path / "s.json"
    scores.write_text(json.dumps({"adapter": _run(passed=40, vtj=(1, 4))}))
    assert main(["fill-card", "--scores", str(scores), "--card", str(card)]) == 1
    assert card.read_text() == original


def test_publish_script_gates_before_any_upload():
    script = (ROOT / "training" / "publish_model.sh").read_text()
    assert "set -euo pipefail" in script            # a refusal stops the script
    gate_at = script.index("publish_gate check")
    upload_at = script.index("hf upload")
    assert gate_at < upload_at
    # criteria come from the committed pin, not from flags or env
    assert "publish_gate.json" in script


def test_missing_run_key_is_a_refusal(tmp_path, capsys):
    scores = tmp_path / "s.json"
    scores.write_text(json.dumps({"baseline": _run()}))
    assert main(["check", "--scores", str(scores)]) == 1
    assert "no 'adapter' run" in capsys.readouterr().out
