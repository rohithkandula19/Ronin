"""Tests for the v3/v3b report aggregator — parsing, verdicts, and honesty rules."""
from __future__ import annotations

from pathlib import Path

from ronin_training.aggregate_comparison import (
    EXPECTED_V3B,
    build_verdict,
    discover_reports,
    parse_report,
    pick_baseline,
    render,
)

REPORTS_DIR = Path(__file__).resolve().parents[1] / "reports"

# A fixture mirroring the exact format eval_runner.write_report produces.
V2_REPORT = """# v2_iter150 (91-case, runtime-parity)

- cases: **91**
- passed: **36**  ·  pass rate: **39.6%**

## By category

| category | passed | total |
|---|---|---|
| final_answer | 7 | 10 |
| gate_respect | 6 | 12 |
| grounding | 5 | 20 |
| multi_turn_stability | 11 | 28 |
| valid_tool_json | 0 | 4 |

## Failures

- **tool_json_read_valid** (valid_tool_json): did not call required tool 'read_file'
"""


def _v3b_report(passed: int, *, tool_json: int = 0, gates: int = 6, cases: int = 91) -> str:
    return f"""# v3b_iter0600 (91-case frozen, runtime-parity)

- cases: **{cases}**
- passed: **{passed}**  ·  pass rate: **{passed / cases * 100:.1f}%**

## By category

| category | passed | total |
|---|---|---|
| gate_respect | {gates} | 12 |
| grounding | 5 | 20 |
| multi_turn_stability | 11 | 28 |
| valid_tool_json | {tool_json} | 4 |
"""


# ------------------------------------------------------------------ parsing

def test_parse_report_exact_format(tmp_path: Path):
    p = tmp_path / "eval91_v2_iter150.md"
    p.write_text(V2_REPORT, encoding="utf-8")
    s = parse_report(p)
    assert s is not None
    assert (s.cases, s.passed) == (91, 36)
    assert s.family == "v2" and s.iteration == 150 and not s.frozen
    assert s.categories["valid_tool_json"] == (0, 4)
    assert s.categories["multi_turn_stability"] == (11, 28)
    assert s.failures == [
        ("tool_json_read_valid", "valid_tool_json", "did not call required tool 'read_file'")
    ]


def test_filename_identity_variants(tmp_path: Path):
    for name, family, iteration, frozen in [
        ("eval91f_v3b_iter0300.md", "v3b", 300, True),
        ("eval91f_v3b_final.md", "v3b", None, True),
        ("eval91_base.md", "base", None, False),
        ("eval91f_v2_iter150.md", "v2", 150, True),
    ]:
        p = tmp_path / name
        p.write_text(V2_REPORT, encoding="utf-8")
        s = parse_report(p)
        assert s is not None, name
        assert (s.family, s.iteration, s.frozen) == (family, iteration, frozen), name


def test_non_report_markdown_is_ignored(tmp_path: Path):
    (tmp_path / "eval91_notes.md").write_text("# just notes\nno scores here\n", encoding="utf-8")
    assert discover_reports(tmp_path) == []


# ------------------------------------------------------------------ verdicts

def test_baseline_prefers_frozen_rerun(tmp_path: Path):
    (tmp_path / "eval91_v2_iter150.md").write_text(V2_REPORT, encoding="utf-8")
    (tmp_path / "eval91f_v2_iter150.md").write_text(V2_REPORT, encoding="utf-8")
    baseline = pick_baseline(discover_reports(tmp_path))
    assert baseline is not None and baseline.frozen


def test_verdict_beats_and_tool_json_improvement(tmp_path: Path):
    (tmp_path / "eval91f_v2_iter150.md").write_text(V2_REPORT, encoding="utf-8")
    (tmp_path / "eval91f_v3b_iter0600.md").write_text(
        _v3b_report(40, tool_json=2), encoding="utf-8")
    scores = discover_reports(tmp_path)
    v = build_verdict(scores, pick_baseline(scores))
    assert v.beats_baseline() is True
    improved, _ = v.category_improved("valid_tool_json")
    assert improved is True
    regressed, _ = v.gates_regressed()
    assert regressed is False


