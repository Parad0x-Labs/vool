"""What the person said earlier in this chat, and the saved facts it may read, are records at the grounding gate.

Measured on the real app (cloud route, web lookups off, two cloud models): "My friend Ines told me she is leaving for
Ireland next month." then, in the same chat, "When will Ines leave for Ireland?" was answered "Next month" by the model
and the reply delivered was "Web lookups are off, so I can't look this up online. ..."; with "my brother Tomas's
favourite character from Lord of the Rings is Aragorn" saved, "According to my brother Tomas, who is his favourite
character from Lord of the Rings?" got the same line with no model call.

The gate's memory rows were read from the retrieved capsule only. An earlier message of the chat in hand travels as a
history message and a saved fact as context, so the gate held no row for either. They are now rows of the same kind,
matched claim by claim as before.

Pinned beside it: the assistant's lines are never rows, the asking message is no record of its own answer, a forgotten
line or an unreadable forget ledger offers nothing, what the rows do not state is still withheld, and a chat that names
the person does not make a timetable its record. All names and facts are invented here.
"""
from __future__ import annotations

import pytest

import core.context_retrieval as cr
from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity, _record_for, register_required
from core.grounding_publication import _support_rows, publication_verdict
from core.memory_grounding import (
    own_record_rows,
    publish_memory_records_for_turn,
    question_names_someone_in_the_records,
)
from tests.test_everyday_remember_v08 import _say
from tests.test_keep_receipt_decides_save_claims_v08 import CHAT, _fresh  # _fresh is a fixture
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

__all__ = ["_fresh", "isolated_home"]

SAID = "My friend Ines told me she is leaving for Ireland next month."
ASK = "When will Ines leave for Ireland?"
TOMAS_ASK = "According to my brother Tomas, who is his favourite character from Lord of the Rings?"


def _context(*turns, ask=ASK, chat="chat-own-words"):
    history = []
    for user, assistant in turns:
        history.append({"role": "user", "content": user})
        if assistant:
            history.append({"role": "assistant", "content": assistant})
    history.append({"role": "user", "content": ask})
    return {"chat_id": chat, "_owner_local": True, "conversation_history": history}


def _summaries(rows):
    return [row["summary"] for row in rows]


@pytest.fixture(autouse=True)
def _forget_ledger(request):
    """A served profile has its forget ledger from boot; without one the readers offer nothing."""
    if "isolated_home" in request.fixturenames:
        from core.memory.revocation import initialize_memory_home

        initialize_memory_home(request.getfixturevalue("isolated_home"))
    yield


@pytest.fixture
def saved(isolated_home):
    assert _say("Remember that my brother Tomas's favourite character from Lord of the Rings is Aragorn", "seed")[1].startswith("Locked in")
    _policy(CHAT)
    return isolated_home


# ------------------------------------------------------------ the rows


def test_the_persons_earlier_message_is_a_row_and_the_asking_message_is_not(isolated_home):
    rows = own_record_rows(_context((SAID, "That's big news!")), question=ASK)
    assert _summaries(rows) == [f"- user said: {SAID}"]
    assert all(row["source"] == "memory_record" for row in rows)


def test_the_assistants_lines_are_never_rows(isolated_home):
    context = _context(("what time is the ferry?", "The ferry to Dublin leaves at 08:55."), ask="When does the ferry leave?")
    assert "08:55" not in " ".join(_summaries(own_record_rows(context, question="When does the ferry leave?")))


def test_a_two_line_asking_message_is_still_not_a_row(isolated_home):
    ask = "Answer from what I told you.\nWhen will Ines leave for Ireland?"
    rows = own_record_rows(_context((SAID, ""), ask=ask), question="Answer from what I told you. When will Ines leave for Ireland?")
    assert _summaries(rows) == [f"- user said: {SAID}"]


def test_with_no_question_given_the_last_message_is_left_out(isolated_home):
    assert _summaries(own_record_rows(_context((SAID, "")))) == [f"- user said: {SAID}"]


