"""Present-anchored amount asks require a current observation (routing lane, F15-05).

Measured on the frozen head 9174b42c (V corpus F15-05, owner-review replay
corrected-v3, 2026-09-29): "How much graded gravel is in the quarry yard at the
moment?" read DIRECT/stable_knowledge with ``current_information_required=False``,
so neither unsourced-current guard had jurisdiction, and the plain chat route
delivered a scripted "There are about 12,400 tonnes of graded gravel sitting in
the quarry yard at the moment." unchanged -- while the SAME question phrased
"right now" was already guarded. A marker drift between the requirements
authority's currency markers and the temporal authority's closed CURRENT anchor
class, closed here on the ASK shape (interrogative head + temporal anchor, never
a subject list) plus a measured-quantity kind on the reply-side live-claim guard
(the same SI/ISO vocabulary `core.unsourced_current_claim` owns, at prose-safe
tolerance, under explicit-nowness anchors only).

Served-path proof (four surfaces: selected source, final request, raw reply,
delivered) lives in the routing lane rig artifacts; these tests pin the owning
contracts so the wiring cannot silently disappear.
"""

from __future__ import annotations

import pytest

from core.execution_requirements import requirements_for
from core.model_output_guard import (
    replace_unobserved_live_claims,
    unobserved_live_value_claims,
)
from core.unsourced_current_claim import (
    inspect_unsourced_current_claim,
    question_asks_for_measured_amount,
    unverified_current_answer,
)

# ---------------------------------------------------------------------------------------------
# The original failure, reproduced at its owners
# ---------------------------------------------------------------------------------------------

F15_QUESTION = "How much graded gravel is in the quarry yard at the moment?"
F15_REPLY = (
    "There are about 12,400 tonnes of graded gravel sitting in the quarry yard at the moment."
)


def test_the_original_question_now_requires_a_current_observation() -> None:
    req = requirements_for(F15_QUESTION)
    assert req.current_information_required is True
    assert req.answer_mode == "DIRECT"  # no forced web tools for a private quantity
    assert "present_anchored_measured_amount" in req.reason_codes


def test_the_marker_drift_is_closed_all_present_anchor_phrasings_agree() -> None:
    """The defect was a drift: "right now" was guarded, "at the moment" was not."""

    for question in (
        "How much graded gravel is in the quarry yard right now?",
        "How much graded gravel is in the quarry yard at the moment?",
        "How much graded gravel is in the quarry yard currently?",
        "How much graded gravel is in the quarry yard at present?",
    ):
        assert requirements_for(question).current_information_required, question


def test_the_whole_answer_guard_convicts_on_a_record_free_session() -> None:
    verdict = inspect_unsourced_current_claim(
        answer=F15_REPLY,
        requires_current=requirements_for(F15_QUESTION).current_information_required,
        session_id="f15-05-no-records",
        user_turn_text=F15_QUESTION,
    )
    assert verdict.unsupported
    notice = unverified_current_answer(F15_QUESTION)
    assert "12,400" not in notice
    assert "not going to state" in notice


def test_the_reply_side_guard_catches_the_quantity_kind_on_every_route() -> None:
    """The question-blind backstop: the plain chat route (model_minimal) has no
    requirements reading of its own at the render seam, so the value shape must
    convict there independently of how the question classified."""

    kinds = unobserved_live_value_claims(F15_REPLY)
    assert "measured-quantity" in kinds
    replaced = replace_unobserved_live_claims(F15_REPLY)
    assert "12,400" not in replaced
    assert "not going to state" in replaced


# ---------------------------------------------------------------------------------------------
# Genuinely new cases: different wording, entities, values, relationships
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question",
    (
        "How many litres of diesel are in the bowser tank at present?",
        "What is the level of the reservoir currently?",
        "How many kilograms of seed are in the hopper at the moment?",
        "What's the total weight on the ferry ramp right now?",
    ),
)
def test_new_present_anchored_amount_asks_are_guarded(question: str) -> None:
    assert requirements_for(question).current_information_required, question


