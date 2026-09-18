"""F-04 repair proofs: every guard the verifier's mutation matrix removed is
pinned by a test that ACTUALLY INSTANTIATES the forbidden state.

Pins (LANE_A_MUTATION survivors M08/M11/M12/M14/M15/M22/M23):
- claim-CAS rowcount-0 refusal (claiming a non-claimable attempt aborts)
- admission conflict classifies REJECTED_DIFFERENT, never silent IDENTICAL
- a rejected (accepted=0) admission is NOT a valid A2 referent
- an ABSENT obligation blocks structural closure
- K-05 fail-closed: onboarded lane without a verdict refuses finalization
- DELIVERED is absorbing: no downgrade to FAILED_TRANSPORT/ATTEMPTED_UNKNOWN
- availability is monotone: WITHHELD → AVAILABLE reversal refused

Each test drives the REAL modules over an isolated temp DB.
"""
from __future__ import annotations

import json

import pytest

import storage.db as sdb


@pytest.fixture()
def fresh_store(tmp_path):
    from core.runtime_continuity import configure_runtime_continuity_db_path
    from storage.db import active_default_db_path

    sdb.configure_default_db_path(tmp_path / "f04.db")
    from storage.migrations import run_migrations

    run_migrations()
    configure_runtime_continuity_db_path(active_default_db_path())
    yield
    sdb.configure_default_db_path(None)


# --- M08: claim-CAS rowcount-0 refusal ---------------------------------------


def test_claiming_non_claimable_attempt_is_refused(fresh_store):
    from core.runtime_continuity import (
        AttemptClaimRefused,
        claim_runtime_attempt,
        create_runtime_attempt,
    )

    created = create_runtime_attempt(session_id="s", original_request="q")
    attempt_id = created["attempt_id"]
    claim_runtime_attempt(attempt_id, to_state="RUNNING")  # first claim wins
    # The row is now RUNNING — no longer claimable. The CAS rowcount-0 path
    # MUST refuse loudly (the swallowed variant let a second executor through).
    with pytest.raises(AttemptClaimRefused):
        claim_runtime_attempt(attempt_id, to_state="RUNNING")


# --- M11: honest admission-conflict classification ---------------------------


def test_admission_conflict_with_different_payload_rejected_different(fresh_store):
    from core.semantic.semantic_admissions import record_admission

    sr = "sr:f04-m11"
    assert record_admission(sr, source_class="model_lane") == "ACCEPTED_FIRST"
    outcome = record_admission(sr, source_class="different_lane")
    assert outcome == "REJECTED_DIFFERENT", outcome


# --- M12: rejected admissions are not referents ------------------------------


def test_rejected_admission_is_not_a_valid_referent(fresh_store):
    from core.semantic.semantic_admissions import admission_exists, record_admission

    sr = "sr:f04-m12-rejected"
    record_admission(sr, source_class="model_lane", accepted=False)
    assert admission_exists(sr) is False


# --- M14: ABSENT obligation blocks structural closure ------------------------


def test_absent_obligation_blocks_closure(fresh_store):
    from core.conductor import obligation_ledger as ol

    obset = ol.open_obligation_set(
        obligations=[{"obligation_id": "ob:a", "text": "t", "kind": "prose"}]
    )
    # Instantiate the forbidden state honestly: one obligation sits ABSENT
    # (minted but never discharged, structurally missing at closure time).
    conn = sdb.get_connection()
    try:
        row = conn.execute(
            "SELECT snapshot_json FROM obligation_sets WHERE set_id = ? AND version = ?",
            (obset["set_id"], obset["version"]),
        ).fetchone()
        snapshot = json.loads(row["snapshot_json"])
        snapshot["obligations"][0]["state"] = "absent"
        conn.execute(
            "UPDATE obligation_sets SET snapshot_json = ? WHERE set_id = ? AND version = ?",
            (json.dumps(snapshot), obset["set_id"], obset["version"]),
        )
        conn.commit()
    finally:
        conn.close()

    verdict = ol.closure_verdict(obset["set_id"], obset["version"])
    assert verdict["covered"] is False, verdict
    assert verdict["open_count"] >= 1, verdict


# --- M15: K-05 fail-closed on an onboarded lane without a verdict ------------


def test_onboarded_lane_without_verdict_refuses_finalization(fresh_store):
    from core.finalization import _active_closure_verdict
    from core.semantic.semantic_admissions import set_execution_context

    token = set_execution_context(
        {"execution_id": "exec-f04", "generation": 1, "runtime_epoch": "epoch-f04"}
    )
    try:
        with pytest.raises(RuntimeError, match="K-05"):
            _active_closure_verdict()
    finally:
        from core.semantic.semantic_admissions import clear_execution_context

        clear_execution_context()
        del token


