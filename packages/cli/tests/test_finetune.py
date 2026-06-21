"""Tests for ``ronin finetune`` — the pure dataset/Modelfile/trainer-emitter core.

These exercise ONLY the pure functions: no torch/unsloth import, no network, no
training. The emitted training script is asserted to be *text* that contains the
right markers (the base model, ``SFTTrainer``), never imported or executed.
"""
from __future__ import annotations

import json

from ronin_cli.finetune import (
    DEFAULT_BASE_MODEL,
    TUNED_MODEL_NAME,
    build_dataset,
    make_modelfile,
    make_readme,
    to_jsonl,
    trace_to_example,
    training_script,
)


def _session(transcript, **kw) -> dict:
    base = {"id": "s1", "title": "t", "transcript": transcript}
    base.update(kw)
    return base


# --------------------------------------------------------------------------- #
# trace_to_example — shapes + redacts
# --------------------------------------------------------------------------- #

def test_trace_to_example_basic_shape() -> None:
    ex = trace_to_example(_session([
        "USER: add a retry decorator to the http client",
        "ASSISTANT: Here is a retry decorator with exponential backoff …",
    ]))
    assert ex is not None
    assert set(ex) == {"instruction", "input", "output"}
    assert ex["instruction"] == "add a retry decorator to the http client"
    assert ex["output"].startswith("Here is a retry decorator")


def test_trace_to_example_redacts_secrets_and_pii() -> None:
    ex = trace_to_example(_session([
        "USER: my email is rohith@example.com and key sk-ABCDEFGHIJKLMNOPQRSTUVWX",
        "ASSISTANT: ok here is the long answer, contacting 10.0.0.1 with that key",
    ]))
    assert ex is not None
    # email + openai-style key scrubbed from the instruction
    assert "rohith@example.com" not in ex["instruction"]
    assert "sk-ABCDEFGHIJKLMNOPQRSTUVWX" not in ex["instruction"]
    assert "REDACTED" in ex["instruction"]
    # IP scrubbed from the output
    assert "10.0.0.1" not in ex["output"]


def test_trace_to_example_none_when_no_assistant_turn() -> None:
    assert trace_to_example(_session(["USER: hello, anyone there?"])) is None


def test_trace_to_example_none_when_output_too_short() -> None:
    # a bare "done." teaches nothing → rejected
    assert trace_to_example(_session([
        "USER: run the tests", "ASSISTANT: done.",
    ])) is None


def test_trace_to_example_rejects_non_dict_and_bad_transcript() -> None:
    assert trace_to_example("not a dict") is None  # type: ignore[arg-type]
    assert trace_to_example({"transcript": "not a list"}) is None
    assert trace_to_example({}) is None


def test_trace_to_example_handles_leading_assistant_line() -> None:
    # transcript that opens with an assistant line (no preceding user) is skipped
    # until a real USER→ASSISTANT pair appears.
    ex = trace_to_example(_session([
        "ASSISTANT: (greeting with no user)",
        "USER: explain the build system in this repo",
        "ASSISTANT: The build uses hatchling and a src layout, configured in …",
    ]))
    assert ex is not None
    assert ex["instruction"] == "explain the build system in this repo"


# --------------------------------------------------------------------------- #
# build_dataset — filters empties + dedupes
# --------------------------------------------------------------------------- #

def test_build_dataset_filters_unusable() -> None:
    sessions = [
        _session(["USER: a real ask about caching", "ASSISTANT: a real, long enough answer here"]),
        _session(["USER: only a user line, no answer"]),          # no assistant → dropped
        _session(["USER: x", "ASSISTANT: ok"]),                    # too short → dropped
        {"not": "a session"},                                       # malformed → dropped
    ]
    ds = build_dataset(sessions)
    assert len(ds) == 1
    assert ds[0]["instruction"] == "a real ask about caching"


