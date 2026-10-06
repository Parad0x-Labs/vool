"""A computed past-time value ships when its cited operands are supported and the arithmetic holds.

The past-time guard withdrew correct reader answers whose value was computed from two recorded
events ("ordered on the 5th, arrived on the 10th -- 5 days"), because it re-derived intervals only
from the question's own frame wording. The contract here: each operand the answer cites must pass
the unchanged request-support law (actor, action, object, episode), and the stated value must equal
the arithmetic over those operands. Everything that was refused before for a wrong event, a wrong
actor, a wrong object or an invented value is still refused. Names, domains and dates below are
authored for this contract and appear in no benchmark.
"""

import json

from core.agent_runtime.turn_reasoning import _past_time_check_within_format
from core.model_output_guard import (
    replace_unsupported_past_time_claims,
    stated_past_time_claims,
    unverified_past_time_notice,
)
from core.raw_output_contract import RawOutputContract

_NOTICE_LEAD = unverified_past_time_notice("").split(",")[0]


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


KAYAK = [
    "- user said (stated 2025-03-02): I reserved the two-seat kayak for the estuary trip on March 2nd.",
    "- user said (stated 2025-03-09): We finally paddled the estuary in that kayak on March 9th.",
]


# ------------------------------------------------------------- computed value from cited operands


def test_day_count_from_two_cited_supported_dates_ships():
    question = "How many days passed between reserving the kayak and paddling the estuary?"
    answer = "Reserved on March 2nd, paddled on March 9th, so 7 days."
    assert guard(answer, question, KAYAK) == answer


def test_wrong_arithmetic_over_supported_operands_is_withdrawn():
    question = "How many days passed between reserving the kayak and paddling the estuary?"
    answer = "Reserved on March 2nd, paddled on March 9th, so 8 days."
    assert "8 days" not in guard(answer, question, KAYAK)


def test_value_and_operands_in_separate_sentences_ship():
    question = "How many days passed between reserving the kayak and paddling the estuary?"
    answer = "7 days. You reserved the kayak on March 2nd and paddled the estuary on March 9th."
    assert guard(answer, question, KAYAK) == answer


def test_whole_weeks_and_weeks_plus_days_split_ship():
    question = "How many weeks passed between reserving the kayak and paddling the estuary?"
    assert guard("Reserved on March 2nd, paddled on March 9th: exactly 1 week.", question, KAYAK).endswith("1 week.")
    evidence = [
        "- user said (stated 2025-01-04): I signed up for the pottery wheel course on January 4th.",
        "- user said (stated 2025-03-26): I glazed my first bowl at the pottery studio on March 26th.",
    ]
    question = "How many weeks passed between signing up for the pottery course and glazing my first bowl?"
    answer = "You signed up on January 4th and glazed the bowl on March 26th: 81 days, about 11.5 weeks (11 weeks and 4 days)."
    assert guard(answer, question, evidence) == answer


def test_hedged_value_rounds_at_most_half_a_unit():
    evidence = [
        "- user said (stated 2025-01-04): I signed up for the pottery wheel course on January 4th.",
        "- user said (stated 2025-03-26): I glazed my first bowl at the pottery studio on March 26th.",
    ]
    question = "How many weeks passed between signing up for the pottery course and glazing my first bowl?"
    far = "You signed up on January 4th and glazed the bowl on March 26th, about 13 weeks."
    assert "13 weeks" not in guard(far, question, evidence)


def test_reference_clock_is_never_an_operand():
    # The reader measured from today instead of between the two recorded events.
    evidence = [
        "- user said (stated 2025-05-21): I took the sourdough class at the bakery school yesterday.",
        "- user said (stated 2025-06-10): I baked the rye loaf for my neighbour's party today.",
    ]
    question = "How many days before baking the rye loaf did I take the sourdough class?"
    answer = "The class was 2025-05-20. Today is 2025-06-15, so that's 26 days ago."
    assert "26 days" not in guard(answer, question, evidence)


# ----------------------------------------------------------------- operands keep the support law


def test_operand_of_a_different_action_is_not_an_operand():
    evidence = [
        "- user said (stated 2025-03-02): I reserved the two-seat kayak for the estuary trip on March 2nd.",
        "- user said (stated 2025-03-09): I returned the kayak to the rental shed on March 9th.",
    ]
    question = "How many days passed between reserving the kayak and paddling the estuary?"
    answer = "Reserved on March 2nd, paddled on March 9th, so 7 days."
    delivered = guard(answer, question, evidence)
    assert "7 days" not in delivered


