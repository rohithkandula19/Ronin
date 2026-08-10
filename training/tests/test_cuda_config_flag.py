"""``train_cuda.py --config``: one recipe per run, and the report says which.

The CUDA trainer carried its hyperparameters as module constants — the *v1*
ronin-code-1.5b recipe — while ``training/config/adapter_sft.yaml`` describes the
phase-12 adapter. The two disagreed on the single most consequential number by a factor
of twenty (lr 2e-4 against 1.0e-5) and on context length by half (2048 against 4096).

Two files claiming to describe one training run, disagreeing like that, is worse than
one being absent: whichever the operator did not read is silently wrong, the artifact
is named the same either way, and the resulting eval number is attributed to a recipe
that did not produce it. So the config is selectable, validated before any GPU work,
and recorded in the run report.

No torch, no peft, no GPU here. :func:`resolve` is deliberately separate from the
trainer body so exactly this is testable — it is the whole reason that function exists
rather than the values being read inline.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "training" / "cuda" / "train_cuda.py"
SFT_CONFIG = ROOT / "training" / "config" / "adapter_sft.yaml"


def load_script() -> object:
    """Import ``train_cuda.py`` by path — it is a script, not a package module."""
    spec = importlib.util.spec_from_file_location("train_cuda_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> object:
    return load_script()


# --------------------------------------------------------------------------- #
# The two lanes
# --------------------------------------------------------------------------- #


def test_no_config_uses_the_v1_constants(script: object) -> None:
    """The default is unchanged, so the v1 run stays reproducible."""
    base, lora, train, seed, problems = script.resolve("")  # type: ignore[attr-defined]
    assert problems == []
    assert base == "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    assert seed == 42
    assert train["learning_rate"] == 2e-4
    assert train["max_length"] == 2048
    assert lora["r"] == 16 and lora["task_type"] == "CAUSAL_LM"


def test_a_config_overrides_every_hyperparameter_that_differed(script: object) -> None:
    """The point of the flag, asserted as the specific numbers that were in conflict."""
    base, lora, train, seed, problems = script.resolve(str(SFT_CONFIG))  # type: ignore[attr-defined]
    assert problems == [], problems
    assert base == "Qwen/Qwen2.5-Coder-1.5B-Instruct"
    assert train["learning_rate"] == pytest.approx(1.0e-5)
    assert train["max_length"] == 4096
    assert train["per_device_train_batch_size"] == 1
    assert train["gradient_accumulation_steps"] == 4
    assert train["gradient_checkpointing"] is True
    assert train["lr_scheduler_type"] == "cosine"
    assert seed == 0
    assert lora["target_modules"] == [
        "q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"
    ]


def test_the_two_lanes_actually_disagree(script: object) -> None:
    """Pins the conflict itself, so nobody "tidies up" one side into the other.

    If these ever match, either the configs were reconciled deliberately — in which
    case this test should be deleted in the same commit — or someone edited one file
    believing it was the only one.
    """
    _, _, builtin, _, _ = script.resolve("")  # type: ignore[attr-defined]
    _, _, from_yaml, _, _ = script.resolve(str(SFT_CONFIG))  # type: ignore[attr-defined]
    assert builtin["learning_rate"] != from_yaml["learning_rate"]
    assert builtin["max_length"] != from_yaml["max_length"]


def test_output_dir_is_not_taken_from_the_config(script: object) -> None:
    """``--out`` is the flag a human passes; a config silently winning would surprise.

    The config *does* carry ``adapter_out``, which is right for the mlx projection —
    but here the trainer writes ``<out>/adapters`` and ``<out>/fused``, and two sources
    for one destination is how an operator loses a checkpoint.
    """
    _, _, train, _, _ = script.resolve(str(SFT_CONFIG))  # type: ignore[attr-defined]
    assert "output_dir" not in train


# --------------------------------------------------------------------------- #
# Failing before the GPU
# --------------------------------------------------------------------------- #


def test_a_missing_config_is_reported_not_raised(script: object) -> None:
    """Returned as problems so ``--check`` prints all of them at once."""
    _, _, _, _, problems = script.resolve("/no/such/config.yaml")  # type: ignore[attr-defined]
    assert problems
    assert "/no/such/config.yaml" in problems[0]


def test_an_invalid_config_fails_before_any_gpu_work(tmp_path: Path, script: object) -> None:
    """A config error found forty minutes into a rented box costs real money.

    ``holdout.by: example`` is the specific mistake worth catching: it puts two examples
    of the same task in train and holdout, so the reported score is partly a
    memorisation score — and nothing about the run looks wrong.
    """
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        SFT_CONFIG.read_text(encoding="utf-8").replace("by: task", "by: example"),
        encoding="utf-8",
    )
    _, _, _, _, problems = script.resolve(str(bad))  # type: ignore[attr-defined]
    assert problems, "holdout by example must not be accepted"


# --------------------------------------------------------------------------- #
# --check, end to end through argv
# --------------------------------------------------------------------------- #


def run_check(*extra: str) -> dict[str, object]:
    """``--check`` as a subprocess: the way CI and an operator actually invoke it."""
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--data", "/nonexistent", *extra],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return dict(json.loads(completed.stdout))


def test_check_names_which_recipe_it_would_use() -> None:
    """A report that cannot answer "was this the 2e-4 run or the 1e-5 run" is useless.

    Two runs whose reports are indistinguishable cannot be compared to each other, and
    comparing runs is the entire purpose of keeping reports.
    """
    default = run_check()
    assert default["config"] == "(built-in v1 constants)"

    chosen = run_check("--config", str(SFT_CONFIG))
    assert chosen["config"] == str(SFT_CONFIG)
    train = chosen["train"]
    assert isinstance(train, dict)
    assert train["learning_rate"] == pytest.approx(1.0e-5)


def test_check_still_reports_missing_data_alongside_config_problems() -> None:
    """Both classes of problem in one pass, so an operator fixes them in one trip."""
    payload = run_check()
    errors = payload["errors"]
    assert isinstance(errors, list)
    assert any("train.jsonl" in str(error) for error in errors)
