"""
Embedding service for VoolMemory semantic retrieval.

Priority:
  1. Ollama /api/embed with nomic-embed-text  (best semantic quality)
  2. Ollama /api/embed with any embed-capable model
  3. Hash-bag-of-words fallback              (pure Python, keyword overlap)

Every backend's output is projected to a single canonical EMBED_DIM (384) so a
vector stored under one backend stays comparable to one queried under another.
Equal dimensions do NOT make the spaces interchangeable: embed_stamped() reports
WHICH backend produced a vector so storage and search can refuse to cosine-compare
across different spaces. The fallback produces good exact-match and keyword-overlap
similarity without requiring any model download.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
import itertools
import json
import logging
import math
import re
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Sequence

from core.ollama_endpoint import ollama_base_url
from core.provider_invocation_gateway import (
    seal_direct_provider_invocation,
)

_EMBED_MODELS = ["nomic-embed-text", "mxbai-embed-large", "all-minilm"]

# Canonical embedding dimension for the conversational-recall path. embed() ALWAYS
# returns this many dims regardless of backend — the Ollama model (e.g. 768-dim
# nomic-embed-text) is projected down — so a vector stored while Ollama was up stays
# comparable to one queried while it is down. Previously embed() returned 768 or 384
# depending on availability, and a dimension mismatch silently scored 0.0, so recall
# could die with no trace.
EMBED_DIM = 384
_FALLBACK_DIMS = EMBED_DIM

# Measured against the installed local service (2026-09-24): inputs beyond roughly
# 2,048 tokens are rejected with HTTP 400 "input length exceeds the context length"
# (9,000 chars of conversational text passed; 10,000 failed), and the request-level
# num_ctx / truncate options do not lift that limit. The mean benchmark session is
# ~10.4k chars, so unbounded whole-session input never reached the model: it either
# 400'd into the hash fallback or lost its tail. Long inputs are segmented below
# this conservative bound (turn boundaries preserved) and the chunk vectors are
# length-weighted pooled, so the whole conversation is represented.
EMBED_MAX_CHARS = 6000

# How long a backend outage keeps skipping the neural attempt before re-probing,
# and how long a successful model-list probe is trusted. Previously the model list
# was cached for the whole process, so a startup with Ollama down pinned hash
# fallback until restart.
_NEURAL_COOLDOWN_S = 30.0
_MODEL_TTL_S = 30.0

NEURAL_BACKEND_PREFIX = "ollama:"
HASH_BACKEND_ID = f"hash-bow:{EMBED_DIM}"

_logger = logging.getLogger(__name__)
_warned_dim_pairs: set[tuple[int, int]] = set()

_stats = {
    "neural_calls": 0,
    "hash_fallback_calls": 0,
    "chunks_embedded": 0,
    "neural_failures": 0,
}
_stats_lock = threading.Lock()
_neural_down_until = 0.0
_model_cache: tuple[str | None, float] = (None, 0.0)


def project_to_dim(vec: list[float], dim: int) -> list[float]:
    """Deterministically map a vector to ``dim`` dimensions, unit-normalized.

    ``len == dim`` returns as-is; ``len > dim`` folds (``out[i % dim] += vec[i]``) — a
    fixed linear map, so two vectors folded the same way stay comparable; ``len < dim``
    pads with zeros.
    """
    n = len(vec)
    dim = max(1, int(dim))
    if n == dim:
        return [float(x) for x in vec]
    out = [0.0] * dim
    if n > dim:
        for i, v in enumerate(vec):
            out[i % dim] += float(v)
    else:
        for i, v in enumerate(vec):
            out[i] = float(v)
    mag = math.sqrt(sum(x * x for x in out))
    return [x / mag for x in out] if mag else out


def _warn_dim_mismatch_once(la: int, lb: int) -> None:
    key = (la, lb) if la <= lb else (lb, la)
    if key not in _warned_dim_pairs:
        _warned_dim_pairs.add(key)
        _logger.warning(
            "embedding dim mismatch %d vs %d — projecting to %d to compare (recall "
            "degraded, NOT silently zero); re-embed stored vectors to clear",
            la, lb, min(la, lb),
        )


_RETRIEVAL_TASK = ContextVar("memory_embedding_task", default="search_document")


@contextmanager
def embedding_query():
    """Bind the query role per call without changing the embedding test seam."""
    token = _RETRIEVAL_TASK.set("search_query")
    try:
        yield
    finally:
        _RETRIEVAL_TASK.reset(token)


def _nomic_retrieval_vector(vec: list[float]) -> list[float]:
    """Nomic v1.5: layer-center, truncate, then L2-normalize.

    The common layer-norm scale cancels during the final L2 normalization.
    Folding dimensions mixes the model's ordered Matryoshka coordinates.
    """
    mean = sum(vec) / len(vec) if vec else 0.0
    out = [x - mean for x in vec[:EMBED_DIM]]
    out += [0.0] * (EMBED_DIM - len(out))
    mag = math.sqrt(sum(x*x for x in out))
    return [x/mag for x in out] if mag else out


# ── Ollama embed ───────────────────────────────────────────────────────────────


class _EmbeddingInputTooLong(ValueError):
    pass


def _ollama_embed_batch(texts: list[str], model: str, timeout: int = 15) -> list[list[float]] | None:
    """Embed several inputs in one request. Returns None on any failure — a partial
    batch would silently drop the missing chunks' content from the pooled vector."""
    # The canonical LocalModelPolicy binds direct Ollama callers too, not just the registry:
    # disabled means no local model may execute — fall back to the hash-bag embedding exactly
    # as when Ollama is down (dims stay comparable; recall degrades, never silently routes).
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return None
    if model.split(":")[0] == "nomic-embed-text":
        texts = [f"{_RETRIEVAL_TASK.get()}: {text}" for text in texts]
    payload = {"model": model, "input": list(texts)}
    request_id = "embedding-" + hashlib.sha256(
        "\x00".join(texts).encode("utf-8")
    ).hexdigest()
    try:
        permit = seal_direct_provider_invocation(
            provider_id="ollama:embedding",
            model_id=model,
            operation="embedding",
            payload=payload,
            request_id=request_id,
            header_names=("Content-Type",),
        )
    except Exception:
        return None
    req = urllib.request.Request(
        f"{ollama_base_url()}/api/embed",
        data=json.dumps(permit.consume()).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        embs = data.get("embeddings") or []
        if len(embs) != len(texts) or any(not e for e in embs):
            return None
        return [[float(x) for x in e] for e in embs]
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode("utf-8", errors="replace").lower()
        if exc.code == 400 and "input length" in detail and "context length" in detail:
            raise _EmbeddingInputTooLong("Embedding input exceeds model context") from None
        return None
    except Exception:
        return None


def _ollama_embed(text: str, model: str, timeout: int = 15) -> list[float] | None:
    vecs = _embed_chunks_resilient([text], model)
    return vecs[0] if vecs else None


def _best_embed_model() -> str | None:
    """Return the first embed-capable model Ollama has, or None.

    Cached for a short TTL only: a probe that runs while Ollama is starting must
    not pin hash fallback for the whole process lifetime."""
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return None
    global _model_cache
    model, cached_at = _model_cache
    if time.monotonic() - cached_at < _MODEL_TTL_S:
        return model
    try:
        with urllib.request.urlopen(f"{ollama_base_url()}/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode())
        available = {m["name"].split(":")[0] for m in data.get("models", [])}
        found = None
        for m in _EMBED_MODELS:
            if m in available:
                found = m
                break
        if found is None:
            # Last resort: try any model tagged as embed
            for m in data.get("models", []):
                if "embed" in m["name"].lower():
                    found = m["name"]
                    break
    except Exception:
        found = None
    _model_cache = (found, time.monotonic())
    return found


# ── Hash bag-of-words fallback ─────────────────────────────────────────────────


def _hash_bow_embed(text: str, dims: int = _FALLBACK_DIMS) -> list[float]:
    """
    Lightweight deterministic embedding via character n-gram hashing.
    Good for keyword overlap; no semantic generalisation.
    """
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    if not tokens:
        return [0.0] * dims

    vec = [0.0] * dims
    # unigrams
    for tok in tokens:
        idx = int(hashlib.md5(tok.encode()).hexdigest(), 16) % dims
        vec[idx] += 1.0
    # bigrams (improve phrase matching)
    for a, b in itertools.pairwise(tokens):
        idx = int(hashlib.md5(f"{a}_{b}".encode()).hexdigest(), 16) % dims
        vec[idx] += 0.5

    mag = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / mag for x in vec]


# ── Public API ─────────────────────────────────────────────────────────────────


def split_for_embedding(text: str, max_chars: int = EMBED_MAX_CHARS) -> list[str]:
    """Split text into chunks under ``max_chars``, preferring turn (newline)
    boundaries so roles/turns stay intact. Deterministic; never drops content."""
    text = str(text or "")
    if len(text) <= max_chars:
        return [text] if text.strip() else []
    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + max_chars, n)
        if end < n:
            # prefer the last turn boundary inside the window
            boundary = text.rfind("\n", start + 1, end)
            if boundary > start:
                end = boundary + 1
        piece = text[start:end]
        if piece.strip():
            chunks.append(piece)
        start = end
    return chunks