@pytest.mark.parametrize(
    "reply",
    (
        "The bowser tank holds 4,300 litres of diesel at present.",
        "The reservoir sits at 68 percent of capacity currently.",
        "There are 950 kilograms of seed in the hopper at the moment.",
    ),
)
def test_new_unsourced_quantity_replies_are_replaced(reply: str) -> None:
    kinds = unobserved_live_value_claims(reply)
    assert kinds, reply
    replaced = replace_unobserved_live_claims(reply)
    for value in ("4,300", "68 percent", "950 kilograms"):
        if value in reply:
            assert value not in replaced, reply
    assert "not going to state" in replaced


def test_a_mixed_answer_keeps_its_supported_history_half() -> None:
    reply = (
        "Your 2019 ledger says you paid 170 dollars for the parts. "
        "The yard currently holds 12,400 tonnes of graded gravel."
    )
    replaced = replace_unobserved_live_claims(
        reply, user_turn_text="What is in the yard currently, and what did the parts cost?"
    )
    assert "170 dollars" in replaced  # supported history survives
    assert "12,400" not in replaced  # the unsourced present claim goes
    assert "not going to state" in replaced


# ---------------------------------------------------------------------------------------------
# Preservation controls: what must NEVER be caught
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question",
    (
        "How many moons does Jupiter have?",  # stable knowledge, no present anchor
        "How much gravel was in the yard last month?",  # past-anchored
        "How much diesel does the generator usually burn?",  # habitual
        "What is the capital of France?",  # stable knowledge, not an amount ask
        "How much did the anemometer service cost?",  # past transaction
    ),
)
def test_non_present_asks_keep_their_old_requirements(question: str) -> None:
    assert not requirements_for(question).current_information_required, question


def test_user_provided_present_fact_is_retained_at_both_layers() -> None:
    question = "I have 3 tonnes of gravel at the moment — how many kilos is that?"
    req = requirements_for("How many kilos of gravel do I have at the moment?")
    verdict = inspect_unsourced_current_claim(
        answer="You have 3 tonnes of gravel at the moment, which is 3,000 kilograms.",
        requires_current=req.current_information_required,
        session_id="user-fact-retention",
        user_turn_text=question,
    )
    assert not verdict.unsupported
    kinds = unobserved_live_value_claims(
        "You have 3 tonnes of gravel at the moment.",
        user_turn_text=question,
    )
    assert kinds == ()


def test_calculation_answers_are_not_live_claims() -> None:
    assert unobserved_live_value_claims("15 percent of 240 euros is 36 euros.") == ()


def test_fictional_premise_echo_is_retained_via_the_user_fact_exemption() -> None:
    user_text = (
        "In my novel, the quarry holds 12,400 tonnes at the moment — is that realistic?"
    )
    kinds = unobserved_live_value_claims(
        "For a small operation, 12,400 tonnes at the moment would be on the large side.",
        user_turn_text=user_text,
    )
    assert kinds == ()


def test_dated_memory_is_not_a_current_observation_channel() -> None:
    """The F15-04 law: a stored August reading never licenses a present claim. The
    exemption consults only the CURRENT turn's user text, never history."""

    context = {
        "conversation_history": [
            {"role": "user", "content": "The yard held 12,400 tonnes when we checked in August."}
        ]
    }
    verdict = inspect_unsourced_current_claim(
        answer=F15_REPLY,
        requires_current=True,
        source_context=context,
        user_turn_text=F15_QUESTION,
    )
    assert verdict.unsupported


def test_same_turn_observation_keeps_the_answer() -> None:
    """A retrieval receipt is a same-turn evidence channel both guard layers read
    (`web_retrieval_receipts` in `turn_has_current_evidence`; `turn_ran_observations`
    reads the same channel list at the render seam)."""

    context = {
        "web_retrieval_receipts": [
            {"status": "available", "source_count": 1, "failure_class": ""}
        ]
    }
    verdict = inspect_unsourced_current_claim(
        answer=F15_REPLY,
        requires_current=True,
        source_context=context,
        user_turn_text=F15_QUESTION,
    )
    assert not verdict.unsupported


