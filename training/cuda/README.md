# ronin-code-1.5b — CUDA training bundle

Turnkey LoRA fine-tune of **Qwen/Qwen2.5-Coder-1.5B-Instruct** on the ronin
dialect corpus, for a rented GPU box (the 8 GB Mac can't train this —
BLOCKED_HARDWARE). Everything is pinned and deterministic; the run report
records seed, hyperparameters, row counts, frozen-split hash, and git SHA.

## On a fresh A100 / 4090 box

```bash
git clone https://github.com/rohithkandula19/Ronin && cd Ronin
bash training/cuda/run.sh
```

That's the whole run: pinned deps → deterministic corpus regeneration
(volumes 284 rows + synthetic 5,000 rows, seed 42) → frozen-split hash
preflight → QLoRA (r=16, α=32, 3 epochs, lr 2e-4, seq 2048, seed 42) →
artifacts in `training/adapters/ronin-code-1.5b-v4/`:

- `adapters/` — the PEFT LoRA adapter
- `fused/` — base+adapter merged; convert for the mlx embedded provider with
  `mlx_lm.convert --hf-path fused/ -q` on a Mac

## Cost

| box | rent | wall clock (est.) | total |
|---|---|---|---|
| RTX 4090 | $0.4–0.8/hr | 2–4 h | **$1–3** |
| A100 40GB | $1.3–3/hr | 1–2 h | **$2–6** |

## Honesty rails

- The **frozen test split is hash-pinned** (`test.jsonl.sha256`); preflight
  refuses to train if the file drifted. It is never in the train/valid mix.
- Rows render through the model's **own chat template with the full 13-tool
  registry attached** — the exact prompt distribution the runtime and the
  91-case eval use (root-cause fix D1; see
  `training/reports/valid_tool_json_root_cause.md`).
- Ship gate (pre-committed): a checkpoint ships only if the frozen eval scores
  **valid_tool_json 4/4 AND >60/91**. Otherwise: archive, honestly.

## Validate the bundle without a GPU

```bash
python training/cuda/train_cuda.py --check --data training/data/merged
bash -n training/cuda/run.sh
```

`--check` parses config, verifies data presence and the frozen-split hash, and
exits without importing torch.
