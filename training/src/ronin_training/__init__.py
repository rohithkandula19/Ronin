"""Ronin volume-driven fine-tuning toolkit.

Turns Ronin's behavioral *volumes* (protocol law written as machine-readable
training patterns) into validated MLX-format JSONL + a protocol eval suite, then
into a local LoRA/full fine-tune. Deterministic: no LLM generates the final JSONL.

Pipeline: volumes → parse → validate (schema + real tool registry) → split →
train/valid/test.jsonl + evals → (MLX) train → eval base vs adapter → integrate.

Honesty invariants (inherited from Ronin): never fabricate a benchmark or an eval
result; never train on proprietary assistant output; unknown stays unknown.
"""
__version__ = "0.1.0"
