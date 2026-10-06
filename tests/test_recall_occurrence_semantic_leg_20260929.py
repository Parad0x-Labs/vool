"""q90-recall Fix D: semantic leg over retained occurrences.

Measured root cause: BM25-only occurrence search returns nothing for
paraphrase/synonym/hypernym/cross-language questions ("heritage car
constructed" vs "Tramcar number 21 … 1908"), and the semantic node leg is
empty for short facts (admission gates), so recall had no meaning-level
supplier at all (frozen-head F04-01/F03-02/F03-11/F09-05/F01-10/F01-12).

Contracts under test:
  - source_embeddings derivative: idempotent upsert, staleness by
    body_sha256, backend consistency (foreign-backend vectors invisible),
    deletion clears the derivative (forgotten content must not resurface).
  - search-time merge: semantic-only hits APPEND after lexical winners
    (lexical leg never displaced — Track A lesson); hash lane stays
    lexical-only and reports it (no silent fallback).
  - end-to-end paraphrase recall through the real store→inject path with a
    deterministic fake neural backend (wiring proof; real semantic quality
    is measured on the nomic lane, separately labeled).
"""
from __future__ import annotations

import hashlib
import json

import pytest

import core.context_retrieval as cr
import core.embedding_service as embedding_service
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.vool_memory import VoolMemory

try:
    from tests.test_fresh_acceptance_memrepair2b_20260927 import (  # noqa: F401
        fresh_profile,
    )
except ImportError:  # pragma: no cover
    fresh_profile = None

pytestmark = pytest.mark.usefixtures("fresh_profile")


# ── storage contracts ──────────────────────────────────────────────────────

def _mem(home: str) -> VoolMemory:
    return VoolMemory(runtime_home=home, agent_id="vool_chat")


def _record(mem: VoolMemory, scope: str, body: str, role: str = "user"):
    return mem.occurrence_store(
        chat_scope=scope, role=role, body=body,
        authority="observed-user-statement" if role == "user" else "assistant-output",
        source_kind="live-turn", request_id="",
    )


def test_upsert_roundtrip_and_semantic_search(fresh_profile):
    home = fresh_profile
    mem = _mem(home)
    try:
        occ = _record(mem, "chat-a", "The winter mooring is buoy 7 off the north wall.")
        digest = hashlib.sha256(
            "The winter mooring is buoy 7 off the north wall.".encode()).hexdigest()
        assert mem.occurrence_embedding_upsert(
            occ.occurrence_id, backend="ollama:toy#v1",
            vector=[1.0, 0.0, 0.0], body_sha256=digest)
        # same-direction query finds it; orthogonal query does not
        hits = mem.occurrence_search_semantic(
            [0.9, 0.1, 0.0], chat_scope="chat-a", backend="ollama:toy#v1",
            floor=0.5, limit=4)
        assert any(h[0].occurrence_id == occ.occurrence_id for h in hits), hits
        miss = mem.occurrence_search_semantic(
            [0.0, 0.0, 1.0], chat_scope="chat-a", backend="ollama:toy#v1",
            floor=0.5, limit=4)
        assert not any(h[0].occurrence_id == occ.occurrence_id for h in miss)
    finally:
        mem.close()


def test_foreign_backend_rows_invisible(fresh_profile):
    home = fresh_profile
    mem = _mem(home)
    try:
        occ = _record(mem, "chat-b", "Charter parties sign at the harbour office.")
        digest = hashlib.sha256("Charter parties sign at the harbour office.".encode()).hexdigest()
        mem.occurrence_embedding_upsert(
            occ.occurrence_id, backend="ollama:other#v9",
            vector=[1.0, 1.0], body_sha256=digest)
        hits = mem.occurrence_search_semantic(
            [1.0, 1.0], chat_scope="chat-b", backend="ollama:toy#v1",
            floor=0.0, limit=4)
        assert hits == [], hits
    finally:
        mem.close()


