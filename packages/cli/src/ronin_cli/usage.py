"""Token / cost tracking. Records every agent run to ``.csk/usage.jsonl``.

The pricing table covers common Anthropic / OpenAI / Together / Groq / Fireworks
models in USD per million tokens. Local providers (Ollama, etc.) are free.
Override any entry by editing ``PRICING`` or by passing custom prices to
``estimate_cost``.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.0, "output": 30.0},
    # Together (representative pricing for popular OSS models)
    "meta-llama/Llama-3.3-70B-Instruct-Turbo": {"input": 0.88, "output": 0.88},
    # Groq (representative)
    "llama-3.3-70b-versatile": {"input": 0.59, "output": 0.79},
    # Fireworks (representative)
    "accounts/fireworks/models/llama-v3p3-70b-instruct": {"input": 0.90, "output": 0.90},
    # Local (Ollama) — free
    "llama3.1": {"input": 0.0, "output": 0.0},
    "llama3": {"input": 0.0, "output": 0.0},
}


class UsageRecord(BaseModel):
    timestamp: str
    command: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: dict[str, dict[str, float]] | None = None,
) -> float:
    """Cost in USD. Unknown models cost 0 (unknown-by-design, not a bug)."""
    table = pricing or PRICING
    rates = table.get(model, {"input": 0.0, "output": 0.0})
    return round(
        (input_tokens / 1_000_000) * rates["input"]
        + (output_tokens / 1_000_000) * rates["output"],
        6,
    )


def usage_path() -> Path:
    return Path(".csk") / "usage.jsonl"


def record_usage(
    command: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    path: Path | None = None,
    pricing: dict[str, dict[str, float]] | None = None,
) -> UsageRecord:
    """Append a usage record. Silently no-ops if the input usage looks empty."""
    if input_tokens == 0 and output_tokens == 0:
        return UsageRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            command=command, provider=provider, model=model,
            input_tokens=0, output_tokens=0, cost_usd=0.0,
        )
    path = path or usage_path()
    record = UsageRecord(
        timestamp=datetime.now(timezone.utc).isoformat(),
        command=command,
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=estimate_cost(model, input_tokens, output_tokens, pricing),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(record.model_dump_json() + "\n")
    return record


def load_records(path: Path | None = None) -> list[UsageRecord]:
    path = path or usage_path()
    if not path.exists():
        return []
    out: list[UsageRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(UsageRecord.model_validate_json(line))
    return out


class Summary(BaseModel):
    total_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    by_model: dict[str, "Summary"] = Field(default_factory=dict)
    by_day: dict[str, "Summary"] = Field(default_factory=dict)


def _add(s: Summary, r: UsageRecord) -> None:
    s.total_calls += 1
    s.total_input_tokens += r.input_tokens
    s.total_output_tokens += r.output_tokens
    s.total_cost_usd = round(s.total_cost_usd + r.cost_usd, 6)


def summarize(records: list[UsageRecord]) -> Summary:
    """Aggregate by model and by day in one pass."""
    overall = Summary()
    for r in records:
        _add(overall, r)
        per_model = overall.by_model.setdefault(r.model, Summary())
        _add(per_model, r)
        day = r.timestamp[:10]
        per_day = overall.by_day.setdefault(day, Summary())
        _add(per_day, r)
    return overall