def test_operand_of_another_person_is_not_an_operand():
    evidence = [
        "- user said (stated 2025-03-02): Tamsin: I reserved the kayak on March 2nd.",
        "- user said (stated 2025-03-09): Odile: I paddled the estuary on March 9th.",
    ]
    question = "How many days passed between Tamsin reserving the kayak and Tamsin paddling the estuary?"
    answer = "Tamsin reserved it on March 2nd and paddled on March 9th, so 7 days."
    assert "7 days" not in guard(answer, question, evidence)


def test_invented_operand_date_cannot_carry_a_duration():
    question = "How many days passed between reserving the kayak and paddling the estuary?"
    answer = "Reserved on March 2nd, paddled on March 12th, so 10 days."
    delivered = guard(answer, question, KAYAK)
    assert "10 days" not in delivered and "March 12" not in delivered


# ------------------------------------------------------------------ relative points as operands


def test_difference_of_two_supported_relative_points_ships():
    evidence = [
        "- user said (stated 2025-08-20): I started practicing the cello every morning about 5 months ago.",
        "- user said (stated 2025-08-20): Last month, I finally played my first recital at the community hall.",
    ]
    question = "How long had I been practicing the cello every morning when I played my first recital?"
    answer = "About 4 months. You started practicing around 5 months ago and the recital was last month."
    assert guard(answer, question, evidence) == answer
    wrong = "About 2 months. You started practicing around 5 months ago and the recital was last month."
    assert "2 months" not in guard(wrong, question, evidence)


# ---------------------------------------------------- generalized support, still episode-bound


def test_each_possessed_subject_of_an_interval_question_is_an_asked_event():
    evidence = [
        "- user said (stated 2025-02-11): By the way, I bought my new Garmin running watch on February 2nd.",
        "- user said (stated 2025-02-11): The strap on my old Casio diving watch snapped on February 9th.",
    ]
    question = "How many days had passed since I bought my Garmin running watch when the strap on my old Casio diving watch snapped?"
    answer = "7 days: you bought the Garmin watch on February 2nd and the Casio strap snapped on February 9th."
    assert guard(answer, question, evidence) == answer


def test_bare_date_range_without_event_words_is_not_an_operand():
    # Known conservative limit: an operand must be bound to the event its own clause names.
    # "from February 2nd to February 9th" names none, so the computed value is not checked as
    # derived and is withdrawn even when it happens to be right.
    evidence = [
        "- user said (stated 2025-02-11): By the way, I bought my new Garmin running watch on February 2nd.",
        "- user said (stated 2025-02-11): The strap on my old Casio diving watch snapped on February 9th.",
    ]
    question = "How many days had passed since I bought my Garmin running watch when the strap on my old Casio diving watch snapped?"
    assert "7 days" not in guard("7 days, from February 2nd to February 9th.", question, evidence)


def test_a_parallel_episode_with_an_unasked_possession_still_cannot_supply_a_date():
    evidence = ["- user said (stated 2025-02-11): The strap on my cousin's sailing watch snapped on February 9th."]
    question = "How many days had passed since I bought my Garmin running watch when the strap on my old Casio diving watch snapped?"
    answer = "The strap snapped on February 9th."
    assert "February 9" not in guard(answer, question, evidence)


def test_just_with_a_past_verb_dates_the_event_at_its_statement():
    evidence = [
        "- user said (stated 2025-04-03): I just opened my ceramics shop on the high street.",
        "- user said (stated 2025-04-24): I sold my first teapot set in the shop today.",
    ]
    question = "How many days after opening my ceramics shop did I sell my first teapot set?"
    answer = "21 days: the shop opened on 2025-04-03 and the first teapot set sold on 2025-04-24."
    assert guard(answer, question, evidence) == answer


def test_just_without_a_completed_event_dates_nothing():
    evidence = ["- user said (stated 2025-04-03): I just need forty more stamps to finish the album."]
    question = "When did I finish the stamp album?"
    answer = "You finished the stamp album on April 3rd."
    assert "April 3" not in guard(answer, question, evidence)


def test_ordinal_of_month_and_abbreviated_month_dates_are_parsed():
    evidence = ["- user said (stated 2025-07-01): I hosted the lantern supper on the 14th of June."]
    question = "What was the date of the lantern supper I hosted?"
    assert guard("That was June 14th, the lantern supper.", question, evidence) == "That was June 14th, the lantern supper."
    evidence = [
        "- user said (stated 2025-02-03): I mailed the grant draft on Feb 3, 2025.",
        "- user said (stated 2025-02-17): The grant office confirmed receipt on February 17, 2025.",
    ]
    question = "How many days after mailing the grant draft did the office confirm receipt?"
    answer = "14 days. Mailed Feb 3, 2025; receipt confirmed Feb 17, 2025."
    assert guard(answer, question, evidence) == answer


