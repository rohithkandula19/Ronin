"""Session metrics: counters and gauges, a snapshot, and prometheus-style text.

Pure accumulation, no I/O. ``render_prometheus`` writes the text-exposition format by
hand rather than importing ``prometheus_client``: the format is a dozen lines, a hard
dependency for a dozen lines is a bad trade, and hand-writing it keeps ``obs`` a
stdlib-only leaf. ``snapshot`` is the same numbers as a plain dict, for a log line or an
assertion. Nothing here reaches for data — a caller records what it saw, one event at a
time, through the ``record_*`` / ``add_cost`` methods.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MetricSpec:
    """One exported series: its prometheus name, kind (``counter``/``gauge``) and help."""

    name: str
    kind: str
    help: str


#: Every series :meth:`Metrics.render_prometheus` emits, in output order. A table so the
#: renderer and the tests read one source, and a new metric cannot ship without a HELP
#: line and a declared type — the two things that make exposition text self-describing.
PROMETHEUS_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec("ronin_turns_total", "counter", "Model turns (ReAct iterations) observed."),
    MetricSpec("ronin_input_tokens_total", "counter", "Prompt tokens sent to the model."),
    MetricSpec("ronin_output_tokens_total", "counter", "Completion tokens returned by the model."),
    MetricSpec("ronin_cost_usd_total", "counter", "Estimated spend in USD."),
    MetricSpec("ronin_tool_calls_total", "counter", "Tool invocations observed."),
    MetricSpec("ronin_tool_errors_total", "counter", "Tool invocations that returned an error."),
    MetricSpec("ronin_cache_hits_total", "counter", "Prompt-cache hits."),
    MetricSpec("ronin_cache_misses_total", "counter", "Prompt-cache misses."),
    MetricSpec("ronin_cache_hit_rate", "gauge", "Cache hits / (hits + misses); 0 when none."),
)


def _format(value: float) -> str:
    """A prometheus-safe number: a plain int, or a float without float-error noise."""
    if isinstance(value, int):
        return str(value)
    return format(value, ".10g")


@dataclass(slots=True)
class Metrics:
    """Additive counters plus two derived gauges. Mutable; one instance per session.

    Every ``record_*`` folds in one observation, so the numbers are always the running
    total and never a snapshot that can go stale. Token and cost inputs are checked for
    sign, because a negative count is a caller subtracting in the wrong order and it
    would poison the least-inspected numbers in the run rather than fail where the bug is.
    """

    turns: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    tool_errors: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    def record_turn(self, count: int = 1) -> None:
        if count < 0:
            raise ValueError(f"record_turn count must be >= 0, got {count}")
        self.turns += count

    def record_tokens(self, input_tokens: int, output_tokens: int) -> None:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError(
                f"token counts must be >= 0, got input={input_tokens}, output={output_tokens}"
            )
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def add_cost(self, usd: float) -> None:
        if usd < 0:
            raise ValueError(f"cost must be >= 0, got {usd}")
        self.cost_usd += usd

    def record_tool(self, ok: bool) -> None:
        """One tool call, and whether it succeeded — a failure is also a call."""
        self.tool_calls += 1
        if not ok:
            self.tool_errors += 1

    def record_cache(self, hit: bool) -> None:
        if hit:
            self.cache_hits += 1
        else:
            self.cache_misses += 1

    def cache_hit_rate(self) -> float:
        """Hits over hits-plus-misses; ``0.0`` when nothing was ever looked up."""
        total = self.cache_hits + self.cache_misses
        return self.cache_hits / total if total else 0.0

    def snapshot(self) -> dict[str, float]:
        """Every counter and the derived rate, as a plain dict."""
        return {
            "turns": self.turns,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "cache_hit_rate": self.cache_hit_rate(),
        }

    def render_prometheus(self) -> str:
        """The metrics as prometheus text exposition — ``# HELP``, ``# TYPE``, value.

        Iterates :data:`PROMETHEUS_METRICS` so the output is exactly the declared set:
        a counter that gained no HELP line, or a series with no value, is impossible by
        construction rather than by review.
        """
        values: dict[str, float] = {
            "ronin_turns_total": self.turns,
            "ronin_input_tokens_total": self.input_tokens,
            "ronin_output_tokens_total": self.output_tokens,
            "ronin_cost_usd_total": self.cost_usd,
            "ronin_tool_calls_total": self.tool_calls,
            "ronin_tool_errors_total": self.tool_errors,
            "ronin_cache_hits_total": self.cache_hits,
            "ronin_cache_misses_total": self.cache_misses,
            "ronin_cache_hit_rate": self.cache_hit_rate(),
        }
        lines: list[str] = []
        for spec in PROMETHEUS_METRICS:
            lines.append(f"# HELP {spec.name} {spec.help}")
            lines.append(f"# TYPE {spec.name} {spec.kind}")
            lines.append(f"{spec.name} {_format(values[spec.name])}")
        return "\n".join(lines) + "\n"


__all__ = [
    "PROMETHEUS_METRICS",
    "MetricSpec",
    "Metrics",
]
