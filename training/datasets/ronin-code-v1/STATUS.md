# ronin-code-v1 — status

**State: GENERATED — training BLOCKED_HARDWARE. No trained adapter exists.**

This environment is x86-64 Linux with no NVIDIA GPU, no CUDA/torch, and no MLX,
so QLoRA/LoRA training cannot run here. Per the program's honesty rules, no
adapter is claimed. What exists is a complete, reproducible, statically-validated
training bundle under `bundle/`.

## Verified locally (executable evidence)

- 44 human-authored behavior examples (34 single-turn across 16 categories +
  10 multi-turn with prior-turn context in the `input` field: constraint memory,
  resume-after-detour, no-redo of approved work, in-context correction, claim
  retraction, status, scope, grounding, recovery) (repository_analysis,
  read_before_write, file_selection, planning, tool_choice, structured_edits,
  test_generation, debugging, verification, refusal_unsafe, approval_aware,
  uncertainty, no_invention, scope_control, code_review, failure_recovery) —
  the RIGHT use of a fine-tune (behavior/format), not facts. Edit `corpus.py`
  and re-run `build.py`; a regression test guards dedup + contamination.
- A 10-row LOCKED eval split (`locked_eval.jsonl`, hashed; 8 single-turn + 2
  multi-turn probes) held OUT of training for honest base-vs-adapter comparison.
- Provenance gate: 44/44 eligible (owner license + owner_self consent + reviewed
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
