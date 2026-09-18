"""A pasted document is the turn's material: no lane may send the turn to the web behind it.

Measured live on the isolated daemon (validation-logs/long-input-document-p1-20260902/
nemotron_free_2_result.json): "In the log I pasted, which order failed ...?" arrived with the
document bound and its exact text in the evidence, the requirements authority read the text as
DIRECT (stable knowledge), and then the adaptive-research lane's private recognizer proposed a
search, the authority ESCALATED the turn to current-information on that claim, three web
retrievals ran (apo24.ch, owl.purdue.edu), the answering call was handed those pages as the
evidence, and the publication gate refused the answer because nothing "retrieved" supported it.
The document -- the only source the user asked about -- was released as `ignored`.

The law pinned here: `user_material_supplied` sees the ONE attachment authority's evidence
(the door's own items, never a client field), and a lane's escalation to current-information
declines on a turn whose material the user supplied unless the authority's OWN reading of the
text is current. The decline is recorded as a typed refusal, never silent. The text's own
reading still governs: a pasted document plus "what is the weather in Rome now" is a live-data
turn exactly as before.
"""

from __future__ import annotations

import pytest

from core import chat_attachments as ca
from core import runtime_paths
from core.execution_requirements import (
    current_requirement_record,
    escalate_current_requirement,
    requirements_for,
)
from core.retrieval_authority_gate import authorize_retrieval

SESSION = "openclaw:d0c0d0c0d0c0d0c0eeee"
QUESTION = "In the log I pasted, which order failed, with what error, and what magic token was issued on the retry? Quote the exact lines."


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    yield
    runtime_paths.configure_runtime_home(None)


def _document_turn_context(turn: str = "turn-1") -> dict:
    """The context the door builds for a turn carrying one pasted document."""
    doc = ca.stage_document(session_id=SESSION, data=("2026-09-02T10:42:17Z ERROR ORDER-7731 failed with ECONNRESET\n" * 200).encode())
    ca.bind_to_turn(session_id=SESSION, turn_id=turn, attachment_ids=[doc["id"]])
    return {
        "runtime_session_id": SESSION,
        "attachment_turn_id": turn,
        "external_evidence": ca.evidence_items_for_turn(session_id=SESSION, turn_id=turn),
    }


def test_the_doors_document_evidence_counts_as_user_material() -> None:
    context = _document_turn_context()
    requirements = requirements_for(QUESTION, source_context=context)
    assert requirements.user_material_supplied is True
    assert "user_material_supplied" in requirements.reason_codes
    assert requirements.answer_mode == "DIRECT" and requirements.current_information_required is False
    # The door's items alone are material, before any session lookup: a context that names no
    # session (a sub-turn, a test scope) still reads the authority's own evidence.
    door_only = {"external_evidence": list(context["external_evidence"])}
    assert requirements_for(QUESTION, source_context=door_only).user_material_supplied is True
    # A client cannot mint the flag: an evidence item without the authority's id is not material.
    forged = {"external_evidence": [{"kind": "text", "origin": "chat_attachment", "document": True, "text": "x"}]}
    assert requirements_for(QUESTION, source_context=forged).user_material_supplied is False


def test_a_lane_claim_cannot_send_a_document_turn_to_the_web() -> None:
    context = _document_turn_context()
    requirements_for(QUESTION, source_context=context)
    widened = escalate_current_requirement(context, text=QUESTION, source="adaptive_research", reason_code="lane:adaptive_research")
    assert widened is False, "the lane's claim escalated a turn whose material the user supplied"
    record = current_requirement_record(context)
    assert record is not None and record.requirements.current_information_required is False
    assert any(code.endswith("user_material_declines:adaptive_research") for code in record.requirements.reason_codes), record.requirements.reason_codes
    # The canonical door the lanes call answers the same, and leaves a typed refusal behind.
    assert authorize_retrieval(context, QUESTION, lane="adaptive_research", proposal_reason="adaptive_research:initial_search") is False
    refusals = context.get("retrieval_authority_refusals") or []
    assert refusals and refusals[-1]["lane"] == "adaptive_research" and refusals[-1]["retrieval_started"] is False
    assert refusals[-1]["reason_code"] == "user_material_supplied"


def test_without_supplied_material_a_lane_claim_still_escalates_as_before() -> None:
    context = {"runtime_session_id": SESSION}
    requirements_for(QUESTION, source_context=context)
    assert escalate_current_requirement(context, text=QUESTION, source="adaptive_research", reason_code="lane:adaptive_research") is True
    assert current_requirement_record(context).requirements.current_information_required is True


def test_the_texts_own_current_reading_still_governs_with_a_document_present() -> None:
    context = _document_turn_context()
    live = requirements_for("what is the weather in Rome right now?", source_context=context)
    assert live.current_information_required is True and live.user_material_supplied is True
    assert authorize_retrieval(context, "what is the weather in Rome right now?", lane="adaptive_research") is True


def test_the_chats_retained_documents_count_as_material_on_later_turns() -> None:
    context = _document_turn_context()
    ca.release_turn(session_id=SESSION, turn_id="turn-1", outcomes=None)
    later = {"runtime_session_id": SESSION}
    requirements = requirements_for("Without me pasting it again: how many seconds passed between the failure and the retry in that log?", source_context=later)
    assert requirements.user_material_supplied is True
    assert "chat_documents_carried" in requirements.reason_codes
    assert escalate_current_requirement(later, text="that log again", source="adaptive_research", reason_code="lane:adaptive_research") is False
    # Another chat has no such material.
    other = {"runtime_session_id": "openclaw:d0c0d0c0d0c0d0c0ffff"}
    assert requirements_for("how many seconds passed?", source_context=other).user_material_supplied is False
    assert context  # the first turn's context is untouched by the later reading
