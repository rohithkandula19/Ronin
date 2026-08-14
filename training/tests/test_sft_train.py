"""The SFT training entrypoint — the offline ``--check`` gate, proven with no torch/mlx.

``--check`` is the smoke this environment runs: it validates the profile, the split, the
learnability of every train row, and holdout validity, then prints the recipe — importing
no training stack. The real train step is GPU/MLX-gated and errors cleanly here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from ronin_training.sft.profiles import QWEN1P5B_DEBUG
from ronin_training.sft.train import TrainError, check_run, main

_CONFIG = Path(__file__).resolve().parents[1] / "config" / "sft_qwen1p5b_debug.yaml"


def _row(task: str) -> dict[str, Any]:
    return {
        "task_id": task,
        "messages": [
            {"role": "system", "content": "you are ronin"},
            {"role": "user", "content": f"work {task}"},
            {
                "role": "assistant",
                "content": "on it",
                "tool_calls": [{"function": {"name": "read", "arguments": {"path": "x"}}}],
            },
            {"role": "tool", "content": "ok"},
            {"role": "assistant", "content": "done"},
        ],
    }


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def test_check_validates_the_split_and_builds_a_recipe() -> None:
    report = check_run(
        QWEN1P5B_DEBUG,
        [_row(f"t{i:02d}") for i in range(12)],
        config_path=str(_CONFIG),
        data_dir="data.jsonl",
        out_dir="out",
        seed=0,
        fraction=0.10,
    )
    assert report.train_examples > 0 and report.holdout_examples > 0
    assert report.holdout_tasks
    assert "ronin_training.sft.train" in " ".join(report.recipe)
    assert "check" in report.render() or "would run" in report.render()


def test_check_refuses_a_row_that_masks_to_nothing() -> None:
    rows = [_row(f"t{i:02d}") for i in range(11)]
    rows.append({"task_id": "empty", "messages": [{"role": "user", "content": "no assistant"}]})
    with pytest.raises(TrainError, match="mask to nothing"):
        check_run(
            QWEN1P5B_DEBUG,
            rows,
            config_path=str(_CONFIG),
            data_dir="d",
            out_dir="o",
            seed=0,
            fraction=0.10,
        )


def test_main_check_exits_zero_and_imports_no_training_stack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import sys

    data = _write_rows(tmp_path / "rows.jsonl", [_row(f"t{i:02d}") for i in range(12)])
    code = main(
        ["--config", str(_CONFIG), "--data", str(data), "--out", str(tmp_path / "out"), "--check"]
    )
    assert code == 0
    assert "sft check" in capsys.readouterr().out
    # The whole point of --check: the heavy training stack was never imported.
    assert "torch" not in sys.modules
    assert "trl" not in sys.modules


def test_main_reports_a_missing_data_file_as_exit_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["--config", str(_CONFIG), "--data", str(tmp_path / "absent.jsonl"), "--check"])
    assert code == 1
    assert "sft train:" in capsys.readouterr().err
