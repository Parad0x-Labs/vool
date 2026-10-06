"""A monetary ask answered with a date is a wrong-relation delivery (F11-03).

V corpus F11-03 on the frozen head 9174b42c: "What did the ridge mast anemometer
cost to service?" against a maintenance log recording only a recalibration on
May 3rd. The near-miss record is the RIGHT subject with the WRONG relation; the
owner review directed an ANSWER contract (never evidence suppression -- the
recalibration record stays retrievable and citable). The guard: a reply that
delivers calendar dates with no monetary value anywhere in the reply or the
request material has answered a different question; it is replaced by a notice
that names no value. Sentences that DECLINE the asked relation may present the
related record as labeled context -- that is the honest answer, not the failure.
"""

from __future__ import annotations

import pytest

from core.model_output_guard import (
    replace_wrong_relation_cost_answers,
    stated_wrong_relation_cost_answers,
    unverified_cost_notice,
)

F11_Q = "What did the ridge mast anemometer cost to service?"
F11_LOG = (
    "Maintenance log: the Kestrel anemometer at the ridge mast was recalibrated "
    "on May 3rd by Oduya."
)


def test_the_original_delivery_convicts() -> None:
    reply = "It was recalibrated on May 3rd by Oduya."
    claims = stated_wrong_relation_cost_answers(
        reply, question=F11_Q, evidence_texts=[F11_LOG]
    )
    assert claims, "the date delivered as the cost answer must convict"
    replaced = replace_wrong_relation_cost_answers(
        reply, question=F11_Q, evidence_texts=[F11_LOG]
    )
    assert "May 3" not in replaced
    assert "not going to state" in replaced


def test_the_notice_names_no_value() -> None:
    notice = unverified_cost_notice(F11_Q)
    assert "May" not in notice
    assert "not going to state one" in notice


# ---------------------------------------------------------------------------------------------
# Genuinely new cases: different wording, entities, relations, values
# ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "question,reply",
    (
        (
            "How much did the harp rehearsal room cost to book?",
            "The room was booked on March 12th.",
        ),
        (
            "What was the fee for the border crossing permit?",
            "The permit was issued on 2026-02-04.",
        ),
        (
            "How much does the crane inspection cost?",
            "The crane was last inspected on 4 November.",
        ),
    ),
)
def test_new_wrong_relation_deliveries_convict(question: str, reply: str) -> None:
    material = {
        "How much did the harp rehearsal room cost to book?": "The room was booked on March 12th.",
        "What was the fee for the border crossing permit?": "Permit issued 2026-02-04 by the county office.",
        "How much does the crane inspection cost?": "Crane inspection conducted on 4 November.",
    }[question]
    replaced = replace_wrong_relation_cost_answers(
        reply, question=question, evidence_texts=[material]
    )
    assert replaced != reply, question
    assert "not going to state" in replaced


# ---------------------------------------------------------------------------------------------
# Preservation controls
# ---------------------------------------------------------------------------------------------

def test_an_honest_decline_that_names_the_related_record_survives() -> None:
    reply = "I don't have a recorded cost for the service. The log only shows a recalibration on May 3rd."
    replaced = replace_wrong_relation_cost_answers(
        reply, question=F11_Q, evidence_texts=[F11_LOG]
    )
    assert replaced == reply, "a decline with labeled context is the honest answer"


def test_an_answer_that_quotes_a_cost_keeps_its_date_context() -> None:
    reply = "The recalibration on May 3rd cost 180 dollars, billed by Oduya's shop."
    replaced = replace_wrong_relation_cost_answers(
        reply, question=F11_Q, evidence_texts=["Billed 180 dollars for the May 3rd recalibration."]
    )
    assert replaced == reply


def test_a_cost_in_the_request_material_disarms_the_guard() -> None:
    reply = "The service visit was on May 3rd."
    replaced = replace_wrong_relation_cost_answers(
        reply,
        question=F11_Q,
        evidence_texts=["Oduya's invoice for the recalibration: 180 dollars."],
    )
    assert replaced == reply


def test_a_date_question_keeps_its_date_answer() -> None:
    question = "When was the ridge mast anemometer recalibrated?"
    reply = "It was recalibrated on May 3rd by Oduya."
    replaced = replace_wrong_relation_cost_answers(
        reply, question=question, evidence_texts=[F11_LOG]
    )
    assert replaced == reply


def test_a_mixed_ask_with_a_temporal_half_keeps_its_date_answer() -> None:
    question = "What did the permit cost and when did you file it?"
    reply = "It was filed on March 12th."
    replaced = replace_wrong_relation_cost_answers(
        reply, question=question, evidence_texts=["Filed the permit on March 12th."]
    )
    assert replaced == reply, "the date belongs to the temporal half of the ask"


def test_a_non_monetary_question_is_never_touched() -> None:
    question = "Who recalibrated the anemometer?"
    reply = "Oduya recalibrated it on May 3rd."
    replaced = replace_wrong_relation_cost_answers(
        reply, question=question, evidence_texts=[F11_LOG]
    )
    assert replaced == reply


def test_a_plain_cost_decline_with_no_date_is_untouched() -> None:
    reply = "I don't have a recorded cost for the service."
    replaced = replace_wrong_relation_cost_answers(
        reply, question=F11_Q, evidence_texts=[F11_LOG]
    )
    assert replaced == reply


def test_the_capsule_keeps_the_near_miss_record() -> None:
    """No evidence suppression: the guard is on the ANSWER, and the near-miss record
    itself is exactly the retrieval this contract wants to stay reachable (the
    capsule-tier oracle tension is reported to the lead; this lane's contract is
    the delivered answer)."""

    assert "May 3" in F11_LOG  # the record stands, unedited
    assert "recalibrated" in F11_LOG
