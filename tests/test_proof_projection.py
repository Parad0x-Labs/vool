"""The Proof Chip's projection: honest evidence states bound to ONE served turn.

The chip is a strictly read-only projection over evidence that already exists. These tests pin
the three properties it exists for:

* BINDING -- evidence is addressed by the canonical (session_id, request_id) pair, the same pair
  the conversation log row carries. A query that cannot be joined to ONE served turn is refused
  (typed ProofNotBound), never answered from "the latest record": cross-turn and cross-session
  evidence must be impossible, not merely unlikely.
* HONEST STATES -- VERIFIED only when the finalization's own integrity re-verifies AND no
  expected terminal evidence is missing AND the execution witness is consistent; RECORDED when
  rows exist but integrity is not proven (corrupt bytes, unavailable payload, legacy turns);
  INCOMPLETE when expected terminal evidence is missing; UNVERIFIED when no trustworthy proof
  exists at all. Row presence alone must never produce the top state.
* REDACT-AT-PROJECTION -- local personal paths and secrets are masked in what the projection
  returns, while the stored rows are left byte-identical (this module writes nothing).

Driven through the real seams: `append_conversation_event` writes the binding row,
`emit_runtime_event` records the events AND the authoritative execution facts, and the
`a7_finalizations` row is written the way `_bind_durably` writes it (same columns, same hash
convention).
"""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from core.execution_truth import facts_for_turn
from core.persistent_memory import append_conversation_event
from core.proof_projection import (
    STATE_INCOMPLETE,
    STATE_RECORDED,
    STATE_UNVERIFIED,
    STATE_VERIFIED,
    ProofNotBound,
    build_turn_proof,
)
from core.runtime_task_events import emit_runtime_event
from core.semantic.semantic_admissions import bound_request_context
from storage.db import get_connection


def _hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _bind_turn(session: str, request_id: str, answer: str = "The answer.") -> None:
    """The real binding row: one served turn in the conversation log."""
    with bound_request_context(request_id):
        append_conversation_event(
            session_id=session,
            user_input="Question",
            assistant_output=answer,
        )


def _insert_finalization(
    request_id: str,
    turn_id: str,
    content: str,
    *,
    availability: str = "available",
    content_hash: str | None = None,
) -> str:
    """An `a7_finalizations` row with the columns `_bind_durably` writes."""
    finalization_id = "fc:" + hashlib.sha256(request_id.encode()).hexdigest()[:32]
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
                finalization_id,
                "sr-test-" + request_id,
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
    return finalization_id


def _emit_turn_event(
    session: str,
    turn_id: str,
    request_id: str,
    event_type: str,
    details: dict | None = None,
) -> None:
    emit_runtime_event(
        {
            "session_id": session,
            "runtime_session_id": session,
            "cancel_turn_id": turn_id,
        },
        event_type=event_type,
        message=f"{event_type} for {turn_id}",
        details={**(details or {}), "request_id": request_id},
    )


# ---------------------------------------------------------------------------------------------
# Binding: the pair, never "latest".
# ---------------------------------------------------------------------------------------------


def test_empty_identity_is_refused_not_guessed() -> None:
    with pytest.raises(ProofNotBound):
        build_turn_proof(session_id="", request_id="req-x")
    with pytest.raises(ProofNotBound):
        build_turn_proof(session_id="sess-x", request_id="")


def test_an_unbound_pair_is_refused_even_when_other_turns_exist() -> None:
    session = "proof-unbound-refusal"
    _bind_turn(session, "req-real")
    with pytest.raises(ProofNotBound):
        build_turn_proof(session_id=session, request_id="req-never-served")


def test_cross_session_evidence_is_impossible() -> None:
    """The request id is real -- but it was served in ANOTHER session."""
    session_a = "proof-cross-a"
    session_b = "proof-cross-b"
    _bind_turn(session_b, "req-of-b")
    with pytest.raises(ProofNotBound):
        build_turn_proof(session_id=session_a, request_id="req-of-b")


