"""``ronin finetune`` — turn your own ronin sessions into ronin's own brain.

The honest pitch
----------------
ronin already archives every session under ``.ronin/sessions/<id>.json`` (see
:mod:`ronin_cli.sessions`). Those transcripts — your real prompts and the
agent's real answers, in *your* domains, *your* repos, *your* style — are an
instruction-tuning corpus that nobody else has. This module mines them into a
clean, **PII/secret-scrubbed** dataset, emits a *real* QLoRA training script you
launch on a rented GPU, and then wires the resulting model back into ronin's
local (Ollama) serving path so ``ronin --offline`` runs on a model fine-tuned on
you.

Be honest about what this is and is NOT:

* This **LoRA-fine-tunes an EXISTING open coding model** (default
  ``unsloth/Qwen2.5-Coder-7B-bnb-4bit``). It does **NOT** pre-train a model
  from scratch — that needs thousands of GPU-hours and millions of dollars and
  is not what anyone should do here.
* Training **cannot** run in this process. ``ronin`` runs on your laptop; QLoRA
  needs a CUDA GPU (≈16 GB VRAM for a 7B in 4-bit). So this module is a
  *kit-emitter*: it produces a dataset + a runnable ``train.py`` + run
  instructions for Colab / Modal / RunPod. You launch the actual training on the
  rented GPU. Rough order of magnitude: **~1-3 hours**, **~$5-30** depending on
  GPU and dataset size.
* The output is a GGUF you load into **Ollama** (ronin's local provider), so
  serving stays 100% local and offline after the one-time rented-GPU step.

Module shape (mirrors the rest of ronin: a small pure core + thin impure edges)
------------------------------------------------------------------------------
PURE, unit-tested (no I/O, no network, no torch — the train script is emitted as
*text*, never imported here):

* :func:`trace_to_example`  — one session dict → one ``{instruction, input,
  output}`` example, fully redacted; ``None`` if unusable.
* :func:`build_dataset`     — list of sessions → deduped, non-empty examples.
* :func:`to_jsonl`          — examples → JSONL text (one compact JSON per line).
* :func:`make_modelfile`    — a valid Ollama ``Modelfile`` (FROM + SYSTEM +
  TEMPLATE) for serving a fine-tuned GGUF.
* :func:`training_script`   — a COMPLETE, runnable QLoRA ``train.py`` *as text*
  (unsloth / peft+transformers+trl, 4-bit load, LoRA, ``SFTTrainer``, save +
  GGUF export), with a header documenting Colab/Modal/RunPod steps + cost.
* :func:`make_readme`       — the rent-a-GPU README (honesty section included).

IMPURE (filesystem + command flows), used by the three CLI commands:

* :func:`collect`           — load saved sessions from a repo's ``.ronin/sessions``.
* :func:`run_collect` / :func:`run_script` / :func:`run_serve` — the command bodies.

Nothing here needs a network connection or a model at import time; only stdlib +
``rich`` are imported eagerly.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

# Reuse ronin's shared redactor AND the stronger index-grade scrubber so the
# training corpus masks exactly the secret/PII classes the rest of the tool
# already knows about (JWTs, AWS/GitHub/Slack/Stripe/OpenAI keys, bearer tokens,
# emails, IPs) — one source of truth for "what is secret", plus the length-gated
# catch-alls from the recall index path.
from .redact import redact_text
from .session_search import redact_for_index

# --------------------------------------------------------------------------- #
# Tunables / defaults
# --------------------------------------------------------------------------- #

# Default open coder to fine-tune. Unsloth ships a pre-quantized 4-bit build that
# fits a single ~16 GB GPU and trains ~2x faster than vanilla HF — the sweet spot
# for a "rent one GPU for an afternoon" job.
DEFAULT_BASE_MODEL = "unsloth/Qwen2.5-Coder-7B-bnb-4bit"

# The name the fine-tuned model is registered under in Ollama, and the model id
# you then point ronin's local provider at.
TUNED_MODEL_NAME = "ronin-tuned"

# The persona baked into the served model's SYSTEM prompt. This is "ronin's own
# brain": a terminal-native, provider-agnostic coding agent.
DEFAULT_SYSTEM = (
    "You are ronin, a masterless, terminal-native coding agent. You are precise, "
    "concise, and you bias toward running commands and editing files over "
    "explaining. You write idiomatic, well-tested code and you never invent "
    "files, symbols, or APIs you have not seen. When unsure, you say so."
)

# Drop turns whose answer is too short to teach anything (a bare "ok", "done").
_MIN_OUTPUT_CHARS = 12
# Guard against a single pathological transcript blowing up an example.
_MAX_FIELD_CHARS = 24_000

_USER_PREFIX = "USER: "
_ASSISTANT_PREFIX = "ASSISTANT: "


# --------------------------------------------------------------------------- #
# PURE CORE 1 — transcript → instruction-tuning example
# --------------------------------------------------------------------------- #

def _scrub(text: str) -> str:
    """Run ronin's full redaction stack over ``text``.

    First the shared :func:`ronin_cli.redact.redact_text` (high-confidence secret
    classes), then the stronger :func:`redact_for_index` catch-all pass that
    masks short ``sk-`` / ``rk_`` / ``xoxb-`` tokens and any remaining email the
    length-gated patterns miss. Belt and suspenders — secrets must never reach
    the training corpus. Pure."""
    once = redact_text(text or "").get("text", text or "")
    return redact_for_index(once)


def _split_turns(transcript: list[str]) -> list[tuple[str, str]]:
    """Pair up a flat ``["USER: …", "ASSISTANT: …", …]`` transcript into
    ``(user, assistant)`` turns.

    ronin stores transcripts as an alternating list of ``USER: ``/``ASSISTANT: ``
    prefixed strings (see :mod:`ronin_cli.sessions`). We walk it, and for each
    ASSISTANT entry we attach the most recent USER entry as its instruction.
    Robust to leading assistant lines, consecutive user lines (last one wins),
    and stray unprefixed lines (ignored). Pure."""
    turns: list[tuple[str, str]] = []
    pending_user: str | None = None
    for raw in transcript:
        if not isinstance(raw, str):
            continue
        if raw.startswith(_USER_PREFIX):
            pending_user = raw[len(_USER_PREFIX):].strip()
        elif raw.startswith(_ASSISTANT_PREFIX):
            assistant = raw[len(_ASSISTANT_PREFIX):].strip()
            if pending_user is not None and assistant:
                turns.append((pending_user, assistant))
            pending_user = None
    return turns


def trace_to_example(session: dict) -> dict | None:
    """Turn one saved ronin session into an instruction-tuning example.

    Returns ``{"instruction": str, "input": str, "output": str}`` — the
    Alpaca/Stanford schema ``SFTTrainer`` and friends expect — or ``None`` if the
    session has no usable USER→ASSISTANT turn.

    Shape decisions:

    * The session's **first** USER→ASSISTANT turn becomes the example
      (``instruction`` = the user's ask, ``output`` = the agent's reply). Most
      ronin sessions are a single focused turn; taking the first keeps one clean
      example per session and avoids leaking later-turn context into ``input``.
    * Any **earlier** turns in the same session become the ``input`` field as a
      short prior-conversation block, so multi-turn sessions still carry their
      setup. (For the common single-turn session this is just ``""``.)
    * **Every** field is passed through :func:`_scrub` so secrets/PII never enter
      the dataset, and each is capped at :data:`_MAX_FIELD_CHARS`.
    * Returns ``None`` when there is no answerable turn or the answer is shorter
      than :data:`_MIN_OUTPUT_CHARS` (a bare "ok" teaches nothing).

    Pure: no I/O, no network, deterministic."""
    if not isinstance(session, dict):
        return None
    transcript = session.get("transcript")
    if not isinstance(transcript, list):
        return None

    turns = _split_turns(transcript)
    if not turns:
        return None

    instruction, output = turns[0]
    # Earlier-than-the-chosen-turn context is empty for turn 0; if a future
    # caller picks a later turn this is where prior turns would go. We keep the
    # field for schema stability and to carry any session title as light context.
    prior: list[str] = []
    title = session.get("title")
    if isinstance(title, str) and title.strip() and title.strip() not in instruction:
        prior.append(f"(session: {title.strip()})")
    input_text = "\n".join(prior)

    instruction = _scrub(instruction)[:_MAX_FIELD_CHARS].strip()
    output = _scrub(output)[:_MAX_FIELD_CHARS].strip()
    input_text = _scrub(input_text)[:_MAX_FIELD_CHARS].strip()

    if not instruction or len(output) < _MIN_OUTPUT_CHARS:
        return None

    return {"instruction": instruction, "input": input_text, "output": output}


# --------------------------------------------------------------------------- #
# PURE CORE 2 — dataset assembly
# --------------------------------------------------------------------------- #

def build_dataset(sessions: list[dict]) -> list[dict]:
    """Build a clean instruction-tuning dataset from a list of session dicts.

    Maps each session through :func:`trace_to_example`, drops the ``None``s
    (unusable sessions), and **de-duplicates** on the ``(instruction, output)``
    pair so re-running the same prompt many times (very common while iterating)
    doesn't over-weight that example. Order is preserved (first occurrence wins),
    which keeps the dataset stable across runs. Pure."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for session in sessions:
        ex = trace_to_example(session)
        if ex is None:
            continue
        key = (ex["instruction"], ex["output"])
        if key in seen:
            continue
        seen.add(key)
        out.append(ex)
    return out


