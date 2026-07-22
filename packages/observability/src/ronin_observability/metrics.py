"""In-memory metrics: counters, gauges, and histograms.

No exporters, no background threads. Quantiles are computed deterministically
(nearest-rank) from recorded observations. A cardinality guard rejects absurd
label sets fail-closed so a runaway caller cannot explode memory silently.
"""

from __future__ import annotations

import math
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

# Type alias for a label set: name -> value.
Labels = Mapping[str, str]


class MetricError(RuntimeError):
    """A metric operation violated a guard (cardinality, name, or value)."""


def _freeze(labels: Labels | None) -> tuple[tuple[str, str], ...]:
    """Canonical, order-independent key for a label set."""
    if not labels:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in labels.items()))


class CardinalityGuard(BaseModel):
    """Bounds on label sets. Exceeding any bound is a fail-closed error."""

    model_config = ConfigDict(extra="forbid")

    max_labels_per_metric: int = 12
    max_label_value_len: int = 256
    max_series_per_metric: int = 1000

    def check_labels(self, labels: Labels | None) -> None:
        if not labels:
            return
        if len(labels) > self.max_labels_per_metric:
            raise MetricError(
                f"label set has {len(labels)} keys; "
                f"max is {self.max_labels_per_metric}"
            )
        for k, v in labels.items():
            if not str(k):
                raise MetricError("empty label key is not allowed")
            if len(str(v)) > self.max_label_value_len:
                raise MetricError(
                    f"label {k!r} value length {len(str(v))} exceeds "
                    f"{self.max_label_value_len}"
                )


class Histogram(BaseModel):
    """Ordered observations with deterministic count/sum/quantile."""

    model_config = ConfigDict(extra="forbid")

    observations: list[float] = Field(default_factory=list)

    def observe(self, value: float) -> None:
        self.observations.append(float(value))

    def count(self) -> int:
        return len(self.observations)

    def sum(self) -> float:
        return math.fsum(self.observations)

    def quantile(self, q: float) -> float | None:
        """Nearest-rank quantile. ``None`` when there is no data.

        ``q`` in [0, 1]. Deterministic: sort ascending, take the value at
        rank ``ceil(q * n)`` (1-based), clamped into range.
        """
        if not 0.0 <= q <= 1.0:
            raise MetricError(f"quantile q must be in [0,1], got {q}")
        n = len(self.observations)
        if n == 0:
            return None
        ordered = sorted(self.observations)
        if q == 0.0:
            return ordered[0]
        rank = math.ceil(q * n)
        idx = min(max(rank, 1), n) - 1
        return ordered[idx]

    def p50(self) -> float | None:
        return self.quantile(0.50)

    def p95(self) -> float | None:
        return self.quantile(0.95)

    def p99(self) -> float | None:
        return self.quantile(0.99)


class MetricsRegistry:
    """Holds counter/gauge/histogram series keyed by (name, label set)."""

    def __init__(self, guard: CardinalityGuard | None = None) -> None:
        self.guard = guard if guard is not None else CardinalityGuard()
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[
            tuple[str, tuple[tuple[str, str], ...]], Histogram
        ] = {}

    # -- internal helpers ------------------------------------------------
    @staticmethod
    def _check_name(name: str) -> None:
        if not name or not name.strip():
            raise MetricError("metric name must be non-empty")

    def _series_count(
        self,
        store: dict[tuple[str, tuple[tuple[str, str], ...]], object],
        name: str,
    ) -> int:
        return sum(1 for (n, _lbls) in store if n == name)

    def _key(
        self,
        store: dict[tuple[str, tuple[tuple[str, str], ...]], object],
        name: str,
        labels: Labels | None,
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        self._check_name(name)
        self.guard.check_labels(labels)
        key = (name, _freeze(labels))
        if key not in store and self._series_count(store, name) >= (
            self.guard.max_series_per_metric
        ):
            raise MetricError(
                f"metric {name!r} would exceed "
                f"{self.guard.max_series_per_metric} distinct label sets"
            )
        return key

    # -- counters --------------------------------------------------------
    def inc_counter(
        self, name: str, value: float = 1.0, *, labels: Labels | None = None
    ) -> float:
        if value < 0:
            raise MetricError("counters may only increase (value must be >= 0)")
        key = self._key(self._counters, name, labels)  # type: ignore[arg-type]
        self._counters[key] = self._counters.get(key, 0.0) + float(value)
        return self._counters[key]

    def counter_value(
        self, name: str, *, labels: Labels | None = None
    ) -> float:
        return self._counters.get((name, _freeze(labels)), 0.0)

    # -- gauges ----------------------------------------------------------
    def set_gauge(
        self, name: str, value: float, *, labels: Labels | None = None
    ) -> float:
        key = self._key(self._gauges, name, labels)  # type: ignore[arg-type]
        self._gauges[key] = float(value)
        return self._gauges[key]

    def gauge_value(
        self, name: str, *, labels: Labels | None = None
    ) -> float | None:
        return self._gauges.get((name, _freeze(labels)))

    # -- histograms ------------------------------------------------------
    def observe(
        self, name: str, value: float, *, labels: Labels | None = None
    ) -> Histogram:
        key = self._key(self._histograms, name, labels)  # type: ignore[arg-type]
        hist = self._histograms.get(key)
        if hist is None:
            hist = Histogram()
            self._histograms[key] = hist
        hist.observe(value)
        return hist

    def histogram(
        self, name: str, *, labels: Labels | None = None
    ) -> Histogram | None:
        return self._histograms.get((name, _freeze(labels)))

    # -- introspection ---------------------------------------------------
    def series_count(self, name: str) -> int:
        return (
            self._series_count(self._counters, name)  # type: ignore[arg-type]
            + self._series_count(self._gauges, name)  # type: ignore[arg-type]
            + self._series_count(self._histograms, name)  # type: ignore[arg-type]
        )
