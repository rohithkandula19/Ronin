"""The Vault store: scoped memory items, isolation enforcement, usage audit.

Local-first: one JSON file. The store is deliberately small and boring —
the value is in the boundaries it refuses to cross.
"""

from __future__ import annotations

import json
import uuid
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class MemoryScope(str, Enum):
    session = "session"
    conversation = "conversation"
    project = "project"
    user = "user"
    organization = "organization"
    industry = "industry"
    tool_context = "tool_context"  # temporary; never recalled across turns


class Sensitivity(str, Enum):
    normal = "normal"
    personal = "personal"
    health = "health"
    financial = "financial"
    legal = "legal"
    minor = "minor"  # data that may concern minors
    credential = "credential"


# Sensitivities whose items may never become training-eligible through this
# API at all — not even with a consent reference. Removing data from these
# categories from a trained model is not practically possible, so the gate
# is absolute here and a deliberate policy decision, not a default.
_TRAINING_FORBIDDEN = frozenset({Sensitivity.credential, Sensitivity.minor})


class MemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    scope: MemoryScope
    owner: str  # user id or org id
    text: str = Field(min_length=1)
    source: str = ""  # where this came from (conversation id, upload, manual)
    world: str = ""  # industry pack id for industry scope; "" = no world binding
    created: str = ""  # ISO timestamps, caller-supplied (deterministic tests)
    updated: str = ""
    confidence: float = 1.0
    sensitivity: Sensitivity = Sensitivity.normal
    retention_days: int | None = None  # None = keep until deleted
    training_eligible: bool = False  # NEVER defaults to True
    consent_ref: str = ""  # non-empty iff training_eligible
    visible: bool = True  # user can always see their memory
    disabled: bool = False  # excluded from recall but not deleted


class IsolationError(RuntimeError):
    """A recall or transfer would cross a boundary it must not cross."""


