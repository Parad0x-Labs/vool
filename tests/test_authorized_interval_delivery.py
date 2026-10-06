"""A checked historical interval reaches delivery without borrowing another episode."""
import pytest

from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims

QUESTION = "How many nights was my blue cargo bicycle at the canal repair co-op?"
ANSWER = "4 nights — handed in 3 September, collected 7 September 2026."
EVIDENCE = ['''<retrieved_context>
Distilled local facts. Answer from these exact facts only.
- user said: I handed my blue cargo bicycle to the canal repair co-op on 3 September 2026. I collected it on 7 September 2026. The two brake cables were replaced. (stated: 2026-09-07)
- user said: The sample invoice quotes "seven nights in the workshop" for a fictional bicycle. That is not my invoice or my repair. (stated: 2026-09-07)
- user said (stated 2026-09-07): Nadi handed in her green city bicycle on 4 September 2026 and collected it on 5 September 2026. It was a different repair.
</retrieved_context>''']


def guard(answer, question=QUESTION, evidence=EVIDENCE):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


def test_captured_correct_interval_reaches_delivery():
    assert stated_past_time_claims(ANSWER, question=QUESTION, evidence_texts=EVIDENCE) == ()
    assert guard(ANSWER) == ANSWER


def test_captured_second_person_control_is_another_supported_interval():
    question = "How many nights was Nadi's green city bicycle at the co-op?"
    answer = "One night — she handed it in on 4 September 2026 and collected it on 5 September 2026."
    assert guard(answer, question) == answer


def test_fresh_submission_and_collection_interval_reaches_delivery():
    question = "How many nights was Tova's brass telescope at the lens lab?"
    answer = "Tova's brass telescope was there for 6 nights, from 22 February to 28 February 2024."
    evidence = ["Tova handed her brass telescope to the lens lab on 22 February 2024. She collected it on 28 February 2024."]
    assert guard(answer, question, evidence) == answer


def test_fresh_other_endpoint_actions_support_days():
    question = "How many days was my leather case at the depot?"
    answer = "Your leather case was at the depot for 8 days, from 1 March to 9 March 2024."
    evidence = ["I dropped my leather case off at the depot on 1 March 2024 and picked it up on 9 March 2024."]
    assert guard(answer, question, evidence) == answer


@pytest.mark.parametrize("answer", [
    "5 nights — handed in 3 September, collected 7 September 2026.",
    "4 nights — handed in 2 September, collected 7 September 2026.",
    "4 nights — handed in 3 September, collected 8 September 2026.",
    "1 night — handed in 4 September, collected 5 September 2026.",
])
def test_invented_interval_or_other_person_endpoint_is_withdrawn(answer):
    assert guard(answer).startswith("I don't have that time")


def test_quoted_invoice_cannot_donate_personal_duration():
    evidence = ['I handed my blue cargo bicycle to the canal repair co-op. The sample invoice quotes "7 nights at the canal repair co-op for a blue cargo bicycle". That is not my repair.']
    assert guard("7 nights.", evidence=evidence).startswith("I don't have that time")


def test_same_dates_for_different_episode_cannot_donate_interval():
    evidence = ["I handed my red city bicycle to the canal repair co-op on 3 September 2026 and collected it on 7 September 2026."]
    assert guard("4 nights.", evidence=evidence).startswith("I don't have that time")


def test_one_endpoint_from_each_episode_cannot_form_interval():
    evidence = ["I handed my blue cargo bicycle to the canal repair co-op on 3 September 2026.",
                "I collected my red city bicycle from the canal repair co-op on 7 September 2026."]
    assert guard("4 nights.", evidence=evidence).startswith("I don't have that time")


def test_same_object_belonging_to_another_actor_cannot_form_interval():
    evidence = ["Nadi handed her blue cargo bicycle to the canal repair co-op on 3 September 2026 and collected it on 7 September 2026."]
    assert guard("4 nights.", evidence=evidence).startswith("I don't have that time")


def test_named_actor_cannot_borrow_user_interval():
    assert guard("Nadi waited 4 nights.", "How many nights was Nadi's green city bicycle at the co-op?").startswith("I don't have that time")


def test_same_date_different_episode_cannot_form_zero_interval():
    evidence = ["I handed my blue cargo bicycle to the canal repair co-op on 3 September 2026.",
                "I collected my red city bicycle from the canal repair co-op on 3 September 2026."]
    assert guard("0 nights.", evidence=evidence).startswith("I don't have that time")


def test_same_day_checked_interval_supports_zero_nights():
    evidence = ["I handed my blue cargo bicycle to the canal repair co-op on 3 September 2026 and collected it on 3 September 2026."]
    assert guard("0 nights.", evidence=evidence) == "0 nights."


