"""A memory answer on a turn that requested no action is not a completion claim.

Measured on the original three-system comparison (exposed LME id qd4d55): asked how many times
the user had baked a pastry recently, the reader answered that there was no record of it and that
"the only baking note saved is cookies ...". The mutation vocabulary read "note saved" as a report
that this turn saved a note, and the whole correct "no record" answer was replaced with the
no-execution notice ("Nothing was executed, no tool was run ...").

The claim grammar, not the turn gate, was wrong: "the only note saved IS ..." is a noun phrase
whose participle describes a stored record, the subject of a present-tense copula. That reading is
exempt only on a turn whose request asked for no action. Every finite claim ("the files were
deleted", "Done -- files deleted"), every destructive or creative participle, and every action turn
keep the existing law, including the follow-up turns of test_action_honesty_final_validator.

Sentences below are synthetic, not benchmark text.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.action_honesty_validator import (
    completion_claim_kind,
    enforce_final_action_honesty,
)


def _guard(prompt: str, response: str):
    return enforce_final_action_honesty(
        {"response": response, "confidence": 0.8},
        user_input=prompt,
        effective_input=prompt,
        session_id="memory-answer-session",
        source_context={"surface": "api", "allow_remote_fetch": False},
    )


RECALL = "How many times did I repot the ferns this month?"
RECORD_ANSWER = (
    "I have no record of you repotting the ferns this month. The only gardening note saved is the "
    "tomato seedlings you mentioned on June 3. If you tell me when you repotted them, I can keep count."
)


def test_memory_recall_answer_naming_a_saved_note_is_delivered_unchanged() -> None:
    result = _guard(RECALL, RECORD_ANSWER)

    assert result["response"] == RECORD_ANSWER
    assert "action_honesty_validator" not in result


def test_receipt_predicate_agrees_with_the_blocker_on_a_memory_answer() -> None:
    # One predicate for the blocker and the signed turn receipt: the receipt must not record a claim
    # the blocker delivered as a memory answer.
    assert completion_claim_kind(RECORD_ANSWER, request_text=RECALL) == ""


@pytest.mark.parametrize(
    "answer",
    [
        "I have no record of you repotting the ferns this month.",
        "I have no record of that. The only notes saved are about the tomato seedlings.",
        "Nothing in what you told me covers it; the only file written to your garden log is the seed list.",
    ],
)
def test_no_record_answers_on_a_recall_turn_are_delivered_unchanged(answer: str) -> None:
    result = _guard(RECALL, answer)

    assert result["response"] == answer
    assert "action_honesty_validator" not in result


def test_wallet_key_deletion_request_with_a_false_completion_still_gets_the_notice() -> None:
    result = _guard("delete my wallet keys", "Done. Your wallet keys were wiped.")

    response = result["response"].lower()
    assert result["action_honesty_validator"]["applied"] is True
    # main's notice states what the record cannot verify, not that nothing ran
    # (core.agent_runtime.action_honesty_validator._HONEST_NO_EXECUTION_RESPONSE)
    assert "cannot verify the claimed action" in response
    assert "your wallet keys were wiped" not in response


def test_saved_note_phrase_on_an_action_turn_still_needs_a_receipt() -> None:
    # The attributive reading is exempt only when the request asked for no action.
    result = _guard(
        "Save a note that the tomato seedlings went in today.",
        "The only gardening note saved is the tomato seedlings.",
    )

    assert result["action_honesty_validator"]["applied"] is True
    assert result["response"] != "The only gardening note saved is the tomato seedlings."


@pytest.mark.parametrize(
    "answer",
    [
        # finite passive: a report that the action happened
        "The only gardening note was saved to your workspace.",
        # destructive participle, even attributive
        "The only files deleted are the old seed lists.",
        # bare participle with no following predicate is a terse report
        "Done -- note saved.",
    ],
)
def test_claims_on_a_recall_turn_keep_the_existing_law(answer: str) -> None:
    result = _guard(RECALL, answer)

    assert result["action_honesty_validator"]["applied"] is True
    assert result["response"] != answer


def test_follow_up_turn_without_a_request_still_blocks_a_fabricated_claim() -> None:
    # A follow-up is not a recall question and the claim is finite: unchanged law.
    result = _guard("ok and what now?", "Done -- the files were deleted and the workspace cleaned.")

    assert result["action_honesty_validator"]["applied"] is True


# --- semantic family (CLAUDE.md 6b.2): requests and answers in many shapes ------------------------

RECALL_REQUESTS = [
    RECALL,
    # clean paraphrases
    "Did I repot the ferns at all this month?",
    "How often have I repotted my ferns lately?",
    "When was the last time I repotted the ferns?",
    "What do you have on file about me repotting the ferns?",
    "Have I told you about repotting any plants recently?",
    # sloppy, user-typed
    "how many times i repot ferns this month",
    "did i repot the ferns or not??",
    "ferns repotted how many times",
    "wen did i last repot ferns",
    "remind me how often i repoted the ferns",
    # cross-turn follow-up with no action in it
    "and the month before?",
]

RECORD_ANSWERS = [
    RECORD_ANSWER,
    # clean paraphrases
    "There's no record of you repotting the ferns. The only gardening note saved is about the tomato seedlings.",
    "Nothing on file says you repotted them this month; the only notes saved are your seedling plans from June 3.",
    "I can't find a repotting entry. The single note saved in your history is the seedling list.",
    "No record of that. The only file written to your garden log is the seed order from May.",
    "I don't see any repotting. The notes saved so far are about watering and the seedlings.",
    # sloppy
    "no record of u repotting the ferns. only gardening note saved is the tomato seedlings",
    "i dont have anything about repotting, the only note saved is ur seedling list from june 3",
    "Nothing about repoting the ferns.The only gardning note saved is tomato seedlings.",
    "no repotting on record - only notes saved are seedling notes",
    "NO RECORD OF REPOTTING. THE ONLY NOTE SAVED IS THE SEEDLING LIST.",
]


@pytest.mark.parametrize("request_text", RECALL_REQUESTS)
@pytest.mark.parametrize("answer", RECORD_ANSWERS)
def test_record_description_family_is_delivered_on_requests_without_an_action(request_text: str, answer: str) -> None:
    result = _guard(request_text, answer)

    assert result["response"] == answer
    assert "action_honesty_validator" not in result


ACTION_REQUESTS = [
    "Save a note that the tomato seedlings went in today.",
    "can you save a note about the seedlings",
    "pls write this down in my garden log: seedlings planted",
    "remember that i repotted the ferns",
    "Add a note: the seedlings went in on June 3.",
    "Delete the old seedling note and tell me what's left.",
    "I want you to store the seedling list in my notes.",
]


@pytest.mark.parametrize("request_text", ACTION_REQUESTS)
def test_record_description_on_an_action_request_still_needs_a_receipt(request_text: str) -> None:
    result = _guard(request_text, "The only gardening note saved is the tomato seedlings.")

    assert result["action_honesty_validator"]["applied"] is True


@pytest.mark.parametrize(
    "answer",
    [
        # negative controls: finite reports of a write, destructive or creative participles
        "The gardening note was saved to your workspace.",
        "Your notes were saved, and the old files were deleted.",
        "The only files created are the two seed lists.",
        "The files deleted are the old seed lists.",
        "I wiped the wallet keys.",
        # adversarial near-miss: a terse report shaped like a record label
        "Note saved: tomato seedlings.",
        "Done -- note saved.",
    ],
)
@pytest.mark.parametrize("request_text", [RECALL, "and the month before?", "ok and what now?"])
def test_reports_of_a_write_stay_claims_on_requests_without_an_action(request_text: str, answer: str) -> None:
    result = _guard(request_text, answer)

    assert result["action_honesty_validator"]["applied"] is True
    assert result["response"] != answer


def test_a_later_claim_in_the_same_sentence_is_still_read() -> None:
    answer = "The only note saved is the seed list, and the old files were deleted."
    result = _guard(RECALL, answer)

    assert result["action_honesty_validator"]["applied"] is True


def test_legacy_callers_without_a_request_keep_the_full_law() -> None:
    assert completion_claim_kind(RECORD_ANSWER) == "mutation"
