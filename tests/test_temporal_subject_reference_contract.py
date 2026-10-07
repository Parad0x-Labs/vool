"""Subject references constrain admitted dates; exclusions supply no authority."""
import pytest

from core.model_output_guard import replace_unsupported_past_time_claims

QUESTION = "How many complete calendar months did I, Elena Ruiz, work on the estuary survey across my two recorded assignments? Give both exact ISO date intervals and the total, without counting Julian's interval."
EVIDENCE = [
    "I am Elena Ruiz. My first estuary-survey assignment ran from 2024-03-01 through 2024-06-30 inclusive: March, April, May, and June, four complete calendar months. Julian Voss is a different surveyor; Julian's continuous assignment ran from 2024-01-15 through 2024-10-15 inclusive.",
    "I, Elena Ruiz, resumed the estuary survey from 2024-09-01 through 2025-02-28 inclusive: September, October, November, December, January, and February, six complete calendar months. I was not assigned during July or August 2024, and I have no assignment beginning after 2025-02-28 in this record. Julian's January-to-October 2024 interval belongs only to Julian.",
]
ANSWER = "Elena Ruiz worked from 2024-03-01 through 2024-06-30 inclusive for four complete calendar months, then from 2024-09-01 through 2025-02-28 inclusive for six complete calendar months. The total is 10 complete calendar months. Julian's interval is not counted."


def guard(answer=ANSWER, question=QUESTION, evidence=EVIDENCE, receipt=None):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence, decision_receipt=receipt)


def test_exact_native_first_person_appositive_preserves_four_supported_dates():
    receipt = {}
    assert guard(receipt=receipt) == ANSWER
    assert {item["question_actor"] for item in receipt["checks"]} == {"user"}
    assert not receipt["unsupported_values"]


@pytest.mark.parametrize("question", [
    "How many months did I work on the estuary survey, without counting Julian's interval?",
    "How many months did I, Elena Ruiz, work on the estuary survey, excluding Julian's interval?",
    "How many months did I work on the estuary survey, without including Julian's interval?",
])
def test_first_person_action_subject_wins_over_excluded_possessive(question):
    answer = "You worked for four complete calendar months, from 2024-03-01 through 2024-06-30."
    assert guard(answer, question, EVIDENCE[:1]) == answer


def test_independently_named_appositive_and_exclusion_share_no_dates():
    question = "When did I, Amara Bell, finish the river survey, without using Dorian Pike's dates?"
    evidence = ["I finished the river survey on 2024-04-09. Dorian Pike finished the river survey on 2024-05-19."]
    answer = "Amara Bell finished the river survey on 2024-04-09."
    assert guard(answer, question, evidence) == answer
    assert "2024-05-19" not in guard("Amara Bell finished the river survey on 2024-05-19.", question, evidence)


@pytest.mark.parametrize("answer", [
    "Julian Voss worked on the estuary survey from 2024-03-01 through 2024-06-30.",
    "Elena Park worked on the estuary survey from 2024-03-01 through 2024-06-30.",
])
def test_appositive_alias_does_not_authorize_another_named_answer_actor(answer):
    out = guard(answer)
    assert "2024-03-01" not in out and "2024-06-30" not in out


def test_first_person_reference_cannot_borrow_excluded_person_interval():
    out = guard("You worked on the estuary survey from 2024-01-15 through 2024-10-15.")
    assert "2024-01-15" not in out and "2024-10-15" not in out


def test_subject_appositive_is_not_event_evidence():
    assert "2024-03-01" not in guard("Elena Ruiz worked on the estuary survey from 2024-03-01 through 2024-06-30.", evidence=[])


def test_named_source_matching_first_person_appositive_is_compatible():
    question = "When did I, Amara Bell, finish the river survey?"
    answer = "Amara Bell finished the river survey on 2024-04-09."
    assert guard(answer, question, [answer]) == answer