def test_reversed_endpoints_do_not_support_positive_interval():
    evidence = ["I handed my blue cargo bicycle to the canal repair co-op on 7 September 2026 and collected it on 3 September 2026."]
    assert guard("4 nights.", evidence=evidence).startswith("I don't have that time")


def test_unsupported_interval_keeps_supported_other_sentence():
    answer = "The two brake cables were replaced. It was there for 9 nights."
    delivered = guard(answer)
    assert "The two brake cables were replaced." in delivered
    assert "9 nights" not in delivered
    assert "I don't have that time" in delivered


def test_current_scope_keeps_its_observation_requirement():
    from core.temporal_question_scope import question_time_scope
    assert question_time_scope("How many nights has my bicycle been at the co-op as of now?").asks_current


def test_yearless_endpoints_do_not_guess_a_year():
    evidence = ["I handed my blue cargo bicycle to the canal repair co-op on 3 September and collected it on 7 September."]
    assert guard("4 nights.", evidence=evidence).startswith("I don't have that time")


def test_explicit_new_subject_does_not_inherit_previous_named_actor():
    evidence = ["Nadi handed her blue cargo bicycle to the canal repair co-op on 3 September 2026. I collected it on 7 September 2026."]
    assert guard("Nadi waited 4 nights.", "How many nights was Nadi's blue cargo bicycle at the co-op?", evidence).startswith("I don't have that time")


def test_explicit_finished_object_does_not_inherit_a_different_started_object():
    evidence = ["I started my copper mosaic restoration on 6 April 2025 and finished my silver mosaic restoration on 25 April 2025."]
    question = "How many days did my copper mosaic restoration take?"
    assert guard("19 days.", question, evidence).startswith("I don't have that time")


def test_locative_pronoun_cannot_hide_explicit_changed_object():
    evidence = ["I handed my blue cargo bicycle to the canal repair co-op on 3 September 2026. I collected my red city bicycle from there on 7 September 2026."]
    assert guard("4 nights.", evidence=evidence).startswith("I don't have that time")


def test_single_date_for_compound_actions_does_not_invent_an_operand():
    evidence = ["I handed my blue cargo bicycle to the canal repair co-op and collected it on 3 September 2026."]
    assert guard("0 nights.", evidence=evidence).startswith("I don't have that time")


def test_explicit_question_about_quotation_keeps_quoted_duration():
    answer = "The sample invoice quoted 7 nights."
    question = "How many nights did the sample invoice quote?"
    evidence = ['The sample invoice quotes "7 nights in the workshop". That is not my repair.']
    assert guard(answer, question, evidence) == answer


def test_mentioning_quoted_source_does_not_turn_personal_interval_into_quote_request():
    question = "How many nights was my blue cargo bicycle at the co-op, given that quoted invoice?"
    evidence = ['The invoice quotes "7 nights for my blue cargo bicycle at the co-op". That is not my repair.']
    assert guard("7 nights.", question, evidence).startswith("I don't have that time")


def test_quoted_object_label_does_not_erase_asserted_endpoints():
    question = "How many nights was my blue bicycle at the shop?"
    evidence = ['I handed my "blue bicycle" to the shop on 3 July 2026 and collected it on 7 July 2026.']
    answer = "4 nights, from 3 July to 7 July 2026."
    assert guard(answer, question, evidence) == answer


def test_quoted_personal_event_cannot_borrow_outer_statement_date():
    evidence = ['I handed my blue cargo bicycle to the canal repair co-op on 3 September 2026. On 7 September 2026 the exercise quoted "I collected it".']
    assert guard("4 nights.", evidence=evidence).startswith("I don't have that time")


def test_literal_duration_cannot_come_from_explicitly_different_object():
    evidence = ["My red city bicycle was at the canal repair co-op for 4 nights."]
    assert guard("4 nights.", evidence=evidence).startswith("I don't have that time")


def test_abbreviated_subject_still_supports_recorded_literal_duration():
    answer = "You used your Garmin Vivosmart 4 for 17 months."
    question = "How long have I used my Garmin Vivosmart 4?"
    evidence = ["I've used my Garmin for 17 months."]
    assert guard(answer, question, evidence) == answer


def test_quoted_possessive_label_keeps_its_surrounding_asserted_action():
    question = "How many nights was Tova's brass telescope at the lens lab?"
    evidence = ['Tova handed her "Tova\'s brass telescope" to the lens lab on 22 February 2024. She collected it on 28 February 2024.']
    answer = "Tova's brass telescope was there for 6 nights, from 22 February to 28 February 2024."
    assert guard(answer, question, evidence) == answer


def test_possessive_reported_subject_does_not_borrow_reporting_actor():
    question = "When did Nora collect the telescope?"
    answer = "Nora collected the telescope on 19 October 2023."
    evidence = ["Nora said Liam's telescope was collected on 19 October 2023."]
    assert "19 October" not in guard(answer, question, evidence)
