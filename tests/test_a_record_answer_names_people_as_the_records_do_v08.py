"""An answer from the person's own records is read against them the way the records are written.

Served on the real app (cloud route, web lookups off, 2026-11-19) with "my brother Tomas's favourite character from
Lord of the Rings is Aragorn" saved and the question "According to my brother Tomas, who is his favourite character
from Lord of the Rings?":

- one model wrote "Aragorn — that's what I've got saved for Tomas." and the reply delivered was "That is not mentioned
  in the records I have from our conversations, so I am not going to state one.". The claim check looked for the name
  Tomas in the record and did not find it, because the record writes it as a possessive ("Tomas's"); and it read
  "I've" as the name of something no record mentions;
- the other wrote "According to your brother Tomas, his favourite character from Lord of the Rings is Aragorn." and it
  went out with "Web lookups are off, so this is from what I already know and may be out of date." under it, although
  the answer came from the saved fact.

Pinned beside it: a name the record does not hold is still refused, a longer word is not the name, another apostrophe
word is not the name, a name with a contraction is still checked, and a live reading keeps its web-off note. The
replies are the served sentences; the other names are invented here.
"""
from __future__ import annotations

import pytest

from core.claim_support import match_claims
from core.grounding_lifecycle import GroundingLifecycle, TurnIdentity
from core.grounding_publication import publication_verdict
from core.memory_grounding import own_record_rows
from tests.test_everyday_remember_v08 import _say
from tests.test_keep_receipt_decides_save_claims_v08 import CHAT, _fresh  # _fresh is a fixture
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # isolated_home is a fixture

__all__ = ["_fresh", "isolated_home"]

TOMAS_ASK = "According to my brother Tomas, who is his favourite character from Lord of the Rings?"
TOMAS_ROW = {"summary": "- user said: my brother Tomas's favourite character from Lord of the Rings is Aragorn", "source": "memory_record"}
SERVED_SHORT = "Aragorn — that's what I've got saved for Tomas."
SERVED_LONG = "According to your brother Tomas, his favourite character from Lord of the Rings is Aragorn."
OWNER = {"chat_id": CHAT, "_owner_local": True}


def _statuses(answer, rows, request):
    return [(claim.status, claim.reasons) for claim in match_claims(answer=answer, notes=rows, request_text=request).claims]


def _lifecycle(request, rows):
    return GroundingLifecycle(lifecycle_id="record-answer-turn", identity=TurnIdentity(), request_text=request,
                              retrieval_outcome="no_sources", model_authored=True, memory_records=tuple(rows))


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


# ------------------------------------------------------------ the name in the record


def test_the_served_short_answer_is_supported_by_the_saved_fact():
    assert _statuses(SERVED_SHORT, [TOMAS_ROW], TOMAS_ASK) == [("supported", ("entity_match",))]


def test_the_served_short_answer_is_published_at_the_gate(saved):
    rows = own_record_rows(OWNER, CHAT, question=TOMAS_ASK)
    verdict = publication_verdict(_lifecycle(TOMAS_ASK, rows), SERVED_SHORT)
    assert verdict.state == "published", verdict
    assert verdict.content == SERVED_SHORT


def test_a_name_the_record_does_not_hold_is_still_refused(saved):
    rows = own_record_rows(OWNER, CHAT, question=TOMAS_ASK)
    verdict = publication_verdict(_lifecycle(TOMAS_ASK, rows), "Gandalf — that's what I've got saved for Tomas.")
    assert verdict.state == "refused", verdict
    assert "Gandalf" not in verdict.content


def test_a_longer_word_in_the_record_is_not_the_name():
    row = {"summary": "- user said: my colleague Tomasz's favourite character from Lord of the Rings is Aragorn"}
    assert _statuses(SERVED_SHORT, [row], TOMAS_ASK) == [("unsupported", ("subject_absent_from_source",))]


def test_a_possessive_in_the_record_carries_a_name_the_answer_brings():
    row = {"summary": "- user said: Don's van will carry the ladder on Saturday"}
    assert _statuses("Don is bringing the ladder.", [row], "Who is bringing the ladder?") == [("supported", ("entity_match",))]


def test_another_apostrophe_word_in_the_record_is_not_the_name():
    row = {"summary": "- user said: I don't know who will carry the ladder on Saturday"}
    statuses = _statuses("Don is bringing the ladder.", [row], "Who is bringing the ladder?")
    assert statuses == [("unsupported", ("entity_absent_from_source",))]


# ------------------------------------------------------------ a contraction names nobody new


def test_a_first_person_contraction_is_not_read_as_a_name():
    answer = f"{SERVED_LONG} I've got that saved from what you told me earlier."
    statuses = _statuses(answer, [TOMAS_ROW], TOMAS_ASK)
    assert statuses[0][0] == "supported"
    assert statuses[1] == ("uncertain", ("no_structural_overlap",))


def test_a_name_with_a_contraction_is_still_checked_against_the_records():
    ask = "Who is bringing the ladder?"
    named = {"summary": "- user said: James signed up to carry the ladder on Saturday"}
    other = {"summary": "- user said: the ladder is in the shed until Saturday"}
    assert _statuses("James'll bring the ladder.", [named], ask) == [("supported", ("entity_match",))]
    assert _statuses("James'll bring the ladder.", [other], ask) == [("unsupported", ("entity_absent_from_source",))]


# ------------------------------------------------------------ the web-off note


def test_an_answer_from_a_saved_fact_is_not_labelled_as_possibly_out_of_date(saved):
    from core.agent_runtime.turn_reasoning import _asks_about_something_that_changes
    from core.execution_requirements import requirements_for

    requirements = requirements_for(TOMAS_ASK, source_context=dict(OWNER))
    assert not _asks_about_something_that_changes(TOMAS_ASK, requirements, dict(OWNER), session_id=CHAT)


def test_without_the_saved_fact_the_same_question_keeps_the_label(isolated_home):
    from core.agent_runtime.turn_reasoning import _asks_about_something_that_changes
    from core.execution_requirements import requirements_for

    _policy(CHAT)
    requirements = requirements_for(TOMAS_ASK, source_context=dict(OWNER))
    assert _asks_about_something_that_changes(TOMAS_ASK, requirements, dict(OWNER), session_id=CHAT)


def test_a_live_reading_keeps_the_label_even_when_a_saved_fact_names_the_place(isolated_home):
    from core.agent_runtime.turn_reasoning import _asks_about_something_that_changes
    from core.execution_requirements import requirements_for

    assert _say("Remember that my sister Rasa lives in Kaunas", "seed")[1].startswith("Locked in")
    _policy(CHAT)
    ask = "What is the weather in Kaunas right now?"
    requirements = requirements_for(ask, source_context=dict(OWNER))
    assert _asks_about_something_that_changes(ask, requirements, dict(OWNER), session_id=CHAT)
