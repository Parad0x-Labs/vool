"""Historical values retain actor, event and unambiguous derivation support."""
from core.model_output_guard import replace_unsupported_past_time_claims


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(
        answer, question=question, evidence_texts=evidence
    )


def test_two_source_dates_support_elapsed_days():
    question = "How many days did it take for me to find a house I loved after starting to work with Rachel?"
    answer = "14 days. You started with Rachel on February 15th and saw the house you loved on March 1st."
    evidence = [
        "- user said (stated 2022-03-02): Since I started working with Rachel on 2/15, I'd like to see new listings.",
        "- user said (stated 2022-03-02): The house I saw on March 1st really checks all the boxes.",
    ]
    assert guard(answer, question, evidence) == answer


def test_fresh_two_events_support_elapsed_days():
    question = "How many days passed between Nora filing the permit and collecting it?"
    answer = "Nora waited 9 days between filing and collecting the permit."
    evidence = [
        "Session date: 2024-04-15. Nora filed the permit on April 3, 2024.",
        "Session date: 2024-04-15. Nora collected the permit on April 12, 2024.",
    ]
    assert guard(answer, question, evidence) == answer


def test_relative_day_resolves_from_own_statement_date():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Session date: 20 October 2023. Nora: I collected the parcel yesterday."]
    assert guard(answer, question, evidence) == answer


def test_same_literal_date_for_wrong_actor_is_not_support():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Liam collected the parcel on 19 October 2023."]
    assert "19 October" not in guard(answer, question, evidence)


def test_same_literal_date_for_wrong_event_is_not_support():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Nora left the airport on 19 October 2023."]
    assert "19 October" not in guard(answer, question, evidence)


def test_relative_day_from_wrong_actor_is_not_support():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Session date: 20 October 2023. Liam: I collected the parcel yesterday."]
    assert "19 October" not in guard(answer, question, evidence)


def test_ambiguous_two_dates_do_not_authorize_duration():
    question = "How many days passed between Nora filing the permit and collecting it?"
    answer = "Nora waited 9 days between filing and collecting the permit."
    evidence = [
        "Nora filed the permit on April 3, 2024 or April 4, 2024; I'm unsure which.",
        "Nora collected the permit on April 12, 2024.",
    ]
    assert "9 days" not in guard(answer, question, evidence)


def test_other_actor_endpoint_cannot_complete_duration():
    question = "How many days passed between Nora filing the permit and collecting it?"
    answer = "Nora waited 9 days between filing and collecting the permit."
    evidence = [
        "Nora filed the permit on April 3, 2024.",
        "Liam collected the permit on April 12, 2024.",
    ]
    assert "9 days" not in guard(answer, question, evidence)


def test_no_date_anchor_cannot_resolve_yesterday():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Nora collected the parcel yesterday."]
    assert "19 October" not in guard(answer, question, evidence)


def test_supported_literal_date_retains_correct_actor_event():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Nora collected the parcel on October 19, 2023."]
    assert guard(answer, question, evidence) == answer


def test_unrelated_dates_cannot_supply_missing_duration():
    question = "How many days did Nora spend restoring the bicycle?"
    answer = "Nora spent 9 days restoring the bicycle."
    evidence = [
        "Nora filed the permit on April 3, 2024.",
        "Nora collected the permit on April 12, 2024.",
    ]
    assert "9 days" not in guard(answer, question, evidence)


def test_unsupported_clock_time_still_refuses():
    question = "What time did Nora collect the parcel?"
    answer = "Nora collected the parcel at 9:30 AM."
    evidence = ["Nora collected the parcel yesterday."]
    assert "9:30" not in guard(answer, question, evidence)


def test_same_actor_same_verb_different_object_is_not_support():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Nora collected the permit on 19 October 2023."]
    assert "19 October" not in guard(answer, question, evidence)


def test_native_shape_combined_start_finish_source_supports_duration():
    question = "How many days did our copper mosaic restoration take?"
    answer = "The copper mosaic restoration took 19 days, from 6 April 2025 to 25 April 2025."
    evidence = [
        "Session date: 25 April 2025\nNeri: Our copper mosaic restoration started on 6 April 2025 and finished on 25 April 2025."
    ]
    assert guard(answer, question, evidence) == answer


def test_native_shape_relative_date_supports_named_actor():
    question = "When did Orin finish the kiln rebuild?"
    answer = "Orin finished the kiln rebuild on 12 February 2025."
    evidence = [
        "Session date: 13 February 2025\nOrin: I finished the kiln rebuild yesterday. The replacement bearings finally arrived."
    ]
    assert guard(answer, question, evidence) == answer


