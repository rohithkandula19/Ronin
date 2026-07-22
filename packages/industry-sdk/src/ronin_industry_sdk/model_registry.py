"""Model Registry and Adapter Registry.

Typed records over what already exists in Ronin (provider presets, the
embedded local models, trained MLX adapters), plus a lifecycle for adapters
that never conflates states: an adapter whose training bundle was merely
GENERATED is not TRAINED, and one that was trained is not RELEASED until its
required evaluations passed and a human approved it.

Storage is a plain JSON file (local-first, zero infra); the store is an
interface so a database can back it later without changing callers.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Modality(str, Enum):
    text = "text"
    vision = "vision"
    audio = "audio"


class AdapterState(str, Enum):
    """Lifecycle. Transitions are validated — no jumping from generated to released."""

    generated = "generated"  # a training bundle exists; NOTHING has been trained
    submitted = "submitted"
    running = "running"
    failed = "failed"
    completed = "completed"  # weights exist; not yet evaluated
    evaluating = "evaluating"
    approved = "approved"  # evals passed + human approval recorded
    rejected = "rejected"
    released = "released"
    rolled_back = "rolled_back"


_ALLOWED_TRANSITIONS: dict[AdapterState, frozenset[AdapterState]] = {
    AdapterState.generated: frozenset({AdapterState.submitted}),
    AdapterState.submitted: frozenset({AdapterState.running, AdapterState.failed}),
    AdapterState.running: frozenset({AdapterState.completed, AdapterState.failed}),
    AdapterState.failed: frozenset({AdapterState.submitted}),
    AdapterState.completed: frozenset({AdapterState.evaluating}),
    AdapterState.evaluating: frozenset({AdapterState.approved, AdapterState.rejected}),
    AdapterState.approved: frozenset({AdapterState.released, AdapterState.rejected}),
    AdapterState.rejected: frozenset({AdapterState.evaluating}),
    AdapterState.released: frozenset({AdapterState.rolled_back}),
    AdapterState.rolled_back: frozenset({AdapterState.evaluating}),
}


class ModelRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)  # e.g. "anthropic:claude-sonnet-5"
    provider: str
    model_name: str
    display_name: str
    open_weights: bool
    license: str = "proprietary"
    context_length: int | None = None
    input_modalities: tuple[Modality, ...] = (Modality.text,)
    output_modalities: tuple[Modality, ...] = (Modality.text,)
    supports_tool_use: bool = False
    supports_structured_output: bool = False
    supports_embeddings: bool = False
    local: bool = False
    hardware_notes: str = ""
    price_in_per_mtok: float | None = None  # None = unknown, never guessed
    price_out_per_mtok: float | None = None
    country_availability: tuple[str, ...] = ()  # empty = no restriction recorded
    safety_notes: str = ""
    recommended_for: tuple[str, ...] = ()
    version: str = ""
    deprecated: bool = False

    @property
    def capabilities(self) -> set[str]:
        caps = {"text"}
        if Modality.vision in self.input_modalities:
            caps.add("vision")
        if self.supports_tool_use:
            caps.add("tool_use")
        if self.supports_structured_output:
            caps.add("structured_output")
        if self.supports_embeddings:
            caps.add("embedding")
        return caps


class EvalResultRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite: str
    passed: int
    total: int
    report_path: str = ""


class AdapterRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)  # e.g. "ronin-coding-1.5b-v2-iter150"
    base_model: str  # ModelRecord id
    industry: str
    role: str = ""
    country: str = ""
    language: str = "en"
    version: str
    dataset_version: str = ""
    training_method: str = "qlora"
    training_config: dict = Field(default_factory=dict)
    license_compatible: bool = True
    eval_results: tuple[EvalResultRef, ...] = ()
    safety_results: tuple[EvalResultRef, ...] = ()
    state: AdapterState = AdapterState.generated
    storage_location: str = ""  # local path; adapters are never committed to git
    serving_format: str = "mlx-lora"  # mlx-lora | gguf | safetensors
    created: str = ""  # ISO date, caller-supplied (determinism)
    approved_by: str = ""
    rollback_target: str = ""  # adapter id to fall back to

    @field_validator("id")
    @classmethod
    def _id_shape(cls, v: str) -> str:
        if not v.replace("-", "").replace(".", "").replace("_", "").isalnum():
            raise ValueError(f"adapter id {v!r} has unexpected characters")
        return v


class StateTransitionError(RuntimeError):
    pass


class _JsonStore:
    """Minimal local JSON persistence. Swap for a DB behind the same methods."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


