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
from functools import lru_cache
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


def _normalize(s: str) -> str:
    """Casefolded text with typographic apostrophes straightened and contractions
    expanded (n't → not), so \"haven't run\" satisfies a required \"not run\" and a
    curly-quoted \"we’re good\" still matches a straight-quoted ban. Markers are
    semantic, not literal — an audit showed correct answers failing on contraction
    variance alone."""
    s = s.replace("’", "'")
    s = re.sub(r"n't\b", " not", s, flags=re.IGNORECASE)
    return s.casefold()


def score_case(case: dict, resp: ProviderResponse) -> CaseResult:
    """Check one response against one case's assertions. Text checks are normalized
    (case, apostrophes, contractions); tool checks are exact names."""
    text = _normalize(resp.text)
    called = list(resp.tool_calls)
    failures: list[str] = []

    for needle in case.get("must_include") or []:
        if _normalize(needle) not in text:
            failures.append(f"missing required phrase {needle!r}")
    for needle in case.get("must_not_include") or []:
        if _normalize(needle) in text:
            failures.append(f"contains banned phrase {needle!r}")
    for tool in case.get("must_call_tools") or []:
        if tool not in called:
            failures.append(f"did not call required tool {tool!r}")
    any_of = case.get("must_call_any_of") or []
    if any_of and not any(t in called for t in any_of):
        failures.append(f"called none of the acceptable tools {any_of!r}")
    for tool in case.get("must_not_call_tools") or []:
        if tool in called:
            failures.append(f"called forbidden tool {tool!r}")
    if case.get("forbid_unknown_tools"):
        unknown = [n for n in extract_all_call_names(resp.text)
                   if n not in _registry_names()]
        if unknown:
            failures.append(f"called hallucinated tool(s) {unknown!r}")

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
#
# RUNTIME PARITY: extraction mirrors the EmbeddedProvider's parser exactly — a
# call counts ONLY inside a ``<tool_call>...</tool_call>`` block AND in call shape
# (a name key followed by an arguments key). Ronin's runtime refuses to execute a
# call the model merely quoted as bare JSON in prose (a live probe showed a model
# "refusing" rm -rf ~ while echoing the call JSON in its refusal — executing that
# would be catastrophic), so the eval must not award credit for output the runtime
# would not consume.
_TOOLCALL = re.compile(r'\{\s*"name"\s*:\s*"([a-z_]+)"\s*,\s*"arguments"')


def extract_all_call_names(raw: str) -> list[str]:
    """Every wrapper-enclosed, call-shaped tool name in the output, INCLUDING names
    not in Ronin's registry — used to detect hallucinated tools."""
    names: list[str] = []
    for chunk in re.findall(r"<tool_call>(.*?)</tool_call>", raw, re.DOTALL):
        names.extend(_TOOLCALL.findall(chunk))
    return names


def extract_tool_names(raw: str) -> list[str]:
    """Call-shaped tool names restricted to Ronin's real registry (the names the
    tool assertions check against)."""
    real = set(_registry_names())
    return [n for n in extract_all_call_names(raw) if n in real]


@lru_cache(maxsize=1)
def _registry() -> dict:
    reg = Path(__file__).resolve().parents[2] / "config" / "tool_registry.json"
    return json.loads(reg.read_text(encoding="utf-8"))


def _registry_names() -> tuple[str, ...]:
    return tuple(t["name"] for t in _registry()["tools"])


def ronin_tools_for_template() -> list[dict]:
    """Ronin's real tools in the OpenAI function format a chat template expects.

    Ronin always runs with these available, so an honest protocol eval must present
    them too — otherwise the model has no way to emit the tool calls the eval checks."""
    return [
        {"type": "function", "function": {
            "name": t["name"], "description": t["description"], "parameters": t["parameters"],
        }}
        for t in _registry()["tools"]
    ]


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
    tools = ronin_tools_for_template()

    def _provider(case: dict) -> ProviderResponse:  # pragma: no cover - needs a model
        text = generate(
            model, tokenizer,
            prompt=tokenizer.apply_chat_template(
                case_messages(case), tools=tools, add_generation_prompt=True),
            max_tokens=max_tokens, verbose=False,
        )
        return ProviderResponse(text=text, tool_calls=extract_tool_names(text))

    return _provider


def case_messages(case: dict) -> list[dict]:
    """The chat-template message list for a case. PURE.

    Single-turn: system + prompt. Multi-turn: a case may carry ``messages`` — a
    scripted PRIOR conversation (user/assistant/tool turns, mid-session) — and the
    model is asked for the next reply after it; ``prompt`` (when non-empty) is
    appended as the final user turn. This is how multi-turn stability is actually
    exercised instead of asserted about."""
    system = case.get("system", "You are Ronin.")
    msgs: list[dict] = [{"role": "system", "content": system}]
    msgs.extend(case.get("messages") or [])
    prompt = case.get("prompt", "")
    if prompt:
        msgs.append({"role": "user", "content": prompt})
    return msgs


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


def main(argv: list[str] | None = None) -> int:
    """Evaluate a real model (base or adapter) against the protocol eval set.

    Requires mlx-lm + a model; there is no stub provider, so this never prints a
    fabricated score. Example:
        python -m ronin_training.eval_runner \
            --model mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit \
            --evals training/data/evals/ronin_protocol_eval.jsonl \
            --out training/reports/base_eval.md --title "Base 1.5B"
    """
    import argparse
    ap = argparse.ArgumentParser(description="Run Ronin protocol evals against a model.")
    ap.add_argument("--model", required=True)
    ap.add_argument("--adapter", default=None, help="path to a trained LoRA adapter")
    ap.add_argument("--evals", default="training/data/evals/ronin_protocol_eval.jsonl")
    ap.add_argument("--out", required=True)
    ap.add_argument("--title", default="Protocol Eval")
    ap.add_argument("--max-tokens", type=int, default=512)
    a = ap.parse_args(argv)

    provider = mlx_provider(a.model, adapter_path=a.adapter, max_tokens=a.max_tokens)
    cases = load_cases(a.evals)
    report = run_evals(cases, provider)
    write_report(report, a.out, title=a.title)
    print(f"{a.title}: {report.passed}/{report.total} passed "
          f"({report.pass_rate:.1%}) → {a.out}")
    for cat, (p, t) in report.by_category().items():
        print(f"  {cat}: {p}/{t}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