def _pooled_vector(vectors: list[list[float]], weights: list[int]) -> list[float]:
    """Length-weighted mean of chunk vectors, unit-normalized."""
    total = float(sum(weights)) or 1.0
    dim = len(vectors[0])
    out = [0.0] * dim
    for vec, w in zip(vectors, weights, strict=True):
        for i, x in enumerate(vec[:dim]):
            out[i] += x * (w / total)
    mag = math.sqrt(sum(x * x for x in out))
    return [x / mag for x in out] if mag else out


def embed_stamped(text: str) -> tuple[list[float], str]:
    """Embed *text* and report WHICH vector space produced it.

    Returns ``(vector, backend_id)`` where backend_id is
    ``ollama:<model>#p384`` (neural, projected to the canonical dim) or
    ``hash-bow:384`` (keyword fallback). Equal dims do NOT make these spaces
    comparable — callers must store the stamp and never cosine-compare across
    different backends (see VoolMemory's embedding-backend gating)."""
    global _neural_down_until
    text = str(text or "").strip()
    if not text:
        return [0.0] * _FALLBACK_DIMS, HASH_BACKEND_ID

    if time.monotonic() >= _neural_down_until:
        model = _best_embed_model()
        if model:
            chunks = split_for_embedding(text)
            vecs = _embed_chunks_resilient(chunks, model)
            if vecs:
                with _stats_lock:
                    _stats["neural_calls"] += 1
                    _stats["chunks_embedded"] += len(chunks)
                is_nomic = model.split(":")[0] == "nomic-embed-text"
                projected = [
                    _nomic_retrieval_vector(v) if is_nomic else project_to_dim(v, EMBED_DIM)
                    for v in vecs
                ]
                # Never compare the changed retrieval representation with
                # legacy folded vectors just because dimensions happen to match.
                backend = (f"{NEURAL_BACKEND_PREFIX}{model}#retrieval-mrl{EMBED_DIM}-v1"
                           if is_nomic else f"{NEURAL_BACKEND_PREFIX}{model}#p{EMBED_DIM}")
                if len(projected) == 1:
                    return projected[0], backend
                return (
                    _pooled_vector(projected, [len(c) for c in chunks]),
                    backend,
                )
        # neural path unavailable or failed: open the breaker so a string of
        # queries during an outage does not each pay the request timeout
        with _stats_lock:
            _stats["neural_failures"] += 1
        _neural_down_until = time.monotonic() + _NEURAL_COOLDOWN_S
        _logger.warning(
            "embedding backend unavailable (HTTP failure or no model); "
            "falling back to %s for %.0fs", HASH_BACKEND_ID, _NEURAL_COOLDOWN_S,
        )
    else:
        with _stats_lock:
            _stats["neural_failures"] += 1

    with _stats_lock:
        _stats["hash_fallback_calls"] += 1
    return _hash_bow_embed(text), HASH_BACKEND_ID


