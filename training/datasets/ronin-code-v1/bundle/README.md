# Training bundle — coding (qlora)

**Status: GENERATED. Nothing has been trained.** This directory is a complete,
runnable bundle. Training is a separate manual step on hardware you provide.

- base model: `mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit`
- training rows (eligible only): 8
- seed: 0 (reproducible)

## Estimates (heuristic, not guarantees)

- min VRAM (QLoRA 1.5B): ~6 GB
- recommended RAM: ~16 GB
- approx wall-clock: ~15 min
- approx cloud GPU cost: ~$1.12

## Run

```bash
pip install -r requirements.txt   # Apple Silicon: mlx-lm
python train.py                   # writes adapters/
```

## Evaluate before trusting

```bash
python -m ronin_training.eval_runner \
    --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit --adapter adapters \
    --evals ../evals/ronin_protocol_eval.jsonl --out eval.md
```

Compare against the base model and the current best adapter. Only promote via
the adapter registry after evals pass and a human approves.

## Serve locally (after training)

```bash
ollama create ronin-tuned -f Modelfile   # if exported to GGUF
# or point Ronin at the MLX adapter:
export RONIN_ADAPTER=$(pwd)/adapters
ronin --provider local "read pyproject.toml and tell me the package name"
```
