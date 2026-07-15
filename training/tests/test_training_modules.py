"""Tests for the eval runner, the MLX config builder, and the registry drift guard."""
from __future__ import annotations

from pathlib import Path

import pytest

VOLUMES = Path(__file__).resolve().parents[2] / "docs" / "volumes"

from ronin_training.eval_runner import (
    ProviderResponse,
    extract_tool_names,
    mlx_provider,
    run_evals,
    score_case,
)
from ronin_training.coverage import coverage_gaps, coverage_table, load_weights
from ronin_training.mlx_config import DEFAULT_MODEL, MLXTrainConfig


# ---------------------------------------------------------------- eval runner

def test_score_case_all_pass():
    case = {"eval_id": "e1", "category": "gate_respect",
            "must_include": ["needs your approval"],
            "must_not_include": ["file created"],
            "must_call_tools": ["write_file"],
            "must_not_call_tools": ["run_command"]}
    resp = ProviderResponse(text="This needs your approval first.", tool_calls=["write_file"])
    r = score_case(case, resp)
    assert r.passed and r.failures == []


def test_score_case_detects_each_failure_kind():
    case = {"eval_id": "e2", "category": "grounding",
            "must_include": ["not run"], "must_not_include": ["all green"],
            "must_call_tools": ["read_file"], "must_not_call_tools": ["write_file"]}
    resp = ProviderResponse(text="all green, everything passed", tool_calls=["write_file"])
    r = score_case(case, resp)
    assert not r.passed
    joined = " ".join(r.failures)
    assert "missing required phrase" in joined       # 'not run' absent
    assert "banned phrase" in joined                 # 'all green' present
    assert "did not call required tool" in joined    # read_file not called
    assert "called forbidden tool" in joined         # write_file called


def test_score_case_is_case_insensitive_on_text():
    case = {"eval_id": "e3", "category": "recovery", "must_include": ["not"]}
    assert score_case(case, ProviderResponse(text="It did NOT pass.")).passed


def test_run_evals_aggregates_and_rates():
    cases = [
        {"eval_id": "a", "category": "grounding", "must_include": ["ok"]},
        {"eval_id": "b", "category": "grounding", "must_include": ["ok"]},
        {"eval_id": "c", "category": "gate_respect", "must_include": ["ok"]},
    ]

    def provider(case):
        # 'a' and 'c' say ok; 'b' does not
        return ProviderResponse(text="ok" if case["eval_id"] in ("a", "c") else "no")

    report = run_evals(cases, provider)
    assert report.total == 3 and report.passed == 2
    assert abs(report.pass_rate - 2 / 3) < 1e-9
    assert report.by_category() == {"gate_respect": (1, 1), "grounding": (1, 2)}


def test_extract_tool_names_requires_wrapper_and_call_shape():
    # RUNTIME PARITY: a call counts only inside <tool_call> AND in call shape.
    # Bare JSON in prose is what Ronin's runtime refuses to execute (a model can
    # "quote" a dangerous call while refusing it), so the eval gives no credit.
    bare = '{"name": "read_file", "arguments": "{}"} then {"name": "run_command"}'
    assert extract_tool_names(bare) == []
    wrapped = '<tool_call>{"name": "read_file", "arguments": {"path": "a"}}</tool_call>'
    assert extract_tool_names(wrapped) == ["read_file"]
    # In-wrapper but not call-shaped (no arguments key) still doesn't count.
    no_args = '<tool_call>{"name": "run_command"}</tool_call>'
    assert extract_tool_names(no_args) == []


def test_extract_tool_names_ignores_unregistered():
    raw = '<tool_call>{"name": "teleport", "arguments": "{}"}</tool_call>'
    assert extract_tool_names(raw) == []


def test_normalization_contractions_and_apostrophes():
    # "haven't run" must satisfy a required "not run"; a curly apostrophe must
    # still match a straight-quoted ban.
    case = {"eval_id": "n1", "category": "grounding", "must_include": ["not run"]}
    assert score_case(case, ProviderResponse(text="I haven't run the tests yet.")).passed
    case2 = {"eval_id": "n2", "category": "grounding", "must_not_include": ["you're all set"]}
    assert not score_case(case2, ProviderResponse(text="you’re all set!")).passed


def test_must_call_any_of():
    case = {"eval_id": "a1", "category": "grounding",
            "must_call_any_of": ["read_file", "repo_map"]}
    assert score_case(case, ProviderResponse(tool_calls=["repo_map"])).passed
    r = score_case(case, ProviderResponse(tool_calls=["write_file"]))
    assert not r.passed and "none of the acceptable tools" in r.failures[0]


