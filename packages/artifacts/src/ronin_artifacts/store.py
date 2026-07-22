"""Structured, versioned artifact store. Local JSON; DB-swappable interface."""

from __future__ import annotations

import difflib
import json
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ArtifactKind(str, Enum):
    document = "document"
    report = "report"
    code = "code"
    diff = "diff"
    plan = "plan"
    table = "table"
    dataset = "dataset"
    diagram = "diagram"
    timeline = "timeline"
    checklist = "checklist"
    study_plan = "study_plan"
    medical_summary = "medical_summary"
    research_notebook = "research_notebook"
    architecture = "architecture"
    workflow = "workflow"
    dashboard_config = "dashboard_config"


class ArtifactLinks(BaseModel):
    """Provenance: what produced this artifact. All optional, all auditable."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = ""
    source_ids: tuple[str, ...] = ()
    model_id: str = ""
    adapter_id: str = ""
    tool_call_ids: tuple[str, ...] = ()
    approval_ids: tuple[str, ...] = ()


class ArtifactVersion(BaseModel):
    """One immutable revision. `content` is structured, not rendered HTML."""

    model_config = ConfigDict(extra="forbid")

    version: int
    title: str
    content: dict  # structured body; renderers turn this into HTML safely
    created: str  # ISO, caller-supplied
    author: str = ""
    note: str = ""
    links: ArtifactLinks = Field(default_factory=ArtifactLinks)


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    kind: ArtifactKind
    owner: str
    world: str = ""
    versions: list[ArtifactVersion] = Field(default_factory=list)

    @property
    def current(self) -> ArtifactVersion:
        return self.versions[-1]

    @property
    def title(self) -> str:
        return self.current.title


@dataclass
class VersionDiff:
    artifact_id: str
    from_version: int
    to_version: int
    title_changed: bool
    unified: str  # a unified text diff of the JSON content


class ArtifactStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._artifacts: dict[str, Artifact] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._artifacts = {
                aid: Artifact.model_validate(rec) for aid, rec in data.items()
            }

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {aid: a.model_dump(mode="json") for aid, a in self._artifacts.items()},
                indent=2, sort_keys=True,
            ),
            encoding="utf-8",
        )

    # -------------------------------------------------------------- create
    def create(
        self,
        *,
        kind: ArtifactKind,
        owner: str,
        title: str,
        content: dict,
        world: str = "",
        created: str,
        author: str = "",
        links: ArtifactLinks | None = None,
    ) -> Artifact:
        artifact = Artifact(kind=kind, owner=owner, world=world)
        artifact.versions.append(ArtifactVersion(
            version=1, title=title, content=content, created=created,
            author=author, links=links or ArtifactLinks(),
        ))
        self._artifacts[artifact.id] = artifact
        self._persist()
        return artifact

    # ---------------------------------------------------------------- edit
    def edit(
        self,
        artifact_id: str,
        *,
        title: str | None = None,
        content: dict | None = None,
        created: str,
        author: str = "",
        note: str = "",
        links: ArtifactLinks | None = None,
    ) -> Artifact:
        """Append a new immutable version. Prior versions are never mutated."""
        artifact = self._require(artifact_id)
        cur = artifact.current
        artifact.versions.append(ArtifactVersion(
            version=cur.version + 1,
            title=cur.title if title is None else title,
            content=cur.content if content is None else content,
            created=created, author=author, note=note,
            links=links or cur.links,
        ))
        self._persist()
        return artifact

    # ------------------------------------------------------------- queries
    def get(self, artifact_id: str) -> Artifact | None:
        return self._artifacts.get(artifact_id)

    def list_for(self, owner: str, *, world: str | None = None, kind: ArtifactKind | None = None) -> list[Artifact]:
        out = [a for a in self._artifacts.values() if a.owner == owner]
        if world is not None:
            out = [a for a in out if a.world == world]
        if kind is not None:
            out = [a for a in out if a.kind == kind]
        return sorted(out, key=lambda a: a.id)

    def version(self, artifact_id: str, version: int) -> ArtifactVersion:
        for v in self._require(artifact_id).versions:
            if v.version == version:
                return v
        raise KeyError(f"artifact {artifact_id!r} has no version {version}")

    # --------------------------------------------------------- compare
    def compare(self, artifact_id: str, a: int, b: int) -> VersionDiff:
        va, vb = self.version(artifact_id, a), self.version(artifact_id, b)
        ja = json.dumps(va.content, indent=2, sort_keys=True).splitlines(keepends=True)
        jb = json.dumps(vb.content, indent=2, sort_keys=True).splitlines(keepends=True)
        unified = "".join(difflib.unified_diff(ja, jb, f"v{a}", f"v{b}"))
        return VersionDiff(
            artifact_id=artifact_id, from_version=a, to_version=b,
            title_changed=va.title != vb.title, unified=unified,
        )

    # --------------------------------------------------------- restore
    def restore(self, artifact_id: str, version: int, *, created: str, author: str = "") -> Artifact:
        """Restore = append the old content as a NEW version (never rewrites history)."""
        target = self.version(artifact_id, version)
        return self.edit(
            artifact_id, title=target.title, content=target.content,
            created=created, author=author, note=f"restored from v{version}",
        )

    # --------------------------------------------------------- delete
    def delete(self, artifact_id: str, *, owner: str) -> bool:
        art = self._artifacts.get(artifact_id)
        if art is None or art.owner != owner:
            return False
        del self._artifacts[artifact_id]
        self._persist()
        return True

    def _require(self, artifact_id: str) -> Artifact:
        art = self._artifacts.get(artifact_id)
        if art is None:
            raise KeyError(f"unknown artifact {artifact_id!r}")
        return art
