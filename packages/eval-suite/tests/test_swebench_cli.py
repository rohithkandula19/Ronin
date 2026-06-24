"""Tests for `csk-eval swebench` — offline (evaluator monkeypatched)."""
from __future__ import annotations

import json

from ronin_eval_suite import cli
from ronin_eval_suite.swebench import SWEBenchReport, SWEBenchResult, TaskEvaluation


def _write_tasks(path):
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "instance_id": "demo__pkg-1",
                        "base_commit": "abc",
                        "FAIL_TO_PASS": "[\"t::new\"]",
                        "PASS_TO_PASS": "[\"t::old\"]",
                    }
                ),
                json.dumps(
                    {
                        "instance_id": "demo__pkg-2",
                        "base_commit": "abc",
                        "FAIL_TO_PASS": "[\"t::new2\"]",
                        "PASS_TO_PASS": "[]",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )


def test_load_predictions_accepts_standard_keys(tmp_path):
    p = tmp_path / "preds.jsonl"
    p.write_text(
        "\n".join(
            [
                "# header",
                json.dumps({"instance_id": "a", "model_patch": "diffA"}),
                json.dumps({"instance_id": "b", "patch": "diffB"}),
                json.dumps({"instance_id": "c", "prediction": "diffC"}),
            ]
        ),
        encoding="utf-8",
    )
    preds = cli._load_predictions(p)
    assert preds == {"a": "diffA", "b": "diffB", "c": "diffC"}


def test_swebench_cmd_scores_predictions_and_writes_report(tmp_path, monkeypatch, capsys):
    tasks = tmp_path / "tasks.jsonl"
    _write_tasks(tasks)
    preds = tmp_path / "preds.jsonl"
    preds.write_text(
        "\n".join(
            [
                json.dumps({"instance_id": "demo__pkg-1", "model_patch": "diff --git a b\n+fix\n"}),
                # demo__pkg-2 has no prediction -> unresolved, evaluator not called for it
            ]
        ),
        encoding="utf-8",
    )

    # Resolve task 1 (all its tests pass); evaluator never sees task 2 (no patch).
    def fake_evaluator_factory(repo_root, **kw):
        def evaluate(task, patch):
            return TaskEvaluation(passed_tests=task.fail_to_pass + task.pass_to_pass)

        return evaluate

    monkeypatch.setattr(cli, "make_local_git_evaluator", fake_evaluator_factory)

    out_json = tmp_path / "report.json"
    rc = cli.main(
        [
            "swebench",
            str(tasks),
            "--predictions",
            str(preds),
            "--repo-root",
            str(tmp_path),
            "--json-out",
            str(out_json),
            "--model",
            "ronin-test",
        ]
    )
    assert rc == 0

    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["model"] == "ronin-test"
    assert report["summary"]["total"] == 2.0
    assert report["summary"]["resolved"] == 1.0
    assert report["summary"]["resolved_rate"] == 0.5

    printed = capsys.readouterr().out
    assert "Resolved 1/2" in printed
    assert "50.0%" in printed


def _write_report(path, resolved_by_id, rate):
    rep = SWEBenchReport(
        results=[
            SWEBenchResult(instance_id=i, resolved=r, patch_generated=True)
            for i, r in resolved_by_id.items()
        ],
        summary={"resolved_rate": rate},
    )
    path.write_text(rep.model_dump_json(), encoding="utf-8")


def test_swebench_compare_exits_nonzero_on_regression(tmp_path, capsys):
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    _write_report(base, {"a": True, "b": True}, 1.0)
    _write_report(cand, {"a": True, "b": False}, 0.5)  # 'b' regressed

    rc = cli.main(["swebench-compare", str(base), str(cand)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "100.0% -> 50.0%" in out
    assert "-1 broken" in out


def test_swebench_compare_exits_zero_without_regression(tmp_path, capsys):
    base = tmp_path / "base.json"
    cand = tmp_path / "cand.json"
    _write_report(base, {"a": False, "b": True}, 0.5)
    _write_report(cand, {"a": True, "b": True}, 1.0)  # only improvements

    rc = cli.main(["swebench-compare", str(base), str(cand)])
    assert rc == 0
    assert "+1 resolved" in capsys.readouterr().out