# -------------------------------------------------------------- numerals, roles, report verbs


def test_number_words_joined_by_and_are_two_quantities():
    question = "How long did the glassblowing retreat last?"
    evidence = ["The glassblowing retreat lasted three days and two nights."]
    assert guard("The retreat lasted three days and two nights.", question, evidence).endswith("two nights.")
    assert "five days" not in guard("The retreat lasted five days and two nights.", question, evidence)
    evidence = ["The bridge restoration took one hundred and two days."]
    assert guard("It took one hundred and two days.", "How long did the bridge restoration take?", evidence) == (
        "It took one hundred and two days."
    )


def test_assistant_first_person_does_not_support_a_user_event():
    question = "When did I visit the lighthouse museum?"
    answer = "You visited the lighthouse museum on May 6, 2025."
    evidence = ["- assistant said (stated 2025-05-06): I visited the lighthouse museum today."]
    assert "May 6" not in guard(answer, question, evidence)
    evidence = ["- user said (stated 2025-05-06): I visited the lighthouse museum today."]
    assert guard(answer, question, evidence) == answer


def test_report_time_with_common_reporting_verbs_is_supported():
    question = "How long have I been keeping bees on the allotment?"
    answer = "According to what you noted on March 15th, you've been keeping bees on the allotment for 6 years."
    evidence = ["- user said (stated 2025-03-15): I've been keeping bees on the allotment for 6 years now."]
    assert guard(answer, question, evidence) == answer


# ------------------------------------------------------------ claim units and edit grammar


def test_abbreviation_period_does_not_split_a_claim():
    question = "How many days did I spend volunteering in November?"
    answer = "That's 1 day: November 9th, the food drive at St. Brigid's Hall with your sister."
    evidence = ["- user said (stated 2025-11-20): I volunteered at the food drive at St. Brigid's Hall on November 9th."]
    delivered = guard(answer, question, evidence)
    assert not delivered.startswith("Brigid")


def test_withdrawn_adjunct_never_leaves_broken_text():
    question = "Who gave me the brass compass?"
    answer = ("Nothing records who gave it to you. Last Saturday would have been October 4th, and while we "
              "talked about compasses that day, you never mentioned a giver. Tell me and I'll remember.")
    delivered = guard(answer, question, ["- user said (stated 2025-10-08): I got a brass compass last Saturday."])
    assert "been," not in delivered and "been ," not in delivered
    assert "Tell me and I'll remember." in delivered
    assert "****" not in guard("That was **October 4th**, the compass day.", "What date did I get the compass?", [])


# ------------------------------------------------------- a format request is not a truth exemption


def test_json_deliverable_is_checked_inside_its_values_and_stays_json():
    question = "When did I visit the lighthouse museum? Return one valid JSON object."
    contract = RawOutputContract(json_required=True, json_object=True)
    reply = json.dumps({"visited": "You visited the lighthouse museum on May 9, 2025."})
    check = lambda text, receipt: replace_unsupported_past_time_claims(  # noqa: E731
        text, question=question, evidence_texts=["- user said (stated 2025-05-06): I visited the lighthouse museum today."],
        decision_receipt=receipt,
    )
    receipt: dict = {}
    delivered = _past_time_check_within_format(reply, contract, check, receipt)
    parsed = json.loads(delivered)
    assert "May 9" not in parsed["visited"] and parsed["visited"].startswith(_NOTICE_LEAD)
    assert receipt["unsupported_values"]
    supported = json.dumps({"visited": "You visited the lighthouse museum on May 6, 2025."})
    assert _past_time_check_within_format(supported, contract, check, {}) == supported


def test_stated_claims_receipt_names_the_checked_operands():
    question = "How many days passed between reserving the kayak and paddling the estuary?"
    receipt: dict = {}
    stated_past_time_claims("Reserved on March 2nd, paddled on March 9th, so 7 days.",
                            question=question, evidence_texts=KAYAK, decision_receipt=receipt)
    assert receipt["operand_derivation"]["operand_dates"] == ["2025-03-02", "2025-03-09"]
    assert "d7days" in receipt["operand_derivation"]["derived_values"]