def test_later_named_actor_does_not_borrow_source_speaker():
    question = "When did Neri finish the gallery mural?"
    answer = "Neri finished the gallery mural on 19 May 2025."
    evidence = [
        "Session date: 23 May 2025\nNeri: I finished the gallery mural today. Theo finished his gallery mural on 19 May 2025."
    ]
    assert "19 May" not in guard(answer, question, evidence)


def test_statement_date_does_not_replace_relative_event_date():
    question = "When did Orin finish the kiln rebuild?"
    answer = "According to what Orin said, Orin finished the kiln rebuild on 13 February 2025."
    evidence = ["Session date: 13 February 2025\nOrin: I finished the kiln rebuild yesterday."]
    assert "13 February" not in guard(answer, question, evidence)


def test_explicit_provenance_date_survives_alongside_derived_event_date():
    question = "When did Orin finish the kiln rebuild?"
    answer = "Orin said on 13 February 2025 that the kiln rebuild finished on 12 February 2025."
    evidence = ["Session date: 13 February 2025\nOrin: I finished the kiln rebuild yesterday."]
    assert guard(answer, question, evidence) == answer


def test_wrong_elapsed_number_is_not_authorized_by_endpoints():
    question = "How many days did our copper mosaic restoration take?"
    answer = "The copper mosaic restoration took 20 days, from 6 April 2025 to 25 April 2025."
    evidence = [
        "Session date: 25 April 2025\nNeri: Our copper mosaic restoration started on 6 April 2025 and finished on 25 April 2025."
    ]
    assert "20 days" not in guard(answer, question, evidence)


def test_reversed_start_finish_does_not_authorize_positive_duration():
    question = "How many days did our copper mosaic restoration take?"
    answer = "The copper mosaic restoration took 19 days."
    evidence = [
        "Neri: Our copper mosaic restoration started on 25 April 2025 and finished on 6 April 2025."
    ]
    assert "19 days" not in guard(answer, question, evidence)


def test_incompatible_statement_dates_leave_relative_event_unresolved():
    question = "When did Orin finish the kiln rebuild?"
    answer = "Orin finished the kiln rebuild on 12 February 2025."
    evidence = [
        "Session date: 13 February 2025\n(stated 14 February 2025)\nOrin: I finished the kiln rebuild yesterday."
    ]
    assert "12 February" not in guard(answer, question, evidence)


def test_uncertain_literal_date_does_not_authorize_firm_answer():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Nora collected the parcel on either 19 October 2023 or 20 October 2023; I'm unsure."]
    assert "19 October" not in guard(answer, question, evidence)


def test_conditional_literal_date_does_not_authorize_firm_answer():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["If the courier was on schedule, Nora collected the parcel on 19 October 2023."]
    assert "19 October" not in guard(answer, question, evidence)


def test_same_sentence_changed_actor_cannot_supply_date():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Nora collected the parcel while Liam collected the permit on 19 October 2023."]
    assert "19 October" not in guard(answer, question, evidence)


def test_reported_actor_is_not_the_reporting_actor():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on 19 October 2023."
    evidence = ["Nora said Liam collected the parcel on 19 October 2023."]
    assert "19 October" not in guard(answer, question, evidence)


def test_literal_date_does_not_authorize_different_answer_actor():
    question = "When did Nora collect the parcel?"
    answer = "Liam collected the parcel on 19 October 2023."
    evidence = ["Nora collected the parcel on 19 October 2023."]
    assert "19 October" not in guard(answer, question, evidence)


def test_deleted_operand_cannot_support_interval():
    question = "How many days did our copper mosaic restoration take?"
    answer = "The copper mosaic restoration took 19 days."
    evidence = ["Neri: Our copper mosaic restoration finished on 25 April 2025."]
    assert "19 days" not in guard(answer, question, evidence)


def test_two_endpoints_without_year_do_not_infer_wall_clock_year():
    question = "How many days passed between Nora filing the permit and collecting it?"
    answer = "Nora waited 9 days between filing and collecting the permit."
    evidence = ["Nora filed the permit on April 3.", "Nora collected the permit on April 12."]
    assert "9 days" not in guard(answer, question, evidence)


def test_leap_day_uses_recorded_year():
    question = "How many days passed between Nora filing the permit and collecting it?"
    answer = "Nora waited 2 days between filing and collecting the permit."
    evidence = ["Nora filed the permit on February 28, 2024.", "Nora collected the permit on March 1, 2024."]
    assert guard(answer, question, evidence) == answer


