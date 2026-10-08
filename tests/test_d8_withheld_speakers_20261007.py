"""The absence gate publishes the speaker names of the records it refused, as withheld markers (D8, 2026-10-07).

"How much does James pay per dance class?" over a chat where James priced a cooking class: the gate refuses James's
row (dance never retained), the lane honours it (5459e0b2), and the publication gate then saw no row naming James
and shipped "needed current information" instead of "not mentioned in the records". The gate now records the
refused rows' speaker NAMES in telemetry (never their text); memory grounding publishes them as withheld markers
(summary = the name) that the names-someone check reads and that never become support rows. Every record and
question below was written for this file, except the James case the port's LoCoMo run exposed.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.context_retrieval as cr
from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
from core.grounding_publication import _support_rows, publication_verdict
from core.memory_grounding import memory_record_rows, question_names_someone_in_the_records
from tests.test_question_date_time_leg_20261002 import _hash_backend

RECORDS_LEAD = "That is not mentioned in the records I have from our conversations"
LIVE_LEAD = "I can't publish an answer to this: it needed current information"
TIM = ("<retrieved_context>\n- user said (stated 2024-01-07): Session date: 7 January, 2024\n"
       "Tim: Great news - I'm finally in the study abroad program I applied for! Next month, I'm off to Ireland.\n"
       "</retrieved_context>")
JAMES_MARKER = {"summary": "James", "withheld_speaker": "James", "withheld": True}


def _lifecycle(question: str, rows):
    return GroundingLifecycle(lifecycle_id="withheld-" + question[:12], identity=TurnIdentity(),
                              request_text=question, model_authored=True, memory_records=tuple(rows))


def test_a_records_question_whose_speaker_was_withheld_gets_the_records_refusal():
    rows = [*memory_record_rows(TIM), JAMES_MARKER]
    verdict = publication_verdict(_lifecycle("How much does James pay per dance class?", rows), "James pays $25 per dance class.")
    assert verdict.state in {"refused", "failed"}, verdict
    assert verdict.content.startswith(RECORDS_LEAD), verdict.content
    assert "$25" not in verdict.content and "current information" not in verdict.content


def test_a_live_question_in_a_chat_with_unrelated_records_keeps_the_live_notice():
    rows = [*memory_record_rows(TIM), JAMES_MARKER]
    verdict = publication_verdict(_lifecycle("When does the next train to Vilnius leave?", rows),
                                  "The next train to Vilnius leaves at 14:05 from platform 3.")
    assert verdict.content.startswith(LIVE_LEAD), verdict.content


def test_a_question_naming_someone_who_never_speaks_keeps_the_live_notice():
    rows = [*memory_record_rows(TIM), JAMES_MARKER]
    verdict = publication_verdict(_lifecycle("How much does Olga pay per pottery class?", rows), "Olga pays $30 per class.")
    assert verdict.content.startswith(LIVE_LEAD), verdict.content


def test_a_records_question_with_its_rows_retrieved_behaves_as_before():
    james = ("<retrieved_context>\n- user said (stated 2022-05-01): Session date: 1 May, 2022\n"
             "James: At only $10 per class, it's very cheap! Also, I made meringue there.\n</retrieved_context>")
    verdict = publication_verdict(_lifecycle("How much does James pay per dance class?", memory_record_rows(james)),
                                  "James pays $25 per dance class.")
    assert verdict.content.startswith(RECORDS_LEAD), verdict.content


def test_a_withheld_marker_is_never_a_support_row_and_carries_the_name_only():
    rows = [*memory_record_rows(TIM), JAMES_MARKER]
    support, origin = _support_rows(_lifecycle("How much does James pay per dance class?", rows))
    assert all(not row.get("withheld") for row in support), support
    assert all("James" not in str(row.get("summary")) for row in support), support
    assert JAMES_MARKER["summary"] == "James"
    assert question_names_someone_in_the_records("How much does James pay per dance class?", rows)
    assert not question_names_someone_in_the_records("When does the next train to Vilnius leave?", rows)


def _store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy

    profile = tmp_path / "home"; profile.mkdir(parents=True, exist_ok=True)
    for name in ("NULLA_HOME", "VOOL_HOME"):
        monkeypatch.setenv(name, str(profile))
    for name in ("NULLA_WORKSPACE_ROOT", "VOOL_WORKSPACE_ROOT"):
        monkeypatch.setenv(name, str(profile / "workspace"))
    ensure_chat_namespace("class-chat", grant_current_receipts=False)
    policy = resolve_memory_access_policy(chat_id="class-chat")
    for body in ("Session date: 2022/05/01\nJames: Yes, two days ago I signed up for a cooking class. At only $10 per class, it's very cheap!",
                 "Session date: 2024/01/07\nTim: Great news, I'm finally in the study abroad program. Next month I'm off to Ireland."):
        receipt = cr.store_turn("class-chat", body, "Noted.", access_policy=policy,
                                source_context={"runtime_home": str(profile), "statement_at": 1767600000.0})
        assert receipt["status"] in ("stored", "retained"), receipt
    return profile


@pytest.mark.usefixtures("_hash_backend")
def test_the_gate_publishes_the_refused_speaker_and_never_the_text(tmp_path, monkeypatch):
    from core.memory.entries import resolve_memory_access_policy

    profile = _store(tmp_path, monkeypatch)
    question = "How much does James pay per dance class?"
    messages = cr.inject_retrieved("class-chat", question, [{"role": "user", "content": question}],
                                   access_policy=resolve_memory_access_policy(chat_id="class-chat"),
                                   source_context={"runtime_home": str(profile), "chat_id": "class-chat"},
                                   env={"NULLA_CONTEXT_CAPSULE_V2": "1", "VOOL_CONTEXT_CAPSULE_V2": "1"})
    block = "\n".join(m["content"] for m in messages if m["role"] == "system")
    telemetry = cr.get_last_retrieval_telemetry()
    assert "James" in (telemetry.get("absence_gate_withheld_speakers") or []), telemetry.get("absence_gate_withheld_speakers")
    # the refused record's text is in neither the distilled section nor the lane; the receipts packet (kernel on) is
    # the compiler's and does not face the absence gate today (triage item 17, pre-existing at ec0f46b6)
    distilled_and_lane = block.split("Evidence receipts (", 1)[0] + (
        block.split(cr._TURN_LANE_HEADER, 1)[1] if cr._TURN_LANE_HEADER in block else "")
    assert "cooking" not in distilled_and_lane and "$10" not in distilled_and_lane, block