class TransferPreview(BaseModel):
    """Exactly what would move in a cross-world transfer. Nothing moves yet."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    text: str
    from_world: str
    to_world: str
    sensitivity: Sensitivity
    warnings: tuple[str, ...] = ()


class VaultStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._items: dict[str, MemoryItem] = {}
        self._usage: list[dict] = []  # {item_id, query, world, when}
        self._load()

    # ------------------------------------------------------------- persistence
    def _load(self) -> None:
        if not self._path.exists():
            return
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self._items = {
            iid: MemoryItem.model_validate(rec) for iid, rec in data.get("items", {}).items()
        }
        self._usage = data.get("usage", [])

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "items": {iid: item.model_dump(mode="json") for iid, item in self._items.items()},
            "usage": self._usage,
        }
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    # ------------------------------------------------------------------ CRUD
    def add(self, item: MemoryItem) -> MemoryItem:
        if item.scope is MemoryScope.industry and not item.world:
            raise IsolationError("industry-scope memory must name its world")
        if item.training_eligible:
            # Training eligibility can never arrive pre-set; it goes through
            # mark_training_eligible so the consent gate cannot be skipped.
            raise IsolationError(
                "items cannot be created training-eligible; use mark_training_eligible"
            )
        self._items[item.id] = item
        self._persist()
        return item

    def get(self, item_id: str) -> MemoryItem | None:
        return self._items.get(item_id)

    def edit(self, item_id: str, *, text: str, updated: str = "") -> MemoryItem:
        item = self._require(item_id)
        new = item.model_copy(update={"text": text, "updated": updated or item.updated})
        self._items[item_id] = new
        self._persist()
        return new

    def delete(self, item_id: str) -> bool:
        existed = self._items.pop(item_id, None) is not None
        if existed:
            self._persist()
        return existed

    def disable(self, item_id: str, disabled: bool = True) -> MemoryItem:
        item = self._require(item_id)
        new = item.model_copy(update={"disabled": disabled})
        self._items[item_id] = new
        self._persist()
        return new

    def export_all(self, owner: str) -> list[dict]:
        """Everything the owner has — visible by right, machine-readable."""
        return [
            item.model_dump(mode="json")
            for item in sorted(self._items.values(), key=lambda i: i.id)
            if item.owner == owner
        ]

    def list_items(self, owner: str, *, scope: MemoryScope | None = None) -> list[MemoryItem]:
        out = [i for i in self._items.values() if i.owner == owner]
        if scope is not None:
            out = [i for i in out if i.scope == scope]
        return sorted(out, key=lambda i: i.id)

    # ------------------------------------------------------------- training
    def mark_training_eligible(self, item_id: str, *, consent_ref: str) -> MemoryItem:
        """The ONLY path to training eligibility: an explicit consent record."""
        item = self._require(item_id)
        if not consent_ref.strip():
            raise IsolationError("training eligibility requires a non-empty consent_ref")
        if item.sensitivity in _TRAINING_FORBIDDEN:
            raise IsolationError(
                f"{item.sensitivity.value} data can never be marked training-eligible"
            )
        new = item.model_copy(update={"training_eligible": True, "consent_ref": consent_ref})
        self._items[item_id] = new
        self._persist()
        return new

    def training_pool(self, owner: str) -> list[MemoryItem]:
        """Items eligible for dataset building. Consent-gated by construction."""
        return [
            i for i in self.list_items(owner)
            if i.training_eligible and i.consent_ref
        ]

    # --------------------------------------------------------------- recall
    def recall(
        self,
        query: str,
        *,
        owner: str,
        world: str,
        workspace_kind: str = "personal",  # "personal" | "organization"
        scopes: tuple[MemoryScope, ...] = (
            MemoryScope.user,
            MemoryScope.project,
            MemoryScope.industry,
            MemoryScope.organization,
        ),
        k: int = 8,
        when: str = "",
    ) -> list[MemoryItem]:
        """Scored recall that enforces every isolation rule.

        - industry-scope items surface only inside their own world;
        - organization-scope items never surface in personal workspaces;
        - tool_context and disabled items never surface;
        - every returned item is logged to the usage audit.
        """
        q_tokens = _tokens(query)
        candidates: list[tuple[float, MemoryItem]] = []
        for item in self._items.values():
            if item.owner != owner or item.disabled:
                continue
            if item.scope is MemoryScope.tool_context:
                continue
            if item.scope not in scopes:
                continue
            if item.scope is MemoryScope.industry and item.world != world:
                continue  # THE cross-industry wall
            if item.scope is MemoryScope.organization and workspace_kind != "organization":
                continue  # org memory stays out of personal workspaces
            score = _overlap(q_tokens, _tokens(item.text))
            if score > 0:
                candidates.append((score, item))
        candidates.sort(key=lambda pair: (-pair[0], pair[1].id))
        hits = [item for _, item in candidates[:k]]
        for item in hits:
            self._usage.append(
                {"item_id": item.id, "query": query, "world": world, "when": when}
            )
        if hits:
            self._persist()
        return hits

    def usage_for(self, item_id: str) -> list[dict]:
        """Why was this memory used? Every recall that returned it."""
        return [u for u in self._usage if u["item_id"] == item_id]

    # -------------------------------------------------------------- transfer
    def preview_transfer(self, item_id: str, *, to_world: str) -> TransferPreview:
        """Show exactly what a cross-world transfer would move. Moves nothing."""
        item = self._require(item_id)
        warnings: list[str] = []
        if item.sensitivity is not Sensitivity.normal:
            warnings.append(
                f"this item is {item.sensitivity.value}-sensitive; moving it exposes "
                f"it to the {to_world!r} world's context"
            )
        return TransferPreview(
            item_id=item.id,
            text=item.text,
            from_world=item.world,
            to_world=to_world,
            sensitivity=item.sensitivity,
            warnings=tuple(warnings),
        )

    def transfer(self, item_id: str, *, to_world: str, confirmed: bool) -> MemoryItem:
        """Execute a cross-world transfer. Requires explicit confirmation."""
        if not confirmed:
            raise IsolationError(
                "cross-world transfer requires confirmed=True after a preview"
            )
        item = self._require(item_id)
        new = item.model_copy(update={"world": to_world})
        self._items[item_id] = new
        self._persist()
        return new

    # ------------------------------------------------------------- retention
    def prune_expired(self, *, now_iso: str) -> int:
        """Delete items past their retention window. Returns count removed."""
        from datetime import date

        today = date.fromisoformat(now_iso[:10])
        doomed: list[str] = []
        for item in self._items.values():
            if item.retention_days is None or not item.created:
                continue
            created = date.fromisoformat(item.created[:10])
            if (today - created).days > item.retention_days:
                doomed.append(item.id)
        for iid in doomed:
            del self._items[iid]
        if doomed:
            self._persist()
        return len(doomed)

    # -------------------------------------------------------------- internal
    def _require(self, item_id: str) -> MemoryItem:
        item = self._items.get(item_id)
        if item is None:
            raise KeyError(f"unknown memory item {item_id!r}")
        return item


def _tokens(text: str) -> set[str]:
    return {t for t in "".join(c if c.isalnum() else " " for c in text.lower()).split() if len(t) > 2}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