def to_jsonl(examples: list[dict]) -> str:
    """Serialise examples to JSONL — one compact JSON object per line.

    This is the on-disk format ``train.py`` loads (HF ``datasets`` reads JSONL
    natively). ``ensure_ascii=False`` keeps non-ASCII intact; a trailing newline
    is included so the file is POSIX-clean. Pure."""
    lines = [json.dumps(ex, ensure_ascii=False, sort_keys=True) for ex in examples]
    return ("\n".join(lines) + "\n") if lines else ""


# --------------------------------------------------------------------------- #
# PURE CORE 3 — Ollama Modelfile (the local serving artifact)
# --------------------------------------------------------------------------- #

def make_modelfile(base_model: str, gguf_path: str, system: str) -> str:
    """Build a valid Ollama ``Modelfile`` that serves a fine-tuned GGUF.

    ``ollama create ronin-tuned -f Modelfile`` reads this to register the model;
    ``ronin`` then talks to it through the local Ollama provider
    (``http://localhost:11434/v1``).

    The file has the three things a served chat model needs:

    * ``FROM <gguf_path>`` — load the fine-tuned weights from the local GGUF the
      training script exported (not pulled from a registry).
    * a ``TEMPLATE`` — a sane ChatML-style prompt template so multi-turn chat,
      the system prompt, and stop tokens are wired correctly for Qwen-class
      coders.
    * ``SYSTEM`` — ronin's persona, and a couple of ``PARAMETER`` lines (stop
      tokens + a low temperature suited to coding).

    ``base_model`` is recorded as a comment for provenance. Pure (string only).
    """
    system_clean = (system or DEFAULT_SYSTEM).strip().replace('"""', '\\"\\"\\"')
    # ChatML template (Qwen2.5-Coder uses <|im_start|>/<|im_end|>). Ollama
    # renders .System / .Prompt / .Response into this Go-template skeleton.
    template = (
        "{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}"
        "{{ if .Prompt }}<|im_start|>user\n{{ .Prompt }}<|im_end|>\n{{ end }}"
        "<|im_start|>assistant\n{{ .Response }}<|im_end|>\n"
    )
    return (
        f"# Ollama Modelfile for {TUNED_MODEL_NAME} — ronin's own brain.\n"
        f"# Fine-tuned (QLoRA) from base: {base_model}\n"
        f"# Build:  ollama create {TUNED_MODEL_NAME} -f Modelfile\n"
        f"# Serve:  ronin set-key --provider ollama --model {TUNED_MODEL_NAME}\n"
        f"#         ronin --offline           # local brain, nothing leaves the box\n"
        "\n"
        f"FROM {gguf_path}\n"
        "\n"
        f'TEMPLATE """{template}"""\n'
        "\n"
        f'SYSTEM """{system_clean}"""\n'
        "\n"
        "# Coding-friendly decoding + ChatML stop tokens.\n"
        "PARAMETER temperature 0.2\n"
        "PARAMETER top_p 0.9\n"
        'PARAMETER stop "<|im_start|>"\n'
        'PARAMETER stop "<|im_end|>"\n'
    )