def test_a_bound_pair_projects_a_proof() -> None:
    session = "proof-bound-ok"
    _bind_turn(session, "req-bound")
    proof = build_turn_proof(session_id=session, request_id="req-bound")
    assert proof["schema"] == "vool.turn_proof.v1"
    assert proof["bound"] is True
    assert proof["session_id"] == session
    assert proof["request_id"] == "req-bound"
    assert proof["state"] in {
        STATE_VERIFIED,
        STATE_RECORDED,
        STATE_INCOMPLETE,
        STATE_UNVERIFIED,
    }


# ---------------------------------------------------------------------------------------------
# Evidence states.
# ---------------------------------------------------------------------------------------------


def test_verified_requires_the_finalization_verifier_to_have_passed() -> None:
    session = "proof-verified"
    turn = "turn-verified"
    answer = "2 + 2 is 4."
    _bind_turn(session, "req-verified", answer)
    _insert_finalization("req-verified", turn, answer, availability="available")

    proof = build_turn_proof(session_id=session, request_id="req-verified")
    assert proof["state"] == STATE_VERIFIED, proof.get("state_reasons")
    assert proof["expanded"]["finalization"]["content_hash_ok"] is True
    assert proof["expanded"]["finalization"]["finalization_id"].startswith("fc:")


def test_the_store_s_uppercase_availability_constant_verifies_the_same() -> None:
    """Found on the wire proof: `a7_finalizations.availability` stores the store's own uppercase
    constants (`AVAILABLE`), and a case-sensitive comparison mislabeled every live AVAILABLE row
    as `payload_unavailable`. The projection must normalize, and the hash check must run."""
    session = "proof-verified-upper"
    turn = "turn-verified-upper"
    answer = "Deterministic and true."
    _bind_turn(session, "req-verified-upper", answer)
    _insert_finalization("req-verified-upper", turn, answer, availability="AVAILABLE")

    proof = build_turn_proof(session_id=session, request_id="req-verified-upper")
    assert proof["state"] == STATE_VERIFIED, proof.get("state_reasons")
    assert proof["expanded"]["finalization"]["availability"] == "AVAILABLE"
    assert proof["expanded"]["finalization"]["content_hash_ok"] is True


def test_corrupted_finalization_bytes_are_recorded_never_verified() -> None:
    """The stored bytes no longer hash to the committed digest: rows exist, integrity fails."""
    session = "proof-corrupt"
    turn = "turn-corrupt"
    _bind_turn(session, "req-corrupt", "The honest answer.")
    _insert_finalization(
        "req-corrupt",
        turn,
        "The honest answer.",
        content_hash=_hash("TAMPERED BYTES"),
    )

    proof = build_turn_proof(session_id=session, request_id="req-corrupt")
    assert proof["state"] == STATE_RECORDED
    assert "content_hash_mismatch" in proof["state_reasons"]
    # Corrupt stored bytes are never served as if they were the answer.
    assert "canonical_content" not in proof["expanded"]["finalization"]


def test_unavailable_payload_is_recorded_with_its_reason() -> None:
    session = "proof-erased"
    turn = "turn-erased"
    _bind_turn(session, "req-erased", "To be erased.")
    _insert_finalization("req-erased", turn, "To be erased.", availability="erased")

    proof = build_turn_proof(session_id=session, request_id="req-erased")
    assert proof["state"] == STATE_RECORDED
    assert "payload_unavailable" in proof["state_reasons"]


def test_rows_without_a_finalization_are_recorded_not_verified() -> None:
    """A served turn with execution evidence but no governing finalization row (a legacy turn):
    the evidence is recorded, and nothing may dress it up as verified."""
    session = "proof-legacy"
    turn = "turn-legacy"
    _bind_turn(session, "req-legacy")
    _emit_turn_event(
        session,
        turn,
        "req-legacy",
        "tool_executed",
        {"tool_name": "workspace.read_file", "status": "ok"},
    )

    proof = build_turn_proof(session_id=session, request_id="req-legacy")
    assert proof["state"] == STATE_RECORDED
    assert "no_finalization_row" in proof["state_reasons"]
    assert proof["compact"]["actions"] >= 1


