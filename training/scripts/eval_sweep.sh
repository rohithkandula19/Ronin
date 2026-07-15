#!/bin/zsh
# Sequential protocol-eval sweep on the 91-case set (runtime-parity extraction).
# Detached runner: each line of sweep.log marks a completed checkpoint eval.
set -u
cd /Users/rohithkandula/ronin
MODEL="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
EVALS="training/data/evals/ronin_protocol_eval.jsonl"
LOG="training/reports/sweep.log"
: > "$LOG"

run() {
  local name="$1" adapter="$2"
  if [ -n "$adapter" ]; then
    uv run python -m ronin_training.eval_runner --model "$MODEL" --adapter "$adapter" \
      --evals "$EVALS" --out "training/reports/eval91_${name}.md" --title "${name} (91-case, runtime-parity)" \
      >> "$LOG" 2>&1
  else
    uv run python -m ronin_training.eval_runner --model "$MODEL" \
      --evals "$EVALS" --out "training/reports/eval91_${name}.md" --title "${name} (91-case, runtime-parity)" \
      >> "$LOG" 2>&1
  fi
  echo "DONE ${name} rc=$?" >> "$LOG"
}

run base ""
run v1_iter150 "training/adapters/ronin_1.5b_iter150"
run v2_iter075 "training/adapters/ronin_1.5b_v2_ck/i075"
run v2_iter100 "training/adapters/ronin_1.5b_v2_ck/i100"
run v2_iter150 "training/adapters/ronin_1.5b_v2_ck/i150"
run v2_iter200 "training/adapters/ronin_1.5b_v2_ck/i200"
run v2_iter300 "training/adapters/ronin_1.5b_v2_ck/i300"
echo "ALL DONE" >> "$LOG"
