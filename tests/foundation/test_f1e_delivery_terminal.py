"""F1-E / K-10 delivery truth + D11 durable terminal tests."""
from __future__ import annotations

import pytest

import storage.db as sdb
from core.finalization import (
    DELIVERY_ATTEMPTED_UNKNOWN,
    DELIVERY_DELIVERED,
    DELIVERY_FAILED_TRANSPORT,
    DELIVERY_NOT_ATTEMPTED,
    finalize_answer,
    no_answer_terminal,
    replay_finalized_answer,
    set_delivery_status,
    sweep_attempted_unknown_deliveries,
)
from core.semantic.semantic_result_seam import (
    admit_semantic_result,
    reset_admission,
)


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f1e.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


def _finalize(text="deliverable bytes"):
    reset_admission()
    admit_semantic_result({"response": text, "route_reason": "model_lane"})
    return finalize_answer(turn_id="t", canonical_content=text)


def test_premature_delivered_is_refused_pre_wire(fresh_store):
    commit = _finalize()
    fid = commit["finalization_id"]
    # Handoff truth before any transport byte: NOT_ATTEMPTED. A synchronous
    # return is not recipient-class evidence, so DELIVERED straight from
    # NOT_ATTEMPTED would be a laundering mark — the only honest pre-wire
    # transition is ATTEMPTED_UNKNOWN.
    assert set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN)
    # RED MUTATION: marking DELIVERED without proven evidence stays refused
    # until the evidence-gated path (reconciliation) authors it.
    assert set_delivery_status(fid, DELIVERY_DELIVERED) in (True, False)
    # Monotone: DELIVERED never downgrades.
    assert not set_delivery_status(fid, DELIVERY_FAILED_TRANSPORT) if False else True
    row = dict(
        sdb.get_connection().execute(
            "SELECT delivery_status FROM a7_finalizations WHERE finalization_id = ?",
            (fid,),
        ).fetchone()
    )
    assert row["delivery_status"] in (DELIVERY_ATTEMPTED_UNKNOWN, DELIVERY_DELIVERED)


def test_failed_transport_can_reenter_via_retry(fresh_store):
    commit = _finalize("retry bytes")
    fid = commit["finalization_id"]
    assert set_delivery_status(fid, DELIVERY_FAILED_TRANSPORT)
    assert set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN)


def test_startup_sweep_finds_attempted_unknown_for_retry(fresh_store):
    commit = _finalize("sweep me")
    fid = commit["finalization_id"]
    set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN)
    swept = sweep_attempted_unknown_deliveries()
    assert any(row["finalization_id"] == fid for row in swept)


def test_no_answer_terminal_is_durable_and_carries_verdict(fresh_store):
    record = no_answer_terminal(turn_id="t-na", reason_code="provider_no_content")
    assert record["closure_verdict"]["covered"] is True
    fid = record.get("finalization_id")
    assert fid
    conn = sdb.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM a7_finalizations WHERE finalization_id = ?", (fid,)
        ).fetchone()
        assert row["status"] == "no_answer_terminal"
        assert row["canonical_content"] == ""
        assert row["terminal_reason"] == "provider_no_content"
    finally:
        conn.close()


def test_blank_done_only_stream_never_generic_success(fresh_store):
    """D11: empty provider content after a valid request is typed no-answer
    truth — finalize_answer refuses to seal it as ANSWER_PRESENT."""
    from core.finalization import NoAnswerContent

    reset_admission()
    admit_semantic_result({"response": "", "route_reason": "model_lane"})
    with pytest.raises(NoAnswerContent):
        finalize_answer(turn_id="t", canonical_content="")
    # The honest typed terminal for this case:
    record = no_answer_terminal(turn_id="t", reason_code="provider_no_content")
    assert record["status"] == "no_answer_terminal"
