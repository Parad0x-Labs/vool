"""
Embedding service for VoolMemory semantic retrieval.

Priority:
  1. Ollama /api/embed with nomic-embed-text  (best semantic quality)
  2. Ollama /api/embed with any embed-capable model
  3. Hash-bag-of-words fallback              (pure Python, keyword overlap)

Every backend's output is projected to a single canonical EMBED_DIM (384) so a
vector stored under one backend stays comparable to one queried under another.
The fallback produces good exact-match and keyword-overlap similarity without
requiring any model download.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import logging
import math
import re
import urllib.error
import urllib.request
from collections.abc import Sequence
from functools import lru_cache

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

_logger = logging.getLogger(__name__)
_warned_dim_pairs: set[tuple[int, int]] = set()


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


# ── Ollama embed ───────────────────────────────────────────────────────────────


def _ollama_embed(text: str, model: str, timeout: int = 15) -> list[float] | None:
    # The canonical LocalModelPolicy binds direct Ollama callers too, not just the registry:
    # disabled means no local model may execute — fall back to the hash-bag embedding exactly
    # as when Ollama is down (dims stay comparable; recall degrades, never silently routes).
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return None
    payload = {"model": model, "input": text}
    try:
        permit = seal_direct_provider_invocation(
            provider_id="ollama:embedding",
            model_id=model,
            operation="embedding",
            payload=payload,
            request_id=(
                "embedding-"
                + hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest()
            ),
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
        if embs and embs[0]:
            return [float(x) for x in embs[0]]
    except Exception:
        pass
    return None


@lru_cache(maxsize=1)
def _best_embed_model() -> str | None:
    """Return the first embed-capable model Ollama has, or None."""
    from core.local_model_policy import local_models_enabled

    if not local_models_enabled():
        return None
    try:
        with urllib.request.urlopen(f"{ollama_base_url()}/api/tags", timeout=3) as r:
            data = json.loads(r.read().decode())
        available = {m["name"].split(":")[0] for m in data.get("models", [])}
        for m in _EMBED_MODELS:
            if m in available:
                return m
        # Last resort: try any model tagged as embed
        for m in data.get("models", []):
            if "embed" in m["name"].lower():
                return m["name"]
    except Exception:
        pass
    return None


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


def embed(text: str) -> list[float]:
    """
    Generate an embedding vector for *text*.

    Tries Ollama first (semantic quality), falls back to hash-BoW (exact match).
    """
    text = str(text or "").strip()
    if not text:
        return [0.0] * _FALLBACK_DIMS

    model = _best_embed_model()
    if model:
        vec = _ollama_embed(text, model)
        if vec:
            return project_to_dim(vec, EMBED_DIM)  # 768 (or whatever) -> canonical 384

    return _hash_bow_embed(text)  # already EMBED_DIM


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
    return f"ollama:{model}" if model else f"hash-bow:{_FALLBACK_DIMS}d"


__all__ = [
    "EMBED_DIM",
    "cosine_similarity",
    "embed",
    "embed_batch",
    "embedding_backend",
    "project_to_dim",
]
