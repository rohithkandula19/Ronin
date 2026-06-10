"""Style — the first slice of 'ronin learns to be you'.

The endgame of provider-agnosticism is your *own* model: one that runs free and
codes in your style. That needs a dataset. ronin already archives every session;
this turns those USER/ASSISTANT transcripts into instruction→response pairs you
can fine-tune a local (or hosted) model on — optionally prepending your Bushido
so the model learns your standing conventions too.

The transcript→dataset transform and JSONL rendering are pure and unit-tested;
the command just gathers sessions and writes the file.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from .replay import transcript_to_turns

# Skip trivially short turns — they add noise, not signal.
_MIN_INSTRUCTION = 8
_MIN_RESPONSE = 16


@dataclass
class Example:
    instruction: str
    response: str


def transcript_to_examples(transcript: list[str]) -> list[Example]:
    """Pair each USER turn with the following ASSISTANT turn into a training
    example. Drops unpaired or trivially short turns. Pure."""
    turns = transcript_to_turns(transcript)
    out: list[Example] = []
    i = 0
    while i < len(turns) - 1:
        if turns[i].role == "user" and turns[i + 1].role == "assistant":
            instr, resp = turns[i].text.strip(), turns[i + 1].text.strip()
            if len(instr) >= _MIN_INSTRUCTION and len(resp) >= _MIN_RESPONSE:
                out.append(Example(instruction=instr, response=resp))
            i += 2
        else:
            i += 1
    return out


def build_dataset(transcripts: list[list[str]], *, system: str | None = None) -> list[dict]:
    """Build a chat-style fine-tuning dataset from many transcripts. Each row is
    an OpenAI-style ``{"messages": [...]}`` record; ``system`` (e.g. your Bushido)
    is prepended to every example when given. Pure."""
    rows: list[dict] = []
    for t in transcripts:
        for ex in transcript_to_examples(t):
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs.append({"role": "user", "content": ex.instruction})
            msgs.append({"role": "assistant", "content": ex.response})
            rows.append({"messages": msgs})
    return rows


def to_jsonl(rows: list[dict]) -> str:
    """Render dataset rows as JSONL (one JSON object per line). Pure."""
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