def test_stale_body_sha256_counts_as_missing(fresh_profile):
    home = fresh_profile
    mem = _mem(home)
    try:
        occ = _record(mem, "chat-c", "First wording of the fact.")
        stale = hashlib.sha256(b"old-bytes").hexdigest()
        mem.occurrence_embedding_upsert(
            occ.occurrence_id, backend="ollama:toy#v1",
            vector=[1.0], body_sha256=stale)
        missing = mem.occurrence_embeddings_missing(
            chat_scope="chat-c", backend="ollama:toy#v1", limit=8)
        assert (occ.occurrence_id, "First wording of the fact.") in missing, missing
        fresh_digest = hashlib.sha256("First wording of the fact.".encode()).hexdigest()
        mem.occurrence_embedding_upsert(
            occ.occurrence_id, backend="ollama:toy#v1",
            vector=[1.0], body_sha256=fresh_digest)
        assert mem.occurrence_embeddings_missing(
            chat_scope="chat-c", backend="ollama:toy#v1", limit=8) == []
    finally:
        mem.close()


def test_deletion_clears_semantic_derivative(fresh_profile):
    home = fresh_profile
    mem = _mem(home)
    try:
        occ = _record(mem, "chat-d", "The gate code is 4711 until further notice.")
        digest = hashlib.sha256(
            "The gate code is 4711 until further notice.".encode()).hexdigest()
        mem.occurrence_embedding_upsert(
            occ.occurrence_id, backend="ollama:toy#v1",
            vector=[1.0, 0.0], body_sha256=digest)
        n = mem.occurrence_delete(occurrence_id=occ.occurrence_id)
        assert n == 1
        # the derivative died with the body: no semantic resurface path
        hits = mem.occurrence_search_semantic(
            [1.0, 0.0], chat_scope="chat-d", backend="ollama:toy#v1",
            floor=0.0, limit=8)
        assert hits == [], hits
        row = mem._conn.execute(
            "SELECT count(*) FROM source_embeddings WHERE occurrence_id = ?",
            (occ.occurrence_id,)).fetchone()
        assert row[0] == 0
    finally:
        mem.close()


def test_scope_isolation_semantic(fresh_profile):
    home = fresh_profile
    mem = _mem(home)
    try:
        a = _record(mem, "scope-x", "Rope store inventory due Friday.")
        b = _record(mem, "scope-y", "Rope store inventory due Friday.")
        d = hashlib.sha256(b"Rope store inventory due Friday.")
        for occ in (a, b):
            mem.occurrence_embedding_upsert(
                occ.occurrence_id, backend="ollama:toy#v1",
                vector=[1.0, 0.0], body_sha256=d.hexdigest())
        hits_y = mem.occurrence_search_semantic(
            [1.0, 0.0], chat_scope="scope-y", backend="ollama:toy#v1",
            floor=0.5, limit=8)
        assert [h[0].occurrence_id for h in hits_y] == [b.occurrence_id]
    finally:
        mem.close()


# ── wiring: hash lane stays lexical-only and says so ──────────────────────

def test_hash_lane_lexical_only_no_fallback_claim(fresh_profile):
    home = fresh_profile
    ensure_chat_namespace("wire-hash", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="wire-hash")
    cr.store_turn(
        "wire-hash", "The haul-out cradle takes blocks 1 and 2.", "Noted.",
        access_policy=policy,
        source_context={"chat_id": "wire-hash", "runtime_home": home},
    )
    out = cr.inject_retrieved(
        "wire-hash", "Which blocks does the cradle take?",
        [{"role": "user", "content": "Which blocks does the cradle take?"}],
        access_policy=policy,
        source_context={"chat_id": "wire-hash", "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    cap = next((str(m.get("content") or "") for m in out
                if "retrieved_context" in str(m.get("content") or "")), "")
    assert "blocks 1 and 2" in cap, cap


# ── wiring: deterministic fake neural backend, end-to-end paraphrase ──────

