"""Fully-local embeddings — semantic-ish search with ZERO network egress.

``embeddings.py`` already prefers local Ollama, but it falls back to a *hosted*
OpenAI-compatible endpoint when no local model is around, and it depends on
``httpx``. That's two problems for the air-gapped / ``--offline`` story:

1. **Egress.** A query can still leave the machine (the hosted endpoint), which
   defeats the offline guarantee the moment Ollama isn't running.
2. **Hard dependency.** Without a model pulled (``nomic-embed-text``) there is no
   semantic search at all — you fall straight back to lexical BM25.

This module is the local-first answer. ``embed`` tries a LOCAL embedder first and
*always* has something to return:

  (a) **Ollama** if a server answers on localhost (queried via stdlib ``urllib``,
      so there's no third-party dependency and no chance of a remote host); else
  (b) a deterministic, dependency-free **hashing / bag-of-character-ngrams**
      embedding — no model download, no network, same text → same vector, and
      similar texts score a higher cosine than dissimilar ones. It's not as good
      as a real model, but it makes overlap-based semantic-ish ranking work on a
      plane with nothing installed.

Everything here is stdlib only (``hashlib`` + ``math``, plus ``urllib`` for the
*optional, localhost-only* Ollama call). It never calls a remote cloud API.

Integration (intentionally NOT wired here — see ``integration_note()``)
------------------------------------------------------------------------
``embeddings.get_backend`` returns ``None`` when no embeddings key/provider is
configured, and ``semantic_search`` then returns ``None`` so callers drop to
BM25. A local-first wiring would, when ``config.offline`` is set (or no embedding
key is available), build a tiny ``EmbeddingBackend`` whose ``.embed`` delegates
to :func:`embed` with ``prefer_local=True`` — so semantic search keeps working
offline instead of disappearing. The same applies to ``ronin recall`` if/when it
grows vector recall: call :func:`embed` rather than any hosted embedder whenever
offline. :func:`pick_backend` encodes the policy (offline ⇒ never remote).
"""
from __future__ import annotations

import hashlib
import json
import math
import urllib.error
import urllib.request

# Default Ollama endpoint — localhost only, never a remote host.
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
DEFAULT_DIM = 256
_NGRAM = 3                  # character n-gram width for the hashing fallback
_PROBE_TIMEOUT = 0.5        # seconds — Ollama reachability probe (kept snappy)
_EMBED_TIMEOUT = 30.0       # seconds — per Ollama embedding request


# --------------------------------------------------------------------------- #
# Pure math
# --------------------------------------------------------------------------- #
def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors.

    Returns ``0.0`` if either vector is all-zeros (or they differ in length),
    so it never raises on a degenerate input. For L2-normalized vectors (what
    :func:`hashing_embed` produces) this is just the dot product, in ``[-1, 1]``
    — and ``>= 0`` for the non-negative hashing vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


def _tokens(text: str) -> list[str]:
    """Lowercased alphanumeric word tokens. Splits on any non-alphanumeric run,
    so punctuation never glues words together."""
    out: list[str] = []
    cur: list[str] = []
    for ch in text.lower():
        if ch.isalnum():
            cur.append(ch)
        elif cur:
            out.append("".join(cur))
            cur = []
    if cur:
        out.append("".join(cur))
    return out


def _features(text: str, ngram: int = _NGRAM) -> list[str]:
    """The bag of features hashed into the vector: whole word tokens *plus*
    character n-grams of each padded token.

    - **Word tokens** give exact-overlap signal ("http" matches "http").
    - **Character n-grams** give *partial*-overlap signal so near-spellings and
      morphological variants ("client" vs "clients", "retry" vs "retries") still
      share many dimensions — this is what keeps a query close to a slightly
      reworded doc. Tokens are padded with a sentinel space so word boundaries
      become features too (e.g. ``" ht"``, ``"nt "``).
    """
    feats: list[str] = []
    for tok in _tokens(text):
        feats.append(tok)                       # whole-word feature
        padded = f" {tok} "
        if len(padded) >= ngram:
            for i in range(len(padded) - ngram + 1):
                feats.append(padded[i:i + ngram])
    return feats


