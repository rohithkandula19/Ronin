"""Adapters that feed Ronin's existing local knowledge into agent contracts."""
from __future__ import annotations

from pathlib import Path

from ronin_agent_patterns import AgentRequest, ContextFragment, ContextProvider

from .project_memory import ProjectMemory, load_project_memory
from .repo_index import index_db_path, query_index


class ProjectInstructionsContext(ContextProvider):
    """Maintainer-authored repository instructions are trusted local context."""

    name = "project-instructions"

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()

    def resolve(self, request: AgentRequest) -> ContextFragment | None:
        found = load_project_memory(self.root)
        if found is None:
            return None
        filename, text = found
        return ContextFragment(
            source=f"project-instructions:{filename}", content=text, priority=100, trust="trusted",
        )


class ProjectFactsContext(ContextProvider):
    """Relevant local facts stay attributable and untrusted by default."""

    name = "project-memory"

    def __init__(self, root: Path | str, *, limit: int = 5) -> None:
        self.root = Path(root).resolve()
        self.limit = max(1, min(limit, 20))

    def resolve(self, request: AgentRequest) -> ContextFragment | None:
        rows = ProjectMemory(self.root).recall(request.task, k=self.limit)
        if not rows:
            return None
        facts = "\n".join(f"- {row['text']}" for row in rows if isinstance(row.get("text"), str))
        if not facts:
            return None
        return ContextFragment(source="project-memory", content=facts, priority=80, trust="untrusted")


class RepositoryContext(ContextProvider):
    """Use the persistent index when available, with the established fallback."""

    name = "repository-retrieval"

    def __init__(self, root: Path | str, *, token_budget: int = 8_000) -> None:
        self.root = Path(root).resolve()
        self.token_budget = max(1, token_budget)

    def resolve(self, request: AgentRequest) -> ContextFragment | None:
        db_path = index_db_path(self.root)
        paths = query_index(request.task, db_path, token_budget=self.token_budget) if db_path.exists() else []
        if paths:
            return ContextFragment(
                source="repository-index", content="\n".join(f"- {path}" for path in paths), priority=60,
                trust="trusted",
            )
        from .context_engine import relevant_context

        fallback = relevant_context(request.task, self.root)
        if not fallback:
            return None
        return ContextFragment(source="repository-retrieval", content=fallback, priority=50, trust="trusted")


def build_code_context_providers(
    root: Path | str,
    *,
    include_retrieval: bool,
    retrieval_token_budget: int,
) -> list[ContextProvider]:
    """One context pipeline for coding turns, without duplicate retrieval paths."""
    providers: list[ContextProvider] = [ProjectInstructionsContext(root), ProjectFactsContext(root)]
    if include_retrieval:
        providers.append(RepositoryContext(root, token_budget=retrieval_token_budget))
    return providers
