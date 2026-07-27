#!/bin/zsh
# v3 checkpoint sweep on the FROZEN 91-case acceptance set (runtime-parity).
# Anchor: v2_iter150 must reproduce 36/91 (proves the frozen file == v2's eval set).
set -u
cd /Users/rohithkandula/ronin
MODEL="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
EVALS="training/eval_snapshots/eval91_frozen.jsonl"
LOG="training/reports/sweep_v3.log"
: > "$LOG"

# stage per-iter checkpoint dirs from the v3 run
V3="training/adapters/ronin_1.5b_v3"
for it in 0000050 0000075 0000100 0000150 0000200 0000250; do
  d="training/adapters/ronin_1.5b_v3_ck/i${it: -3}"
  mkdir -p "$d"
  cp "$V3/adapter_config.json" "$d/" 2>/dev/null
  cp "$V3/${it}_adapters.safetensors" "$d/adapters.safetensors" 2>/dev/null
done

run() {
  local name="$1" adapter="$2"
  if [ -n "$adapter" ]; then
    uv run python -m ronin_training.eval_runner --model "$MODEL" --adapter "$adapter" \
      --evals "$EVALS" --out "training/reports/eval91f_${name}.md" --title "${name} (frozen-91)" >> "$LOG" 2>&1
  else
    uv run python -m ronin_training.eval_runner --model "$MODEL" \
      --evals "$EVALS" --out "training/reports/eval91f_${name}.md" --title "${name} (frozen-91)" >> "$LOG" 2>&1
  fi
  echo "DONE ${name} rc=$?" >> "$LOG"
}

run v2_iter150 "training/adapters/ronin_1.5b_v2_ck/i150"
run v3_iter050 "training/adapters/ronin_1.5b_v3_ck/i050"
run v3_iter075 "training/adapters/ronin_1.5b_v3_ck/i075"
run v3_iter100 "training/adapters/ronin_1.5b_v3_ck/i100"
run v3_iter150 "training/adapters/ronin_1.5b_v3_ck/i150"
run v3_iter200 "training/adapters/ronin_1.5b_v3_ck/i200"
run v3_iter250 "training/adapters/ronin_1.5b_v3_ck/i250"
echo "ALL DONE" >> "$LOG"
