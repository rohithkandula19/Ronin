# Fine-tune experiment record — Qwen2.5-Coder-1.5B on the Ronin protocol corpus

On-device (M2, 8 GB), 2026-07-14. Everything below was actually run; nothing is
projected or assumed.

## Setup

- **Base model:** `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` (QLoRA: LoRA over
  the 4-bit base), via `mlx_lm.lora`, mlx-lm 0.31.3.
- **Data:** 45 train / 5 valid / 5 test rows built from `docs/volumes/volume_01..06`
  by `ronin_training.dataset_builder` (55 unique rows, 0 rejected, 0 coverage gaps).
- **Run:** 300 iters, batch 1, 8 LoRA layers, lr 1e-5, seq 2048, seed 0,
  grad-checkpoint. Trainable params 0.171% (2.6M/1543M).
- **Peak memory: 1.78 GB** — an 8 GB M2 handles this with a wide margin.
  (A 2-iter 0.5B smoke run peaked at 0.65 GB.)

## Loss curve (from the actual run log)

| iter | train loss | val loss |
|---|---|---|
| 1 | — | 3.792 |
| 75 | 1.439 | **1.070** |
| 150 | 0.681 | 1.073 |
| 225 | 0.332 | 1.140 |
| 300 | 0.198 | 1.211 |

Val loss bottoms at ~iter 75–150 and then rises while train loss keeps falling:
textbook overfitting on a 45-row corpus. The iter-150 checkpoint is the usable one.

## Protocol eval results

19 deterministic cases (`training/data/evals/ronin_protocol_eval.jsonl`), same set
for every checkpoint, run via `python -m ronin_training.eval_runner`.

**The first eval set was defective.** A 70-agent adversarial audit confirmed 51
defects across all 19 cases — training/eval contradictions (an SFT refusal that
names `rm -rf ~` while the eval banned that substring), brittle single-token
`must_include: ["not"]` checks, over-strict bans on quoting-while-refusing, and
missing `run_background` in shell bans. The scorer and every case were fixed
(contraction/apostrophe normalization, claim-shaped bans, call-shaped tool
extraction, `must_call_any_of`, `forbid_unknown_tools`), and all three checkpoints
were re-scored on the corrected set. Both rounds are reported for honesty:

| checkpoint | defective evals (historical) | corrected evals |
|---|---|---|
| base 1.5B | 11/19 (57.9%) | 10/19 (52.6%) |
| adapter iter-150 (val-loss min) | 9/19 (47.4%) | **13/19 (68.4%)** |
| adapter iter-300 (final) | 8/19 (42.1%) | 12/19 (63.2%) |

On the defective set the fine-tune looked like a regression; that was mis-scoring
(e.g. the adapter faithfully reproduced a trained refusal that named the dangerous
command, and the eval banned the name). On the corrected set:

- **The fine-tune helps: +15.8pp at iter-150 over base** (10 → 13 of 19).
- **Checkpoint order matches the loss curve** — iter-150 beats iter-300, i.e. the
  overtrained adapter genuinely lost ground, visible in both val loss and evals.
- Category movement (base → iter-150): gate_respect 6/9 → 8/9, recovery 1/2 → 2/2,
  final_answer 0/1 → 1/1; valid_tool_json regressed 1/1 → 0/1.

## What is still weak (unresolved, not excused)

- **grounding 0/2 on every checkpoint** — the single-turn eval expects a read tool
  call before describing unseen code; no checkpoint reliably emits it.
- **multi_turn_stability 0/1 on every checkpoint** — a single-turn harness limits
  what this case can test; a real multi-turn eval loop is future work.
- 45 training rows is a tiny corpus; the val-loss curve says more data matters more
  than more iterations. Scale the volumes before scaling `--iters`.

## Reproduce

```bash
uv run --package ronin-training python -m ronin_training.dataset_builder \
    --volumes docs/volumes --out training/data/generated
uv run mlx_lm.lora --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit \
    --train --data training/data/generated --fine-tune-type lora \
    --iters 150 --batch-size 1 --num-layers 8 --learning-rate 1e-5 \
    --max-seq-length 2048 --seed 0 --grad-checkpoint \
    --adapter-path training/adapters/ronin_1.5b
uv run python -m ronin_training.eval_runner \
    --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit \
    --adapter training/adapters/ronin_1.5b \
    --evals training/data/evals/ronin_protocol_eval.jsonl \
    --out training/reports/adapter_eval.md --title "Adapter"
```

Adapters are not committed (binary, reproducible from the seed and data above).
