"""A date for the record speaker's own communicative act is that record's statement time.

A record IS its speaker saying, sharing, telling or asking something in the conversation it comes
from. "On 14 May 2024, Dana shared a photo of her study corner" claims when Dana SHARED it, and Dana's
own record from 14 May 2024 is that sharing. Withdrawing the reply as an invented time removed a
correct answer the user asked for in full.

Contract (core.model_output_guard._communicative_act_values, _statement_is_answer_provenance):
  * supported, strict and licensed: the act's subject is the record's own speaker, the date dates the
    act itself (a fronted adverbial of its clause, or a date after the verb or its plain object),
    and the date equals the statement time at the claim's granularity (day, month-year, year);
  * not supported: another person's act; a date after an embedded clause ("said she went to the
    concert on <S>") -- the reported event's date; a record that itself reports an act of the same
    kind ("I told my landlord yesterday"); a date other than the statement time.
A hyphenated "never-" compound ("never-ending") is not a negated clause.
"""
from core.model_output_guard import replace_unsupported_past_time_claims, stated_past_time_claims

NOTICE = "I don't have that time in anything I can see from our conversation, so I am not going to state one."

DANA = ("- user said: Session date: 6:05 pm on 14 May, 2024\n"
        "Dana: Finals week has me swamped. This is my study corner right now. "
        "(stated: 6: 05 pm on 14 May, 2024; stated: 2024-05-14)")
LENA = ("- user said: Session date: 9:40 am on 20 May, 2024\n"
        "Lena: My week was calm, I mostly read on the balcony. "
        "(stated: 9: 40 am on 20 May, 2024; stated: 2024-05-20)")
WHAT = "What picture did Dana share about finals week?"
WHEN = "When did Dana share the photo of her study corner?"


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


def test_fronted_date_of_the_speakers_own_sharing_ships_unchanged():
    answer = "On 14 May 2024, Dana shared a photo of her study corner."
    assert guard(answer, WHAT, [DANA, LENA]) == answer


def test_date_after_the_object_of_the_sharing_ships_unchanged():
    answer = "Dana shared a photo of her study corner on 14 May 2024."
    assert guard(answer, WHAT, [DANA, LENA]) == answer


def test_date_right_after_the_verb_ships_unchanged():
    question = "What did Dana say about finals week?"
    answer = "Dana said on 14 May 2024 that finals week had her swamped."
    assert guard(answer, question, [DANA]) == answer


def test_a_when_question_about_the_sharing_is_answered_by_statement_time():
    answer = "Dana shared the photo of her study corner on 14 May 2024."
    assert guard(answer, WHEN, [DANA, LENA]) == answer


def test_month_and_year_granularity_of_the_sharing():
    for answer in ("In May 2024, Dana shared a photo of her study corner.",
                   "In 2024, Dana shared a photo of her study corner."):
        assert guard(answer, WHEN, [DANA]) == answer, answer


def test_another_month_of_the_same_year_is_not_the_sharing_date():
    answer = "In June 2024, Dana shared a photo of her study corner."
    assert stated_past_time_claims(answer, question=WHEN, evidence_texts=[DANA]) == ("y2024",)
    assert guard(answer, WHEN, [DANA]) == NOTICE


def test_another_day_is_not_the_sharing_date():
    answer = "On 15 May 2024, Dana shared a photo of her study corner."
    assert guard(answer, WHEN, [DANA]) == NOTICE


def test_another_speakers_act_is_not_supported_by_this_record():
    # Lena's sharing is not Dana's record, even on Dana's statement date.
    answer = "On 14 May 2024, Lena shared the photo of Dana's study corner."
    assert guard(answer, WHEN, [DANA, LENA]) == NOTICE
    answer = "Lena shared the photo of Dana's study corner on 14 May 2024."
    assert guard(answer, WHEN, [DANA, LENA]) == NOTICE


