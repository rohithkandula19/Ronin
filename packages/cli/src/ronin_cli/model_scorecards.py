"""Project-local evidence imported from benchmark and evaluation reports."""
from __future__ import annotations

import datetime as _datetime
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .agent_routing import ModelCandidate


SCHEMA_VERSION = 1
_PATH = ".ronin/model-scorecards.json"


@dataclass(frozen=True)
class ModelScorecard:
    model: str
    quality: float
    source: str
    cases: int = 0
    latency_seconds: float | None = None
    estimated_cost: float | None = None
    updated: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "model": self.model, "quality": self.quality, "source": self.source,
            "cases": self.cases, "latency_seconds": self.latency_seconds,
            "estimated_cost": self.estimated_cost, "updated": self.updated,
        }


class ModelScorecardStore:
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.path = self.root / _PATH

    def list(self) -> dict[str, ModelScorecard]:
        raw = self._load().get("models", {})
        out: dict[str, ModelScorecard] = {}
        for model, value in raw.items():
            if not isinstance(model, str) or not isinstance(value, dict):
                continue
            try:
                out[model] = ModelScorecard(
                    model=model,
                    quality=_quality(value.get("quality")),
                    source=str(value.get("source", "unknown")),
                    cases=max(0, int(value.get("cases", 0))),
                    latency_seconds=_optional_number(value.get("latency_seconds")),
                    estimated_cost=_optional_number(value.get("estimated_cost")),
                    updated=str(value.get("updated", "")),
                )
            except (TypeError, ValueError):
                continue
        return out

    def upsert(self, cards: Iterable[ModelScorecard]) -> None:
        data = self._load()
        models = data["models"]
        for card in cards:
            if not card.model.strip():
                continue
            models[card.model] = card.as_dict()
        self._save(data)

    def record_bench(self, result: Any) -> list[ModelScorecard]:
        """Persist objective-bench rows without retaining prompts or raw outputs."""
        now = _datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds")
        cards: list[ModelScorecard] = []
        for row in getattr(result, "rows", []):
            total = max(0, int(getattr(row, "total", 0)))
            if not total or getattr(row, "error", None):
                continue
            cards.append(ModelScorecard(
                model=str(getattr(row, "label", "")),
                quality=_quality(getattr(row, "rate", 0.0)),
                source="objective-bench",
                cases=total,
                latency_seconds=max(0.0, float(getattr(row, "elapsed", 0.0))) / total,
                estimated_cost=getattr(row, "est_cost", None),
                updated=now,
            ))
        self.upsert(cards)
        return cards

    def import_report(self, path: Path | str, *, model: str | None = None) -> ModelScorecard:
        """Import a standard SWE-bench or judge report using its summary score."""
        source_path = Path(path)
        try:
            raw = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"could not read evaluation report: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValueError("evaluation report must be a JSON object")
        summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
        quality = _report_quality(summary)
        if quality is None:
            raise ValueError("report summary has no supported quality metric")
        label = model or raw.get("model") or raw.get("target_model") or raw.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("report has no model label; pass --model")
        cases = int(summary.get("total", len(raw.get("results", raw.get("cases", []))) if isinstance(raw.get("results", raw.get("cases", [])), list) else 0) or 0)
        card = ModelScorecard(
            model=label.strip(), quality=quality, source=f"import:{source_path.name}", cases=max(0, cases),
            updated=_datetime.datetime.now(_datetime.timezone.utc).isoformat(timespec="seconds"),
        )
        self.upsert([card])
        return card

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"schema_version": SCHEMA_VERSION, "models": {}}
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION or not isinstance(raw.get("models"), dict):
            return {"schema_version": SCHEMA_VERSION, "models": {}}
        return raw

    def _save(self, data: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".model-scorecards-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(name, self.path)
        except OSError:
            try:
                os.unlink(name)
            except OSError:
                pass
            raise


def apply_scorecards(candidates: Iterable[ModelCandidate], scorecards: Mapping[str, ModelScorecard]) -> tuple[ModelCandidate, ...]:
    """Replace default quality with an explicit locally stored evaluation result."""
    enriched: list[ModelCandidate] = []
    for candidate in candidates:
        card = scorecards.get(candidate.spec)
        if card is None:
            enriched.append(candidate)
            continue
        enriched.append(ModelCandidate(candidate.spec, quality=card.quality, cost=candidate.cost, latency=candidate.latency))
    return tuple(enriched)


def _report_quality(summary: Mapping[str, Any]) -> float | None:
    for key in ("resolved_rate", "pass_rate", "task_success", "quality"):
        if key in summary:
            return _quality(summary[key])
    numeric = [float(value) for value in summary.values() if isinstance(value, (int, float))]
    if numeric:
        # Judge reports usually use a 1-5 scale. Normalise only when the value
        # is plainly above one; otherwise preserve a 0-1 score.
        return _quality(sum(numeric) / len(numeric) / (5.0 if max(numeric) > 1 else 1.0))
    return None


def _quality(value: object) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _optional_number(value: object) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None
