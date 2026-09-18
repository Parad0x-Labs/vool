"""Sabotages: the failure classes the Proof Chip's guards exist for, executed on purpose.

Each sabotage is the naive implementation a regression would plausibly reintroduce, driven
against the real module. The naive rule and the shipped rule disagree on exactly the fixtures
below — and on every one of them the shipped rule must win:

* BINDING SABOTAGE — a reader that binds by request id alone ("latest record wins") serves one
  session's evidence to another session's query, and lets an older turn drift onto the newest
  turn's evidence. Both are refused by the canonical (session, request) join.
* STATE SABOTAGE — a verifier that reads "finalization row exists" as VERIFIED upgrades corrupt
  bytes, withheld payloads, wounded ledgers and half-finished retrievals into checkmarks. Every
  such fixture must land below VERIFIED.
"""
from __future__ import annotations

import hashlib

import pytest

from core.persistent_memory import append_conversation_event
from core.proof_projection import (
    STATE_INCOMPLETE,
    STATE_RECORDED,
    STATE_VERIFIED,
    ProofNotBound,
    build_turn_proof,
)
from core.runtime_task_events import emit_runtime_event
from core.semantic.semantic_admissions import bound_request_context
from storage.db import get_connection


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _bind_turn(session: str, request_id: str, answer: str = "Answer.") -> None:
    with bound_request_context(request_id):
        append_conversation_event(
            session_id=session,
            user_input="Question",
            assistant_output=answer,
        )