class ModelRegistry:
    def __init__(self, store_path: str | Path = "", *, backend: object | None = None) -> None:
        # ``backend`` is any object exposing load()/save(dict) — e.g. a
        # ronin_persistence DocumentStore (Postgres/JSON-file/in-memory).
        # Default keeps the byte-compatible local JSON file.
        self._store = backend if backend is not None else _JsonStore(Path(store_path))
        self._models: dict[str, ModelRecord] = {
            mid: ModelRecord.model_validate(rec)
            for mid, rec in self._store.load().items()
        }

    def register(self, record: ModelRecord, *, overwrite: bool = False) -> None:
        if record.id in self._models and not overwrite:
            raise ValueError(f"model {record.id!r} already registered")
        self._models[record.id] = record
        self._persist()

    def get(self, model_id: str) -> ModelRecord | None:
        return self._models.get(model_id)

    def list_models(self, *, include_deprecated: bool = False) -> list[ModelRecord]:
        models = sorted(self._models.values(), key=lambda m: m.id)
        if not include_deprecated:
            models = [m for m in models if not m.deprecated]
        return models

    def find_capable(self, required_capabilities: set[str], *, local_only: bool = False) -> list[ModelRecord]:
        out = []
        for m in self.list_models():
            if local_only and not m.local:
                continue
            if required_capabilities <= m.capabilities:
                out.append(m)
        return out

    def _persist(self) -> None:
        self._store.save({mid: m.model_dump(mode="json") for mid, m in self._models.items()})


class AdapterRegistry:
    def __init__(self, store_path: str | Path = "", *, backend: object | None = None) -> None:
        self._store = backend if backend is not None else _JsonStore(Path(store_path))
        self._adapters: dict[str, AdapterRecord] = {
            aid: AdapterRecord.model_validate(rec)
            for aid, rec in self._store.load().items()
        }

    def register(self, record: AdapterRecord, *, overwrite: bool = False) -> None:
        if record.id in self._adapters and not overwrite:
            raise ValueError(f"adapter {record.id!r} already registered")
        self._adapters[record.id] = record
        self._persist()

    def get(self, adapter_id: str) -> AdapterRecord | None:
        return self._adapters.get(adapter_id)

    def list_adapters(self, *, industry: str | None = None, state: AdapterState | None = None) -> list[AdapterRecord]:
        out = sorted(self._adapters.values(), key=lambda a: a.id)
        if industry is not None:
            out = [a for a in out if a.industry == industry]
        if state is not None:
            out = [a for a in out if a.state == state]
        return out

    def transition(
        self,
        adapter_id: str,
        new_state: AdapterState,
        *,
        approved_by: str = "",
        eval_results: tuple[EvalResultRef, ...] | None = None,
    ) -> AdapterRecord:
        """Move an adapter along its lifecycle, enforcing honesty gates."""
        rec = self._adapters.get(adapter_id)
        if rec is None:
            raise KeyError(f"unknown adapter {adapter_id!r}")
        allowed = _ALLOWED_TRANSITIONS[rec.state]
        if new_state not in allowed:
            raise StateTransitionError(
                f"adapter {adapter_id!r}: {rec.state.value} -> {new_state.value} is not "
                f"a legal transition (allowed: {sorted(s.value for s in allowed)})"
            )
        updates: dict = {"state": new_state}
        if eval_results is not None:
            updates["eval_results"] = tuple(eval_results)
        if new_state is AdapterState.approved:
            results = updates.get("eval_results", rec.eval_results)
            if not results:
                raise StateTransitionError(
                    f"adapter {adapter_id!r} cannot be approved with no eval results on record"
                )
            if not approved_by:
                raise StateTransitionError(
                    f"adapter {adapter_id!r} approval requires approved_by (a human identity)"
                )
            updates["approved_by"] = approved_by
        new_rec = rec.model_copy(update=updates)
        self._adapters[adapter_id] = new_rec
        self._persist()
        return new_rec

    def released_for(self, industry: str) -> AdapterRecord | None:
        """The single released adapter for an industry, if any."""
        released = self.list_adapters(industry=industry, state=AdapterState.released)
        return released[0] if released else None

    def _persist(self) -> None:
        self._store.save({aid: a.model_dump(mode="json") for aid, a in self._adapters.items()})


