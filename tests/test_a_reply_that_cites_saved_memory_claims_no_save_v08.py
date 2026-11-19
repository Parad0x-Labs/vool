"""A reply that cites what is already saved is delivered as it is; a reply that claims a save this turn did not make is still replaced.

Measured on the real app (2026-11-19, the cloud route answered by a second cloud model than the usual one; the start
commit and the fix branch alike): "who is my physiotherapist?" with the fact saved was answered by the model "Your
physiotherapist is Ms Adeyinka, according to what you've saved in my memory." and delivered as "I did not write
anything to memory -- no preference, rule or setting was stored on this turn ...". Five of nineteen plain recall
questions of a sealed set ended that way on that model; two on the usual model on the start commit.

All names and places here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import action_claim_gate as gate
from core.agent_runtime import action_honesty_validator as validator
from core.saved_fact_citation import without_citations

OWNER = {"_owner_local": True}

CITES = [
    "Your optician is Mr Halloway on Reed Street, according to what you've saved in my memory.",
    "The trailer lock key is in the red jar by the door, based on what I have saved in memory.",
    "Rosalind has fencing on Tuesdays, according to what you've saved to memory.",
    "From what I have saved in memory, your optician is Mr Halloway.",
    "That's what is saved in memory for your optician.",
    "I have that saved in memory: Mr Halloway on Reed Street.",
    "According to my saved memory, the grinder calls on the second Monday.",
    "Based on everything you've stored in memory, it is Tuesdays.",
    "Going by what's recorded in memory, the key is in the red jar.",
    # wordings one cloud model used on the real app (other names here)
    "Based on what you've saved, your optician is Mr Halloway on Reed Street.",
    "It's in the red jar by the door — that's what I have saved for it.",
    "Your optician is Mr Halloway, on Reed Street — that's what I've got saved.",
    "I see the old one (30917) still stored in my memory.",
    "Reed Street — it's saved in my memory from an earlier chat.",
]
CLAIMS = [
    "Writing formatting rule to workspace memory... Done.",
    "I've saved your preference to memory.",
    "Stored it in workspace memory.",
    "Your preference has been saved.",
    "I saved that to memory for you.",
    "Here is what I saved to memory just now: your preference for metric.",
    "I confirm that I saved your preference to memory.",
]


@pytest.mark.parametrize("said", CITES)
def test_a_citation_of_what_is_saved_is_no_claim_of_a_save(said):
    assert validator.completion_claim_kind(said, request_text="who is my optician?") == ""
    assert gate._claim_form(said) != "action"


@pytest.mark.parametrize("said", CITES)
def test_an_answer_that_cites_memory_is_delivered_as_the_model_wrote_it(said):
    out = validator.enforce_final_action_honesty({"response": said}, user_input="who is my optician?", session_id="ask", source_context=dict(OWNER))
    assert out["response"] == said and "action_honesty_validator" not in out


@pytest.mark.parametrize("said", CITES)
def test_the_wider_reading_of_kept_wording_sees_no_claim_in_a_citation(said):
    # The second check also reads wider "kept" wording as a memory claim; a citation is none there either
    # (served on the first cut of this change: one recall was still answered "Nothing from this message was saved").
    spans = gate._claim_spans(said)
    assert not [claim for claim in spans if claim[5] == "memory"]
    assert gate._retention_spans(said, {(index, a, b) for index, a, b, *_ in spans}) == []


@pytest.mark.parametrize("said", ["I've kept that in memory for you.", "Got it, I'll keep that in memory for you."])
def test_the_wider_reading_still_sees_a_claim_that_something_is_kept_now(said):
    spans = gate._claim_spans(said)
    assert [claim for claim in spans if claim[5] == "memory"] or gate._retention_spans(said, {(index, a, b) for index, a, b, *_ in spans})


@pytest.mark.parametrize("said", CLAIMS)
def test_a_claimed_save_the_turn_did_not_make_is_still_replaced(said):
    assert validator.completion_claim_kind(said, request_text="use metric units from now on") == "persistence"
    out = validator.enforce_final_action_honesty({"response": said}, user_input="use metric units from now on", session_id="ask", source_context=dict(OWNER))
    assert out["response"] == validator._HONEST_NO_PERSISTENCE_RESPONSE


def test_a_reply_that_cites_and_then_claims_keeps_its_claim():
    said = "Your optician is Mr Halloway, from what I have saved in memory. I've also saved your new preference to memory."
    assert validator.completion_claim_kind(said, request_text="who is my optician?") == "persistence"


def test_only_the_citing_words_are_set_aside():
    assert "saved" not in without_citations("based on what I have saved in memory").lower()
    assert without_citations("I've saved your preference to memory.") == "I've saved your preference to memory."
    # "just" marks this turn's write, whatever the grammar
    assert without_citations("what I saved just now") == "what I saved just now"