# --- helpers for delivery/availability pins ----------------------------------


def _make_finalized_row(content: str) -> str:
    """Admit + finalize one canonical turn; return its finalization id."""
    from core.finalization import finalize_answer
    from core.semantic.semantic_admissions import set_request_context
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    token = set_request_context("req:http:f04-pins")
    try:
        reset_admission()
        admit_semantic_result(
            {
                "response": content,
                "success": True,
                "confidence": 0.9,
                "route_reason": "model_lane",
                "mode": "advice_only",
            }
        )
        commit = finalize_answer(turn_id="turn-f04", canonical_content=content)
        return str(commit["finalization_id"])
    finally:
        from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID

        _CURRENT_REQUEST_ID.reset(token)


def _delivery_row(fid: str) -> dict:
    conn = sdb.get_connection()
    try:
        row = conn.execute(
            "SELECT delivery_status, delivery_evidence_class FROM a7_finalizations "
            "WHERE finalization_id = ?",
            (fid,),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


# --- M22: DELIVERED is absorbing ---------------------------------------------


@pytest.mark.parametrize("downgrade_to", ["FAILED_TRANSPORT", "ATTEMPTED_UNKNOWN"])
def test_delivered_truth_never_downgrades(fresh_store, downgrade_to):
    from core.finalization import (
        DELIVERY_ATTEMPTED_UNKNOWN,
        DELIVERY_DELIVERED,
        set_delivery_status,
    )

    fid = _make_finalized_row("F22 delivered absorbing")
    assert set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN) is True
    assert (
        set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class="TRANSPORT_HANDOFF")
        is True
    )
    assert set_delivery_status(fid, downgrade_to, evidence_class="TRANSPORT_HANDOFF") is False
    row = _delivery_row(fid)
    assert row["delivery_status"] == "DELIVERED", row


# --- F-06: evidence vocabulary enforced at the durable write -----------------


@pytest.mark.parametrize("bad_class", ["LEGACY_UNVERIFIED", "made_up_class", "", "   "])
def test_delivered_requires_canonical_evidence_class(fresh_store, bad_class):
    from core.finalization import (
        DELIVERY_ATTEMPTED_UNKNOWN,
        DELIVERY_DELIVERED,
        set_delivery_status,
    )

    fid = _make_finalized_row(f"F06 vocabulary {bad_class!r}")
    assert set_delivery_status(fid, DELIVERY_ATTEMPTED_UNKNOWN) is True
    assert set_delivery_status(fid, DELIVERY_DELIVERED, evidence_class=bad_class) is False
    row = _delivery_row(fid)
    assert row["delivery_status"] == "ATTEMPTED_UNKNOWN", row


# --- M23: availability machine has no reverse edge ---------------------------


def test_withheld_availability_cannot_be_republished(fresh_store):
    from core.finalization import AVAILABILITY_AVAILABLE, AVAILABILITY_WITHHELD, set_availability

    fid = _make_finalized_row("F23 withheld stays withheld")
    assert set_availability(fid, AVAILABILITY_WITHHELD, reason="policy") is True
    assert set_availability(fid, AVAILABILITY_AVAILABLE) is False
    conn = sdb.get_connection()
    try:
        row = conn.execute(
            "SELECT availability FROM a7_finalizations WHERE finalization_id = ?",
            (fid,),
        ).fetchone()
        assert row["availability"] == "WITHHELD"
    finally:
        conn.close()


# --- F-07: caller-supplied covered verdict cannot close an OPEN ledger -------


def test_caller_covered_verdict_cannot_override_open_ledger(fresh_store):
    from core.conductor import obligation_ledger as ol
    from core.finalization import FinalizationRejected, finalize_answer
    from core.semantic.semantic_admissions import set_request_context
    from core.semantic.semantic_result_seam import admit_semantic_result, reset_admission

    obset = ol.open_obligation_set(
        obligations=[
            {"obligation_id": "ob:effect:x", "text": "machine effect", "kind": "effect"},
        ]
    )
    ol.bind_active_set(obset["set_id"], obset["version"])
    token = set_request_context("req:http:f04-f07")
    try:
        reset_admission()
        admit_semantic_result(
            {
                "response": "F07 forgery target",
                "success": True,
                "confidence": 0.9,
                "route_reason": "model_lane",
                "mode": "advice_only",
            }
        )
        with pytest.raises(FinalizationRejected):
            finalize_answer(
                turn_id="turn-f07",
                canonical_content="F07 forgery target",
                # The forged rider: claims coverage while the durable ledger
                # still holds a PLANNED effect obligation.
                closure={"covered": True, "open_count": 0, "set_version": obset["version"]},
            )
    finally:
        from core.semantic.semantic_admissions import _CURRENT_REQUEST_ID

        _CURRENT_REQUEST_ID.reset(token)
