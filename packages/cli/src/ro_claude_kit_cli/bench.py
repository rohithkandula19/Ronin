"""``ronin bench`` — an eval-driven model bake-off.

Claude Code has no concept of "is a cheaper model good enough for this?" — it's
Claude, full stop. ronin already ships a *deterministic* eval suite (objective
outcome checks, no LLM judge), so it can run the SAME battery across several
models, score them on identical terms, and tell you the cheapest model that
clears a quality bar. Pick the model with data, not vibes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .agent_eval import TaskOutcome, run_eval
from .config import CSKConfig
from .runner import config_for_spec

# Providers whose default tiers we treat as free ($0) for ranking purposes.
FREE_PROVIDERS: frozenset[str] = frozenset({
    "ollama", "gemini", "cerebras", "groq", "openrouter",
})

# Rough blended $/1M-token estimates for paid providers (input+output averaged).
# Deliberately approximate — labelled "est." in the UI. Unknown → None.
COST_PER_MTOK: dict[str, float] = {
    "anthropic": 6.0,
    "openai": 0.6,
    "together": 0.9,
    "fireworks": 0.9,
}


@dataclass
class BenchRow:
    label: str
    provider: str
    passed: int
    total: int
    tokens: int
    elapsed: float
    error: str | None = None

    @property
    def rate(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def est_cost(self) -> float | None:
        """Estimated USD for this run. Free providers → 0.0; unknown → None."""
        if self.provider in FREE_PROVIDERS:
            return 0.0
        rate = COST_PER_MTOK.get(self.provider)
        return None if rate is None else self.tokens / 1_000_000 * rate


@dataclass
class BenchResult:
    rows: list[BenchRow] = field(default_factory=list)
    threshold: float = 0.8
    recommendation: str = ""


def _cost_key(row: BenchRow) -> tuple:
    """Sort key for 'cheapest, then most correct, then fastest'. Unknown cost
    sorts last."""
    cost = row.est_cost
    cost_rank = float("inf") if cost is None else cost
    return (cost_rank, -row.rate, row.elapsed)


def _recommend(rows: list[BenchRow], threshold: float) -> str:
    passing = [r for r in rows if r.rate >= threshold]
    if passing:
        best = min(passing, key=_cost_key)
        cost = best.est_cost
        price = "free" if cost == 0 else (f"~${cost:.3f}/run" if cost is not None else "cost unknown")
        return (f"{best.label} — cheapest model clearing {threshold:.0%} "
                f"({best.rate:.0%} pass, {price})")
    if not rows:
        return "no models benchmarked."
    best = max(rows, key=lambda r: r.rate)
    return (f"no model cleared {threshold:.0%}. Best was {best.label} at "
            f"{best.rate:.0%} — raise iterations or lower the bar.")


def _bench_one(base: CSKConfig, spec: dict[str, Any], max_iterations: int) -> BenchRow:
    sub = config_for_spec(base, spec)
    label = f"{sub.provider}:{sub.resolved_model()}"
    try:
        outcomes: list[TaskOutcome] = run_eval(sub, max_iterations=max_iterations)
    except Exception as e:  # noqa: BLE001 — a dead model shouldn't sink the bake-off
        return BenchRow(label, sub.provider, 0, 0, 0, 0.0, error=str(e))
    passed = sum(1 for o in outcomes if o.passed)
    tokens = sum(o.tokens for o in outcomes)
    elapsed = sum(o.elapsed for o in outcomes)
    return BenchRow(label, sub.provider, passed, len(outcomes), tokens, elapsed)


def run_bench(base: CSKConfig, specs: list[dict[str, Any]], *, threshold: float = 0.8,
              max_iterations: int = 12, max_workers: int = 3) -> BenchResult:
    """Run the eval battery on each model (in parallel) and rank them."""
    result = BenchResult(threshold=threshold)
    if not specs:
        return result
    with ThreadPoolExecutor(max_workers=min(max_workers, len(specs))) as ex:
        result.rows = list(ex.map(lambda s: _bench_one(base, s, max_iterations), specs))
    result.recommendation = _recommend(result.rows, threshold)
    return result


def parse_specs_or_exit(models: str, console) -> list[dict[str, Any]]:
    """Parse a comma-separated 'provider[:model]' list; exit(2) if empty."""
    import typer

    from .consensus import parse_model_spec

    specs = [parse_model_spec(s) for s in models.split(",") if s.strip()]
    if not specs:
        console.print("[yellow]give at least one model[/yellow] — e.g. "
                      "[cyan]--models anthropic,gemini[/cyan]")
        raise typer.Exit(2)
    return specs


def render_bench(console, result: BenchResult) -> None:
    from rich.table import Table

    from .theme import ACCENT, ERR, MUTE, OK

    table = Table(title=None, box=None, padding=(0, 2))
    table.add_column("model", style=ACCENT)
    table.add_column("pass", justify="right")
    table.add_column("rate", justify="right")
    table.add_column("tokens", justify="right", style=MUTE)
    table.add_column("time", justify="right", style=MUTE)
    table.add_column("est. cost", justify="right", style=MUTE)

    for r in sorted(result.rows, key=_cost_key):
        if r.error:
            table.add_row(r.label, f"[{ERR}]err[/{ERR}]", "—", "—", "—",
                          f"[{ERR}]{r.error[:24]}[/{ERR}]")
            continue
        color = OK if r.rate >= result.threshold else ERR
        cost = r.est_cost
        price = "free" if cost == 0 else (f"~${cost:.3f}" if cost is not None else "—")
        table.add_row(r.label, f"{r.passed}/{r.total}",
                      f"[{color}]{r.rate:.0%}[/{color}]", f"{r.tokens:,}",
                      f"{r.elapsed:.1f}s", price)

    console.print()
    console.print(f"[bold]ronin bench[/bold]  [dim]· objective eval across {len(result.rows)} model(s)[/dim]")
    console.print(table)
    console.print()
    console.print(f"  [bold {ACCENT}]▶ recommended:[/bold {ACCENT}] {result.recommendation}")
    console.print()