def test_uncertain_later_clause_does_not_remove_firm_duration():
    question = "How long have I worked as a professional designer?"
    answer = "You've worked as a professional designer for 9 years."
    evidence = ["I've worked as a professional designer for 9 years, but I'm unsure which notebook is efficient."]
    assert guard(answer, question, evidence) == answer


def test_source_note_date_and_duration_are_preserved():
    question = "How long had I used my tracker?"
    answer = "By your June 2nd note, you'd been using your tracker for 17 months."
    evidence = ["- user said (stated 2023-06-02): I've used my tracker for 17 months."]
    assert guard(answer, question, evidence) == answer


def test_conversation_citation_is_not_event_date():
    question = "Where did I meet Arun?"
    answer = "You met Arun in the coffee shop, from our conversation on May 21."
    evidence = ["- user said (stated 2023-05-21): I met Arun in the coffee shop."]
    assert guard(answer, question, evidence) == answer


def test_unsupported_optional_date_does_not_erase_color_answer():
    question = "What color did I repaint the bedroom?"
    answer = "You repainted the bedroom gray — done just before May 27."
    evidence = ["- user said (stated 2023-05-27): I recently repainted the bedroom gray."]
    result = guard(answer, question, evidence)
    assert "You repainted the bedroom gray" in result
    assert "May 27" not in result
    assert "not going to state" not in result
    assert result == "You repainted the bedroom gray."


def test_unsupported_parenthetical_date_does_not_erase_location():
    question = "Where did Nora move?"
    answer = "Nora moved to the city (May 24)."
    evidence = ["- user said (stated 2023-05-24): Nora recently moved to the city."]
    result = guard(answer, question, evidence)
    assert "Nora moved to the city" in result
    assert "May 24" not in result
    assert "not going to state" not in result


def test_unsupported_optional_date_does_not_erase_count():
    question = "How many postcards have I added since I resumed collecting?"
    answer = "You added 17 postcards, then bought 8 more on November 30 — 25 postcards total."
    evidence = ["- user said (stated 2023-11-30): I've added 25 postcards since I resumed collecting."]
    result = guard(answer, question, evidence)
    assert "25 postcards total" in result
    assert "November 30" not in result
    assert "not going to state" not in result


def test_unsupported_primary_date_still_withdraws():
    question = "When did Nora collect the parcel?"
    answer = "Nora collected the parcel on May 24."
    evidence = ["Nora collected the parcel."]
    result = guard(answer, question, evidence)
    assert "May 24" not in result
    assert result.startswith("I don't have that time")


def test_report_of_event_is_not_the_date_of_reporting():
    question = "When did Orin finish the kiln rebuild?"
    answer = "You said Orin finished the kiln rebuild on 13 February 2025."
    evidence = ["Session date: 13 February 2025\nOrin: I finished the kiln rebuild yesterday."]
    assert "13 February" not in guard(answer, question, evidence)


def test_optional_date_removal_preserves_balanced_prose():
    question = "Where did I get my guitar serviced?"
    answer = "You had the guitar serviced on Main St — you said they were good (that was back on May 29)."
    evidence = ["- user said (stated 2023-05-29): I had the guitar serviced on Main St, and they were good."]
    result = guard(answer, question, evidence)
    assert "Main St" in result
    assert "May 29" not in result
    assert result.count("(") == result.count(")")


def test_optional_date_removal_does_not_leave_double_commas():
    question = "Which event did I attend first?"
    answer = "The bake sale came first. I attended the gallery later, around March 28."
    evidence = ["I attended the bake sale before the gallery."]
    result = guard(answer, question, evidence)
    assert "bake sale came first" in result
    assert "March 28" not in result
    assert ",." not in result


def test_named_place_binds_date_for_non_temporal_visit_count():
    question = "How many different historic venues did I visit in September?"
    answer = "You visited one: Blue Harbor Archive on 12 September 2024."
    evidence = ["I met the archivist at the opening of Blue Harbor Archive on 12 September 2024, but I'm unsure where the leaflet came from."]
    assert guard(answer, question, evidence) == answer


def test_named_place_cannot_overrule_wrong_actor():
    question = "How many public gardens did Nora visit in September?"
    answer = "Nora visited Blue Harbor Garden on 12 September 2024."
    evidence = ["Liam visited Blue Harbor Garden on 12 September 2024."]
    assert "12 September" not in guard(answer, question, evidence)