@pytest.mark.parametrize(
    "prose",
    (
        "We watched the temperature creep up over the month.",  # no nowness anchor
        "Plan b covers section 3 c and the annex.",  # single-letter unit shapes are not quantities here
        "The meeting runs 2 hours.",  # duration with no claim of nowness
        "The yard held 11,000 tonnes in the 2019 survey.",  # dated history
    ),
)
def test_prose_without_explicit_nowness_is_never_convicted_by_the_quantity_kind(prose: str) -> None:
    assert "measured-quantity" not in unobserved_live_value_claims(prose)


# ---------------------------------------------------------------------------------------------
# The ask-shape recognizer itself: closed interrogative heads, not a subject list
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,expected",
    (
        ("How much gravel is in the yard?", True),
        ("How many tonnes remain?", True),
        ("What is the level of the tank?", True),
        ("What's the total weight on the ramp?", True),
        ("Is the gravel dry?", False),
        ("Who operates the quarry?", False),
        ("The yard holds gravel.", False),
    ),
)
def test_amount_ask_heads(text: str, expected: bool) -> None:
    assert question_asks_for_measured_amount(text) is expected, text


def test_assistant_state_amount_asks_match_their_right_now_twin() -> None:
    """Drift symmetry, not escalation: "How much RAM do you have right now?" already
    required current information on the base (GROUNDED via the currency markers); the
    "at the moment" phrasing must agree with it, no more and no less. The answer-shape
    conjunct keeps these safe in practice: an introspection answer with no measured
    unit value is never withdrawn."""

    assert requirements_for("How much RAM do you have right now?").current_information_required
    assert requirements_for("How much RAM do you have at the moment?").current_information_required
    verdict = inspect_unsourced_current_claim(
        answer="I can run with up to 16 GB of memory on this machine.",
        requires_current=requirements_for("How much RAM do you have at the moment?").current_information_required,
        session_id="assistant-ram",
        user_turn_text="How much RAM do you have at the moment?",
    )
    assert verdict.unsupported  # the "right now" twin convicts the same shape on the base


# ---------------------------------------------------------------------------------------------
# The calculation class: unit conversions of the user's own number
# ---------------------------------------------------------------------------------------------

def test_a_unit_conversion_of_the_users_number_is_retained() -> None:
    user_text = "I have 3 tonnes of gravel in the yard at the moment - how many kilos is that?"
    reply = "That is 3 tonnes of gravel at the moment, which is 3,000 kilograms."
    kinds = unobserved_live_value_claims(reply, user_turn_text=user_text)
    assert kinds == ()
    verdict = inspect_unsourced_current_claim(
        answer=reply,
        requires_current=True,
        session_id="conversion-retention",
        user_turn_text=user_text,
    )
    assert not verdict.unsupported


def test_an_invented_neighbour_quantity_beside_the_users_value_still_convicts() -> None:
    user_text = "I have 3 tonnes of gravel in the yard at the moment - how many kilos is that?"
    reply = "That is 3,000 kilograms. The neighbour's yard currently holds 9,000 kilograms."
    kinds = unobserved_live_value_claims(reply, user_turn_text=user_text)
    assert "measured-quantity" in kinds
    replaced = replace_unobserved_live_claims(reply, user_turn_text=user_text)
    assert "3,000 kilograms" in replaced  # the conversion of the user's number survives
    assert "9,000 kilograms" not in replaced  # the invented neighbour reading goes


def test_cross_dimension_scaling_is_not_a_conversion() -> None:
    from core.unsourced_current_claim import reply_match_is_user_supplied

    # tonnes -> kilograms is a conversion; tonnes -> litres is a different quantity
    assert reply_match_is_user_supplied(
        "3,000 kilograms", "I have 3 tonnes at the moment.")
    assert not reply_match_is_user_supplied(
        "3,000 litres", "I have 3 tonnes at the moment.")
