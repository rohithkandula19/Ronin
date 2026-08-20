"""Embeddings-backed semantic retrieval — find code by *meaning*, not keywords.

The repo map (BM25) is great and zero-config, but it's lexical: a query for
"how do we authenticate users" won't rank a file that says "verify credentials"
unless the words overlap. This module adds true semantic search on top, using an
embeddings backend you already have — local **Ollama** (offline-friendly) or any
**OpenAI-compatible** ``/embeddings`` endpoint — with cosine similarity over
one vector per file.

It is local-first and additive: Ollama or a configured compatible endpoint can
provide model vectors, while an always-available local hashing backend keeps
semantic retrieval available without credentials or network access. Embeddings
are cached on disk by content hash, so only changed files are re-embedded.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import httpx

from .config import RoninConfig
from .repo_map import _extract_symbols, _walk

_CACHE_DIR = Path(".ronin") / "embeddings"
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


class LocalHashingBackend(EmbeddingBackend):
    """Dependency-free fallback for local semantic retrieval.

    This deliberately skips the optional Ollama probe: a coding turn must not
    pause on a localhost request merely because no embedding model is installed.
    The deterministic hashing vectors come from ``local_embed`` and never leave
    the machine.
    """

    name = "local-hashing"

    def embed(self, texts: list[str]) -> list[list[float]]:
        from .local_embed import embed

        return embed(texts, prefer_local=False)


def get_backend(config: RoninConfig) -> EmbeddingBackend:
    """Pick an embeddings backend, always retaining a local fallback.

    Offline mode and providers without an embedding endpoint use hashing vectors
    locally. Hosted embeddings remain opt-in through a configured OpenAI
    compatible provider; no fallback path ever sends an embedding remotely.
    """
    extra = config.extra or {}
    if config.offline:
        return LocalHashingBackend()
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
    return LocalHashingBackend()


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


def semantic_search(query: str, root: Path | str, config: RoninConfig,
                    k: int = 5) -> list[tuple[float, str]]:
    """Top-k files by semantic similarity using a local-safe backend."""
    backend = get_backend(config)
    key = str(Path(root).resolve()) + "::" + backend.name
    idx = _INDEX_CACHE.get(key)
    if idx is None:
        idx = SemanticIndex(root, backend).build()
        _INDEX_CACHE[key] = idx
    return idx.search(query, k)


def build_semantic_tools(config: RoninConfig, root: Path | str = ".") -> list:
    """Return the always-local-safe ``semantic_search`` coding tool."""

    from ronin_agent_patterns import Tool

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
