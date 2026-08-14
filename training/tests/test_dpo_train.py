"""The DPO training entrypoint — the offline ``--check`` gate, proven with no torch/trl."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from ronin_training.dpo.pairs import PreferenceClass, pair_from_rejection
from ronin_training.dpo.train import DPOTrainError, check_run, main

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "dpo_qwen7b.yaml"

_PROMPT: list[dict[str, Any]] = [
    {"role": "system", "content": "you are ronin"},
    {"role": "user", "content": "fix it"},
]


def _rows(n_per_class: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pref in (
        PreferenceClass.BLIND_EDIT,
        PreferenceClass.REPEATED_ACTION,
        PreferenceClass.PREMATURE_DONE,
    ):
        for i in range(n_per_class):
            rejected = {"role": "assistant", "content": f"{pref} attempt {i}"}
            rows.append(pair_from_rejection(_PROMPT, rejected, pref).to_row())
    return rows


def _write(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_check_validates_pairs_and_reports_balance() -> None:
    from ronin_training.dpo.profile import QWEN7B_DPO

    report = check_run(QWEN7B_DPO, _rows(), config_path=str(_CONFIG), data_dir="d", out_dir="o")
    assert report.pairs == 9
    assert report.balanced is True
    assert "ronin_training.dpo.train" in " ".join(report.recipe)


def test_check_refuses_a_pair_whose_sides_are_identical() -> None:
    from ronin_training.dpo.profile import QWEN7B_DPO

    same = {"role": "assistant", "content": "identical"}
    bad = [
        {"prompt": _PROMPT, "chosen": [same], "rejected": [dict(same)], "pref_class": "blind_edit"}
    ]
    with pytest.raises(DPOTrainError, match="identical"):
        check_run(QWEN7B_DPO, bad, config_path=str(_CONFIG), data_dir="d", out_dir="o")


def test_require_balanced_refuses_an_unbalanced_set() -> None:
    from ronin_training.dpo.profile import QWEN7B_DPO

    rows = _rows(3)[:7]  # 3 + 3 + 1 -> not balanced
    with pytest.raises(DPOTrainError, match="not balanced"):
        check_run(
            QWEN7B_DPO,
            rows,
            config_path=str(_CONFIG),
            data_dir="d",
            out_dir="o",
            require_balanced=True,
        )


def test_main_check_exits_zero_and_imports_no_training_stack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data = _write(tmp_path / "pairs.jsonl", _rows())
    code = main(
        ["--config", str(_CONFIG), "--data", str(data), "--out", str(tmp_path / "out"), "--check"]
    )
    assert code == 0
    assert "dpo check" in capsys.readouterr().out
    assert "torch" not in sys.modules
    assert "trl" not in sys.modules


def test_main_reports_a_missing_data_file_as_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--config", str(_CONFIG), "--data", str(tmp_path / "absent.jsonl"), "--check"])
    assert code == 1
    assert "dpo train:" in capsys.readouterr().err