def _embed_chunks_resilient(chunks: list[str], model: str) -> list[list[float]] | None:
    """Embed all chunks as one batch, degrading gracefully on transient
    per-request failures before any hash fallback.

    Measured failure mode on the live server: a large multi-chunk batch can
    time out or fail once while the backend is otherwise healthy; the previous
    code treated that as a backend outage, opened the breaker, and hashed every
    remaining document in the process for 30s (9 of 80 screening cases, 228
    documents total). Now: one retry of the same batch, then halved
    sub-batches — each still length-weighted pooled in order, so the text is
    fully represented and the vector space is unchanged. Only when even single
    chunks fail is the neural path treated as unavailable (breaker, then hash
    fallback)."""
    if not chunks:
        return None
    timeout = min(60, 15 + 2 * len(chunks))
    try:
        vecs = _ollama_embed_batch(chunks, model, timeout=timeout)
    except _EmbeddingInputTooLong:
        # Character limits cannot guarantee a model-token limit (dense code,
        # Unicode and repeated symbols are counterexamples). Split only on
        # an explicit length rejection; pool back to one vector per ORIGINAL
        # chunk so its caller's weights and backend identity stay aligned.
        if len(chunks) == 1:
            text = chunks[0]
            if len(text) <= 256:
                return None
            mid = len(text) // 2
            split = text.rfind(" ", max(1, mid // 2), mid + 1)
            split = split + 1 if split > 0 else mid
            parts = [text[:split], text[split:]]
            vectors = _embed_chunks_resilient(parts, model)
            return [_pooled_vector(vectors, [len(p) for p in parts])] if vectors else None
        mid = len(chunks) // 2
        left = _embed_chunks_resilient(chunks[:mid], model)
        right = _embed_chunks_resilient(chunks[mid:], model)
        return left + right if left is not None and right is not None else None
    if vecs:
        return vecs
    if len(chunks) == 1:
        return None
    # one whole-batch retry, then halve
    try:
        vecs = _ollama_embed_batch(chunks, model, timeout=timeout)
    except _EmbeddingInputTooLong:
        vecs = None
    if vecs:
        return vecs
    mid = len(chunks) // 2
    for group in (chunks[:mid], chunks[mid:]):
        part = _embed_chunks_resilient(group, model)
        if part is None:
            return None
        vecs = (vecs or []) + part
    return vecs


def embedding_stats() -> dict[str, int | str]:
    """Observable embedding accounting: neural vs fallback calls, chunk counts,
    and the active backend label. Fallback must never be silent or relabeled."""
    with _stats_lock:
        snap = dict(_stats)
    model = _best_embed_model()
    snap["backend"] = f"{NEURAL_BACKEND_PREFIX}{model}" if model else "hash-bow"
    return snap


def embed(text: str) -> list[float]:
    """
    Generate an embedding vector for *text*.

    Tries Ollama first (semantic quality, chunked for long inputs), falls back
    to hash-BoW (keyword overlap). See embed_stamped() for backend identity.
    """
    return embed_stamped(text)[0]


def embed_batch(texts: Sequence[str]) -> list[list[float]]:
    """Embed a list of texts. Uses the same backend for all."""
    return [embed(t) for t in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) != len(b):
        # Never silently score 0 on a dimension mismatch (that hid total recall
        # failure). Project both down to the common dimension and compare — a legacy
        # 768-dim vector stays comparable to a canonical 384-dim one.
        _warn_dim_mismatch_once(len(a), len(b))
        d = min(len(a), len(b))
        a, b = project_to_dim(a, d), project_to_dim(b, d)
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    return dot / (mag_a * mag_b) if mag_a and mag_b else 0.0


def embedding_backend() -> str:
    """Return a label describing which backend is active."""
    model = _best_embed_model()
    return f"{NEURAL_BACKEND_PREFIX}{model}" if model else "hash-bow"


__all__ = [
    "EMBED_DIM",
    "EMBED_MAX_CHARS",
    "HASH_BACKEND_ID",
    "NEURAL_BACKEND_PREFIX",
    "cosine_similarity",
    "embed",
    "embed_batch",
    "embed_stamped",
    "embedding_backend",
    "embedding_stats",
    "project_to_dim",
    "split_for_embedding",
]
