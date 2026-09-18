"""F0-D / K-05 producer-side obligation ledger tests."""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.conductor import obligation_ledger as ol


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f0d.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _open_set():
    return ol.open_obligation_set(
        obligations=[
            {"obligation_id": "ob:prose-1", "text": "answer the question", "kind": "prose"},
            {"obligation_id": "ob:effect-1", "text": "send email", "kind": "effect"},
        ]
    )


def test_open_set_is_durable_and_consultable_cross_lane(fresh_store):
    opened = _open_set()
    verdict = ol.closure_verdict(opened["set_id"], opened["version"])
    assert verdict == {"covered": False, "open_count": 2, "set_version": opened["version"]}
    # Cross-lane consult via ContextVar binding.
    token = ol.bind_active_set(opened["set_id"], opened["version"])
    try:
        assert ol.active_set() == (opened["set_id"], opened["version"])
    finally:
        ol.clear_active_set()


def test_prose_cannot_close_pending_effect(fresh_store):
    opened = _open_set()
    sid, ver = opened["set_id"], opened["version"]
    # RED MUTATION: model prose ("EFFECT CONFIRMED") tries to satisfy the effect.
    assert not ol.record_disposition(
        sid, ver, "ob:effect-1", "satisfied", evidence_source="model_prose"
    )
    # Result-shape source is equally refused.
    assert not ol.record_disposition(
        sid, ver, "ob:effect-1", "satisfied", evidence_source="result_shape"
    )
    # A6 reconciled evidence CAN satisfy.
    assert ol.record_disposition(
        sid, ver, "ob:effect-1", "satisfied", evidence_source="a6_reconciled"
    )


def test_closure_is_structural_not_all_success(fresh_store):
    opened = _open_set()
    sid, ver = opened["set_id"], opened["version"]
    ol.record_disposition(sid, ver, "ob:prose-1", "satisfied", evidence_source="served_bytes")
    # Effect blocked by policy: terminal-permitting, NOT success — still closure.
    assert ol.record_disposition(
        sid, ver, "ob:effect-1", "gated", evidence_source="a1_decision"
    )
    verdict = ol.closure_verdict(sid, ver)
    assert verdict["covered"] is True and verdict["open_count"] == 0


def test_planned_and_absent_block_closure(fresh_store):
    opened = _open_set()
    sid, ver = opened["set_id"], opened["version"]
    ol.record_disposition(sid, ver, "ob:prose-1", "satisfied", evidence_source="served_bytes")
    verdict = ol.closure_verdict(sid, ver)
    assert verdict["covered"] is False and verdict["open_count"] == 1