# --------------------------------------------------------------------------- #
# PURE CORE 4 — the QLoRA training script, emitted as TEXT
# --------------------------------------------------------------------------- #

def training_script(base_model: str, dataset_path: str, *, out_dir: str) -> str:
    """Return a COMPLETE, runnable QLoRA ``train.py`` **as a string**.

    This is emitted as text and written to disk for the user to run on a rented
    GPU — it is deliberately **never imported** by this module (so ``ronin`` has
    no torch/unsloth dependency). The script:

    1. 4-bit-loads ``base_model`` via ``unsloth.FastLanguageModel`` (falls back to
       a documented bitsandbytes + peft + transformers path in comments).
    2. Attaches a LoRA adapter (r=16, alpha=16, the standard attention+MLP target
       modules), gradient-checkpointed.
    3. Formats the Alpaca-schema JSONL at ``dataset_path`` into a single ``text``
       field and trains with TRL's ``SFTTrainer``.
    4. Saves the merged model and **exports a GGUF (q4_k_m)** to ``out_dir`` so it
       can be served by Ollama.

    The header is a runnable runbook: exact Colab, Modal, and RunPod steps plus a
    rough cost/time estimate. Pure (string only)."""
    base = base_model or DEFAULT_BASE_MODEL
    ds = dataset_path
    out = out_dir
    gguf_name = f"{TUNED_MODEL_NAME}.gguf"

    return f'''\
#!/usr/bin/env python3
# =============================================================================
# ronin finetune — QLoRA fine-tune of an OPEN coding model into "ronin's brain".
#
# WHAT THIS IS (be honest):
#   * This LoRA-fine-tunes an EXISTING open model ({base}).
#     It does NOT pre-train from scratch (that costs millions; don't).
#   * It CANNOT run on your laptop — it needs a CUDA GPU (~16 GB VRAM for a 7B
#     in 4-bit). Rent one. Order of magnitude: ~1-3 hours, ~$5-30.
#   * Output: a GGUF you serve locally with Ollama (ronin's local provider), so
#     after this one-time rented-GPU step everything runs offline on your box.
#
# DATA: {ds}  (Alpaca-schema JSONL: {{"instruction","input","output"}} per line,
#       already PII/secret-scrubbed by `ronin finetune collect`).
# OUT:  {out}  (LoRA adapter + merged model + {gguf_name})
#
# ---------------------------------------------------------------------------
# RUN IT — pick ONE platform:
#
#   A) Google Colab (easiest, free T4 works for a 7B in 4-bit; ~$0 but slow,
#      or ~$10/mo Colab Pro for an L4/A100):
#        1. New notebook → Runtime → Change runtime type → GPU (T4/L4/A100).
#        2. Upload this train.py and dataset.jsonl (left panel, or mount Drive).
#        3. In a cell:
#             !pip install -q "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
#             !pip install -q --no-deps trl peft accelerate bitsandbytes
#             !python train.py
#        4. Download {out}/{gguf_name} when it finishes.
#
#   B) Modal (serverless GPU, pay-per-second, reproducible):
#        pip install modal && modal setup
#        # save the wrapper below as modal_run.py, then:  modal run modal_run.py
#        # --- modal_run.py ---------------------------------------------------
#        # import modal
#        # img = (modal.Image.debian_slim()
#        #        .pip_install("unsloth @ git+https://github.com/unslothai/unsloth.git",
#        #                     "trl", "peft", "accelerate", "bitsandbytes", "datasets")
#        #        .add_local_file("train.py", "/root/train.py")
#        #        .add_local_file("{Path(ds).name}", "/root/{Path(ds).name}"))
#        # app = modal.App("ronin-finetune")
#        # @app.function(gpu="A10G", timeout=60*60*3, image=img)
#        # def go():
#        #     import subprocess; subprocess.run(["python", "/root/train.py"], check=True)
#        # @app.local_entrypoint()
#        # def main(): go.remote()
#        # --------------------------------------------------------------------
#        # A10G ~ $1.10/hr → a small dataset finishes for a few dollars.
#
#   C) RunPod (rent a raw GPU pod):
#        1. runpod.io → Deploy a Pod → pick an RTX 4090 / A40 / A100 (~$0.4-1.9/hr),
#           template "RunPod PyTorch 2.x".
#        2. In the pod's web terminal / Jupyter:
#             pip install "unsloth @ git+https://github.com/unslothai/unsloth.git" trl peft accelerate bitsandbytes datasets
#        3. Upload train.py + dataset.jsonl (drag into Jupyter), then:  python train.py
#        4. scp/download {out}/{gguf_name}, then STOP the pod (you pay while it runs).
#
# COST/TIME (rough): 7B QLoRA, a few hundred examples, 1-3 epochs →
#   T4 ~2-3 h (~$0 on free Colab) · A10G/4090 ~30-60 min (~$1-5) · A100 ~15-30 min (~$5-15).
# =============================================================================
import json
import os

# ---- 0. config (edit if you like) -------------------------------------------
BASE_MODEL = "{base}"
DATASET    = "{ds}"
OUT_DIR    = "{out}"
MAX_SEQ_LEN = 2048
EPOCHS      = 3
LR          = 2e-4
os.makedirs(OUT_DIR, exist_ok=True)

# ---- 1. 4-bit load + LoRA via Unsloth ---------------------------------------
# (Unsloth wraps bitsandbytes 4-bit load + PEFT LoRA and is ~2x faster / less
#  VRAM. If you can't use unsloth, the equivalent stack is, in plain terms:
#    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
#    from peft import LoraConfig, get_peft_model
#    bnb = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
#                             bnb_4bit_compute_dtype="bfloat16")
#    model = AutoModelForCausalLM.from_pretrained(BASE_MODEL, quantization_config=bnb,
#                                                 device_map="auto")
#  then wrap with the same LoraConfig below and hand it to SFTTrainer.)
from unsloth import FastLanguageModel

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=BASE_MODEL,
    max_seq_length=MAX_SEQ_LEN,
    load_in_4bit=True,          # QLoRA: 4-bit base weights
    dtype=None,                  # auto (bf16 on Ampere+, fp16 otherwise)
)

model = FastLanguageModel.get_peft_model(
    model,
    r=16,                                       # LoRA rank
    lora_alpha=16,
    lora_dropout=0.0,
    bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                    "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

# ---- 2. data: Alpaca JSONL -> a single prompt string per row -----------------
from datasets import load_dataset

EOS = tokenizer.eos_token or "</s>"
ALPACA = (
    "Below is an instruction that describes a task. "
    "Write a response that appropriately completes the request.\\n\\n"
    "### Instruction:\\n{{instruction}}\\n\\n"
    "{{ctx}}### Response:\\n{{output}}"
)

def _format(batch):
    texts = []
    for ins, inp, out in zip(batch["instruction"], batch["input"], batch["output"]):
        ctx = f"### Input:\\n{{inp}}\\n\\n" if (inp or "").strip() else ""
        texts.append(ALPACA.format(instruction=ins, ctx=ctx, output=out) + EOS)
    return {{"text": texts}}

dataset = load_dataset("json", data_files=DATASET, split="train")
dataset = dataset.map(_format, batched=True)
print(f"[ronin] training on {{len(dataset)}} examples from {{DATASET}}")

# ---- 3. SFTTrainer (TRL) -----------------------------------------------------
from transformers import TrainingArguments
from trl import SFTTrainer

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=MAX_SEQ_LEN,
    packing=False,
    args=TrainingArguments(
        per_device_train_batch_size=2,
        gradient_accumulation_steps=4,
        warmup_steps=5,
        num_train_epochs=EPOCHS,
        learning_rate=LR,
        fp16=False, bf16=True,
        logging_steps=1,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="linear",
        seed=3407,
        output_dir=os.path.join(OUT_DIR, "checkpoints"),
        report_to="none",
    ),
)

trainer.train()

# ---- 4. save adapter + merged model + export GGUF for Ollama -----------------
model.save_pretrained(os.path.join(OUT_DIR, "lora_adapter"))
tokenizer.save_pretrained(os.path.join(OUT_DIR, "lora_adapter"))

# Merge LoRA into the base and export a quantized GGUF Ollama can load.
# Unsloth's save_pretrained_gguf runs llama.cpp conversion + quantization.
try:
    model.save_pretrained_gguf(OUT_DIR, tokenizer, quantization_method="q4_k_m")
    print(f"[ronin] wrote GGUF under {{OUT_DIR}} (look for *.gguf, e.g. {gguf_name}).")
except Exception as e:  # noqa: BLE001
    # Fallback: save merged 16-bit weights and convert with llama.cpp yourself:
    #   python llama.cpp/convert_hf_to_gguf.py {out}/merged_16bit --outfile {out}/{gguf_name}
    #   ./llama.cpp/llama-quantize {out}/{gguf_name} {out}/{gguf_name} q4_k_m
    model.save_pretrained_merged(os.path.join(OUT_DIR, "merged_16bit"),
                                 tokenizer, save_method="merged_16bit")
    print(f"[ronin] GGUF export skipped ({{e}}); saved merged_16bit — convert with llama.cpp.")

print("[ronin] DONE. Next: copy the .gguf to your laptop and run "
      "`ronin finetune serve <path-to.gguf>`.")
'''


