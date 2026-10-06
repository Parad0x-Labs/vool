"""Secrets pasted into chat must not persist verbatim in the semantic-memory sink.

The conversation log already redacts; these pin that the parallel semantic-memory store
(``context_retrieval.store_turn``) also scrubs high-confidence secrets before embedding and
persisting the turn, so a pasted API key does not land in plaintext in VoolMemory + FTS.
"""
from __future__ import annotations

import core.context_retrieval as cr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy

_SECRET = "fixture-sensitive-value-814"


class _FakeMemory:
    def __init__(self):
        self.stored: list[str] = []
        self.occurrences: list[str] = []

    def node_store(self, *, content, keywords, tags, context_description, embedding, embedding_backend="", lineage_request_id="", source_occurrence_id="", importance=None):
        self.stored.append(content)

    def occurrence_store(self, *, chat_scope, role, body, authority, **_kwargs):
        # layer-1 retention seam (CONTRACT mr29/1); bodies recorded for the
        # redaction assertions below to keep covering BOTH layers.
        self.occurrences.append(body)
        from types import SimpleNamespace
        return SimpleNamespace(occurrence_id=f"occ-{len(self.occurrences)}", role=role, body=body, status="active")

    def close(self):
        pass


def test_store_turn_redacts_secret_before_persist(monkeypatch):
    fake = _FakeMemory()
    monkeypatch.setattr(cr, "_open_memory", lambda: fake)
    monkeypatch.setattr(cr, "embed", lambda text: [0.0])
    monkeypatch.setattr(cr, "_session_scope_key", lambda sid: "sess")
    ensure_chat_namespace("sess", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="sess")

    result = cr.store_turn(
        "sess",
        f"please remember api_key={_SECRET}",
        "noted",
        access_policy=policy,
    )

    # The turn was stored (it is a "remember" request), but the raw key is not in it.
    assert result["status"] == "stored", result
    assert fake.stored, "high-importance turn should be persisted"
    for content in fake.stored:
        assert _SECRET not in content
    # layer-1 occurrences are redacted by the same one-time seam
    assert fake.occurrences, "source evidence should be retained"
    for body in fake.occurrences:
        assert _SECRET not in body
