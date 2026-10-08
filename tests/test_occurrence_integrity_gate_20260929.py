"""Occurrence-body integrity gate (F16 contract, 2026-09-29 repair).

The digest recorded at the authorized write (``occurrence_store`` /
import) is the integrity anchor. Damaged-but-retrievable bodies —
marker-prefixed, same-length altered, truncated, or fully replaced — must
be REFUSED by every serving read (lexical, semantic, neighbors, embedding
backfill) and materialization must return a typed refusal, with the
refusal OBSERVABLE in telemetry so it can never be mistaken for a
retrieval miss. Intact recall, legacy rows (no digest), deletion tombs,
exact-Unicode roundtrip and restart persistence are pinned as controls.

Deterministic hash backend: these rows pin the integrity CONTRACT, which is
backend-independent by construction (digest over stored bytes); the native
neural lane is proven by the job probes
(artifacts/q90-f16repair-20260929/probes/).
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

TREE = Path(__file__).resolve().parents[1]

POISON = "ZZQ-CORRUPTED-9b3f-occurrence-body-payload"


@pytest.fixture(autouse=True)
def _hash_backend(monkeypatch):
    from core import embedding_service

    original = embedding_service._best_embed_model
    embedding_service._best_embed_model = lambda: None
    env_before = dict(os.environ)
    yield
    embedding_service._best_embed_model = original
    os.environ.clear()
    os.environ.update(env_before)


@pytest.fixture()
def store(tmp_path: Path):
    """A real per-test profile home so materialize_source_evidence opens the
    SAME store the test wrote (the runtime resolves the canonical db path
    from the configured home)."""
    from core.runtime_paths import configure_runtime_home
    from core.vool_memory import VoolMemory

    profile = tmp_path / "home"
    (profile / "workspace").mkdir(parents=True, exist_ok=True)
    os.environ.update(
        VOOL_HOME=str(profile),
        VOOL_WORKSPACE_ROOT=str(profile / "workspace"),
    )
    configure_runtime_home(profile)
    db_path = profile / "data" / "memory" / "vool_memory.db"
    mem = VoolMemory(runtime_home=profile)
    occ = _store(mem, "f16-gate", BODY)
    yield mem, occ, db_path
    mem.close()


def _mem(db_path: Path):
    from core.vool_memory import VoolMemory

    db_path.parent.mkdir(parents=True, exist_ok=True)
    return VoolMemory(db_path=db_path)


def _store(mem, chat: str, body: str, role: str = "user"):
    return mem.occurrence_store(
        chat_scope=chat, role=role, body=body,
        authority="observed-user-statement" if role == "user" else "assistant-output",
    )


def _damage(db_path: Path, occurrence_id: str, new_body: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE source_occurrences SET body = ? WHERE occurrence_id = ?",
            (new_body, occurrence_id),
        )
        conn.commit()
    finally:
        conn.close()


def _query(db_path: Path, sql: str, params=()):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


BODY = "the tide pool gate combination is 8-4-1-2 at low tide"


# ---------------------------------------------------------------- positives


def test_intact_row_served_and_verified(store):
    mem, occ, _ = store
    assert occ.body_integrity == "verified"
    hits = mem.occurrence_search("tide pool gate combination", chat_scope="f16-gate")
    assert [h.occurrence_id for h, _ in hits] == [occ.occurrence_id]
    assert hits[0][0].body_integrity == "verified"
    assert mem.integrity_refusals_total == 0


def test_materialize_intact_complete(store):
    from core.context_retrieval import materialize_source_evidence

    mem, occ, db_path = store
    mat = materialize_source_evidence(occ.occurrence_id, query="tide pool gate")
    assert mat["complete"] is True
    assert BODY in str(mat["body"])


# ------------------------------------------------- damage shapes → refusal


@pytest.mark.parametrize(
    "shape,maker",
    [
        ("prefix", lambda b: POISON + " " + b),
        ("same-length", lambda b: POISON + b[len(POISON):]),
        ("truncation", lambda b: b[: len(b) // 2]),
        ("full-replacement", lambda b: POISON),
    ],
)
def test_damaged_row_refused_from_search_with_telemetry(tmp_path, shape, maker):
    mem = _mem(tmp_path / "mem.db")
    try:
        occ = _store(mem, "f16-gate", BODY)
        _damage(tmp_path / "mem.db", occ.occurrence_id, maker(BODY))
        hits = mem.occurrence_search("tide pool gate combination", chat_scope="f16-gate")
        assert hits == []
        # refusal is OBSERVABLE — this is detection, not a retrieval miss
        assert mem.integrity_refusals_total == 1
        assert mem.last_integrity_refusals == [
            {"occurrence_id": occ.occurrence_id, "reason": "body_sha256_mismatch"}
        ]
        got = mem.occurrence_get(occ.occurrence_id)
        assert got.body_integrity == "mismatch"
    finally:
        mem.close()


def test_damaged_row_still_lexically_findable_at_the_index(store):
    """Controlled path: the derivative FTS still MATCHES the damaged row's
    id, so the empty result is provably the integrity gate refusing a
    retrieved row, not an index that never saw it."""
    mem, occ, db_path = store
    _damage(db_path, occ.occurrence_id, POISON + " " + BODY)
    rows = _query(
        db_path,
        "SELECT count(*) AS n FROM source_fts WHERE source_fts MATCH ? "
        "AND occurrence_id = ?",
        ("tide pool gate combination", occ.occurrence_id),
    )
    assert rows[0]["n"] == 1
    assert mem.occurrence_search("tide pool gate combination", chat_scope="f16-gate") == []


def test_materialize_refuses_damaged_with_typed_reason(store):
    from core.context_retrieval import materialize_source_evidence

    mem, occ, db_path = store
    _damage(db_path, occ.occurrence_id, POISON + " " + BODY)
    mat = materialize_source_evidence(occ.occurrence_id, query="tide pool gate")
    assert mat["complete"] is False
    assert mat["reason"] == "occurrence_integrity_mismatch"
    assert mat["body"] == "" and mat["spans"] == []
    assert POISON not in str(mat)


def test_semantic_search_refuses_damaged(store):
    mem, occ, db_path = store
    vec = [0.1, 0.2, 0.3]
    assert mem.occurrence_embedding_upsert(
        occ.occurrence_id, backend="hash-bow:384", vector=vec,
        body_sha256=hashlib.sha256(BODY.encode("utf-8")).hexdigest())
    _damage(db_path, occ.occurrence_id, POISON + " " + BODY)
    hits = mem.occurrence_search_semantic(
        vec, chat_scope="f16-gate", backend="hash-bow:384", floor=None)
    assert hits == []
    assert mem.integrity_refusals_total == 1


def test_backfill_never_embeds_damaged_bytes(store):
    mem, occ, db_path = store
    _damage(db_path, occ.occurrence_id, POISON + " " + BODY)
    assert mem.occurrence_embeddings_missing(
        chat_scope="f16-gate", backend="ollama:nomic-embed-text#retrieval-mrl384-v1") == []
    assert mem.integrity_refusals_total == 1


def test_upsert_refuses_damaged_and_foreign_digest(store):
    mem, occ, db_path = store
    vec = [0.1, 0.2, 0.3]
    # damaged body: refused even with the ORIGINAL (authorized) digest
    _damage(db_path, occ.occurrence_id, POISON + " " + BODY)
    assert not mem.occurrence_embedding_upsert(
        occ.occurrence_id, backend="b", vector=vec,
        body_sha256=hashlib.sha256(BODY.encode("utf-8")).hexdigest())
    # intact body but a digest that is not the authorized one: refused —
    # a checksum recomputed over read-time bytes legitimizes nothing
    _damage(db_path, occ.occurrence_id, BODY)
    assert not mem.occurrence_embedding_upsert(
        occ.occurrence_id, backend="b", vector=vec,
        body_sha256=hashlib.sha256((POISON + BODY).encode("utf-8")).hexdigest())
    # intact body + authorized digest: the derivative is accepted
    assert mem.occurrence_embedding_upsert(
        occ.occurrence_id, backend="b", vector=vec,
        body_sha256=hashlib.sha256(BODY.encode("utf-8")).hexdigest())


def test_neighbors_excludes_damaged_neighbor(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        first = _store(mem, "f16-gate", "morning check: the dredge winch was serviced")
        anchor = _store(mem, "f16-gate", BODY)
        third = _store(mem, "f16-gate", "evening log: the sluice lanterns were lit")
        _damage(tmp_path / "mem.db", third.occurrence_id, POISON + " sluice")
        neighbors = mem.occurrence_neighbors(anchor)
        ids = [n.occurrence_id for n in neighbors]
        assert first.occurrence_id in ids
        assert third.occurrence_id not in ids
    finally:
        mem.close()


# ----------------------------------------------------------------- controls


def test_legacy_row_without_digest_is_served_and_flagged(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        occ = _store(mem, "f16-gate", BODY)
        conn = sqlite3.connect(str(tmp_path / "mem.db"))
        try:
            conn.execute(
                "UPDATE source_occurrences SET body_sha256 = '' "
                "WHERE occurrence_id = ?", (occ.occurrence_id,))
            conn.commit()
        finally:
            conn.close()
        hits = mem.occurrence_search("tide pool gate combination", chat_scope="f16-gate")
        assert [h.occurrence_id for h, _ in hits] == [occ.occurrence_id]
        assert hits[0][0].body_integrity == "legacy-unverified"
        assert mem.integrity_refusals_total == 0
    finally:
        mem.close()


def test_deleted_tombstone_still_refused_by_status(store):
    from core.context_retrieval import materialize_source_evidence

    mem, occ, db_path = store
    assert mem.occurrence_delete(occurrence_id=occ.occurrence_id) == 1
    mat = materialize_source_evidence(occ.occurrence_id, query="tide pool gate")
    assert mat["complete"] is False
    assert mat["reason"] == "occurrence_deleted"


def test_unicode_body_roundtrips_verified(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        body = "潮池の暗証番号は 8-4-1-2 🌊 — combinaison à marée basse"
        occ = _store(mem, "f16-gate", body)
        assert occ.body_integrity == "verified"
        got = mem.occurrence_get(occ.occurrence_id)
        assert got.body == body and got.body_integrity == "verified"
        # latin tokens of the same body are lexically findable (CJK runs are
        # single unicode61 tokens, so the latin leg is the query surface)
        hits = mem.occurrence_search("combinaison marée", chat_scope="f16-gate")
        assert [h.occurrence_id for h, _ in hits] == [occ.occurrence_id]
        assert hits[0][0].body == body
        # a single codepoint change is still damage
        _damage(tmp_path / "mem.db", occ.occurrence_id,
                body.replace("8-4-1-2", "8-4-1-3"))
        assert mem.occurrence_search("combinaison marée", chat_scope="f16-gate") == []
        assert mem.integrity_refusals_total == 1
    finally:
        mem.close()


def test_refusal_survives_process_restart(tmp_path):
    db_path = tmp_path / "mem.db"
    mem = _mem(db_path)
    occ = _store(mem, "f16-gate", BODY)
    mem.close()
    _damage(db_path, occ.occurrence_id, POISON + " " + BODY)
    mem2 = _mem(db_path)
    try:
        assert mem2.occurrence_search("tide pool gate combination", chat_scope="f16-gate") == []
        assert mem2.integrity_refusals_total == 1
        assert mem2.occurrence_get(occ.occurrence_id).body_integrity == "mismatch"
    finally:
        mem2.close()


def test_last_refusals_reflect_most_recent_call(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        a = _store(mem, "f16-gate", "alpha: the winch grease drum is blue")
        b = _store(mem, "f16-gate", BODY)
        _damage(tmp_path / "mem.db", a.occurrence_id, POISON + " alpha blue")
        assert mem.occurrence_search("winch grease drum", chat_scope="f16-gate") == []
        assert [r["occurrence_id"] for r in mem.last_integrity_refusals] == [a.occurrence_id]
        # a later clean call clears the per-call view; the TOTAL accumulates
        hits = mem.occurrence_search("tide pool gate combination", chat_scope="f16-gate")
        assert [h.occurrence_id for h, _ in hits] == [b.occurrence_id]
        assert mem.last_integrity_refusals == []
        assert mem.integrity_refusals_total == 1
    finally:
        mem.close()


def test_write_idempotency_still_matches_same_bytes(tmp_path):
    mem = _mem(tmp_path / "mem.db")
    try:
        first = mem.occurrence_store(
            chat_scope="f16-gate", role="user", body=BODY,
            authority="observed-user-statement", request_id="req-1")
        again = mem.occurrence_store(
            chat_scope="f16-gate", role="user", body=BODY,
            authority="observed-user-statement", request_id="req-1")
        assert again.occurrence_id == first.occurrence_id
        assert again.body_integrity == "verified"
    finally:
        mem.close()