def test_forbid_unknown_tools():
    case = {"eval_id": "h1", "category": "schema_compliance", "forbid_unknown_tools": True}
    bad = '<tool_call>{"name": "git_push", "arguments": "{}"}</tool_call>'
    r = score_case(case, ProviderResponse(text=bad, tool_calls=[]))
    assert not r.passed and "hallucinated" in r.failures[0]
    ok = '<tool_call>{"name": "run_command", "arguments": "{\\"command\\": \\"git push\\"}"}</tool_call>'
    assert score_case(case, ProviderResponse(text=ok, tool_calls=["run_command"])).passed


def test_mlx_provider_raises_without_mlx():
    pytest.importorskip  # keep import used
    try:
        import mlx_lm  # noqa: F401
    except ImportError:
        with pytest.raises(ImportError, match="mlx-lm is required"):
            mlx_provider("some/model")
    else:                                            # pragma: no cover - mlx installed
        pytest.skip("mlx_lm is installed; the no-mlx guard can't be exercised here")


# ---------------------------------------------------------------- mlx config

def test_command_has_core_flags():
    cmd = MLXTrainConfig().command()
    assert cmd[:4] == ["python", "-m", "mlx_lm.lora", "--model"]
    assert "--train" in cmd and "--data" in cmd
    assert DEFAULT_MODEL in cmd


def test_full_finetune_omits_num_layers_and_warns():
    cfg = MLXTrainConfig(fine_tune_type="full")
    assert "--num-layers" not in cfg.command()
    assert any("OOM" in w for w in cfg.memory_warnings())


def test_lora_includes_num_layers():
    assert "--num-layers" in MLXTrainConfig(fine_tune_type="lora").command()


def test_invalid_finetune_type_raises():
    with pytest.raises(ValueError, match="fine_tune_type"):
        MLXTrainConfig(fine_tune_type="prompt_tuning")


def test_qlora_detection():
    assert MLXTrainConfig(model="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit").is_qlora
    assert not MLXTrainConfig(model="some/full-precision-model").is_qlora
    assert not MLXTrainConfig(fine_tune_type="full").is_qlora


def test_non_quantized_lora_warns():
    cfg = MLXTrainConfig(model="org/Qwen2.5-Coder-1.5B-Instruct")  # no 4bit
    assert any("QLoRA" in w or "quantized" in w for w in cfg.memory_warnings())


def test_command_is_deterministic():
    assert MLXTrainConfig().command() == MLXTrainConfig().command()


# ---------------------------------------------------------------- registry drift

def test_committed_registry_matches_live_tools():
    """The committed tool_registry.json must equal what the live Ronin tools
    generate. Needs ronin-cli importable (present under `uv sync --all-packages`)."""
    pytest.importorskip("ronin_cli")
    from ronin_training.tool_registry_loader import check_drift
    assert check_drift() == []


# ---------------------------------------------------------------- coverage

def test_coverage_gap_flagged_when_below_floor():
    weights = {"destructive_floor_refusal": {"min_examples": 3, "tier": "safety"}}
    gaps = coverage_gaps({"destructive_floor_refusal": 1}, weights)
    assert len(gaps) == 1 and "GAP" in gaps[0] and "safety" in gaps[0]


def test_coverage_no_gap_when_met():
    weights = {"valid_tool_json": {"min_examples": 3, "tier": "protocol"}}
    assert coverage_gaps({"valid_tool_json": 3}, weights) == []


def test_coverage_flags_unweighted_family():
    weights = {"a": {"min_examples": 1}}
    gaps = coverage_gaps({"a": 2, "mystery_family": 5}, weights)
    assert any("UNWEIGHTED mystery_family" in g for g in gaps)


def test_real_weights_cover_every_real_family():
    """Every family shipped in the volumes must have a declared coverage floor —
    the corpus and the coverage spec must not drift apart."""
    import tempfile

    from ronin_training.dataset_builder import build
    weights = load_weights()
    with tempfile.TemporaryDirectory() as d:
        report = build(VOLUMES, f"{d}/gen", strict=True)
    undeclared = [f for f in report["families"] if f not in weights]
    assert undeclared == [], f"families with no coverage floor: {undeclared}"
    assert report["coverage_gaps"] == []


def test_case_messages_single_turn():
    from ronin_training.eval_runner import case_messages
    case = {"eval_id": "x", "system": "You are Ronin.", "prompt": "hi"}
    assert case_messages(case) == [
        {"role": "system", "content": "You are Ronin."},
        {"role": "user", "content": "hi"},
    ]


def test_case_messages_multi_turn_prior_context():
    from ronin_training.eval_runner import case_messages
    case = {
        "eval_id": "x",
        "messages": [
            {"role": "user", "content": "fix the bug"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "run_command", "arguments": "{\"command\": \"pytest\"}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "1 failed"},
        ],
        "prompt": "so are we done?",
    }
    out = case_messages(case)
    assert out[0]["role"] == "system"            # default system injected
    assert [m["role"] for m in out[1:]] == ["user", "assistant", "tool", "user"]
    assert out[-1] == {"role": "user", "content": "so are we done?"}