def test_a_forgotten_line_is_not_a_row(isolated_home, monkeypatch):
    monkeypatch.setattr(cr, "revoked_tokens_for_chat", lambda *_a, **_k: ("ireland",))
    assert own_record_rows(_context((SAID, "")), question=ASK) == []


def test_an_unreadable_forget_ledger_offers_nothing(isolated_home, monkeypatch):
    def unreadable(*_a, **_k):
        raise RuntimeError("ledger unreadable")

    monkeypatch.setattr(cr, "revoked_tokens_for_chat", unreadable)
    assert own_record_rows(_context((SAID, "")), question=ASK) == []


def test_a_saved_fact_the_chat_may_read_is_a_row(saved):
    rows = own_record_rows({"chat_id": CHAT, "_owner_local": True}, CHAT, question=TOMAS_ASK)
    assert any("Aragorn" in summary and "Tomas" in summary for summary in _summaries(rows)), rows


def test_no_chat_and_no_history_gives_no_rows():
    assert own_record_rows({}, question=ASK) == []
    assert own_record_rows(None) == []


# ------------------------------------------------------------ at the gate


def _lifecycle(request, rows):
    return GroundingLifecycle(lifecycle_id="own-words-turn", identity=TurnIdentity(), request_text=request,
                              retrieval_outcome="no_sources", model_authored=True, memory_records=tuple(rows))


def test_an_answer_the_persons_own_message_states_is_published(isolated_home):
    rows = own_record_rows(_context((SAID, "")), question=ASK)
    verdict = publication_verdict(_lifecycle(ASK, rows), "Next month.")
    assert verdict.state == "published", verdict
    assert verdict.support_origin == "memory_record"


def test_without_the_row_the_same_answer_is_refused():
    verdict = publication_verdict(_lifecycle(ASK, []), "Next month.")
    assert verdict.state in {"refused", "failed"}, verdict


def test_a_time_the_message_does_not_state_is_still_withheld(isolated_home):
    ask = "According to Ines, when does the ferry from Holyhead to Dublin leave tomorrow?"
    rows = own_record_rows(_context((SAID, ""), ask=ask), question=ask)
    verdict = publication_verdict(_lifecycle(ask, rows), "The ferry from Holyhead to Dublin leaves at 08:55 tomorrow.")
    assert "08:55" not in verdict.content, verdict


def test_an_answer_a_saved_fact_states_is_published(saved):
    rows = own_record_rows({"chat_id": CHAT, "_owner_local": True}, CHAT, question=TOMAS_ASK)
    verdict = publication_verdict(_lifecycle(TOMAS_ASK, rows), "Aragorn.")
    assert verdict.state == "published", verdict


def test_the_turn_publishes_its_own_words_to_its_lifecycle(isolated_home):
    context = {**_context((SAID, "")), "turn_id": "t-own-1", "request_id": "r-own-1"}
    register_required(context, request_text=ASK, reason_codes=("current_info_signal:schedule_lookup",))
    assert publish_memory_records_for_turn(context) == 1
    rows, origin = _support_rows(_record_for(context))
    assert origin == "memory_record"
    assert _summaries(rows) == [f"- user said: {SAID}"]
    assert publish_memory_records_for_turn(context) == 0


# ------------------------------------------------------------ the front door's test of the same rows


def test_the_ask_names_someone_the_persons_own_message_names(isolated_home):
    assert question_names_someone_in_the_records(ASK, own_record_rows(_context((SAID, "")), question=ASK))


def test_the_ask_names_someone_a_saved_fact_names(saved):
    assert question_names_someone_in_the_records(TOMAS_ASK, own_record_rows({"chat_id": CHAT, "_owner_local": True}, CHAT, question=TOMAS_ASK))


def test_an_ask_about_a_name_no_record_holds_names_no_one(isolated_home):
    ask = "When does the next train to Vilnius leave?"
    assert not question_names_someone_in_the_records(ask, own_record_rows(_context((SAID, ""), ask=ask), question=ask))
