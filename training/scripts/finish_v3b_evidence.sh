#!/bin/zsh
# Finish the v3b fine-tuning evidence phase (Apple Silicon only).
#
# Scores every v3b checkpoint against the FROZEN 91-case runtime-parity eval set
# and then aggregates all v2/v3/v3b reports into v3_finetune_comparison.md.
# Honest by construction:
#   - refuses to run if the frozen eval set or the v3b adapter dir is missing;
#   - a missing checkpoint file is logged and skipped, never invented;
#   - the frozen eval set is read, never written;
#   - resumable — an existing report is kept unless you pass --fresh.
#
# Usage:  ./training/scripts/finish_v3b_evidence.sh [--fresh]
set -u

cd "$(dirname "$0")/../.." || exit 1
REPO_ROOT="$PWD"

MODEL="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit"
FROZEN="training/eval_snapshots/eval91_frozen.jsonl"
SRC_DIR="training/adapters/ronin_1.5b_v3b"
STAGE_DIR="training/adapters/ronin_1.5b_v3b_ck"
REPORTS="training/reports"
LOG="$REPORTS/sweep_v3b.log"
ITERS=(300 450 600 750 900 1050)
FRESH=0
[ "${1:-}" = "--fresh" ] && FRESH=1

# ---------------------------------------------------------------- preflight
fail() { echo "FATAL: $1" >&2; exit 1; }

branch=$(git rev-parse --abbrev-ref HEAD)
echo "repo:   $REPO_ROOT"
echo "branch: $branch"
[ "$branch" = "feat/v3-protocol-dialect" ] || \
  echo "WARN: expected branch feat/v3-protocol-dialect, on '$branch' — continuing"

[ -f "$FROZEN" ] || fail "frozen eval set not found: $FROZEN (this sweep only scores against the frozen snapshot)"
[ -d "$SRC_DIR" ] || fail "v3b adapter dir not found: $SRC_DIR (train v3b first; adapters are not in git)"
[ -f "$SRC_DIR/adapter_config.json" ] || fail "$SRC_DIR/adapter_config.json missing — cannot stage checkpoints"

command -v uv >/dev/null || fail "uv not found on PATH"
python3 -c "import platform,sys; sys.exit(0 if platform.machine()=='arm64' else 1)" || \
  fail "not an Apple Silicon machine — mlx-lm cannot evaluate these adapters here"

mkdir -p "$STAGE_DIR"
: > "$LOG"
echo "frozen eval set: $FROZEN ($(grep -c . "$FROZEN") cases)" | tee -a "$LOG"

# ------------------------------------------------------------------- sweep
missing=0
run_eval() {
  local name="$1" adapter="$2"
  local out="$REPORTS/eval91f_${name}.md"
  if [ "$FRESH" -eq 0 ] && [ -f "$out" ]; then
    echo "SKIP ${name} — $out exists (pass --fresh to redo)" | tee -a "$LOG"
    return 0
  fi
  uv run python -m ronin_training.eval_runner --model "$MODEL" --adapter "$adapter" \
    --evals "$FROZEN" --out "$out" \
    --title "${name} (91-case frozen, runtime-parity)" >> "$LOG" 2>&1
  echo "DONE ${name} rc=$?" | tee -a "$LOG"
}

for it in "${ITERS[@]}"; do
  it4=$(printf "%04d" "$it")                              # report suffix: iter0300 … iter1050
  ck=$(printf "%s/%07d_adapters.safetensors" "$SRC_DIR" "$it")  # mlx_lm name: 0000300 … 0001050
  if [ ! -f "$ck" ]; then
    echo "SKIP MISSING v3b_iter${it4} — expected $ck" | tee -a "$LOG"
    echo "  (checkpoints present: $(ls "$SRC_DIR" | grep safetensors | tr '\n' ' '))" | tee -a "$LOG"
    missing=$((missing + 1))
    continue
  fi
  stage="$STAGE_DIR/i${it4}"
  mkdir -p "$stage"
  cp "$SRC_DIR/adapter_config.json" "$stage/adapter_config.json"
  cp "$ck" "$stage/adapters.safetensors"
  run_eval "v3b_iter${it4}" "$stage"
done

# the final adapter, if training wrote one at the dir root
if [ -f "$SRC_DIR/adapters.safetensors" ]; then
  run_eval "v3b_final" "$SRC_DIR"
else
  echo "NOTE: no $SRC_DIR/adapters.safetensors — final-adapter eval skipped" | tee -a "$LOG"
fi

# --------------------------------------------------------------- aggregate
uv run python -m ronin_training.aggregate_comparison \
  --reports-dir "$REPORTS" --out "$REPORTS/v3_finetune_comparison.md" \
  --model "$MODEL" --frozen-evals "$FROZEN" | tee -a "$LOG"

echo "ALL DONE (missing checkpoints: $missing)" | tee -a "$LOG"
echo
echo "Next: read $REPORTS/v3_finetune_comparison.md — it states the verdict."
echo "Then: git add training/reports/eval91f_v3b_*.md $REPORTS/v3_finetune_comparison.md"
echo "      (adapter binaries stay untracked — training/.gitignore already excludes them)"
[ "$missing" -eq 0 ] || exit 2
