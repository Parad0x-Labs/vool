"""Development controls for assertion, subject and reference-clock boundaries."""
import pytest
from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims

Q = "How many nights was my blue bicycle at the shop?"
E = ["I handed my blue bicycle to the shop on 3 July 2026 and collected it on 7 July 2026."]

@pytest.mark.parametrize("quantity", ["Four", "four", "4"])
def test_supported_numeral_survives_capitalization(quantity):
    answer = f"{quantity} nights, from July 3 to July 7, 2026."
    assert replace_unsupported_past_time_claims(answer, question=Q, evidence_texts=E) == answer

@pytest.mark.parametrize("quantity", ["Five", "five", "5"])
def test_wrong_numeral_is_checked_in_both_directions(quantity):
    answer = f"{quantity} nights, from July 3 to July 7, 2026."
    assert stated_past_time_claims(answer, question=Q, evidence_texts=E)
    assert "nights" not in replace_unsupported_past_time_claims(answer, question=Q, evidence_texts=E).split("The request was:")[0]

def test_question_premise_is_not_event_authority():
    q = "Was my photography workshop on February 12, 2024?"
    answer = "Your photography workshop was on February 12, 2024."
    ev = ["I attended the photography workshop on November 1, 2023."]
    assert stated_past_time_claims(answer, question=q, evidence_texts=ev)

@pytest.mark.parametrize("name", ["Four", "Today", "May"])
def test_ordinary_word_name_with_action_remains_a_person(name):
    q = f"When did {name} collect the parcel?"
    answer = f"{name} collected the parcel on October 19, 2023."
    assert replace_unsupported_past_time_claims(answer, question=q, evidence_texts=[answer]) == answer
    assert stated_past_time_claims(answer, question=q, evidence_texts=["Nora collected the parcel on October 19, 2023."])

def test_negated_source_does_not_support_positive_event():
    assert stated_past_time_claims("You attended the workshop on November 1, 2023.", question="When did I attend the workshop?", evidence_texts=["I did not attend the workshop on November 1, 2023."])

def test_bare_current_year_is_not_an_event_date_receipt():
    assert stated_past_time_claims("You filed the return in 2026.", question="When did I file the return?", evidence_texts=[], current_year=2026)

@pytest.mark.parametrize("quantity,value", [("Twenty-one", "d21days"), ("one hundred and five", "d105days"), ("zero", "d0days")])
def test_bounded_english_cardinals_share_numeric_normalization(quantity, value):
    from core.model_output_guard import _canonical_time_values
    assert value in _canonical_time_values(quantity + " days")

@pytest.mark.parametrize("quantity", ["one thousand", "two point five", "minus one"])
def test_unparsed_number_form_is_not_verified(quantity):
    from core.model_output_guard import _canonical_time_values
    values = _canonical_time_values(quantity + " nights")
    assert any(value.startswith("unparsed_duration:") for value in values)
    assert stated_past_time_claims(quantity + " nights.", question=Q, evidence_texts=E)

def test_attributed_question_echo_does_not_affirm_it():
    q = "Did the renovation take 6 months?"
    answer = "You asked whether the renovation took 6 months. I cannot confirm that duration."
    assert replace_unsupported_past_time_claims(answer, question=q, evidence_texts=[]) == answer

def test_question_echo_does_not_hide_a_separate_affirmation():
    q = "Did the renovation take 6 months?"
    answer = "You asked about 6 months, and it took 6 months."
    assert stated_past_time_claims(answer, question=q, evidence_texts=[])

def test_same_dates_with_wrong_actor_in_later_sentence_are_not_donated():
    q = "When did Nora collect the parcel?"
    answer = "Liam collected the parcel on October 19, 2023. He collected it on October 19, 2023."
    ev = ["Nora collected the parcel on October 19, 2023."]
    assert "October 19" not in replace_unsupported_past_time_claims(answer, question=q, evidence_texts=ev)

def test_literal_negative_claim_requires_negative_support():
    q = "When did I attend the workshop?"
    answer = "You did not attend the workshop on November 1, 2023."
    assert replace_unsupported_past_time_claims(answer, question=q, evidence_texts=["I did not attend the workshop on November 1, 2023."]) == answer
    assert stated_past_time_claims(answer, question=q, evidence_texts=["I attended the workshop on November 1, 2023."])

@pytest.mark.parametrize("prefix", ["I cannot confirm February 12, 2024, but", "I don't know the exact date, yet"])
def test_refusal_prefix_cannot_hide_an_added_temporal_assertion(prefix):
    answer = prefix + " you attended the workshop on February 12, 2024."
    assert stated_past_time_claims(answer, question="When did I attend the workshop?", evidence_texts=["I attended the workshop on November 1, 2023."])


def test_negative_present_predicate_does_not_become_positive_history():
    assert stated_past_time_claims("You attend the workshop on November 1, 2023.", question="When did I attend the workshop?", evidence_texts=["I don't attend the workshop on November 1, 2023."])
