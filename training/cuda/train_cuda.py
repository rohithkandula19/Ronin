"""ronin-code-1.5b CUDA trainer — QLoRA SFT on Qwen2.5-Coder-1.5B-Instruct.

Runs on a rented A100/4090 box (see README.md next to this file). Deterministic
by construction: pinned deps (requirements.txt), fixed seed, hash-verified
frozen test split, and a run report with every hyperparameter + the exact git
SHA written to training/reports/.

Two artifacts land in --out:
  adapters/   PEFT LoRA adapter (the training truth)
  fused/      base + adapter merged to one HF checkpoint — convert to MLX on a
              Mac with:  mlx_lm.convert --hf-path fused/ -q  (then point the
              embedded provider at the converted model)

`--check` validates config, data presence, and the frozen-split hash WITHOUT
importing torch — safe to run anywhere (CI does).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
from pathlib import Path

BASE_MODEL = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
SEED = 42
LORA = {"r": 16, "alpha": 32, "dropout": 0.05,
        "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj",
                           "gate_proj", "up_proj", "down_proj"]}
TRAIN = {"epochs": 3, "lr": 2e-4, "batch": 4, "grad_accum": 4,
         "max_seq_len": 2048, "warmup_ratio": 0.03}


def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def check(data_dir: Path) -> list[str]:
    """Everything that must be true before burning GPU money. No torch import."""
    errs = []
    for split in ("train", "valid", "test"):
        if not (data_dir / f"{split}.jsonl").is_file():
            errs.append(f"missing {data_dir}/{split}.jsonl — run the corpus "
                        "generation commands in README.md first")
    pin = data_dir / "test.jsonl.sha256"
    if pin.is_file() and (data_dir / "test.jsonl").is_file():
        want = pin.read_text().strip()
        got = _sha256(data_dir / "test.jsonl")
        if want != got:
            errs.append(f"FROZEN SPLIT VIOLATION: test.jsonl sha256 {got} != pinned {want}")
    elif not pin.is_file():
        errs.append(f"missing {pin} — the frozen test split must be hash-pinned")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data", default="training/data/merged")
    ap.add_argument("--out", default="training/adapters/ronin-code-1.5b-v4")
    ap.add_argument("--check", action="store_true",
                    help="validate config + data + frozen-split hash, then exit")
    ap.add_argument("--qlora", action="store_true",
                    help="4-bit QLoRA (bitsandbytes nf4) — fits a free Colab/Kaggle T4 (16GB)")
    ap.add_argument("--fp16", action="store_true",
                    help="fp16 mixed precision (T4 has no bf16)")
    ap.add_argument("--save-steps", type=int, default=0,
                    help="checkpoint every N steps (0 = per epoch); free-tier "
                    "sessions disconnect — small N loses little")
    ap.add_argument("--resume", action="store_true",
                    help="resume from the latest checkpoint in --out/adapters")
    args = ap.parse_args(argv)
    data_dir = Path(args.data)

    errs = check(data_dir)
    if args.check:
        print(json.dumps({"base_model": BASE_MODEL, "seed": SEED, "lora": LORA,
                          "train": TRAIN, "data": str(data_dir),
                          "qlora": args.qlora, "fp16": args.fp16,
                          "save_steps": args.save_steps,
                          "errors": errs}, indent=2))
        return 1 if errs else 0
    if errs:
        raise SystemExit("preflight failed:\n- " + "\n- ".join(errs))

    # ------------------------------------------------- heavy imports (GPU box)
    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, set_seed
    from trl import SFTConfig, SFTTrainer

    set_seed(SEED)
    tokenizer = AutoTokenizer.from_pretrained(BASE_MODEL)

    def render(row):
        # THE load-bearing line: rows are rendered by the model's own chat
        # template WITH the tools list — the exact prompt distribution the
        # runtime and the eval use (root-cause fix D1).
        return {"text": tokenizer.apply_chat_template(
            row["messages"], tools=row.get("tools") or None, tokenize=False)}

    ds = load_dataset("json", data_files={
        "train": str(data_dir / "train.jsonl"),
        "valid": str(data_dir / "valid.jsonl")})
    ds = ds.map(render, remove_columns=ds["train"].column_names)

    if args.qlora:
        from transformers import BitsAndBytesConfig
        model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL, device_map="auto",
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True))
    else:
        model = AutoModelForCausalLM.from_pretrained(
            BASE_MODEL, dtype=torch.float16 if args.fp16 else torch.bfloat16,
            device_map="auto")

    out = Path(args.out)
    trainer = SFTTrainer(
        model=model,
        train_dataset=ds["train"],
        eval_dataset=ds["valid"],
        peft_config=LoraConfig(
            r=LORA["r"], lora_alpha=LORA["alpha"], lora_dropout=LORA["dropout"],
            target_modules=LORA["target_modules"], task_type="CAUSAL_LM"),
        args=SFTConfig(
            output_dir=str(out / "adapters"),
            num_train_epochs=TRAIN["epochs"],
            learning_rate=TRAIN["lr"],
            per_device_train_batch_size=TRAIN["batch"],
            gradient_accumulation_steps=TRAIN["grad_accum"],
            max_length=TRAIN["max_seq_len"],
            warmup_ratio=TRAIN["warmup_ratio"],
            logging_steps=20, eval_strategy="epoch",
            save_strategy="steps" if args.save_steps else "epoch",
            save_steps=args.save_steps or 500,
            fp16=args.fp16, bf16=not args.fp16, seed=SEED, report_to=[],
            dataset_text_field="text"),
    )
    result = trainer.train(resume_from_checkpoint=True if args.resume else None)
    trainer.save_model(str(out / "adapters"))

    merged = trainer.model.merge_and_unload()
    merged.save_pretrained(str(out / "fused"))
    tokenizer.save_pretrained(str(out / "fused"))

    report = {
        "date": datetime.datetime.now().isoformat(timespec="seconds"),
        "base_model": BASE_MODEL, "git_sha": _git_sha(), "seed": SEED,
        "lora": LORA, "train": TRAIN,
        "qlora": args.qlora, "fp16": args.fp16,
        "rows": {"train": sum(1 for _ in open(data_dir / "train.jsonl")),
                 "valid": sum(1 for _ in open(data_dir / "valid.jsonl"))},
        "test_split_sha256": (data_dir / "test.jsonl.sha256").read_text().strip(),
        "train_loss": result.training_loss,
        "out": str(out),
    }
    rp = Path("training/reports") / f"cuda_run_{report['date'].replace(':', '')}.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"run report: {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