def test_a_started_retrieval_without_its_terminal_is_incomplete() -> None:
    """Expected terminal evidence missing: the fetch began and no completed/failed row exists."""
    session = "proof-incomplete"
    turn = "turn-incomplete"
    answer = "Weather answer."
    _bind_turn(session, "req-incomplete", answer)
    _insert_finalization("req-incomplete", turn, answer)
    _emit_turn_event(session, turn, "req-incomplete", "web_retrieval_started")

    proof = build_turn_proof(session_id=session, request_id="req-incomplete")
    assert proof["state"] == STATE_INCOMPLETE
    assert "missing_terminal" in proof["state_reasons"]


def test_a_witnessed_execution_missing_from_the_ledger_is_incomplete() -> None:
    """The independent witness saw an execution the authoritative ledger never recorded:
    the account is incomplete even though the finalization hash verifies."""
    session = "proof-witness"
    turn = "turn-witness"
    answer = "Answer with a tool."
    _bind_turn(session, "req-witness", answer)
    _insert_finalization("req-witness", turn, answer)
    _emit_turn_event(
        session,
        turn,
        "req-witness",
        "tool_executed",
        {"tool_name": "workspace.read_file", "status": "ok"},
    )
    # Sabotage-shaped evidence loss: the fact row vanishes while the witnessing event stream
    # still holds it. The projection must downgrade, not agree with the wounded ledger.
    conn = get_connection()
    try:
        conn.execute("DELETE FROM execution_facts WHERE turn_key = ?", (turn,))
        conn.commit()
    finally:
        conn.close()

    proof = build_turn_proof(session_id=session, request_id="req-witness")
    assert proof["state"] == STATE_INCOMPLETE
    assert "witness_inconsistent" in proof["state_reasons"]


def test_a_turn_with_no_evidence_at_all_is_unverified() -> None:
    session = "proof-unverified"
    _bind_turn(session, "req-unverified")

    proof = build_turn_proof(session_id=session, request_id="req-unverified")
    assert proof["state"] == STATE_UNVERIFIED
    assert proof["compact"]["actions"] == 0
    assert proof["compact"]["sources"] == 0


# ---------------------------------------------------------------------------------------------
# The chip's numbers belong to THIS turn only.
# ---------------------------------------------------------------------------------------------


def test_evidence_never_bleeds_between_turns_of_one_session() -> None:
    session = "proof-two-turns"
    _bind_turn(session, "req-early", "Early answer.")
    _insert_finalization("req-early", "turn-early", "Early answer.")
    _emit_turn_event(
        session,
        "turn-early",
        "req-early",
        "tool_executed",
        {"tool_name": "workspace.read_file", "status": "ok"},
    )
    _bind_turn(session, "req-late", "Late answer.")
    _insert_finalization("req-late", "turn-late", "Late answer.")

    early = build_turn_proof(session_id=session, request_id="req-early")
    late = build_turn_proof(session_id=session, request_id="req-late")
    assert early["compact"]["actions"] == 1
    assert late["compact"]["actions"] == 0


# ---------------------------------------------------------------------------------------------
# The expanded view: what the chip shows when opened.
# ---------------------------------------------------------------------------------------------


def test_expanded_view_carries_model_actions_sources_and_receipts() -> None:
    session = "proof-expanded"
    turn = "turn-expanded"
    answer = "It is 21 degrees and raining."
    _bind_turn(session, "req-expanded", answer)
    _insert_finalization("req-expanded", turn, answer)
    _emit_turn_event(
        session,
        turn,
        "req-expanded",
        "model_usage",
        {
            "model_id": "test-model",
            "provider_id": "test-provider",
            "input_tokens": 100,
            "output_tokens": 20,
            "source": "provider",
        },
    )
    _emit_turn_event(
        session,
        turn,
        "req-expanded",
        "tool_executed",
        {"tool_name": "live_data.weather_lookup", "status": "ok"},
    )
    _emit_turn_event(
        session,
        turn,
        "req-expanded",
        "web_retrieval_completed",
        {
            "receipts": [
                {
                    "operation": "weather_lookup",
                    "status": "available",
                    "url": "https://wttr.in/Kaunas",
                }
            ]
        },
    )
    _emit_turn_event(
        session,
        turn,
        "req-expanded",
        "tool_failed",
        {"tool_name": "workspace.write_file", "status": "refused"},
    )

    proof = build_turn_proof(session_id=session, request_id="req-expanded")
    expanded = proof["expanded"]

    assert expanded["model"] == "test-model"
    assert expanded["provider"] == "test-provider"
    # Typed actions carry outcomes; refusals are their own rows.
    names = {action["name"] for action in expanded["actions"]}
    assert "live_data.weather_lookup" in names
    assert "workspace.write_file" in names
    refused = [row for row in expanded["refusals"] if row["name"] == "workspace.write_file"]
    assert refused, "a refused execution must be a typed refusal row"
    # Sources come from the turn's own retrieval receipts.
    assert proof["compact"]["sources"] == 1
    assert expanded["sources"][0]["host"] == "wttr.in"
    # Cost is what the provider measured -- and nothing when nothing measured it.
    assert proof["compact"]["cost"]["tokens"] == 120
    # Available receipt references, ids only.
    receipts = expanded["receipts"]
    assert any(str(item).startswith("fc:") for item in receipts)
    assert any("live_data.weather_lookup" in str(item) for item in receipts)


