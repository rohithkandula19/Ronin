#!/bin/zsh
# v3b (extended-train) checkpoint sweep on the FROZEN 91-case acceptance set.
# Tests the undertraining hypothesis: v3-250 was ~0.3 epochs; v3b runs to 1200
# (~1.4 epochs) so the format drills are seen multiple times.
set -u
cd /Users/rohithkandula/ronin
MODEL="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
EVALS="training/eval_snapshots/eval91_frozen.jsonl"
LOG="training/reports/sweep_v3b.log"
: > "$LOG"

V3B="training/adapters/ronin_1.5b_v3b"
for it in 0000300 0000450 0000600 0000750 0000900 0001050 0001200; do
  d="training/adapters/ronin_1.5b_v3b_ck/i${it: -4}"
  mkdir -p "$d"
  cp "$V3B/adapter_config.json" "$d/" 2>/dev/null
  cp "$V3B/${it}_adapters.safetensors" "$d/adapters.safetensors" 2>/dev/null
done

run() {
  local name="$1" adapter="$2"
  uv run python -m ronin_training.eval_runner --model "$MODEL" --adapter "$adapter" \
    --evals "$EVALS" --out "training/reports/eval91f_${name}.md" --title "${name} (frozen-91)" >> "$LOG" 2>&1
  echo "DONE ${name} rc=$?" >> "$LOG"
}

run v3b_iter0300 "training/adapters/ronin_1.5b_v3b_ck/i0300"
run v3b_iter0450 "training/adapters/ronin_1.5b_v3b_ck/i0450"
run v3b_iter0600 "training/adapters/ronin_1.5b_v3b_ck/i0600"
run v3b_iter0750 "training/adapters/ronin_1.5b_v3b_ck/i0750"
run v3b_iter0900 "training/adapters/ronin_1.5b_v3b_ck/i0900"
run v3b_iter1050 "training/adapters/ronin_1.5b_v3b_ck/i1050"
run v3b_iter1200 "training/adapters/ronin_1.5b_v3b_ck/i1200"
echo "ALL DONE" >> "$LOG"
