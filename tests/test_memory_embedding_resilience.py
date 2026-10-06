"""Regression tests for embedding resilience (evidence-run mission).

Measured failure mode: a transient large-batch embed failure opened the
outage breaker and hashed every remaining document in the process (9 of 80
screening cases; 228 documents). The resilient path must retry, then halve
sub-batches, staying neural — and only treat single-chunk failure as outage.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import core.embedding_service as es
from core.runtime_paths import configure_runtime_home


@pytest.fixture(autouse=True)
def _fresh_embedding_state():
    """The breaker and counters are module-global; reset per test."""
    es._neural_down_until = 0.0
    with es._stats_lock:
        es._stats.update(
            neural_calls=0, hash_fallback_calls=0, chunks_embedded=0, neural_failures=0
        )
    yield
    es._neural_down_until = 0.0


def test_transient_batch_failure_stays_neural(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        configure_runtime_home(tmp)
        monkeypatch.setattr(es, "_best_embed_model", lambda: "nomic-embed-text")
        calls: list[int] = []

        def fake_batch(texts, model, timeout=15):
            calls.append(len(texts))
            if len(texts) > 1:
                return None  # transient whole-batch failure
            return [[1.0, 0.0, 0.0] for _ in texts]

        monkeypatch.setattr(es, "_ollama_embed_batch", fake_batch)
        body = "\n".join(f"turn {i}: " + "word" * 20 for i in range(120))
        vec, backend = es.embed_stamped(body)
        assert backend.startswith("ollama:"), "must stay neural after transient failure"
        assert es.embedding_stats()["hash_fallback_calls"] == 0
        assert es.embedding_stats()["neural_failures"] == 0, "breaker must not open"
        assert len(calls) > 2, "halving retry must have been attempted"
        assert calls[-1] == 1


def test_single_chunk_failure_is_outage(monkeypatch) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        configure_runtime_home(tmp)

        def fake_batch(texts, model, timeout=15):
            return None

        monkeypatch.setattr(es, "_ollama_embed_batch", fake_batch)
        monkeypatch.setattr(es, "_best_embed_model", lambda: "nomic-embed-text")
        vec, backend = es.embed_stamped("hello world")
        assert backend == es.HASH_BACKEND_ID, "single-chunk failure is a real outage"
        assert es.embedding_stats()["hash_fallback_calls"] == 1


def test_oversized_single_line_preserved(monkeypatch) -> None:
    """A single turn longer than the chunk bound must be hard-split, never
    truncated: the pooled vector must differ when the tail changes."""
    with tempfile.TemporaryDirectory() as tmp:
        configure_runtime_home(tmp)
        monkeypatch.setattr(es, "_best_embed_model", lambda: "nomic-embed-text")
        seen: list[list[str]] = []

        def fake_batch(texts, model, timeout=15):
            seen.append(list(texts))
            out = []
            for t in texts:
                digest = sum(ord(c) for c in t[:100]) + len(t)
                out.append([float(digest % 97), float(len(t)), 0.0])
            return out

        monkeypatch.setattr(es, "_ollama_embed_batch", fake_batch)
        tail_marker = "TAILMARKER" * 2000
        full = "x" * (es.EMBED_MAX_CHARS + 1000) + tail_marker
        vec_full, _ = es.embed_stamped(full)
        chunks_seen = [c for batch in seen for c in batch]
        joined = "".join(chunks_seen)
        assert tail_marker in joined, "chunking must preserve the tail"
        assert sum(len(c) for c in chunks_seen) == len(full), "no content dropped"
        assert len(vec_full) == es.EMBED_DIM
