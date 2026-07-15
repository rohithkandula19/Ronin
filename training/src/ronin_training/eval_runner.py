"""Run Ronin's protocol eval cases against a provider and score them honestly.

The runner is provider-agnostic: you hand it a callable that turns an eval case into
a `ProviderResponse` (the model's text plus the tool names it tried to call), and it
checks the case's `must_include` / `must_not_include` / `must_call_tools` /
`must_not_call_tools` assertions. It computes nothing on its own — if no provider
runs, there is no score. That is deliberate: the spec forbids reporting an eval
result you did not actually produce (measure the base model before claiming a
fine-tune improved anything).

Wiring a real model is the caller's job. `mlx_provider()` is an optional adapter for
an on-device MLX model; it imports `mlx_lm` lazily and raises a clear error if it
isn't installed, rather than silently returning empty responses that would inflate
the pass rate.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class ProviderResponse:
    """What a model produced for one eval case."""
    text: str = ""
    tool_calls: list[str] = field(default_factory=list)   # tool names, in call order


Provider = Callable[[dict], ProviderResponse]


@dataclass
class CaseResult:
    eval_id: str
    category: str
    passed: bool
    failures: list[str] = field(default_factory=list)


def score_case(case: dict, resp: ProviderResponse) -> CaseResult:
    """Check one response against one case's assertions. Text checks are
    case-insensitive (markers are semantic, not literal); tool checks are exact."""
    text = resp.text.casefold()
    called = list(resp.tool_calls)
    failures: list[str] = []

    for needle in case.get("must_include") or []:
        if needle.casefold() not in text:
            failures.append(f"missing required phrase {needle!r}")
    for needle in case.get("must_not_include") or []:
        if needle.casefold() in text:
            failures.append(f"contains banned phrase {needle!r}")
    for tool in case.get("must_call_tools") or []:
        if tool not in called:
            failures.append(f"did not call required tool {tool!r}")
    for tool in case.get("must_not_call_tools") or []:
        if tool in called:
            failures.append(f"called forbidden tool {tool!r}")

    return CaseResult(case["eval_id"], case.get("category", "?"), not failures, failures)


@dataclass
class EvalReport:
    total: int
    passed: int
    results: list[CaseResult]

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    def by_category(self) -> dict[str, tuple[int, int]]:
        """category -> (passed, total)."""
        agg: dict[str, list[int]] = {}
        for r in self.results:
            cell = agg.setdefault(r.category, [0, 0])
            cell[0] += int(r.passed)
            cell[1] += 1
        return {k: (v[0], v[1]) for k, v in sorted(agg.items())}


def load_cases(path: str | Path) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def run_evals(cases: list[dict], provider: Provider) -> EvalReport:
    results = [score_case(c, provider(c)) for c in cases]
    return EvalReport(len(results), sum(r.passed for r in results), results)


# ------------------------------------------------------------------ tool-call parsing

# Ronin's tool calls surface as JSON objects in the model's output. The eval only
# needs the tool NAMES that were requested, so extract them without executing.
_TOOLCALL = re.compile(r'"name"\s*:\s*"([a-z_]+)"')


def extract_tool_names(raw: str) -> list[str]:
    """Best-effort extraction of tool names the model asked to call, from raw text."""
    return _TOOLCALL.findall(raw)


# ------------------------------------------------------------------ optional MLX adapter

def mlx_provider(model_path: str, *, adapter_path: str | None = None,
                 max_tokens: int = 512) -> Provider:
    """Return a Provider backed by an on-device MLX model. Raises ImportError with a
    clear message if `mlx_lm` isn't installed — never degrades to empty responses."""
    try:
        from mlx_lm import generate, load          # type: ignore
    except ImportError as e:                        # pragma: no cover - env-dependent
        raise ImportError(
            "mlx-lm is required for mlx_provider. Install with "
            "`pip install 'ronin-training[mlx]'` on an Apple Silicon Mac."
        ) from e

    model, tokenizer = load(model_path, adapter_path=adapter_path)  # pragma: no cover

    def _provider(case: dict) -> ProviderResponse:  # pragma: no cover - needs a model
        system = case.get("system", "You are Ronin.")
        prompt = case.get("prompt", "")
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": prompt}]
        text = generate(
            model, tokenizer,
            prompt=tokenizer.apply_chat_template(messages, add_generation_prompt=True),
            max_tokens=max_tokens, verbose=False,
        )
        return ProviderResponse(text=text, tool_calls=extract_tool_names(text))

    return _provider


def write_report(report: EvalReport, path: str | Path, *, title: str = "Protocol Eval") -> None:
    lines = [
        f"# {title}", "",
        f"- cases: **{report.total}**",
        f"- passed: **{report.passed}**  ·  pass rate: **{report.pass_rate:.1%}**", "",
        "## By category", "", "| category | passed | total |", "|---|---|---|",
    ]
    for cat, (p, t) in report.by_category().items():
        lines.append(f"| {cat} | {p} | {t} |")
    fails = [r for r in report.results if not r.passed]
    if fails:
        lines += ["", "## Failures", ""]
        for r in fails:
            lines.append(f"- **{r.eval_id}** ({r.category}): {'; '.join(r.failures)}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
