#!/usr/bin/env bash
# ronin-code-1.5b — turnkey CUDA training run for a fresh A100/4090 box.
# Cost ballpark: 4090 rent ≈ $0.4-0.8/hr, A100 ≈ $1.3-3/hr; LoRA over 1.5B on
# ~4.8k rows ≈ 1-4 hours → roughly $1-6 total.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

echo "== 1/5 deps (pinned) =="
python -m pip install -q -r training/cuda/requirements.txt
python -m pip install -q -e packages/dialect -e training

echo "== 2/5 corpus (deterministic; regenerates byte-for-byte) =="
python -m ronin_training.dataset_builder --volumes docs/volumes --out training/data/generated
python -m ronin_training.synthetic_corpus --target 5000 --seed 42 --out training/data/synthetic

echo "== 3/5 merge splits (volumes + synthetic; frozen test stays synthetic-pinned) =="
mkdir -p training/data/merged
for split in train valid; do
  cat training/data/generated/$split.jsonl training/data/synthetic/$split.jsonl \
    > training/data/merged/$split.jsonl
done
# The FROZEN eval split is the hash-pinned synthetic test split — never trained on.
cp training/data/synthetic/test.jsonl training/data/merged/test.jsonl
cp training/data/synthetic/test.jsonl.sha256 training/data/merged/test.jsonl.sha256

echo "== 4/5 preflight (config + frozen-split hash) =="
python training/cuda/train_cuda.py --check --data training/data/merged

echo "== 5/5 train =="
python training/cuda/train_cuda.py --data training/data/merged \
  --out training/adapters/ronin-code-1.5b-v4

echo "done. Artifacts in training/adapters/ronin-code-1.5b-v4/ (adapters/ + fused/)."
echo "Convert fused/ for the mlx embedded provider on a Mac:"
echo "  mlx_lm.convert --hf-path training/adapters/ronin-code-1.5b-v4/fused -q"