def _bucket(feature: str, dim: int) -> int:
    """Stable hash of a feature into ``[0, dim)``. Uses blake2b (fixed across
    processes/platforms, unlike Python's salted ``hash``) so a given text always
    embeds to the same vector — required for the on-disk embedding cache."""
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % dim


def hashing_embed(text: str, dim: int = DEFAULT_DIM) -> list[float]:
    """Deterministic, dependency-free embedding of ``text`` (the hashing trick).

    Each feature (see :func:`_features`) is hashed to a dimension and increments
    it; the resulting count vector is L2-normalized. Properties:

    - **Deterministic.** Same text + same ``dim`` → identical vector, every run.
    - **Normalized.** L2 norm is ``1.0`` for any text with at least one feature
      (so :func:`cosine` is a plain dot product and scores are comparable).
    - **Overlap-sensitive.** Texts that share words / character n-grams light up
      the same dimensions, so a query embeds *closer* to a relevant doc than to
      an unrelated one — enough for semantic-ish ranking with no model at all.

    ``dim`` trades collisions (lower) against vector size (higher); 256 is a good
    default for short code-ish snippets.
    """
    vec = [0.0] * dim
    for feat in _features(text):
        vec[_bucket(feat, dim)] += 1.0
    return _l2_normalize(vec)


# --------------------------------------------------------------------------- #
# Backend selection (pure policy)
# --------------------------------------------------------------------------- #
def pick_backend(offline: bool, ollama_ok: bool) -> str:
    """Choose the LOCAL embedding backend to use: ``"ollama"`` or ``"hashing"``.

    Pure and total — it never returns a remote backend. Both options are local,
    so ``offline`` doesn't *exclude* anything here; it only documents intent and
    pins the contract that offline can never escalate to a cloud API. The choice
    is simply: use Ollama when it's reachable (better vectors), otherwise fall
    back to the always-available hashing embedder.
    """
    return "ollama" if ollama_ok else "hashing"


# --------------------------------------------------------------------------- #
# Ollama (localhost only, stdlib urllib — no third-party deps)
# --------------------------------------------------------------------------- #
def ollama_available(base_url: str = DEFAULT_OLLAMA_URL,
                     *, timeout: float = _PROBE_TIMEOUT) -> bool:
    """Best-effort probe: is an Ollama server answering on this host?

    A quick GET to the server root. Any reachable HTTP response (even a non-200)
    counts as "up"; connection errors / timeouts count as "down". Never raises,
    so callers can branch on it safely. Network is restricted to ``base_url``,
    which defaults to localhost.
    """
    try:
        req = urllib.request.Request(base_url.rstrip("/") + "/", method="GET")
        with urllib.request.urlopen(req, timeout=timeout):  # noqa: S310 — localhost
            return True
    except urllib.error.HTTPError:
        return True            # server responded (just not 200) → it's up
    except Exception:          # noqa: BLE001 — URLError, timeout, DNS, etc.
        return False


def _ollama_embed_one(text: str, *, model: str, base_url: str,
                      timeout: float) -> list[float]:
    payload = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/embeddings",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — localhost
        data = json.loads(resp.read().decode("utf-8"))
    embedding = data.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        raise ValueError("ollama returned no embedding")
    return [float(x) for x in embedding]