def test_another_speakers_act_is_not_supported_after_the_verb_either():
    question = "When did Dana mention the study corner?"
    answer = "Lena mentioned on 14 May 2024 the study corner."
    assert "14 May" not in guard(answer, question, [DANA, LENA])


def test_a_reported_event_at_statement_time_is_still_withdrawn():
    evidence = ["- user said: Session date: 14 May 2024\nDana: I went to the jazz concert last weekend. "
                "(stated: 2024-05-14)"]
    question = "When did Dana go to the jazz concert?"
    for answer in ("Dana went to the jazz concert on 14 May 2024.",
                   "Dana said she went to the jazz concert on 14 May 2024.",
                   "Dana told Lena about the jazz concert on 14 May 2024."):
        assert guard(answer, question, evidence) == NOTICE, answer


def test_a_record_reporting_its_own_telling_does_not_date_the_telling():
    evidence = ["- user said: Session date: 14 May 2024\nDana: I told my landlord about the leak yesterday. "
                "(stated: 2024-05-14)"]
    question = "When did Dana tell her landlord about the leak?"
    answer = "On 14 May 2024, Dana told her landlord about the leak."
    assert guard(answer, question, evidence) == NOTICE


def test_a_record_reporting_its_own_sending_does_not_date_the_sending():
    evidence = ["- user said: Session date: 14 May 2024\nDana: I sent the parcel to my aunt last week. "
                "(stated: 2024-05-14)"]
    question = "When did Dana send the parcel to her aunt?"
    for answer in ("On 14 May 2024, Dana sent the parcel to her aunt.",
                   "Dana sent the parcel to her aunt on 14 May 2024."):
        assert guard(answer, question, evidence) == NOTICE, answer


def test_the_object_must_be_what_the_record_says():
    # Lena's own record from 20 May does not carry Dana's study corner.
    question = "When did Lena share a photo?"
    answer = "On 20 May 2024, Lena shared a photo of a study corner buried under finals notes."
    assert guard(answer, question, [DANA, LENA]) == NOTICE


def test_a_never_compound_is_not_a_negated_sentence():
    evidence = ["- user said: Session date: 14 May 2024\nDana: This is my desk, buried under paperwork. "
                "(stated: 2024-05-14)"]
    question = "When did Dana share the photo of her desk?"
    answer = "On 14 May 2024, Dana shared the photo of her desk and its never-ending paperwork."
    assert guard(answer, question, evidence) == answer


def test_a_real_never_still_negates():
    evidence = ["- user said: Session date: 14 May 2024\nDana: This is my desk, buried under paperwork. "
                "(stated: 2024-05-14)"]
    question = "When did Dana share the photo of her desk?"
    answer = "On 14 May 2024, Dana never shared the photo of her desk."
    assert guard(answer, question, evidence) == NOTICE


def test_another_speakers_report_does_not_take_this_records_statement_time():
    # Dana's record supports the asked subject; "Lena said" names a person who is not its speaker.
    question = "What did Dana say about finals week?"
    answer = "On 14 May 2024, Lena said finals week had Dana swamped."
    result = guard(answer, question, [DANA, LENA])
    assert "14 May" not in result and "2024" not in result
    assert result == "Lena said finals week had Dana swamped."


def test_the_speakers_own_report_keeps_its_statement_time():
    question = "What did Dana say about finals week?"
    answer = "On 14 May 2024, Dana said finals week had her swamped."
    assert guard(answer, question, [DANA, LENA]) == answer


def test_a_record_reporting_its_own_sharing_does_not_date_the_sharing():
    evidence = ["- user said: Session date: 14 May 2024\nDana: I shared this photo with my study group yesterday. "
                "(stated: 2024-05-14)"]
    question = "When did Dana share the photo with her study group?"
    answer = "On 14 May 2024, Dana shared the photo with her study group."
    assert guard(answer, question, evidence) == NOTICE