def test_named_place_cannot_overrule_direct_wrong_event():
    question = "When did Nora file the permit at Blue Harbor Garden?"
    answer = "Nora filed the permit at Blue Harbor Garden on 12 September 2024."
    evidence = ["Nora collected the parcel at Blue Harbor Garden on 12 September 2024."]
    assert "12 September" not in guard(answer, question, evidence)


def test_report_date_when_we_talked_about_it_is_supported():
    question = "How many spare bulbs did I find in storage?"
    answer = "You found 17 spare bulbs — when we talked about it on May 27."
    evidence = ["- user said (stated 2023-05-27): I found 17 spare bulbs in storage."]
    assert guard(answer, question, evidence) == answer


def test_parenthetical_report_date_around_when_said_is_supported():
    question = "Where did I go with the family?"
    answer = "You went to the coast a month before you said it (around May 22)."
    evidence = ["- user said (stated 2023-05-22): I went to the coast with the family last month."]
    assert guard(answer, question, evidence) == answer


def test_talking_about_an_event_does_not_make_statement_date_event_date():
    question = "When did Nora collect the parcel?"
    answer = "We talked about Nora collecting the parcel on May 27."
    evidence = ["Session date: 27 May 2023\nNora collected the parcel yesterday."]
    assert "May 27" not in guard(answer, question, evidence)


def test_trip_span_supports_calendar_days_and_elapsed_nights():
    question = "How many days did I spend on my solo camping trip to Pine Ridge?"
    answer = "You spent 3 days (2 nights) on the Pine Ridge trip, from May 15 to May 17."
    evidence = [
        "- user said (stated 2023-05-15): I started my solo camping trip to Pine Ridge today.",
        "- user said (stated 2023-05-17): I just got back from my solo camping trip to Pine Ridge today.",
    ]
    assert guard(answer, question, evidence) == answer


def test_lodge_stay_supports_calendar_days_and_elapsed_nights():
    question = "How many days did Nora spend on the lodge stay?"
    answer = "Nora's lodge stay covered 4 days and 3 nights."
    evidence = [
        "Nora: Our lodge stay started on 10 June 2024.",
        "Nora: I returned from the lodge stay on 13 June 2024.",
    ]
    assert guard(answer, question, evidence) == answer


def test_trip_calendar_span_does_not_support_extra_day():
    question = "How many days did Nora spend on the lodge stay?"
    answer = "Nora's lodge stay covered 5 days and 3 nights."
    evidence = ["Nora: Our lodge stay started on 10 June 2024 and ended on 13 June 2024."]
    assert "5 days" not in guard(answer, question, evidence)


def test_trip_calendar_span_does_not_support_extra_night():
    question = "How many days did Nora spend on the lodge stay?"
    answer = "Nora's lodge stay covered 4 days and 4 nights."
    evidence = ["Nora: Our lodge stay started on 10 June 2024 and ended on 13 June 2024."]
    assert "4 nights" not in guard(answer, question, evidence)


def test_trip_explicit_elapsed_query_keeps_elapsed_count():
    question = "How many elapsed days passed on Nora's lodge stay?"
    answer = "Nora's lodge stay lasted 4 days."
    evidence = ["Nora: Our lodge stay started on 10 June 2024 and ended on 13 June 2024."]
    assert "4 days" not in guard(answer, question, evidence)


def test_trip_between_query_does_not_add_calendar_day():
    question = "How many days passed between Nora starting the lodge stay and ending it?"
    answer = "4 days passed between Nora starting and ending the lodge stay."
    evidence = ["Nora: Our lodge stay started on 10 June 2024 and ended on 13 June 2024."]
    assert "4 days" not in guard(answer, question, evidence)


def test_trip_explicit_elapsed_query_supports_exact_count():
    question = "How many elapsed days passed on Nora's lodge stay?"
    answer = "Nora's lodge stay lasted 3 days."
    evidence = ["Nora: Our lodge stay started on 10 June 2024 and ended on 13 June 2024."]
    assert guard(answer, question, evidence) == answer


def test_calendar_trip_query_does_not_authorize_wrong_elapsed_label():
    question = "How many days did Nora spend on the lodge stay?"
    answer = "Nora's lodge stay lasted 4 elapsed days."
    evidence = ["Nora: Our lodge stay started on 10 June 2024 and ended on 13 June 2024."]
    assert "4 elapsed days" not in guard(answer, question, evidence)