# --------------------------------------------------------------------------- #
# PURE CORE 5 — the rent-a-GPU README (honesty lives here)
# --------------------------------------------------------------------------- #

def make_readme(base_model: str, dataset_path: str, out_dir: str,
                *, example_count: int | None = None) -> str:
    """The README emitted next to ``train.py`` — what this is, how to rent a GPU,
    and how to serve the result. Honest about scope/cost/time. Pure."""
    count_line = (
        f"- Your dataset has **{example_count}** examples "
        f"(`{dataset_path}`).\n" if example_count is not None else
        f"- Dataset: `{dataset_path}` (built by `ronin finetune collect`).\n"
    )
    return f'''\
# ronin finetune — train ronin's own brain, then serve it locally

This kit fine-tunes an **existing open coding model** on **your** ronin sessions,
then serves the result locally through Ollama so `ronin --offline` runs on a
model tuned to how *you* work.

## Be honest about what this is

- This **LoRA-fine-tunes an existing open model** (`{base_model}`). It is **not**
  pre-training a model from scratch — that needs thousands of GPU-hours and is
  not what this does.
- **It cannot run on your laptop.** QLoRA needs a CUDA GPU (~16 GB VRAM for a 7B
  in 4-bit). You run `train.py` on a **rented GPU**; ronin only *emits* the kit.
- Ballpark: **~1-3 hours** and **~$5-30** (free on a Colab T4 if you're patient).
- After the one-time training step, serving is **100% local/offline** via Ollama.

{count_line}
## 1. Get the dataset (already done if you ran collect)

```
ronin finetune collect
```
→ writes a PII/secret-scrubbed Alpaca-schema JSONL to `{dataset_path}`.
Every transcript is run through ronin's redactor first — emails, IPs, API keys,
JWTs, and tokens are masked **before** anything is written.

## 2. Train on a rented GPU

`train.py` (next to this README) has copy-paste run steps for **Colab**, **Modal**,
and **RunPod** in its header. Short version:

- **Colab (free T4):** new GPU notebook → upload `train.py` + `dataset.jsonl` →
  `!pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git" trl peft accelerate bitsandbytes`
  → `!python train.py`.
- **Modal / RunPod:** see the header of `train.py`.

It produces a quantized **GGUF** under `{out_dir}`.

## 3. Serve it in ronin (local, offline)

Copy the `.gguf` back to this machine, then:

```
ronin finetune serve {out_dir}/{TUNED_MODEL_NAME}.gguf   # writes the Modelfile + instructions
ollama create {TUNED_MODEL_NAME} -f {out_dir}/Modelfile
ronin set-key --provider ollama --model {TUNED_MODEL_NAME}
ronin --offline                                          # now running on YOUR brain
```

(`ronin local --model {TUNED_MODEL_NAME}` is the shorthand once the model is
registered — it points ronin's local Ollama provider at the tuned model.)

Requires [Ollama](https://ollama.com) running locally (`ollama serve`).
'''