def test_verdict_does_not_beat(tmp_path: Path):
    (tmp_path / "eval91f_v2_iter150.md").write_text(V2_REPORT, encoding="utf-8")
    (tmp_path / "eval91f_v3b_iter0600.md").write_text(
        _v3b_report(33, gates=5), encoding="utf-8")
    scores = discover_reports(tmp_path)
    v = build_verdict(scores, pick_baseline(scores))
    assert v.beats_baseline() is False
    regressed, _ = v.gates_regressed()
    assert regressed is True  # 5 < baseline's 6 — a real regression must be called out


def test_non_frozen_and_mismatched_reports_are_excluded_not_dropped(tmp_path: Path):
    (tmp_path / "eval91f_v2_iter150.md").write_text(V2_REPORT, encoding="utf-8")
    (tmp_path / "eval91_v3_iter150.md").write_text(_v3b_report(50), encoding="utf-8")
    (tmp_path / "eval91f_v3b_iter0300.md").write_text(
        _v3b_report(50, cases=90), encoding="utf-8")
    scores = discover_reports(tmp_path)
    v = build_verdict(scores, pick_baseline(scores))
    assert v.candidates == []           # neither counts as frozen-set evidence
    assert len(v.excluded) == 2         # both named with reasons, not silently dropped
    assert v.beats_baseline() is None   # no evidence -> no verdict, never a guess


# ------------------------------------------------------------------ rendering

def test_render_incomplete_evidence_is_stated(tmp_path: Path):
    (tmp_path / "eval91f_v2_iter150.md").write_text(V2_REPORT, encoding="utf-8")
    doc = render(discover_reports(tmp_path), tmp_path,
                 model="m", frozen_evals=tmp_path / "nope.jsonl")
    assert "Missing evidence" in doc
    assert "Evidence phase incomplete" in doc
    assert "v2 iter-150 remains" in doc
    for name in EXPECTED_V3B:
        assert name in doc  # every absent report is named


def test_render_adopts_a_winning_v3b(tmp_path: Path):
    (tmp_path / "eval91f_v2_iter150.md").write_text(V2_REPORT, encoding="utf-8")
    for name in EXPECTED_V3B:
        (tmp_path / name).write_text(_v3b_report(40, tool_json=1), encoding="utf-8")
    doc = render(discover_reports(tmp_path), tmp_path,
                 model="m", frozen_evals=tmp_path / "nope.jsonl")
    assert "Missing evidence" not in doc
    assert "**Adopt" in doc and "40/91" in doc


def test_render_keeps_v2_when_v3b_loses(tmp_path: Path):
    (tmp_path / "eval91f_v2_iter150.md").write_text(V2_REPORT, encoding="utf-8")
    for name in EXPECTED_V3B:
        (tmp_path / name).write_text(_v3b_report(30), encoding="utf-8")
    doc = render(discover_reports(tmp_path), tmp_path,
                 model="m", frozen_evals=tmp_path / "nope.jsonl")
    assert "**Keep v2 iter-150.**" in doc
    assert "hypotheses to test, not measured findings" in doc


# ------------------------------------------- pin the parser to the real repo

def test_parses_the_real_committed_reports():
    scores = discover_reports(REPORTS_DIR)
    by_label = {(s.label, s.frozen): s for s in scores}
    v2 = by_label.get(("v2 iter-150", False)) or by_label.get(("v2 iter-150", True))
    assert v2 is not None, "committed eval91_v2_iter150.md must parse"
    assert (v2.passed, v2.cases) == (36, 91)
    assert v2.categories["valid_tool_json"] == (0, 4)
    assert v2.categories["grounding"] == (5, 20)
    assert v2.categories["multi_turn_stability"] == (11, 28)
    assert len(scores) >= 7  # base + v1 + five v2 checkpoints ship on main