def test_flat_retrieval_receipts_map_their_domains_as_sources() -> None:
    """The live wire shape: `web_retrieval_completed` carries a FLAT typed receipt
    (`vool.web_retrieval_receipt.v1`) whose `source_domains` are the retrieved sources —
    not a nested `receipts` list with URLs. Found on the wire proof 2026-09-02."""
    session = "proof-flat-receipt"
    turn = "turn-flat-receipt"
    answer = "Weather answer."
    _bind_turn(session, "req-flat", answer)
    _insert_finalization("req-flat", turn, answer)
    _emit_turn_event(
        session,
        turn,
        "req-flat",
        "web_retrieval_completed",
        {
            "schema": "vool.web_retrieval_receipt.v1",
            "action": "live_info_search",
            "status": "available",
            "provider_id": "brave",
            "source_domains": ["accuweather.com", "weather.gov"],
        },
    )

    proof = build_turn_proof(session_id=session, request_id="req-flat")
    sources = proof["expanded"]["sources"]
    assert sources, "the flat receipt must yield a source row"
    assert sources[0]["host"] == "accuweather.com"
    assert sources[0]["provider"] == "brave"
    assert sources[0]["status"] == "available"
    assert proof["compact"]["sources"] == 1


def test_elapsed_time_is_derived_from_the_turns_own_stamps_or_absent() -> None:
    session = "proof-elapsed"
    turn = "turn-elapsed"
    answer = "Answer."
    _bind_turn(session, "req-elapsed", answer)
    _insert_finalization("req-elapsed", turn, answer)
    _emit_turn_event(session, turn, "req-elapsed", "tool_executed", {"tool_name": "t", "status": "ok"})

    proof = build_turn_proof(session_id=session, request_id="req-elapsed")
    elapsed = proof["expanded"]["elapsed_ms"]
    assert elapsed is None or (isinstance(elapsed, int) and elapsed >= 0)


# ---------------------------------------------------------------------------------------------
# Redact at projection; never touch the stored row.
# ---------------------------------------------------------------------------------------------


def test_personal_paths_and_secrets_are_masked_in_the_projection_only() -> None:
    session = "proof-redact"
    turn = "turn-redact"
    answer = "Answer."
    secret_tool = "workspace.read_file"
    raw_status = "ok for /Users/example-user/secret-notes.txt key sk-live-abcdef1234567890"
    _bind_turn(session, "req-redact", answer)
    _insert_finalization("req-redact", turn, answer)
    _emit_turn_event(
        session,
        turn,
        "req-redact",
        "tool_executed",
        {"tool_name": secret_tool, "status": raw_status},
    )

    proof = build_turn_proof(session_id=session, request_id="req-redact")
    import json

    serialized = json.dumps(proof)
    assert "/Users/example-user" not in serialized
    assert "sk-live-abcdef1234567890" not in serialized
    # The STORED evidence is untouched by the projection: secrets are scrubbed at the WRITE
    # boundary (`append_runtime_event`), but the personal path persists in the store — and the
    # projection is the only place it is masked.
    facts = facts_for_turn(turn, session_id=session)
    assert "/Users/example-user/secret-notes.txt" in facts[0].status
    assert "/Users/example-user" not in serialized
