"""An update replaces one saved fact with a clean row and keeps the old value as history.

Measured on the v0.8 line (94ce6169, the real local model, a fresh home; step 3 of the memory and context fix,
2026-11-17): "Remember that my pottery class is on Wednesdays and the teacher is called Ottoline", then "Small
change: my pottery class is on Saturdays now", stored "my pottery class is on Saturdays now (this replaces: my
pottery class is on Wednesdays and the teacher is called Ottoline)": the old value rode inside the new row and the
teacher lived on only in that tail. The next update of the same fact marked both rows `disputed`, so no current
value was left. "Update: my bin collection is on Fridays now" was nobody's turn (the correction command's lead, no
value of its kind) and the saved Tuesdays stayed. "Which day was my pottery class before it moved?" was answered
with a day never said. With the model endpoint dead, a fact kept in passing was not mentioned in the reply.

All names, places, days and dates here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core.action_claim_gate import gate_action_claims
from core.agent_runtime.cross_chat_recall import replaced_values_answer
from core.memory import everyday_remember as everyday
from core.memory import keep_receipt
from core.memory.entries import list_memory_entries, list_replaced_entries, memory_entries_path
from core.memory.files import load_jsonl
from core.persistent_memory import (
    _correction_command_takes,
    append_conversation_event,
    keep_stated_facts,
    maybe_handle_memory_command,
)
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home  # noqa: F401  (isolated_home is a fixture)

CHAT = "update-history-chat"
LATER_CHAT = "update-history-later"


def _say(text: str, chat: str = CHAT) -> tuple[bool, str]:
    handled, reply = maybe_handle_memory_command(text, session_id=chat, access_policy=_policy(chat))
    append_conversation_event(
        session_id=chat, user_input=text, assistant_output=str(reply or "ok"), persist_now=True,
        access_policy=_policy(chat),
    )
    return handled, str(reply or "")


def _facts(chat: str = CHAT) -> list[str]:
    rows = list_memory_entries(access_policy=_policy(chat), limit=200)
    return [str(row.get("fact") or row.get("text") or "") for row in rows]


def _raw() -> list[dict]:
    return [row for row in load_jsonl(memory_entries_path()) if str(row.get("text") or "").strip()]


# --- the row an update writes ----------------------------------------------------------------------------------


def test_an_update_writes_a_clean_row_and_the_old_row_becomes_its_history(isolated_home):
    assert _say("Remember that my fencing practice is on Tuesdays and the coach is called Ingrid.")[0]
    handled, reply = _say("Small change: my fencing practice is on Thursdays now.")
    assert handled
    assert reply.startswith("Updated. I now have: my fencing practice is on Thursdays now.")
    assert "Still saved with it: the coach is called Ingrid." in reply
    assert _facts() == ["my fencing practice is on Thursdays now; the coach is called Ingrid"]
    rows = _raw()
    assert not any("this replaces" in str(row["text"]) for row in rows)
    current = [row for row in rows if row["status"] == "active"]
    old = [row for row in rows if row["status"] == "superseded"]
    assert len(current) == 1 and len(old) == 1
    assert old[0]["text"].startswith("my fencing practice is on Tuesdays and the coach is called Ingrid")
    assert old[0]["superseded_record_id"] == current[0]["record_id"]


def test_a_second_update_leaves_one_current_row_and_none_disputed(isolated_home):
    assert _say("Remember that my fencing practice is on Tuesdays and the coach is called Ingrid.")[0]
    assert _say("Small change: my fencing practice is on Thursdays now.")[0]
    handled, reply = _say("My fencing practice is moving to Sundays.")  # no change word: the value restates the fact
    assert handled and reply.startswith("Updated. I now have: My fencing practice is moving to Sundays.")
    assert _facts() == ["My fencing practice is moving to Sundays; the coach is called Ingrid"]
    statuses = sorted(row["status"] for row in _raw())
    assert statuses == ["active", "superseded", "superseded"]
    by_id = {row["record_id"]: row for row in _raw()}
    current = next(row for row in by_id.values() if row["status"] == "active")
    second = next(row for row in by_id.values() if row.get("superseded_record_id") == current["record_id"])
    first = next(row for row in by_id.values() if row.get("superseded_record_id") == second["record_id"])
    assert "Thursdays" in second["text"] and "Tuesdays" in first["text"]


def test_an_update_in_a_two_fact_row_replaces_the_fact_it_speaks_about(isolated_home):
    assert _say("Remember that my vet is Dr Halloran and her surgery is on Pell lane.")[0]
    handled, _ = _say("my vet moved, the surgery is on Orchard row now.", chat=LATER_CHAT)
    assert handled
    assert _facts(LATER_CHAT) == ["my vet moved, the surgery is on Orchard row now; my vet is Dr Halloran"]


def test_a_plain_update_lead_reaches_the_update_and_the_correction_command_keeps_its_own(isolated_home):
    assert _say("Remember that my recycling is collected on Mondays.")[0]
    handled, reply = _say("Update: my recycling is collected on Fridays now.")
    assert handled and reply.startswith("Updated. I now have: my recycling is collected on Fridays now.")
    assert _facts() == ["my recycling is collected on Fridays now"]
    assert not _correction_command_takes("Update: my recycling is collected on Fridays now.")
    assert _correction_command_takes("update the route code to KX-22-ALPHA")


def test_an_update_to_nothing_saved_writes_nothing(isolated_home):
    assert _say("Remember that my recycling is collected on Mondays.")[0]
    handled, _ = _say("Update: my scout hut is number 14 now.")
    assert not handled
    assert _facts() == ["my recycling is collected on Mondays."]


# --- a new value with no change word ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("statement", "saved"),
    [
        ("My fencing practice is moving to Sundays.", "my fencing practice is on Thursdays now"),
        ("my fencing practice is on Sundays", "my fencing practice is on Thursdays now"),
        ("my shed padlock is 4471", "my shed padlock is 2209"),
        ("our ferry is on 14 August", "our ferry is on 9 August"),
        ("my swim slot is at 7:15", "my swim slot is at 6:45"),
    ],
)
def test_the_same_subject_with_a_different_value_of_the_same_kind_restates_the_fact(statement, saved):
    assert everyday.restates(statement, saved)


@pytest.mark.parametrize(
    ("statement", "saved"),
    [
        ("my sister is a nurse", "my sister is 34"),  # another thing about her, no value of that kind
        ("my flat is 80 square metres", "my flat is number 12"),  # both numbers, said about different things
        ("my ferry back is on 14 August", "my ferry is on 9 August"),  # another subject
        ("my rent is due on 3 May", "my rent is 900"),  # a date is not a new amount
        ("my fencing practice is on Thursdays", "my fencing practice is on Thursdays now"),  # the same value
        ("my fencing practice is on Sundays", "the coach is called Ingrid"),
    ],
)
def test_another_subject_another_kind_or_the_same_value_does_not_restate(statement, saved):
    assert not everyday.restates(statement, saved)


@pytest.mark.parametrize(
    "text",
    ["which day is my fencing practice on Sundays?", "can you check my practice is on Sundays", "I have 3 cats",
     "my son is 7 and my daughter is 9"],
)
def test_a_question_a_request_and_a_two_fact_statement_are_not_read_as_a_restatement(text):
    assert not everyday.may_restate(text)


def test_a_new_value_said_in_passing_replaces_the_saved_one(isolated_home):
    assert _say("Remember that my fencing practice is on Tuesdays.")[0]
    with keep_receipt.collecting() as receipt:
        kept = keep_stated_facts(
            "My fencing practice is on Fridays, by the way. What stretches help with lunges?",
            session_id=CHAT, access_policy=_policy(CHAT),
        )
    assert kept == 1 and receipt.saved == ["My fencing practice is on Fridays"]
    assert _facts() == ["My fencing practice is on Fridays"]
    assert sorted(row["status"] for row in _raw()) == ["active", "superseded"]


# --- the facts of one row --------------------------------------------------------------------------------------


def test_a_row_is_split_into_its_facts_and_a_list_of_things_is_not():
    assert everyday.fact_clauses("my vet is Dr Halloran and her surgery is on Pell lane.") == [
        "my vet is Dr Halloran", "her surgery is on Pell lane",
    ]
    assert everyday.fact_clauses("we need bread and milk") == ["we need bread and milk"]
    assert everyday.fact_clauses("our book club meets on thursdays at the library") == [
        "our book club meets on thursdays at the library"
    ]
    assert everyday.clauses_kept("our book club meets on thursdays at the library", "book club moved to wednesdays") == []


# --- history and forget ----------------------------------------------------------------------------------------


def test_what_it_was_before_is_read_from_the_replaced_rows_newest_first(isolated_home):
    assert _say("Remember that my fencing practice is on Tuesdays.")[0]
    assert _say("Small change: my fencing practice is on Thursdays now.")[0]
    assert _say("My fencing practice is moving to Sundays.")[0]
    assert [row["text"] for row in list_replaced_entries(access_policy=_policy(LATER_CHAT))] == [
        "my fencing practice is on Tuesdays.", "my fencing practice is on Thursdays now",
    ]
    answer = replaced_values_answer(
        "Which day was my fencing practice before it changed?", session_id=LATER_CHAT, source_context=None,
    )
    assert answer.startswith("Before the last change, I had saved:\n- my fencing practice is on Thursdays now")
    assert "Earlier than that:\n- my fencing practice is on Tuesdays." in answer
    assert "Sundays" not in answer
    # A question about the value as it is, and a question about a fact with no history, are not history questions.
    assert replaced_values_answer("Which day is my fencing practice?", session_id=LATER_CHAT, source_context=None) == ""
    assert replaced_values_answer("What was my locker number before?", session_id=LATER_CHAT, source_context=None) == ""


def test_a_forget_takes_the_replaced_values_with_the_fact(isolated_home):
    assert _say("Remember that my recycling is collected on Mondays.")[0]
    assert _say("Update: my recycling is collected on Fridays now.")[0]
    handled, reply = _say("Forget my recycling")
    assert handled, reply
    left = [row["text"] for row in _raw() if row.get("status") != "erased"]
    assert not any("recycling" in text for text in left), left
    assert list_replaced_entries(access_policy=_policy(CHAT)) == []


# --- a reply no model wrote says what was kept -------------------------------------------------------------------


NOTICE = "I couldn't get an answer this time: the model could not be reached. Nothing was made up in its place. You can try again."


def test_a_runtime_notice_names_the_fact_the_keep_step_saved_in_passing():
    receipt = keep_receipt.KeepReceipt(saved=["My harp lesson is on 6 October"], in_passing=True)
    context: dict = {}
    keep_receipt.stamp(context, receipt)
    published, record = gate_action_claims(NOTICE, turn_id="notice-kept", source_context=context)
    assert published == NOTICE + '\n\nSaved to memory: "My harp lesson is on 6 October".'
    assert record["memory_receipt"] == "kept_line_added"
    again, _ = gate_action_claims(published, turn_id="notice-kept", source_context=context)
    assert again == published  # said once


def test_a_runtime_reply_gets_no_line_without_a_write_in_passing():
    context: dict = {}
    assert gate_action_claims(NOTICE, turn_id="notice-none", source_context=context)[0] == NOTICE
    keep_receipt.stamp(context, keep_receipt.KeepReceipt(saved=["my locker is 31"], in_passing=False))
    lane_reply = "Locked in. I’ll remember that."
    assert gate_action_claims(lane_reply, turn_id="notice-lane", source_context=context)[0] == lane_reply


def test_an_opening_change_phrase_does_not_empty_the_subject():
    saved = [{"fact": "My fencing practice is moving to Sundays; the coach is called Ingrid"}]
    # About the practice's booking page, not about the practice day: it replaces nothing.
    assert everyday.updated_entry("Change of plan: my fencing practice booking page is now on the club site", saved) is None
    assert (everyday.updated_entry("Change of plan: my fencing practice is now on Fridays", saved) or {}).get("fact") == saved[0]["fact"]


def test_a_history_question_is_not_handed_to_the_transcript_reader(isolated_home):
    from core.agent_runtime.cross_chat_recall import cross_chat_recall

    assert _say("Remember that my fencing practice is on Tuesdays.")[0]
    assert _say("Small change: my fencing practice is on Thursdays now.")[0]
    context = {"_owner_local": True, "surface": "openclaw", "platform": "openclaw"}
    answer = cross_chat_recall("Which day was my fencing practice before it moved to Thursdays?",
                               session_id=LATER_CHAT, source_context=context)
    assert answer is not None and answer.response == (
        "Before the last change, I had saved:\n- my fencing practice is on Tuesdays."
    )


def test_an_update_leaves_the_fact_of_another_kind_it_does_not_change():
    saved = "we rented the cabin at lake Varn for 2-5 march, door code HK-7713"
    assert everyday.fact_clauses(saved) == ["we rented the cabin at lake Varn for 2-5 march", "door code HK-7713"]
    assert everyday.clauses_kept(saved, "small update: the cabin dates changed, its now 9-12 march, same door code") == [
        "door code HK-7713"
    ]
    assert everyday.typed_values("its now 9-12 march") == {"date": frozenset({"9-12 march"})}
    assert everyday.typed_values("door code HK-7713") == {}


def test_what_the_profile_keeps_is_not_also_written_as_a_memory_row(isolated_home):
    with keep_receipt.collecting() as receipt:
        kept = keep_stated_facts("my name is Noor", session_id=CHAT, access_policy=_policy(CHAT))
    assert kept == 0 and receipt.saved == [] and _facts() == []
    assert keep_stated_facts("my harp lesson is on 6 October", session_id=CHAT, access_policy=_policy(CHAT)) == 1


def test_an_update_finds_its_fact_by_the_word_forms_a_question_finds_it_by():
    saved = [{"fact": "the van is now parked on Alder row, still level 2"}]
    assert everyday.updated_entry("the van parking changed again, it's on Birch row now", saved) == saved[0]
    assert everyday.updated_entry("the van insurance changed again, it's with Birch mutual now", saved) is None
