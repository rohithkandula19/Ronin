"""The preflight: the last thing that runs before a GPU-hour is spent.

Its job is to fail on a machine with no GPU rather than forty minutes into a run, so
these tests care most about the failure paths — a leaky corpus, a task-less row, a
config whose holdout rule is wrong — all reported without importing an ML stack.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from ronin_training.adapter.config import SFT_CONFIG_PATH, load_config
from ronin_training.adapter.preflight import main, preflight, render

REPO_ROOT = Path(__file__).resolve().parents[2]
SFT_PATH = REPO_ROOT / SFT_CONFIG_PATH


def _corpus(path: Path, *, tasks: int = 20, per_task: int = 5) -> Path:
    rows = [
        {
            "task_id": f"task-{task:02d}",
            "id": f"task-{task:02d}-{index:02d}",
            "messages": [{"role": "user", "content": "go"}],
        }
        for task in range(tasks)
        for index in range(per_task)
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def _dpo_corpus(path: Path) -> Path:
    rows = [
        {
            "task_id": f"pref-{task:02d}",
            "prompt": "p",
            "chosen": "c",
            "rejected": "r",
        }
        for task in range(12)
    ]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_a_clean_preflight_validates_splits_and_writes(tmp_path: Path) -> None:
    result = preflight(
        config_path=SFT_PATH,
        rows_path=_corpus(tmp_path / "rows.jsonl"),
        out_dir=tmp_path / "split",
    )
    assert result.ok, render(result)
    assert result.split is not None
    assert result.split.leaked_tasks == ()
    assert result.train_rows == len(result.split.train)
    assert (tmp_path / "split" / "train.jsonl").is_file()
    assert (tmp_path / "split" / "holdout.jsonl").is_file()
    manifest = json.loads((tmp_path / "split" / "split_manifest.json").read_text())
    assert manifest["leaked_tasks"] == []


def test_the_rendered_block_states_every_hyperparameter_and_the_leak_check(
    tmp_path: Path,
) -> None:
    """It goes into the notebook's output, which is the run's only durable record."""
    result = preflight(
        config_path=SFT_PATH,
        rows_path=_corpus(tmp_path / "rows.jsonl"),
        out_dir=tmp_path / "split",
    )
    text = render(result)
    assert "r=16 alpha=32" in text
    assert "mlx scale 2.0" in text
    assert "lr=1e-05 cosine epochs=3 seq=4096" in text
    assert "gate_proj" in text
    assert "holdout by:      task" in text
    assert "LEAKED TASKS:    []" in text
    assert "must be []" in text
    assert "<ronin:tool_call>" in text
    assert "peft/trl kwargs:" in text
    assert "mlx iterations for" in text
    assert text.rstrip().endswith("PREFLIGHT OK — safe to train")


def test_a_dpo_preflight_reads_preference_rows_and_reports_beta(tmp_path: Path) -> None:
    result = preflight(
        config_path=REPO_ROOT / "training/config/adapter_dpo.yaml",
        rows_path=_dpo_corpus(tmp_path / "prefs.jsonl"),
        out_dir=tmp_path / "split",
    )
    assert result.ok, render(result)
    text = render(result)
    assert "beta=0.1" in text
    assert "resume=training/adapters/ronin-adapter-qwen1.5b/sft" in text


def test_the_preflight_accepts_the_harvest_pipelines_own_row_shape(tmp_path: Path) -> None:
    """End-to-end reconciliation: rows exactly as ``ronin_training.harvest`` writes them.

    ``SftExample.as_record()`` emits ``{"messages", "tools", "meta"}`` with the task id
    inside ``meta``. If this half only looked at top-level keys, every real harvested
    row would be rejected — so the shape is asserted here rather than assumed.
    """
    rows = [
        {
            "messages": [
                {"role": "system", "content": "you are ronin"},
                {"role": "user", "content": "read a.py"},
            ],
            "tools": [],
            "meta": {
                "task_id": f"harvested-{task:02d}",
                "run_id": f"run-{task}",
                "model": "kimi",
                "source": "eval_suite",
                "turn_index": index,
                "augmented": False,
                "augment_seed": -1,
                "negative_class": "",
                "synthesized_chosen": False,
            },
        }
        for task in range(15)
        for index in range(3)
    ]
    path = tmp_path / "harvested.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = preflight(
        config_path=SFT_PATH, rows_path=path, out_dir=tmp_path / "split"
    )
    assert result.ok, render(result)
    assert result.split is not None
    assert result.split.holdout_tasks
    assert all(task.startswith("harvested-") for task in result.split.holdout_tasks)
    assert result.split.leaked_tasks == ()


def test_a_row_with_no_task_identity_fails_the_preflight(tmp_path: Path) -> None:
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps({"messages": []}) + "\n", encoding="utf-8")
    result = preflight(config_path=SFT_PATH, rows_path=rows, out_dir=tmp_path / "split")
    assert not result.ok
    assert any("no task identity" in problem for problem in result.problems)
    assert "PREFLIGHT FAILED — stop" in render(result)


def test_a_single_task_corpus_fails_the_preflight(tmp_path: Path) -> None:
    result = preflight(
        config_path=SFT_PATH,
        rows_path=_corpus(tmp_path / "rows.jsonl", tasks=1, per_task=40),
        out_dir=tmp_path / "split",
    )
    assert not result.ok
    assert any("more task variety" in problem for problem in result.problems)


def test_a_config_whose_holdout_is_by_example_fails_the_preflight(tmp_path: Path) -> None:
    """The one rule that inflates a score invisibly, caught before any GPU time."""
    import yaml

    raw = yaml.safe_load(SFT_PATH.read_text(encoding="utf-8"))
    raw["holdout"]["by"] = "example"
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(raw), encoding="utf-8")
    result = preflight(
        config_path=bad,
        rows_path=_corpus(tmp_path / "rows.jsonl"),
        out_dir=tmp_path / "split",
    )
    assert not result.ok
    assert any("holdout.by must be 'task'" in error for error in result.validation.errors)


def test_the_preflight_imports_no_ml_stack(tmp_path: Path) -> None:
    """It must run on the machine that has no GPU — that is the whole point."""
    preflight(
        config_path=SFT_PATH,
        rows_path=_corpus(tmp_path / "rows.jsonl"),
        out_dir=tmp_path / "split",
    )
    for heavy in ("torch", "peft", "trl", "transformers", "mlx", "mlx_lm"):
        assert heavy not in sys.modules, f"preflight pulled in {heavy}"


# ------------------------------------------------------------------------ the CLI


def test_the_cli_exits_zero_on_a_clean_preflight(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--config",
            str(SFT_PATH),
            "--rows",
            str(_corpus(tmp_path / "rows.jsonl")),
            "--out",
            str(tmp_path / "split"),
        ]
    )
    assert code == 0
    assert "PREFLIGHT OK" in capsys.readouterr().out


def test_the_cli_exits_nonzero_and_says_stop_on_a_bad_corpus(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = tmp_path / "rows.jsonl"
    rows.write_text(json.dumps({"messages": []}) + "\n", encoding="utf-8")
    code = main(
        ["--config", str(SFT_PATH), "--rows", str(rows), "--out", str(tmp_path / "s")]
    )
    assert code == 1
    assert "PREFLIGHT FAILED" in capsys.readouterr().out


def test_the_cli_exits_two_on_an_unreadable_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "--config",
            str(tmp_path / "nope.yaml"),
            "--rows",
            str(_corpus(tmp_path / "rows.jsonl")),
            "--out",
            str(tmp_path / "s"),
        ]
    )
    assert code == 2
    assert "PREFLIGHT FAILED" in capsys.readouterr().out


def test_the_cli_can_also_write_the_mlx_config(tmp_path: Path) -> None:
    out = tmp_path / "mlx_sft.yaml"
    code = main(
        [
            "--config",
            str(SFT_PATH),
            "--rows",
            str(_corpus(tmp_path / "rows.jsonl")),
            "--out",
            str(tmp_path / "split"),
            "--mlx-config",
            str(out),
        ]
    )
    assert code == 0
    body = out.read_text(encoding="utf-8")
    assert "lora_parameters" in body
    assert "scale: 2.0" in body
    # The iteration count comes from the real split, so it must equal what the config
    # derives from the split's row count — not a default baked into the file.
    split_rows = sum(
        1 for line in (tmp_path / "split" / "train.jsonl").read_text().splitlines() if line
    )
    assert f"iters: {load_config(SFT_PATH).iters_for(split_rows)}\n" in body