def test_named_question_subject_is_not_overridden_by_me_in_object():
    question = "When did Dorian Pike finish the river survey with me, without counting Amara Bell's interval?"
    answer = "Dorian Pike finished the river survey on 2024-05-19."
    evidence = ["I finished the river survey on 2024-04-09.", answer]
    assert guard(answer, question, evidence) == answer
    assert "2024-04-09" not in guard("Dorian Pike finished the river survey on 2024-04-09.", question, evidence)


def test_possessive_requested_actor_precedes_first_person_background():
    question = "How long was Dorian Pike's river survey that I supervised?"
    answer = "Dorian Pike worked on the river survey for six months."
    assert guard(answer, question, [answer, "I worked on the river survey for four months."]) == answer
    assert "four months" not in guard("Dorian Pike worked on the river survey for four months.", question, [answer, "I worked on the river survey for four months."])


def test_negated_named_source_cannot_support_positive_event():
    question = "When did I, Amara Bell, finish the river survey?"
    evidence = ["Amara Bell did not finish the river survey on 2024-04-09."]
    assert "2024-04-09" not in guard("Amara Bell finished the river survey on 2024-04-09.", question, evidence)


def test_supported_negative_named_claim_keeps_its_date():
    question = "When did I, Amara Bell, not finish the river survey?"
    answer = "Amara Bell did not finish the river survey on 2024-04-09."
    assert guard(answer, question, [answer]) == answer


def test_quoted_named_event_cannot_supply_personal_date():
    question = "When did I, Amara Bell, finish the river survey?"
    evidence = ['The sample quotes "Amara Bell finished the river survey on 2024-04-09".']
    assert "2024-04-09" not in guard("Amara Bell finished the river survey on 2024-04-09.", question, evidence)


def test_wrong_episode_cannot_supply_named_appositive_date():
    question = "When did I, Amara Bell, finish the river survey?"
    evidence = ["I finished the kiln rebuild on 2024-04-09."]
    assert "2024-04-09" not in guard("Amara Bell finished the river survey on 2024-04-09.", question, evidence)


@pytest.mark.parametrize("quantity", ["four", "six", "seven", "ten"])
def test_word_durations_remain_bound_to_the_requested_actor(quantity):
    question = "How many months did I, Amara Bell, work on the river survey, excluding Dorian Pike's interval?"
    evidence = ["I worked on the river survey for four months. Dorian Pike worked on the river survey for six months."]
    answer = f"Amara Bell worked on the river survey for {quantity} months."
    out = guard(answer, question, evidence)
    assert (out == answer) is (quantity == "four")


@pytest.mark.parametrize("event_phrase", ["river-survey", "river survey"])
def test_different_complete_compound_cannot_supply_a_date(event_phrase):
    question = f"When did I, Amara Bell, finish the {event_phrase}?"
    answer = f"Amara Bell finished the {event_phrase} on 2024-04-09."
    assert "2024-04-09" not in guard(answer, question, ["I finished the kiln-survey on 2024-04-09."])


def test_hyphenated_requested_event_matches_its_complete_spaced_source():
    question = "When did I, Amara Bell, finish the river-survey?"
    answer = "Amara Bell finished the river-survey on 2024-04-09."
    assert guard(answer, question, ["I finished the river survey on 2024-04-09."]) == answer


def test_later_quoted_first_person_question_does_not_replace_main_named_subject():
    question = "When did Dorian Pike mention the question 'when did I finish the river survey'?"
    evidence = ["I mentioned a river survey question on 2024-04-09."]
    assert "2024-04-09" not in guard("It happened on 2024-04-09.", question, evidence)


@pytest.mark.parametrize("requested_event,source_event", [
    ("river-survey-3", "river-survey-2"),
    ("river survey-3", "river-survey-2"),
    ("orbiter-R2", "orbiter-R1"),
])
def test_compound_spelling_equivalence_cannot_erase_numeric_identifier(requested_event, source_event):
    question = f"When did I, Amara Bell, finish the {requested_event}?"
    answer = f"Amara Bell finished the {requested_event} on 2024-04-09."
    evidence = [f"I finished the {source_event} on 2024-04-09."]
    assert "2024-04-09" not in guard(answer, question, evidence)
