#!/usr/bin/env bash
# Gate-enforced publish for ronin-code-1.5b — $0 end to end.
#
#   bash training/publish_model.sh <adapter-dir> [hf-namespace]
#
# Given an adapter directory from the free Colab/Kaggle run, this:
#   1. re-runs the HONEST eval (frozen, sha256-pinned 91-case set; the runner
#      refuses a drifted set) with the baseline delta;
#   2. checks the measured scores against the pre-committed gate in
#      training/config/publish_gate.json via ronin_training.publish_gate —
#      the gate is enforced IN CODE; a sub-gate score stops this script here;
#   3. fills the model card's PENDING cells from the measured run (SHA-stamped);
#   4. uploads adapter + card with the free `hf` CLI — or, if not logged in,
#      prints the exact commands and exits cleanly.
# No paid resource anywhere. No number is written that wasn't measured.
set -euo pipefail

ADAPTER=${1:?usage: bash training/publish_model.sh <adapter-dir> [hf-namespace]}
NAMESPACE=${2:-}
cd "$(dirname "$0")/.."

GATE=training/config/publish_gate.json
BASE=$(python3 -c "import json; print(json.load(open('$GATE'))['base_model'])")
REPO=$(python3 -c "import json; print(json.load(open('$GATE'))['hf_repo'])")
CARD=$(python3 -c "import json; print(json.load(open('$GATE'))['model_card'])")
STAMP=$(date +%Y%m%d-%H%M%S)
SCORES=training/reports/publish_scores_${STAMP}.json
REPORT=training/reports/publish_eval_${STAMP}.md

[ -d "$ADAPTER" ] || { echo "adapter dir not found: $ADAPTER" >&2; exit 2; }

echo "== 1/4 honest eval (frozen set + baseline delta)"
python3 -m ronin_training.eval_runner --provider hf --model "$BASE" \
  --adapter "$ADAPTER" --baseline --scores-json "$SCORES" \
  --out "$REPORT" --title "publish gate"

echo "== 2/4 the gate (training/config/publish_gate.json — enforced in code)"
python3 -m ronin_training.publish_gate check --scores "$SCORES"

echo "== 3/4 fill the model card from the measured run"
python3 -m ronin_training.publish_gate fill-card --scores "$SCORES" \
  --adapter-path "$ADAPTER"

echo "== 4/4 publish (free HF account)"
TARGET="${NAMESPACE:+$NAMESPACE/}$REPO"
UP_ADAPTER=(hf upload "$TARGET" "$ADAPTER" --repo-type model)
UP_CARD=(hf upload "$TARGET" "$CARD" README.md --repo-type model)
if command -v hf >/dev/null 2>&1 && hf auth whoami >/dev/null 2>&1; then
  "${UP_ADAPTER[@]}"
  "${UP_CARD[@]}"
  echo "published: https://huggingface.co/$TARGET"
else
  echo "hf CLI not logged in — after 'pip install -U huggingface_hub && hf auth login' (free), run:"
  echo "  ${UP_ADAPTER[*]}"
  echo "  ${UP_CARD[*]}"
fi