def test_trip_between_query_supports_exact_elapsed_count():
    question = "How many days passed between Nora starting the lodge stay and ending it?"
    answer = "3 days passed between Nora starting and ending the lodge stay."
    evidence = ["Nora: Our lodge stay started on 10 June 2024 and ended on 13 June 2024."]
    assert guard(answer, question, evidence) == answer


def test_same_permit_different_action_does_not_support_filing_date():
    question = "When did Nora file the permit?"
    answer = "Nora filed the permit on 12 September 2024."
    evidence = ["Nora collected the permit on 12 September 2024."]
    assert "12 September" not in guard(answer, question, evidence)


def test_payment_paraphrase_does_not_authorize_selling_date_as_buy_date():
    question = "When did I buy the ferry ticket?"
    answer = "You bought the ferry ticket on 12 September 2024."
    evidence = ["I sold the ferry ticket on 12 September 2024."]
    assert "12 September" not in guard(answer, question, evidence)


def test_arrival_paraphrase_does_not_authorize_departure_date():
    question = "What time did I reach the clinic?"
    answer = "You reached the clinic at 9:00 AM."
    evidence = ["I left the clinic at 9:00 AM."]
    assert "9:00" not in guard(answer, question, evidence)


def test_lowercase_question_actor_cannot_borrow_another_persons_date():
    question = "When did orin finish the kiln rebuild?"
    answer = "It finished on 19 May 2025."
    evidence = [
        "- user said (stated 2025-05-20): Lena: I finished the kiln rebuild on 19 May 2025.",
        "- user said (stated 2025-05-22): Orin: I finished the kiln rebuild on 21 May 2025.",
    ]
    assert "19 May" not in guard(answer, question, evidence)


def test_lowercase_question_actor_matches_lowercase_source_label():
    question = "When did orin finish the kiln rebuild?"
    answer = "It finished on 21 May 2025."
    evidence = [
        "- user said (stated 2025-05-20): lena: I finished the kiln rebuild on 19 May 2025.",
        "- user said (stated 2025-05-22): orin: I finished the kiln rebuild on 21 May 2025.",
    ]
    assert guard(answer, question, evidence) == answer


def test_actor_capitalization_keeps_same_negative_verdict():
    answer = "It finished on 19 May 2025."
    evidence = [
        "lena: I finished the kiln rebuild on 19 May 2025.",
        "orin: I finished the kiln rebuild on 21 May 2025.",
    ]
    capitalized = guard(answer, "When did Orin finish the kiln rebuild?", evidence)
    lowercase = guard(answer, "When did orin finish the kiln rebuild?", evidence)
    assert capitalized.startswith("I don't have that time")
    assert lowercase.startswith("I don't have that time")
    assert "19 May" not in guard(answer, "When did ORIN finish the kiln rebuild?", evidence)


def test_bare_between_actions_are_not_actor_names():
    question = "How many days passed between filing the permit and collecting it?"
    answer = "9 days passed between filing and collecting the permit."
    evidence = ["I filed the permit on April 3, 2024.", "I collected the permit on April 12, 2024."]
    assert guard(answer, question, evidence) == answer


def test_had_passed_since_is_not_a_lowercase_actor():
    question = "How many days had passed since I started pottery classes when I took the lathe for servicing?"
    answer = "You started pottery classes on March 1 and took the lathe for servicing on March 15, 2024."
    evidence = [
        "- user said (stated 2024-03-01): I started pottery classes today.",
        "- user said (stated 2024-03-15): I took the lathe for servicing today.",
    ]
    assert guard(answer, question, evidence) == answer


def test_has_elapsed_since_is_not_a_lowercase_actor():
    question = "How much time has elapsed since I repaired the gate?"
    answer = "You repaired the gate on March 1, 2024."
    evidence = ["I repaired the gate on March 1, 2024."]
    assert guard(answer, question, evidence) == answer


def test_optional_date_without_substantive_main_answer_keeps_refusal():
    question = "What color did I repaint the bedroom?"
    answer = "It was on May 27."
    evidence = ["- user said (stated 2023-05-27): I recently repainted the bedroom gray."]
    result = guard(answer, question, evidence)
    assert result.startswith("I don't have that time")
    assert "May 27" not in result


def test_separate_unsupported_date_sentence_keeps_clean_color_answer():
    question = "What color did I repaint the bedroom?"
    answer = "You repainted the bedroom gray. That was in 2019."
    evidence = ["I recently repainted the bedroom gray."]
    assert guard(answer, question, evidence) == "You repainted the bedroom gray."