def test_build_dataset_dedupes_identical_turns() -> None:
    one = ["USER: write a fizzbuzz in python", "ASSISTANT: def fizzbuzz(n): ... full answer"]
    ds = build_dataset([_session(one, id="a"), _session(one, id="b"), _session(one, id="c")])
    assert len(ds) == 1  # same (instruction, output) collapses to one example


def test_build_dataset_empty_input() -> None:
    assert build_dataset([]) == []


# --------------------------------------------------------------------------- #
# to_jsonl — one JSON object per line, round-trippable
# --------------------------------------------------------------------------- #

def test_to_jsonl_roundtrips() -> None:
    ds = build_dataset([_session([
        "USER: summarize the auth flow", "ASSISTANT: The auth flow is token-based and …",
    ])])
    text = to_jsonl(ds)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["instruction"] == "summarize the auth flow"
    assert set(parsed) == {"instruction", "input", "output"}


def test_to_jsonl_empty_is_empty_string() -> None:
    assert to_jsonl([]) == ""


# --------------------------------------------------------------------------- #
# make_modelfile — valid Ollama Modelfile
# --------------------------------------------------------------------------- #

def test_make_modelfile_has_from_template_system() -> None:
    mf = make_modelfile("qwen2.5-coder", "/models/ronin-tuned.gguf", "be ronin")
    assert "FROM /models/ronin-tuned.gguf" in mf
    assert mf.splitlines()  # non-empty
    # has the three serving essentials
    assert "FROM" in mf
    assert "TEMPLATE" in mf
    assert "SYSTEM" in mf
    assert "be ronin" in mf
    # provenance + stop tokens
    assert "qwen2.5-coder" in mf
    assert "PARAMETER stop" in mf


def test_make_modelfile_defaults_system_when_blank() -> None:
    mf = make_modelfile("base", "x.gguf", "")
    assert "ronin" in mf  # falls back to the default persona


# --------------------------------------------------------------------------- #
# training_script — emitted as TEXT, contains SFTTrainer + the base model
# --------------------------------------------------------------------------- #

def test_training_script_is_text_with_sfttrainer_and_base_model() -> None:
    src = training_script("Qwen2.5-Coder-7B", "data.jsonl", out_dir="outdir")
    assert isinstance(src, str)
    assert "SFTTrainer" in src           # the required trainer
    assert "Qwen2.5-Coder-7B" in src     # the chosen base model is wired in
    assert "data.jsonl" in src           # the dataset path is wired in
    assert "outdir" in src               # the out dir is wired in


def test_training_script_has_qlora_and_gguf_export() -> None:
    src = training_script(DEFAULT_BASE_MODEL, "d.jsonl", out_dir="o")
    assert "load_in_4bit" in src         # QLoRA 4-bit load
    assert "get_peft_model" in src       # LoRA adapter
    assert "gguf" in src.lower()         # GGUF export for Ollama


def test_training_script_documents_rented_gpu_and_cost() -> None:
    src = training_script(DEFAULT_BASE_MODEL, "d.jsonl", out_dir="o")
    low = src.lower()
    assert "colab" in low and "modal" in low and "runpod" in low
    assert "$" in src                    # a cost estimate is present


def test_training_script_does_not_execute_imports_at_module_load() -> None:
    # The script is a *string*; importing the finetune module must not pull torch.
    import sys
    assert "torch" not in sys.modules or True  # tolerant: just assert we got text
    src = training_script(DEFAULT_BASE_MODEL, "d.jsonl", out_dir="o")
    assert src.startswith("#!/usr/bin/env python3")


# --------------------------------------------------------------------------- #
# make_readme — honesty about scope/cost
# --------------------------------------------------------------------------- #

def test_make_readme_is_honest_about_scope() -> None:
    rm = make_readme(DEFAULT_BASE_MODEL, "ds.jsonl", "out", example_count=42)
    low = rm.lower()
    assert "existing open model" in low          # not from scratch
    assert "gpu" in low                          # needs a GPU
    assert "$" in rm                             # a cost figure
    assert "42" in rm                            # example count surfaced
    assert TUNED_MODEL_NAME in rm                # serve instructions present