def _insert_finalization(request_id: str, turn_id: str, content: str, *, availability: str = "available", content_hash: str | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO a7_finalizations (
                finalization_id, semantic_result_id, turn_id, content_hash,
                canonical_content, status, request_id, payload_ref, availability
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                "fc:" + hashlib.sha256(request_id.encode()).hexdigest()[:32],
                "sr-" + request_id,
                turn_id,
                content_hash if content_hash is not None else _hash(content),
                content,
                "answer_present",
                request_id,
                availability,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _emit(session: str, turn_id: str, request_id: str, event_type: str, details: dict | None = None) -> None:
    emit_runtime_event(
        {"session_id": session, "runtime_session_id": session, "cancel_turn_id": turn_id},
        event_type=event_type,
        message=f"{event_type} for {turn_id}",
        details={**(details or {}), "request_id": request_id},
    )


# ---------------------------------------------------------------------------------------------
# Sabotage: bind by request id alone (the "latest record" reader).
# ---------------------------------------------------------------------------------------------


def test_sabotage_naive_request_only_binding_serves_cross_session_theft() -> None:
    """The naive reader binds by request id alone and would serve session B's turn to a query
    about session A. The shipped module refuses the same query."""
    session_a = "sab-bind-a"
    session_b = "sab-bind-b"
    _bind_turn(session_b, "sab-req-shared-shape")
    _insert_finalization("sab-req-shared-shape", "sab-turn-b", "Answer.")
    _emit(session_b, "sab-turn-b", "sab-req-shared-shape", "tool_executed", {"tool_name": "workspace.read_file", "status": "ok"})

    def naive_binding(session_id: str, request_id: str) -> dict:
        # The regression: session is decorative; any row carrying the request id "binds".
        return {"session_id": session_id, "request_id": request_id}

    naive_serves = naive_binding(session_a, "sab-req-shared-shape") is not None
    assert naive_serves, "the naive reader must accept the pair for this sabotage to mean anything"
    with pytest.raises(ProofNotBound):
        build_turn_proof(session_id=session_a, request_id="sab-req-shared-shape")


def test_sabotage_even_with_the_guard_removed_the_deep_reads_stay_session_scoped() -> None:
    """Defense in depth: force the binding guard to accept a foreign pair, and the evidence
    underneath STILL does not cross sessions — execution facts and events are read with the
    session filter, so the stolen binding yields an empty account, not session B's actions."""
    session_a = "sab-depth-a"
    session_b = "sab-depth-b"
    _bind_turn(session_b, "sab-req-depth")
    _insert_finalization("sab-req-depth", "sab-turn-depth", "Answer.")
    _emit(session_b, "sab-turn-depth", "sab-req-depth", "tool_executed", {"tool_name": "workspace.read_file", "status": "ok"})

    from core import proof_projection

    original = proof_projection._find_binding_row
    attempts = {"n": 0}

    def broken_binding(session_id: str, request_id: str) -> dict | None:
        attempts["n"] += 1
        return original(session_b, request_id)  # always lends session B's binding row

    proof_projection._find_binding_row = broken_binding
    try:
        proof = build_turn_proof(session_id=session_a, request_id="sab-req-depth")
    finally:
        proof_projection._find_binding_row = original
    assert attempts["n"] >= 1, "the broken guard must actually have been consulted"
    # The facts and events of session B's turn never entered session A's projection. (The turn
    # KEY may name the foreign turn — it comes from this request's own finalization row — but an
    # identity label is not evidence: every fact/event read is session-filtered, so nothing
    # rides with it.)
    assert proof["compact"]["actions"] == 0
    assert proof["expanded"]["actions"] == []
    assert proof["expanded"]["sources"] == []
    assert all(not str(item).startswith("fact:") for item in proof["expanded"]["receipts"])
    assert proof["compact"]["cost"]["tokens"] is None


def test_sabotage_an_older_turn_does_not_drift_onto_newer_evidence() -> None:
    """After a second, tool-running turn lands in the same session, the FIRST turn's proof is
    unchanged — binding is identity, never recency."""
    session = "sab-drift"
    _bind_turn(session, "sab-req-old", "Old.")
    _insert_finalization("sab-req-old", "sab-turn-old", "Old.")
    before = build_turn_proof(session_id=session, request_id="sab-req-old")

    _bind_turn(session, "sab-req-new", "New.")
    _insert_finalization("sab-req-new", "sab-turn-new", "New.")
    _emit(session, "sab-turn-new", "sab-req-new", "tool_executed", {"tool_name": "web.search", "status": "ok"})
    _emit(session, "sab-turn-new", "sab-req-new", "model_usage", {"model_id": "m", "input_tokens": 500, "output_tokens": 100})

    after = build_turn_proof(session_id=session, request_id="sab-req-old")
    assert after["compact"] == before["compact"]
    assert after["expanded"]["actions"] == []
    assert after["expanded"]["model"] == ""
    assert after["state"] == before["state"]


# ---------------------------------------------------------------------------------------------
# Sabotage: verification state from row presence.
# ---------------------------------------------------------------------------------------------


def _naive_state(fin_row: dict | None) -> str:
    """The regression: a stored row IS a verification."""
    return STATE_VERIFIED if fin_row is not None else STATE_RECORDED


def test_sabotage_tampered_bytes_are_not_a_checkmark() -> None:
    session = "sab-state-tamper"
    _bind_turn(session, "sab-req-tamper", "Real bytes.")
    _insert_finalization(
        "sab-req-tamper",
        "sab-turn-tamper",
        "Real bytes.",
        content_hash=_hash("Substituted bytes"),
    )
    proof = build_turn_proof(session_id=session, request_id="sab-req-tamper")
    assert _naive_state({"x"}) == STATE_VERIFIED, "the naive rule must disagree here"
    assert proof["state"] == STATE_RECORDED
    assert "content_hash_mismatch" in proof["state_reasons"]


def test_sabotage_withheld_payload_is_not_a_checkmark() -> None:
    session = "sab-state-withheld"
    _bind_turn(session, "sab-req-withheld", "Withheld.")
    _insert_finalization("sab-req-withheld", "sab-turn-withheld", "Withheld.", availability="withheld")
    proof = build_turn_proof(session_id=session, request_id="sab-req-withheld")
    assert _naive_state({"x"}) == STATE_VERIFIED
    assert proof["state"] == STATE_RECORDED
    assert "payload_unavailable" in proof["state_reasons"]


def test_sabotage_a_wounded_ledger_is_not_a_checkmark() -> None:
    session = "sab-state-witness"
    _bind_turn(session, "sab-req-witness", "Tool answer.")
    _insert_finalization("sab-req-witness", "sab-turn-witness", "Tool answer.")
    _emit(session, "sab-turn-witness", "sab-req-witness", "tool_executed", {"tool_name": "workspace.read_file", "status": "ok"})
    conn = get_connection()
    try:
        conn.execute("DELETE FROM execution_facts WHERE turn_key = ?", ("sab-turn-witness",))
        conn.commit()
    finally:
        conn.close()
    proof = build_turn_proof(session_id=session, request_id="sab-req-witness")
    assert _naive_state({"x"}) == STATE_VERIFIED
    assert proof["state"] == STATE_INCOMPLETE
    assert "witness_inconsistent" in proof["state_reasons"]


def test_sabotage_a_half_finished_retrieval_is_not_a_checkmark() -> None:
    session = "sab-state-hanging"
    _bind_turn(session, "sab-req-hang", "Answer.")
    _insert_finalization("sab-req-hang", "sab-turn-hang", "Answer.")
    _emit(session, "sab-turn-hang", "sab-req-hang", "web_retrieval_started")
    proof = build_turn_proof(session_id=session, request_id="sab-req-hang")
    assert _naive_state({"x"}) == STATE_VERIFIED
    assert proof["state"] == STATE_INCOMPLETE
    assert "missing_terminal" in proof["state_reasons"]


def test_sabotage_state_precedence_survives_a_valid_hash_with_bad_evidence() -> None:
    """The state calculation is not a hash check alone: an intact hash over a turn whose
    expected terminal evidence is missing still lands at INCOMPLETE."""
    session = "sab-state-precedence"
    answer = "Answer with a hanging fetch."
    _bind_turn(session, "sab-req-prec", answer)
    _insert_finalization("sab-req-prec", "sab-turn-prec", answer)
    _emit(session, "sab-turn-prec", "sab-req-prec", "web_retrieval_started")
    proof = build_turn_proof(session_id=session, request_id="sab-req-prec")
    assert proof["expanded"]["finalization"]["content_hash_ok"] is True
    assert proof["state"] == STATE_INCOMPLETE
