"""Multi-turn correctness: the assistant mask, the reward broadcast, the DAPO norm.

Three rules a GPU trainer (verl/SkyRL) must apply for agentic GRPO to learn the task
rather than an artefact of how the loss is summed. They are pure list arithmetic here so
they can be *proved* offline; the trainer imports these instead of re-deriving them, which
is how the subtle version drifts and silently trains the wrong thing.

1. **Mask every non-assistant token.** Same rule as SFT (:mod:`ronin_training.harvest`):
   loss is computed on assistant tokens only. Training on tool-result tokens teaches the
   model to *predict* file contents instead of *reading* them — the exact failure that
   ruins agent fine-tunes — and training on the user turn teaches it to write the human's
   half. :func:`assistant_token_mask` turns the assistant spans into a 0/1 mask.

2. **The outcome reward is uniform over assistant tokens.** A rollout earns one scalar
   (:mod:`ronin_training.rl.reward`); :func:`broadcast_reward` spreads it equally across
   the assistant tokens that produced it, so every generated token in a passing trajectory
   is credited the same. Non-assistant tokens get zero — they are not the policy's output.

3. **Normalise the loss by tokens across the batch, not per sequence (the DAPO fix).**
   Dividing each trajectory's loss by *its own* length lets the policy game the reward by
   getting longer or shorter: a per-sequence mean makes each token in a short trajectory
   worth more, so padding or truncating shifts the objective without changing behaviour.
   :func:`dapo_normalize` divides the summed token losses by the *total* assistant-token
   count over the whole batch, so a token is worth the same wherever it sits and
   trajectory length stops being a lever. See the DAPO report; this is that one line.
"""

from __future__ import annotations

from collections.abc import Sequence


def assistant_token_mask(spans: Sequence[tuple[int, int]], n_tokens: int) -> list[int]:
    """A 0/1 mask of length ``n_tokens``; 1 exactly on the assistant ``[start, end)`` spans.

    ``spans`` are half-open token-index ranges (the shape :func:`ronin_training.harvest`'s
    ``assistant_spans`` records). Overlapping or unordered spans are fine — a token in any
    span is 1. A span that runs past ``n_tokens`` is clamped, and a negative start is a
    programming error worth raising on rather than silently masking the prompt.
    """
    if n_tokens < 0:
        raise ValueError("n_tokens must be >= 0")
    mask = [0] * n_tokens
    for start, end in spans:
        if start < 0 or end < start:
            raise ValueError(f"bad assistant span ({start}, {end})")
        for i in range(start, min(end, n_tokens)):
            mask[i] = 1
    return mask


def broadcast_reward(reward: float, mask: Sequence[int]) -> list[float]:
    """Spread one rollout's scalar reward uniformly over its assistant tokens.

    Every masked (assistant) token gets ``reward``; every other token gets ``0.0``. This
    is the "outcome reward, uniform over assistant tokens" starting point — no per-token
    credit assignment, which is a later refinement and not something to fake now.
    """
    return [reward if bit else 0.0 for bit in mask]


def dapo_normalize(
    token_losses: Sequence[Sequence[float]], masks: Sequence[Sequence[int]]
) -> float:
    """Sum masked token losses over the batch and divide by the total assistant tokens.

    ``token_losses`` and ``masks`` are aligned per sequence. The denominator is the batch's
    total assistant-token count — **not** a per-sequence mean-then-average — so a long
    trajectory contributes proportionally to its token count and length cannot be gamed. A
    batch with no assistant tokens returns ``0.0`` rather than dividing by zero.
    """
    if len(token_losses) != len(masks):
        raise ValueError("token_losses and masks must have one entry per sequence")
    total_loss = 0.0
    total_tokens = 0
    for losses, mask in zip(token_losses, masks, strict=True):
        if len(losses) != len(mask):
            raise ValueError("each sequence's losses and mask must be the same length")
        for loss, bit in zip(losses, mask, strict=True):
            if bit:
                total_loss += loss
                total_tokens += 1
    return total_loss / total_tokens if total_tokens else 0.0


__all__ = ["assistant_token_mask", "broadcast_reward", "dapo_normalize"]
