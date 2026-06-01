"""Embeddings-backed semantic retrieval — find code by *meaning*, not keywords.

The repo map (BM25) is great and zero-config, but it's lexical: a query for
"how do we authenticate users" won't rank a file that says "verify credentials"
unless the words overlap. This module adds true semantic search on top, using an
embeddings backend you already have — local **Ollama** (offline-friendly) or any
**OpenAI-compatible** ``/embeddings`` endpoint — with cosine similarity over
one vector per file.

It's strictly optional and additive: ``get_backend`` returns ``None`` when no
embeddings backend is configured, and callers fall back to BM25. Embeddings are
cached on disk by content hash, so only changed files are ever re-embedded.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import httpx

from .config import CSKConfig
from .repo_map import _extract_symbols, _walk

_CACHE_DIR = Path(".csk") / "embeddings"
_MAX_FILES = 1500
_REPR_CHARS = 1500          # representative text length embedded per file
_EMBED_TIMEOUT = 30.0


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #
class EmbeddingBackend:
    name = "base"

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OllamaBackend(EmbeddingBackend):
    name = "ollama"

    def __init__(self, model: str = "nomic-embed-text",
                 base_url: str = "http://localhost:11434") -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        with httpx.Client(timeout=_EMBED_TIMEOUT) as client:
            for t in texts:
                r = client.post(f"{self.base_url}/api/embeddings",
                                json={"model": self.model, "prompt": t})
                r.raise_for_status()
                out.append(r.json()["embedding"])
        return out


class OpenAIEmbeddingBackend(EmbeddingBackend):
    name = "openai"

    def __init__(self, model: str, base_url: str, api_key: str) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def embed(self, texts: list[str]) -> list[list[float]]:
        with httpx.Client(timeout=_EMBED_TIMEOUT) as client:
            r = client.post(f"{self.base_url}/embeddings",
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            json={"model": self.model, "input": texts})
            r.raise_for_status()
            return [row["embedding"] for row in r.json()["data"]]


def get_backend(config: CSKConfig) -> EmbeddingBackend | None:
    """Pick an embeddings backend from the config, or None if none is available.

    Local Ollama is preferred (free + offline); otherwise an OpenAI-compatible
    key enables the hosted endpoint. Returns None for providers without a known
    embeddings route, so callers fall back to BM25."""
    extra = config.extra or {}
    if config.provider == "ollama" or extra.get("embed_provider") == "ollama":
        base = config.resolved_base_url() or "http://localhost:11434/v1"
        base = base.replace("/v1", "")  # ollama embeddings live off the root
        return OllamaBackend(model=extra.get("embed_model", "nomic-embed-text"),
                             base_url=base or "http://localhost:11434")
    if config.provider in ("openai", "custom"):
        key = config.key_for(config.provider)
        if key:
            base = config.resolved_base_url() or "https://api.openai.com/v1"
            return OpenAIEmbeddingBackend(
                model=extra.get("embed_model", "text-embedding-3-small"),
                base_url=base, api_key=key)
    return None


# --------------------------------------------------------------------------- #
# Index
# --------------------------------------------------------------------------- #
def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _representative(path: Path, root: Path) -> str:
    """Compact text embedded for a file: its path, symbol names, and a head slice
    — enough signal for semantic ranking without embedding the whole file."""
    rel = str(path.relative_to(root))
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    syms = ", ".join(n for n, _ in _extract_symbols(text))
    return f"{rel}\n{syms}\n{text[:_REPR_CHARS]}"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class SemanticIndex:
    """One embedding vector per file, with a content-hash disk cache."""

    def __init__(self, root: Path | str, backend: EmbeddingBackend) -> None:
        self.root = Path(root).resolve()
        self.backend = backend
        self.paths: list[str] = []
        self.vectors: list[list[float]] = []

    def _cache_path(self) -> Path:
        key = _hash(str(self.root) + "::" + self.backend.name)[:16]
        return self.root / _CACHE_DIR / f"{key}.json"

    def build(self) -> "SemanticIndex":
        cache = self._load_cache()
        reps: dict[str, str] = {}
        for path in _walk(self.root):
            rel = str(path.relative_to(self.root))
            reps[rel] = _representative(path, self.root)
            if len(reps) >= _MAX_FILES:
                break

        to_embed = [(rel, h) for rel, txt in reps.items()
                    if (h := _hash(reps[rel])) not in cache]
        if to_embed:
            new_vecs = self.backend.embed([reps[rel] for rel, _ in to_embed])
            for (rel, h), vec in zip(to_embed, new_vecs):
                cache[h] = vec
        self.paths, self.vectors = [], []
        for rel, txt in reps.items():
            self.paths.append(rel)
            self.vectors.append(cache[_hash(txt)])
        self._save_cache(cache)
        return self

    def search(self, query: str, k: int = 5) -> list[tuple[float, str]]:
        if not self.vectors:
            return []
        qvec = self.backend.embed([query])[0]
        scored = [(cosine(qvec, v), self.paths[i]) for i, v in enumerate(self.vectors)]
        scored.sort(key=lambda x: x[0], reverse=True)
        return scored[:k]

    def _load_cache(self) -> dict[str, list[float]]:
        p = self._cache_path()
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_cache(self, cache: dict[str, list[float]]) -> None:
        p = self._cache_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(json.dumps(cache), encoding="utf-8")
        except OSError:
            pass


# Per-(root,backend) index cache for the session.
_INDEX_CACHE: dict[str, SemanticIndex] = {}


def semantic_search(query: str, root: Path | str, config: CSKConfig,
                    k: int = 5) -> list[tuple[float, str]] | None:
    """Top-k (score, path) by semantic similarity, or None if no backend is
    configured (caller should fall back to BM25)."""
    backend = get_backend(config)
    if backend is None:
        return None
    key = str(Path(root).resolve()) + "::" + backend.name
    idx = _INDEX_CACHE.get(key)
    if idx is None:
        idx = SemanticIndex(root, backend).build()
        _INDEX_CACHE[key] = idx
    return idx.search(query, k)


def build_semantic_tools(config: CSKConfig, root: Path | str = ".") -> list:
    """A ``semantic_search`` tool — but only when an embeddings backend exists, so
    the agent never sees a capability that can't run. Returns [] otherwise."""
    if get_backend(config) is None:
        return []

    from ro_claude_kit_agent_patterns import Tool

    def semantic_search_tool(query: str, k: int = 5) -> str:
        try:
            results = semantic_search(query, root, config, int(k))
        except Exception as e:  # noqa: BLE001 — never crash a turn on an embed error
            return f"semantic search failed: {e} (fall back to repo_map / search_files)"
        if not results:
            return f"no semantically relevant files for {query!r}"
        lines = [f"Most semantically relevant files for {query!r}:"]
        lines += [f"- {path}  (score {score:.2f})" for score, path in results]
        lines.append("→ read_file the ones that look right.")
        return "\n".join(lines)

    return [Tool(
        name="semantic_search",
        description=(
            "Find files by MEANING using embeddings (not keywords) — use when "
            "repo_map / search_files miss because the wording differs from the code "
            "(e.g. 'authenticate users' should match a file that says 'verify "
            "credentials'). Args: query, k (default 5)."
        ),
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}, "k": {"type": "integer"}},
            "required": ["query"],
        },
        handler=semantic_search_tool,
    )]
