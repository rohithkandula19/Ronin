"""Tests for embeddings-backed semantic retrieval. The HTTP backend is faked, so
the index/search/cache logic is covered deterministically and offline."""
from __future__ import annotations

from ronin_cli import embeddings
from ronin_cli.config import RoninConfig
from ronin_cli.embeddings import EmbeddingBackend, SemanticIndex, cosine


class _FakeBackend(EmbeddingBackend):
    """Embeds by a tiny keyword-presence vector — deterministic, no network."""
    name = "fake"
    VOCAB = ["auth", "login", "verify", "credential", "payment", "charge"]

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            low = t.lower()
            out.append([1.0 if w in low else 0.0 for w in self.VOCAB])
        return out


def _seed(root):
    (root / "auth.py").write_text("def login():\n    verify_credential()\n", encoding="utf-8")
    (root / "billing.py").write_text("def charge():\n    process_payment()\n", encoding="utf-8")


def test_cosine() -> None:
    assert cosine([1, 0], [1, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([], []) == 0.0


def test_semantic_index_ranks_by_meaning(tmp_path) -> None:
    _seed(tmp_path)
    idx = SemanticIndex(tmp_path, _FakeBackend()).build()
    results = idx.search("verify a user credential during login", k=2)
    assert results[0][1] == "auth.py"      # auth file ranks first


def test_index_caches_embeddings(tmp_path) -> None:
    _seed(tmp_path)
    backend = _FakeBackend()
    SemanticIndex(tmp_path, backend).build()
    calls_after_first = backend.calls
    # second build over the same unchanged files should embed nothing new
    SemanticIndex(tmp_path, backend).build()
    assert backend.calls == calls_after_first  # served entirely from disk cache


def test_get_backend_uses_local_hashing_for_unknown_embedding_route() -> None:
    backend = embeddings.get_backend(RoninConfig(provider="anthropic", anthropic_api_key="x"))
    assert backend.name == "local-hashing"


def test_get_backend_ollama() -> None:
    backend = embeddings.get_backend(RoninConfig(provider="ollama"))
    assert backend is not None and backend.name == "ollama"


def test_get_backend_openai() -> None:
    cfg = RoninConfig(provider="openai")
    cfg.set_key_for("openai", "sk-x")
    backend = embeddings.get_backend(cfg)
    assert backend is not None and backend.name == "openai"


def test_semantic_search_uses_local_fallback_without_backend(tmp_path) -> None:
    _seed(tmp_path)
    cfg = RoninConfig(provider="anthropic", anthropic_api_key="x")
    results = embeddings.semantic_search("login credentials", tmp_path, cfg)
    assert results and results[0][1] == "auth.py"


def test_build_semantic_tools_always_has_a_local_safe_backend(tmp_path) -> None:
    assert [tool.name for tool in embeddings.build_semantic_tools(
        RoninConfig(provider="anthropic", anthropic_api_key="x"), tmp_path)] == ["semantic_search"]
    tools = embeddings.build_semantic_tools(RoninConfig(provider="ollama"), tmp_path)
    assert [t.name for t in tools] == ["semantic_search"]


def test_semantic_search_tool_output(tmp_path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr(embeddings, "get_backend", lambda cfg: _FakeBackend())
    embeddings._INDEX_CACHE.clear()
    tools = {t.name: t for t in embeddings.build_semantic_tools(RoninConfig(provider="ollama"), tmp_path)}
    out = tools["semantic_search"].handler(query="login and verify credentials")
    assert "auth.py" in out
