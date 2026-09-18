"""NIA-010 first live seam: every front-door dispatch decision lands as a typed,
canonical, tamper-evident shadow record in the routing-authority V2 store.

Proves: the record round-trips through the closed Phase-0 schema, the live
`record_decision()` funnel writes it (fail-soft, without breaking the JSONL),
records are tamper-evident on read-back, and the store stays non-authorizing.
"""
from __future__ import annotations

import json

import pytest

from core.routing_authority_v2 import (
    ContractValidationError,
    RoutingAuthorityV2ShadowStore,
    RoutingDecisionShadowV2,
    ShadowStoreIntegrityError,
)


@pytest.fixture()
def store(tmp_path):
    return RoutingAuthorityV2ShadowStore(tmp_path / "shadow.sqlite")


def _record(**over):
    base = dict(
        recorded_at_unix_ms=1_748_000_000_000,
        session_ref="sess-1",
        family="weather",
        handled=True,
        message_redacted_digest="a" * 64,
        claims=("weather", "time"),
        arbiter="picked:weather",
    )
    base.update(over)
    return RoutingDecisionShadowV2(**base)


def test_record_roundtrip_and_digest_stability(store):
    rec = _record()
    d1 = store.persist_shadow_record(rec)
    assert store.read_shadow_record(d1) == rec
    # Canonical bytes are the identity: same fields → same digest, idempotent replay.
    assert store.persist_shadow_record(rec) == d1
    assert d1 in store.list_shadow_record_digests(record_type="RoutingDecisionShadowV2")


def test_tampered_payload_is_an_integrity_failure(store, tmp_path, monkeypatch):
    rec = _record()
    digest = store.persist_shadow_record(rec)
    import sqlite3

    with sqlite3.connect(tmp_path / "shadow.sqlite") as conn:
        row = conn.execute(
            "SELECT canonical_bytes FROM routing_authority_v2_shadow_records"
            " WHERE record_digest = ?", (digest,)
        ).fetchone()
        forged = row[0].replace(b'"weather"', b'"weather2"'.ljust(len(b'"weather"'), b'"'))
        conn.execute(
            "UPDATE routing_authority_v2_shadow_records SET canonical_bytes = ?"
            " WHERE record_digest = ?", (forged, digest)
        )
    with pytest.raises(ShadowStoreIntegrityError):
        store.read_shadow_record(digest)


def test_field_validation_rejects_garbage():
    with pytest.raises(ContractValidationError):
        _record(message_redacted_digest="not-a-digest")
    with pytest.raises(ContractValidationError):
        _record(family="")
    with pytest.raises(ContractValidationError):
        _record(recorded_at_unix_ms=-5)


def test_live_funnel_writes_shadow_and_jsonl(tmp_path, monkeypatch):
    """The NIA-010 seam: record_decision() now feeds BOTH stores; fail-soft holds."""
    import core.routing_decision_log as rdl

    monkeypatch.setattr(rdl, "decisions_path", lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr(rdl, "_SHADOW_STORE", None)
    monkeypatch.setattr("core.routing_decision_log.data_path", lambda rel: tmp_path / rel)

    rdl.record_decision(
        session_id="s-shadow", user_input="temp in oslo", family="live_data",
        handled=True, claims=["live_data"], arbiter="picked:live_data",
    )
    # JSONL still written (existing behavior unchanged)...
    rows = [json.loads(l) for l in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    assert rows[-1]["family"] == "live_data"
    # ...and the shadow store holds the typed twin.
    store = RoutingAuthorityV2ShadowStore(tmp_path / "routing_authority_v2_shadow.sqlite")
    digests = store.list_shadow_record_digests(record_type="RoutingDecisionShadowV2")
    assert len(digests) == 1
    rec = store.read_shadow_record(digests[0])
    assert isinstance(rec, RoutingDecisionShadowV2)
    assert rec.family == "live_data" and rec.handled and rec.source == "routing_decision_log"
    assert rec.message_redacted_digest and len(rec.message_redacted_digest) == 64


def test_shadow_fault_never_breaks_the_turn(tmp_path, monkeypatch):
    """A broken shadow store must not cost the JSONL write or raise into the turn."""
    import core.routing_decision_log as rdl

    monkeypatch.setattr(rdl, "decisions_path", lambda: tmp_path / "decisions.jsonl")
    monkeypatch.setattr("core.routing_decision_log.data_path", lambda rel: tmp_path / rel)
    monkeypatch.setattr(rdl, "_SHADOW_STORE", None)

    def _boom(rel):
        raise OSError("disk gone")

    monkeypatch.setattr("core.routing_decision_log.data_path", _boom)
    rdl.record_decision(session_id="s2", user_input="q", family="model_lane", handled=False)
    rows = [json.loads(l) for l in (tmp_path / "decisions.jsonl").read_text().splitlines()]
    assert rows[-1]["family"] == "model_lane"
