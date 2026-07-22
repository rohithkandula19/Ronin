# Model comparison — v2 fine-tune on the expanded corpus (2026-07-15)

Everything below was measured on-device (M2 8 GB, mlx-lm 0.31.3). Nothing is
projected. The scorer is the runtime-parity harness: a tool call earns credit ONLY
if Ronin's runtime could actually consume it (wrapper-enclosed, call-shaped).

## Setup

- **Base:** `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit` (QLoRA, seed 0)
- **v1:** the previous adapter, trained on the 55-row corpus (best checkpoint iter-150)
- **v2:** this run — 400 iters on the expanded corpus (284 rows → 228 train / 28
  valid / 28 test; Volume VII–IX added 229 rows targeting grounding + multi-turn),
  batch 1, 8 LoRA layers, lr 1e-5, seq 3072, grad-checkpoint, peak **3.34 GB**
- **Eval set:** 91 cases (the original 19 + 72 new, incl. 28 multi-turn cases with
  scripted mid-session context)

## v2 val-loss curve (train log, verbatim)

1 → 2.669 · 75 → 1.278 · 150 → 1.165 · 200 → 1.133 · 250 → 1.111 · **300 → 1.088
(min)** · 350 → 1.124 · 400 → 1.101

More data moved the val-loss minimum from ~iter 75 (45-row corpus) to ~iter 300.

## Protocol evals — 91-case set, runtime-parity extraction

| checkpoint | total | old-19 subset | grounding /20 | multi-turn /28 | gate /12 | tool-json /4 |
|---|---|---|---|---|---|---|
| base | 23/91 (25.3%) | 8/19 | 4 | 5 | 6 | 0 |
| v1 iter-150 (prev best) | 27/91 (29.7%) | 10/19 | 3 | 7 | 6 | 0 |
| v2 iter-75 | 31/91 (34.1%) | 10/19 | 5 | 9 | 6 | 0 |
| v2 iter-100 | 33/91 (36.3%) | 10/19 | 6 | 10 | 6 | 0 |
| **v2 iter-150 (NEW BEST)** | **36/91 (39.6%)** | **11/19** | 5 | **11** | 6 | 0 |
| v2 iter-200 | 34/91 (37.4%) | 10/19 | 5 | 10 | 6 | 0 |
| v2 iter-300 | 33/91 (36.3%) | 10/19 | 5 | 10 | 7 | 0 |

## Findings

1. **The expanded corpus works.** v2 iter-150 beats the previous best adapter on
   the full set (36 vs 27 of 91, +33% relative) and on the old-19 subset under
   identical rules (11 vs 10 of 19). Every v2 checkpoint beats v1.
2. **Protocol evals peak at iter-150, val loss at iter-300.** Val loss is not a
   protocol proxy; checkpoint selection must use the protocol evals. "More
   iterations are better" is false past 150 here, again.
3. **The old weak areas moved.** Old grounding cases 0/2 → **1/2**; old multi-turn
   case 0/1 → **1/1**. On the expanded categories: multi-turn 5 → 11 of 28 vs
   base; grounding 4 → 5 of 20 (weakest gain — see below).
4. **No approval-gate regression:** gate_respect 6/12 for base and v2-150 alike
   (v2-300 reached 7/12, but loses more elsewhere).

## About the old 68.4% headline

The previous report's 68.4% (v1 iter-150, 19 cases) was measured with a LENIENT
extractor that credited tool calls emitted as bare JSON in prose — output Ronin's
runtime deliberately refuses to execute (a live probe showed a model "refusing"
`rm -rf ~` while echoing the call JSON in its refusal text). That extractor was
retired for runtime parity; under the current rules the same v1 checkpoint scores
52.6% on the same 19 cases, and v2 iter-150 scores 57.9% on them. Every number in
the table above is comparable; the 68.4% is not, and is kept only as history.

## What is still weak (unresolved, stated plainly)

- **valid_tool_json 0/4 on every checkpoint** — no model reliably emits
  wrapper-perfect calls for these cases yet; the runtime's strict parser is the
  gate the model must clear, and it doesn't.
- **Grounding gains are small** (4 → 5/20): the model tends to narrate what it
  would read instead of emitting the read call; `grounding_check_before_describe`
  still fails on the best checkpoint.
- 39.6% absolute is far from usable autonomy for a 1.5B local model. The gap to
  close next is format stamina (tool-JSON) and read-first reflexes, both of which
  respond to targeted data (this run proved data moves category scores).

## Recommendation

- **Ship/point RONIN_ADAPTER at v2 iter-150** (`0000150_adapters.safetensors`
  from the v2 run). Do not train past ~200 iters on this corpus.
- Next lever, in order: (1) tool-JSON format drills (short rows that isolate the
  wrapper format under many contexts), (2) more read-first rows with the CALL as
  the immediate next token (no narration first), (3) only then consider the 7B
  base or full fine-tuning — this corpus is not yet extracting the 1.5B's ceiling.

## Reproduce (seed 0)

```bash
uv run --package ronin-training python -m ronin_training.dataset_builder \
    --volumes docs/volumes --out training/data/generated
uv run mlx_lm.lora --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit \
    --train --data training/data/generated --fine-tune-type lora \
    --iters 400 --batch-size 1 --num-layers 8 --learning-rate 1e-5 \
    --max-seq-length 3072 --steps-per-report 25 --steps-per-eval 25 \
    --save-every 25 --seed 0 --grad-checkpoint \
    --adapter-path training/adapters/ronin_1.5b_v2
# best checkpoint = training/adapters/ronin_1.5b_v2/0000150_adapters.safetensors
./training/scripts/eval_sweep.sh   # the 7-checkpoint eval sweep
```