# ------------------------------------------------------------------ seeding

def seed_default_models(registry: ModelRegistry) -> int:
    """Register the models Ronin actually ships support for today.

    Facts here mirror packages/cli (PROVIDER_PRESETS, cost.py, embedded
    provider docs) — nothing is invented. Prices are omitted (None) where the
    CLI's cost table has no entry, never guessed.
    """
    defaults = [
        ModelRecord(
            id="anthropic:claude-sonnet",
            provider="anthropic",
            model_name="claude-sonnet",
            display_name="Claude Sonnet (Anthropic API)",
            open_weights=False,
            supports_tool_use=True,
            supports_structured_output=True,
            input_modalities=(Modality.text, Modality.vision),
            price_in_per_mtok=3.0,
            price_out_per_mtok=15.0,
            recommended_for=("coding", "general"),
        ),
        ModelRecord(
            id="local:qwen2.5-coder-1.5b-4bit",
            provider="local",
            model_name="mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit",
            display_name="Qwen2.5-Coder 1.5B (embedded, 4-bit)",
            open_weights=True,
            license="apache-2.0",
            local=True,
            supports_tool_use=True,
            hardware_notes="Apple Silicon (mlx) or llama.cpp GGUF; fits 8 GB RAM",
            price_in_per_mtok=0.0,
            price_out_per_mtok=0.0,
            recommended_for=("offline", "free-mode", "fine-tuning-base"),
        ),
        ModelRecord(
            id="local:qwen2.5-coder-7b",
            provider="local",
            model_name="Qwen2.5-Coder-7B-Instruct",
            display_name="Qwen2.5-Coder 7B (embedded)",
            open_weights=True,
            license="apache-2.0",
            local=True,
            supports_tool_use=True,
            hardware_notes="16-32 GB RAM machines (embedded provider RAM ladder)",
            price_in_per_mtok=0.0,
            price_out_per_mtok=0.0,
            recommended_for=("offline",),
        ),
        ModelRecord(
            id="ollama:default",
            provider="ollama",
            model_name="(configured in Ollama)",
            display_name="Ollama (any local model)",
            open_weights=True,
            license="varies",
            local=True,
            supports_tool_use=True,
            price_in_per_mtok=0.0,
            price_out_per_mtok=0.0,
            recommended_for=("offline", "free-mode"),
        ),
    ]
    count = 0
    for rec in defaults:
        if registry.get(rec.id) is None:
            registry.register(rec)
            count += 1
    return count


def seed_known_adapters(registry: AdapterRegistry) -> int:
    """Register the one adapter with real, committed evidence: v2 iter-150.

    Its numbers come from training/reports/eval91_v2_iter150.md (36/91 on the
    91-case runtime-parity protocol set). It is recorded as `completed` +
    evaluated — NOT released — because release requires a human approval step.
    Storage location is local-only by design (adapters are gitignored).
    """
    aid = "ronin-coding-1.5b-v2-iter150"
    if registry.get(aid) is not None:
        return 0
    registry.register(
        AdapterRecord(
            id=aid,
            base_model="local:qwen2.5-coder-1.5b-4bit",
            industry="coding",
            version="2.0.0",
            dataset_version="volumes-284rows",
            training_method="qlora",
            training_config={
                "iters": 400,
                "best_checkpoint": 150,
                "batch_size": 1,
                "lora_layers": 8,
                "learning_rate": 1e-5,
                "max_seq_length": 3072,
                "seed": 0,
            },
            eval_results=(
                EvalResultRef(
                    suite="coding-protocol",
                    passed=36,
                    total=91,
                    report_path="training/reports/eval91_v2_iter150.md",
                ),
            ),
            state=AdapterState.completed,
            storage_location="training/adapters/ronin_1.5b_v2_ck/i150 (local-only, not in git)",
            serving_format="mlx-lora",
            created="2026-07-15",
        )
    )
    return 1
