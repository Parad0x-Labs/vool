"""An ask scoped to the user's own notes/records is not a current-information request.

Measured on the wls base (2026-10-29, BASE-RED 0.7.8): a tonnage ask carrying both a currency
word and "according to my notes" was widened by the present-anchored amount arm, opened a
grounding lifecycle no retrieval could ever meet, and the reply refused as unmet current
information -- where the only store that can answer is the kept material the ask names. The
records-scope recognizer (core/execution/constants.py, beside the conversation-recall family)
outranks the "currently"-class widenings; the flip leaves the ``records_scoped_not_current``
code on the DIRECT contract it produced, so the decision stays visible.

What must NOT move: a present-anchored amount ask that names no store keeps its widening
(the F15-05 quarry behavior this arm exists for), the live-data arm and GROUNDED-mode promises
are untouched, an ask that names the web is a lookup whatever it recalls, a write command to
the notes is never a records ask, and a bare what-was-said turn still belongs to the
conversation-recall arm.
"""
from __future__ import annotations

import pytest

from core.execution.constants import records_scoped_ask
from core.execution_requirements import classify_requirements

# Records-scoped phrasings, none of them the measured ask verbatim: prepositional anchors,
# placements, the kept material made to speak, the telling asked back, and stores beyond
# notes/records the same shape governs (chats, journal, diary, notebook).
RECORDS_SCOPED_ASKS = (
    "How many tonnes does the yard hold at the moment according to my notes?",
    "how much fuel is in the bowser currently, per my records?",
    "what does my journal say we paid for the trailer?",
    "what do my records show for the gate code?",
    "which of my notes mention the dock master's name?",
    "in my records, what is the saturday delivery slot?",
    "from our chats, when did we move the beehives?",
    "in our conversations, what did we decide about the fence?",
    "the invoice that i told you about, what was the total?",
    "what did i tell you about the Oslo booking?",
    "what did i say about the wedding flowers?",
    "check my notebook for the locksmith's number",
    "read the balance off my diary",
    "based on my records, how many pallets came in?",
    "out of my notes, how many tonnes is that?",
)

# Commands that WRITE to the kept material: they name the store but ask nothing of it.
WRITE_COMMANDS = (
    "write this in my notes",
    "save this in my notes",
    "note in my notes that the garage code is 5523",
    "keep a note: garage code is 5523",
    "can you save this in my notes?",
    "put the tonnage in my records",
)

# Present-tense asks that name no store: the F15-05 widening must keep them.
NON_RECORDS_PRESENT_ASKS = (
    "How much graded gravel is in the quarry yard at the moment?",
    "How many kilograms of seed are in the hopper at the moment?",
    "What is the level of the reservoir currently?",
    "how many pallets are on the loading dock at the moment?",
)


@pytest.mark.parametrize("text", RECORDS_SCOPED_ASKS)
def test_the_phrasings_name_a_store_as_the_source_of_truth(text: str) -> None:
    assert records_scoped_ask(text) is True, text


@pytest.mark.parametrize("text", WRITE_COMMANDS)
def test_a_command_that_writes_the_notes_is_not_a_records_ask(text: str) -> None:
    assert records_scoped_ask(text) is False, text


@pytest.mark.parametrize(
    "text",
    (
        "search the web for what i told you about oslo",
        "look that up online from my notes",
        *NON_RECORDS_PRESENT_ASKS,
        "what is the capital of France?",
        "delete that from my records",
    ),
)
def test_these_are_not_records_asks(text: str) -> None:
    assert records_scoped_ask(text) is False, text


# Currency-word asks a store anchor scoping: the two DIRECT widenings (the signals arm and the
# present-anchored amount arm) must stand down, and the standing-down must be stated.
@pytest.mark.parametrize(
    "text",
    (
        "How many tonnes does the yard currently hold according to my notes?",
        "how many tonnes are in the yard at the moment according to my notes?",
        "How many euros are in the budget envelope currently, per my records?",
        "how many litres of fuel does the bowser hold currently according to my notes?",
        "how many hours did i log in my records currently?",
        "what time does the morning shift start currently, per my notes?",
        "what time does the ferry leave at the moment according to my notes?",
        "who is currently on duty according to my notes?",
        "who is on call at the moment according to my notes?",
    ),
)
def test_a_records_scoped_ask_with_a_currency_word_is_not_current_information(text: str) -> None:
    req = classify_requirements(text)
    assert req.current_information_required is False, (text, req.reason_codes)
    assert req.answer_mode == "DIRECT", (text, req.answer_mode)
    assert req.tools_required is False, text
    assert req.allowed_toolsets == (), (text, req.allowed_toolsets)
    assert "records_scoped_not_current" in req.reason_codes, (text, req.reason_codes)
    assert "present_anchored_measured_amount" not in req.reason_codes, text


def test_the_override_code_appears_only_where_a_widening_actually_stood_down() -> None:
    # Records-scoped, but no currency widening was going to fire: the reading is the same
    # DIRECT/stable one it always was, and no override is claimed.
    req = classify_requirements("what's the yard tonnage at the moment per my records?")
    assert req.current_information_required is False
    assert "records_scoped_not_current" not in req.reason_codes, req.reason_codes


@pytest.mark.parametrize("text", NON_RECORDS_PRESENT_ASKS)
def test_present_anchored_amount_asks_without_a_store_keep_their_widening(text: str) -> None:
    req = classify_requirements(text)
    assert req.current_information_required is True, (text, req.reason_codes)


def test_the_quarry_ask_keeps_the_amount_arm_reason() -> None:
    req = classify_requirements("How much graded gravel is in the quarry yard at the moment?")
    assert "present_anchored_measured_amount" in req.reason_codes, req.reason_codes
    assert "records_scoped_not_current" not in req.reason_codes, req.reason_codes


@pytest.mark.parametrize("text", WRITE_COMMANDS)
def test_write_commands_flip_no_current_flag(text: str) -> None:
    req = classify_requirements(text)
    assert req.current_information_required is False, (text, req.reason_codes)
    assert "records_scoped_not_current" not in req.reason_codes, (text, req.reason_codes)


@pytest.mark.parametrize(
    "text",
    (
        "search the web for what i told you about oslo",
        "search the web for what I told you about the Tesla stock",
    ),
)
def test_a_web_named_ask_stays_a_lookup(text: str) -> None:
    req = classify_requirements(text)
    assert req.current_information_required is True, (text, req.reason_codes)
    assert "records_scoped_not_current" not in req.reason_codes, (text, req.reason_codes)


@pytest.mark.parametrize(
    "text",
    (
        "what did I tell you about oslo?",
        "What did I tell you about the weather at my wedding?",
        "which name did i give the boat in my notes currently?",
        "Do you remember what I said in my notes about the Thursday club?",
    ),
)
def test_a_bare_what_was_said_turn_still_belongs_to_the_recall_arm(text: str) -> None:
    req = classify_requirements(text)
    assert "conversation_recall" in req.reason_codes, (text, req.reason_codes)
    assert req.current_information_required is False, (text, req.reason_codes)
    assert "records_scoped_not_current" not in req.reason_codes, (text, req.reason_codes)