# --------------------------------------------------------------------------- #
# IMPURE EDGE — load saved sessions, output dir
# --------------------------------------------------------------------------- #

def _finetune_dir() -> Path:
    """Where the dataset/script/Modelfile land. Honours ``RONIN_HOME`` (the same
    override memory/runs use), defaulting to ``~/.ronin/finetune``."""
    home = os.environ.get("RONIN_HOME")
    base = Path(home) if home else Path.home() / ".ronin"
    return base / "finetune"


def collect(root: Path | str = ".") -> list[dict]:
    """Load every archived session under ``<root>/.ronin/sessions/*.json``.

    Returns the raw session dicts (each with ``transcript`` etc.) for
    :func:`build_dataset` to mine. Tolerant of unreadable/non-dict files (they're
    skipped). Impure (filesystem read only — never writes, never networks)."""
    session_dir = Path(root) / ".ronin" / "sessions"
    if not session_dir.is_dir():
        return []
    out: list[dict] = []
    for f in sorted(session_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out.append(data)
    return out


# --------------------------------------------------------------------------- #
# COMMAND FLOWS (thin; called by the Typer commands in main.py)
# --------------------------------------------------------------------------- #

def run_collect(console: Any, *, root: Path | str = ".",
                out_dir: Path | str | None = None) -> Path | None:
    """`ronin finetune collect` — mine sessions → scrubbed JSONL dataset.

    Loads sessions from ``root``, builds + redacts the dataset, writes it to
    ``<out_dir>/dataset.jsonl`` (default ``~/.ronin/finetune/``), and reports the
    example count + on-disk size. Returns the dataset path (or ``None`` if there
    was nothing to write). Impure."""
    base = Path(out_dir) if out_dir is not None else _finetune_dir()
    sessions = collect(root)
    examples = build_dataset(sessions)
    if not examples:
        console.print(
            "[#f7768e]No usable sessions found[/#f7768e] under "
            f"[cyan]{Path(root) / '.ronin' / 'sessions'}[/cyan].\n"
            "[dim]Use ronin a bit first — every session is archived and becomes "
            "training data.[/dim]"
        )
        return None

    base.mkdir(parents=True, exist_ok=True)
    path = base / "dataset.jsonl"
    text = to_jsonl(examples)
    path.write_text(text, encoding="utf-8")

    size_kb = len(text.encode("utf-8")) / 1024
    console.print(
        f"[#2dd4bf]✓ wrote {len(examples)} training examples[/#2dd4bf] "
        f"[#6b7089]({size_kb:.1f} KB, PII/secret-scrubbed)[/#6b7089]\n"
        f"  [cyan]{path}[/cyan]\n"
        f"[#6b7089]from {len(sessions)} archived sessions. "
        f"Next: [bold]ronin finetune script[/bold] to emit the trainer.[/#6b7089]"
    )
    return path


def run_script(console: Any, *, base_model: str = DEFAULT_BASE_MODEL,
               out_dir: Path | str | None = None,
               dataset_path: Path | str | None = None,
               model_out: str = "out") -> Path:
    """`ronin finetune script` — emit ``train.py`` + ``README.md``.

    Writes a complete, runnable QLoRA ``train.py`` and a rent-a-GPU README into
    ``<out_dir>`` (default ``~/.ronin/finetune/``). Does **not** train (it can't —
    that needs a GPU). Returns the ``train.py`` path. Impure."""
    base = Path(out_dir) if out_dir is not None else _finetune_dir()
    base.mkdir(parents=True, exist_ok=True)
    ds_path = str(dataset_path) if dataset_path is not None else str(base / "dataset.jsonl")

    # If the dataset exists, report its size in the README for a nicer UX.
    example_count: int | None = None
    ds_file = Path(ds_path)
    if ds_file.is_file():
        try:
            example_count = sum(1 for line in ds_file.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError:
            example_count = None

    script_path = base / "train.py"
    script_path.write_text(training_script(base_model, ds_path, out_dir=model_out), encoding="utf-8")
    readme_path = base / "README.md"
    readme_path.write_text(make_readme(base_model, ds_path, model_out, example_count=example_count), encoding="utf-8")

    console.print(
        f"[#2dd4bf]✓ emitted the training kit[/#2dd4bf] "
        f"[#6b7089](base: {base_model})[/#6b7089]\n"
        f"  [cyan]{script_path}[/cyan]   [#6b7089]the QLoRA trainer (run on a rented GPU)[/#6b7089]\n"
        f"  [cyan]{readme_path}[/cyan]   [#6b7089]Colab / Modal / RunPod steps + cost[/#6b7089]\n"
        f"[#e0af68]⚠ ronin can't train this here — it needs a CUDA GPU.[/#e0af68] "
        f"[#6b7089]~1-3 h, ~$5-30. Open the README.[/#6b7089]"
    )
    return script_path


def run_serve(console: Any, gguf: Path | str, *,
              out_dir: Path | str | None = None,
              system: str = DEFAULT_SYSTEM,
              base_model: str = DEFAULT_BASE_MODEL) -> Path:
    """`ronin finetune serve <gguf>` — write the Ollama Modelfile + serve steps.

    Emits a ``Modelfile`` (via :func:`make_modelfile`) into ``<out_dir>`` pointing
    at the fine-tuned ``gguf``, then prints the exact ``ollama create`` +
    ``ronin`` commands to register and use it locally. Returns the Modelfile
    path. Impure (writes a file + prints; never trains, never networks)."""
    base = Path(out_dir) if out_dir is not None else _finetune_dir()
    base.mkdir(parents=True, exist_ok=True)
    gguf_abs = str(Path(gguf).expanduser())

    modelfile = make_modelfile(base_model, gguf_abs, system)
    mf_path = base / "Modelfile"
    mf_path.write_text(modelfile, encoding="utf-8")

    exists_note = "" if Path(gguf_abs).is_file() else (
        f"\n[#e0af68]⚠ {gguf_abs} not found yet[/#e0af68] "
        "[#6b7089](finish training on the GPU and copy the .gguf here first).[/#6b7089]"
    )
    console.print(
        f"[#2dd4bf]✓ wrote Ollama Modelfile[/#2dd4bf]\n"
        f"  [cyan]{mf_path}[/cyan]{exists_note}\n\n"
        f"[#6b7089]Register + serve it locally (offline):[/#6b7089]\n"
        f"  [bold]ollama create {TUNED_MODEL_NAME} -f {mf_path}[/bold]\n"
        f"  [bold]ronin set-key --provider ollama --model {TUNED_MODEL_NAME}[/bold]\n"
        f"  [bold]ronin local --model {TUNED_MODEL_NAME}[/bold]   "
        f"[#6b7089](or: ronin --offline — runs on YOUR brain, nothing leaves the box)[/#6b7089]"
    )
    return mf_path


# --------------------------------------------------------------------------- #
# CLI snippets — paste into main.py (DO NOT auto-edit main.py).
# --------------------------------------------------------------------------- #

# Add this Typer sub-app near the other ``app.add_typer(...)`` groups in main.py:
#
#     finetune_app = typer.Typer(
#         help="🧠 Fine-tune an open coding model on YOUR ronin sessions, then "
#              "serve it locally via Ollama. (Training runs on a rented GPU.)")
#     app.add_typer(finetune_app, name="finetune")
#
#     @finetune_app.command("collect",
#         help="Mine your archived sessions into a PII-scrubbed training dataset "
#              "(~/.ronin/finetune/dataset.jsonl).")
#     def finetune_collect(
#         root: Path = typer.Option(Path("."), "--root", help="Repo whose .ronin/sessions to mine."),
#     ) -> None:
#         from .finetune import run_collect
#         run_collect(console, root=root)
#
#     @finetune_app.command("script",
#         help="Emit a runnable QLoRA train.py + a rent-a-GPU README "
#              "(Colab/Modal/RunPod). ronin can't train locally — needs a GPU.")
#     def finetune_script(
#         base_model: str = typer.Option(
#             "unsloth/Qwen2.5-Coder-7B-bnb-4bit", "--base-model",
#             help="Open coder to fine-tune."),
#     ) -> None:
#         from .finetune import run_script
#         run_script(console, base_model=base_model)
#
#     @finetune_app.command("serve",
#         help="Write the Ollama Modelfile for a fine-tuned GGUF + the "
#              "`ollama create` / `ronin local` steps to serve it.")
#     def finetune_serve(
#         gguf: Path = typer.Argument(..., help="Path to the fine-tuned .gguf from training."),
#     ) -> None:
#         from .finetune import run_serve
#         run_serve(console, gguf)


__all__ = [
    "trace_to_example",
    "build_dataset",
    "to_jsonl",
    "make_modelfile",
    "training_script",
    "make_readme",
    "collect",
    "run_collect",
    "run_script",
    "run_serve",
    "DEFAULT_BASE_MODEL",
    "DEFAULT_SYSTEM",
    "TUNED_MODEL_NAME",
]