def test_a_bracketed_attachment_note_is_the_sharing_itself():
    evidence = ["- user said: Session date: 14 May 2024\nDana: Look at this mess. [shared photo: a desk under "
                "paperwork] (stated: 2024-05-14)"]
    question = "When did Dana share the photo of her desk?"
    for answer in ("On 14 May 2024, Dana shared the photo of her desk under paperwork.",
                   "Dana shared the photo of her desk under paperwork on 14 May 2024."):
        assert guard(answer, question, evidence) == answer, answer


SUPPORTED_FAMILY = [
    "On 14 May 2024, Dana shared a photo of her study corner.",
    "Dana shared a photo of her study corner on May 14, 2024.",
    "Dana sent a pic of her study corner on 14 May 2024.",
    "on 14 may 2024, Dana showed her study corner",
    "Dana posted a photo of her study corner in May 2024.",
    "In 2024 Dana shared a photo of her study corner.",
    "Dana mentioned on 14 May 2024 that finals week had her swamped.",
]


def test_the_speakers_own_act_family_ships_unchanged():
    for question in (WHAT, "what pic did dana share about finals", "Which photo did Dana post about finals week?"):
        for answer in SUPPORTED_FAMILY:
            assert guard(answer, question, [DANA, LENA]) == answer, (question, answer)


def test_near_misses_of_the_family_are_not_the_speakers_act():
    for answer in ("Dana cleaned her study corner on 14 May 2024.",
                   "Dana's sister shared a photo of her study corner on 14 May 2024.",
                   "Lena shared a photo of the study corner on 14 May 2024.",
                   "Dana shared a photo of her study corner on 20 May 2024."):
        assert guard(answer, WHEN, [DANA, LENA]) == NOTICE, answer


def test_the_same_speakers_record_from_another_day_does_not_date_this_act():
    # Dana's 20 May record is about a beach towel; her study-corner photo is the 14 May record.
    later = ("- user said: Session date: 20 May 2024\nDana: Finals week is over, so here is my beach towel. "
             "(stated: 2024-05-20)")
    question = "What picture did Dana share about finals week?"
    for answer in ("On 20 May 2024, Dana shared a photo of her study corner.",
                   "Dana shared on 20 May 2024 a photo of her study corner.",
                   "Dana shared a photo of her study corner on 20 May 2024."):
        result = guard(answer, question, [DANA, later])
        assert "20 May" not in result and "study corner" in result, answer
    for answer in ("On 14 May 2024, Dana shared a photo of her study corner.",
                   "On 20 May 2024, Dana shared a photo of her beach towel."):
        assert guard(answer, question, [DANA, later]) == answer, answer


def test_your_own_reports_in_a_multi_clause_sentence_keep_their_dates():
    # The object binding is for a NAMED reporter; "you said" over the user's own records keeps its
    # reading even when the sentence carries other clauses.
    evidence = ["- user said (stated 2023-05-11): I went to three sessions of the grief group.",
                "- user said (stated 2023-10-30): Looking back, I went to five sessions of the grief group."]
    question = "How many sessions of the grief group did I attend?"
    answer = ("Your records show two numbers: on 11 May 2023, you said you attended three sessions, but later, "
              "on 30 October 2023, you remembered attending five. I can't say which is right.")
    assert guard(answer, question, evidence) == answer


def test_a_pronoun_subject_is_the_records_speaker_only_when_it_names_them():
    # "you shared" is the user's act, not Dana's record.
    answer = "On 14 May 2024, you shared a photo of your study corner."
    assert guard(answer, WHEN, [DANA, LENA]) == NOTICE


def test_a_record_of_a_person_not_asked_about_does_not_answer_the_asked_persons_time():
    question = "When did Lena share the photo of her study corner?"
    answer = "On 14 May 2024, Dana shared a photo of her study corner."
    assert guard(answer, question, [DANA, LENA]) == NOTICE