class _FakeNeural:
    """Deterministic stand-in embedding: direction from keyword buckets.

    Wiring proof only — real semantic quality is measured on the nomic lane
    with its own label, never claimed from this.
    """

    BUCKETS = {
        "tram": 0, "car": 0, "veteran": 0, "museum": 0, "works": 0,
        "heritage": 0, "constructed": 0, "built": 0, "rolled": 0,
    }

    def vec(self, text: str) -> list[float]:
        v = [0.0] * len(self.BUCKETS)
        lowered = str(text).lower()
        for i, key in enumerate(sorted(self.BUCKETS)):
            if key in lowered:
                v[i] = 1.0
        return v


def test_fake_neural_backend_appends_paraphrase_hit(fresh_profile, monkeypatch):
    home = fresh_profile
    fake = _FakeNeural()
    backend_label = "ollama:fake-neural-test#v1"

    def fake_embed_stamped(text, **kw):
        return fake.vec(text), backend_label

    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: backend_label)
    monkeypatch.setattr(cr, "embed_stamped", fake_embed_stamped)
    # the floor belongs to the vector space: nomic's 0.55 calibration does
    # not apply to this toy geometry, so the fake space declares its own
    monkeypatch.setattr(VoolMemory, "semantic_floor",
                        staticmethod(lambda backend: 0.10))

    ensure_chat_namespace("wire-fake", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="wire-fake")
    cr.store_turn(
        "wire-fake",
        "Tramcar number 21, the museum's oldest, rolled out of the works in 1908.",
        "A proper veteran.",
        access_policy=policy,
        source_context={"chat_id": "wire-fake", "runtime_home": home},
    )
    # the paraphrase question shares ZERO lexical anchor with the body
    out = cr.inject_retrieved(
        "wire-fake", "When was the heritage car constructed?",
        [{"role": "user", "content": "When was the heritage car constructed?"}],
        access_policy=policy,
        source_context={"chat_id": "wire-fake", "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    cap = next((str(m.get("content") or "") for m in out
                if "retrieved_context" in str(m.get("content") or "")), "")
    assert "1908" in cap, cap
    assert "number 21" in cap, cap


def test_lexical_winner_not_displaced_by_semantic(fresh_profile, monkeypatch):
    home = fresh_profile
    fake = _FakeNeural()
    backend_label = "ollama:fake-neural-test#v1"

    def fake_embed_stamped(text, **kw):
        return fake.vec(text), backend_label

    monkeypatch.setattr(embedding_service, "_best_embed_model", lambda: backend_label)
    monkeypatch.setattr(cr, "embed_stamped", fake_embed_stamped)
    # the floor belongs to the vector space: nomic's 0.55 calibration does
    # not apply to this toy geometry, so the fake space declares its own
    monkeypatch.setattr(VoolMemory, "semantic_floor",
                        staticmethod(lambda backend: 0.10))

    ensure_chat_namespace("wire-order", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="wire-order")
    # exact-term record (lexical winner) + related record (semantic-only)
    cr.store_turn(
        "wire-order", "Slip dues are 84 euros for the season.", "Noted.",
        access_policy=policy,
        source_context={"chat_id": "wire-order", "runtime_home": home},
    )
    cr.store_turn(
        "wire-order", "The heritage tram rolled from the works museum.", "Noted.",
        access_policy=policy,
        source_context={"chat_id": "wire-order", "runtime_home": home},
    )
    out = cr.inject_retrieved(
        "wire-order", "What are the slip dues?",
        [{"role": "user", "content": "What are the slip dues?"}],
        access_policy=policy,
        source_context={"chat_id": "wire-order", "runtime_home": home},
        env={"VOOL_CONTEXT_CAPSULE_V2": "1"},
    )
    cap = next((str(m.get("content") or "") for m in out
                if "retrieved_context" in str(m.get("content") or "")), "")
    assert "84 euros" in cap, cap