def ollama_embed(texts: list[str], *, model: str = DEFAULT_OLLAMA_MODEL,
                 base_url: str = DEFAULT_OLLAMA_URL,
                 timeout: float = _EMBED_TIMEOUT) -> list[list[float]]:
    """Embed ``texts`` via a local Ollama server (``POST /api/embeddings``).

    Stdlib ``urllib`` only — no ``httpx``, no third-party deps. Raises on any
    transport/decoding error so :func:`embed` can catch and fall back to the
    hashing embedder. The endpoint is ``base_url`` (localhost by default); this
    function never reaches a remote cloud API.
    """
    return [
        _ollama_embed_one(t, model=model, base_url=base_url, timeout=timeout)
        for t in texts
    ]


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def embed(texts: list[str], *, prefer_local: bool = True,
          dim: int = DEFAULT_DIM, ollama_model: str = DEFAULT_OLLAMA_MODEL,
          ollama_url: str = DEFAULT_OLLAMA_URL) -> list[list[float]]:
    """Embed ``texts`` with graceful, local-first degradation.

    Order of preference when ``prefer_local`` (the offline default):

      1. **Ollama** if a server is reachable on ``ollama_url`` (localhost) — real
         model vectors, still zero egress.
      2. **Hashing** fallback otherwise — deterministic, dependency-free, and
         always available (no model, no network).

    Set ``prefer_local=False`` to skip the Ollama probe and go straight to the
    hashing embedder (useful for tests / forcing fully offline behaviour). Either
    way, **nothing leaves the machine**: this function only ever talks to the
    local Ollama URL or computes vectors in-process. An Ollama error mid-batch is
    swallowed and the whole batch is re-embedded with hashing, so a call never
    fails or returns a partial result.

    Returns one vector per input text, in order. ``[]`` in → ``[]`` out.
    """
    if not texts:
        return []

    ok = bool(prefer_local) and ollama_available(ollama_url)
    if pick_backend(offline=prefer_local, ollama_ok=ok) == "ollama":
        try:
            return ollama_embed(texts, model=ollama_model, base_url=ollama_url)
        except Exception:  # noqa: BLE001 — any failure → deterministic fallback
            pass
    return [hashing_embed(t, dim=dim) for t in texts]


# --------------------------------------------------------------------------- #
# Integration note (returned, not applied — per the brief)
# --------------------------------------------------------------------------- #
def integration_note() -> str:
    """How ``embeddings.py`` / ``recall`` could adopt this for offline use.

    Returned as text on purpose: this module does not modify ``embeddings.py``,
    ``offline.py``, ``config.py``, or ``main.py`` — wiring is left to the caller.
    """
    return (
        "Local-first embeddings wiring (not applied here):\n"
        "\n"
        "1. In embeddings.get_backend(config): when `config.offline` is True, OR\n"
        "   when no embeddings key is available for the provider (the branch that\n"
        "   currently returns None and forces a BM25 fallback), return a small\n"
        "   backend backed by this module instead of None, e.g.:\n"
        "\n"
        "       class LocalEmbeddingBackend(EmbeddingBackend):\n"
        "           name = 'local'\n"
        "           def embed(self, texts):\n"
        "               from . import local_embed\n"
        "               return local_embed.embed(texts, prefer_local=True)\n"
        "\n"
        "   Gate it on `config.offline` first so offline NEVER reaches the hosted\n"
        "   OpenAIEmbeddingBackend; pick_backend(offline=True, ...) guarantees the\n"
        "   choice stays local (Ollama when up, else the hashing embedder).\n"
        "\n"
        "2. SemanticIndex already keys its disk cache by `backend.name`, so giving\n"
        "   this backend a distinct name ('local') keeps offline hashing vectors\n"
        "   from colliding with cloud/Ollama vectors in the cache. hashing_embed is\n"
        "   deterministic, so the content-hash cache stays valid across runs.\n"
        "\n"
        "3. For `ronin recall`: it is keyword/BM25 today (session_search.recall).\n"
        "   If it grows vector recall, embed both stored chunks and the query via\n"
        "   local_embed.embed(...) whenever config.offline, and rank with\n"
        "   local_embed.cosine — so recall stays fully on-device and air-gapped."
    )


__all__ = [
    "embed",
    "hashing_embed",
    "cosine",
    "pick_backend",
    "ollama_available",
    "ollama_embed",
    "integration_note",
    "DEFAULT_DIM",
    "DEFAULT_OLLAMA_URL",
    "DEFAULT_OLLAMA_MODEL",
]
