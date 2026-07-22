# ronin-code-v1 — status

**State: GENERATED — training BLOCKED_HARDWARE. No trained adapter exists.**

This environment is x86-64 Linux with no NVIDIA GPU, no CUDA/torch, and no MLX,
so QLoRA/LoRA training cannot run here. Per the program's honesty rules, no
adapter is claimed. What exists is a complete, reproducible, statically-validated
training bundle under `bundle/`.

## Verified locally (executable evidence)

- 8 owner-authored behavior examples (read-before-write, approval-awareness,
  no-invention, scope control, destructive-refusal) — the RIGHT use of a
  fine-tune (behavior/format), not facts.
- Provenance gate: 8/8 eligible (owner license + owner_self consent + reviewed
  redaction); 0 excluded.
- Quality gate: 0 exact/near duplicates, 0 empty outputs.
- Contamination: 0 train/locked-test overlap pairs.
- `bundle/dataset.sha256` pins the dataset for reproducibility + future
  contamination checks.
- `bundle/train.py` passes static compile validation.
- Forge job state = `generated`, `trained == False` (enforced; cannot be
  mislabeled trained).

## To actually train (on a CUDA GPU or Apple Silicon)

```bash
cd training/datasets/ronin-code-v1/bundle
pip install -r requirements.txt          # Apple Silicon: mlx-lm ; CUDA: unsloth/trl/peft
sha256sum -c <(echo "$(cat dataset.sha256)  dataset.jsonl")   # verify dataset hash
python train.py                          # writes adapters/
# evaluate BEFORE trusting — base vs adapter on the SAME locked set:
python -m ronin_training.eval_runner --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit \
    --adapter adapters --evals ../../../data/evals/ronin_protocol_eval.jsonl --out eval.md
```

## Release gates (must ALL pass before APPROVED — not before)

training completed · dataset hash verified · no secret leakage · locked eval
passes threshold · safety eval passes · beats base on target tasks · no general
regression beyond threshold · local serving works · model card complete ·
rollback target set. Otherwise mark REJECTED / EXPERIMENTAL / BLOCKED_HARDWARE.

Until then the recommended adapter remains the prior v2 iter-150 (registered
`completed`, not released).
