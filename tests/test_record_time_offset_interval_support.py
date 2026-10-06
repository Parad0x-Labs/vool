"""A value the records state or that is checked from them is not withdrawn for an auxiliary value.

Measured on the archived paid replies (zero spend, replayed through the guard): the past-time guard
withdrew right date answers for an AUXILIARY value of the sentence while the answer's own event date
was supported:

(A) a record's own time of day. "The night before the <clock> conversation on <S> -- so <S-1>." The
    session header and the record suffix ("Session date: <clock> on <S>", "(stated: <clock> on <S>)")
    are removed from the record's body before its values are read, so the clock time was a value of
    nothing. The law: a clock time the answer presents AS A RECORD'S TIME (it names the record, is the
    time of a report, or is glossed as one) is supported when a record carries that statement clock:
    the record stated on the date the clock is stated with, else a request-bound record. A clock time
    standing as the event's own time is never the record's clock, and a clock no record carries stays
    unsupported. Written forms are one value: "6:55 pm", "6:55pm", "6:55 PM", "6:55 p.m." and "18:55";
    a time written with no meridiem ("6:55") is supported when either reading is.
    Reviewer near-misses: a record noun that is also an event ("a recording session", "a call with his
    sister", "a talk on beekeeping") or a report with a third party ("chatted with the mayor") was taken
    as a record citation. The law: a noun phrase with an event's own modifier (an activity, a third
    party it is with or belongs to, a topic or a place) is that event; an event-capable noun, or one the
    question asks about, is the record only where the answer cites it (relates a time to it, has it
    report, reports inside it) and never under an event predicate ("was at", "was held at"); a report
    is the record only when it addresses the conversation's own partner (a record speaker, an object
    pronoun), named or not ("told <speaker> at <clock>").

(B) a record-stated offset. "Four years before <S>, so around <S-4y>." for a record of the asked
    subject stated on S saying "four years ago I was in <a place in the asked country>", and "Around
    <S-5y> -- on <S> <subject> said they'd been a couple for five years." The record names the asked
    event only by knowledge the guard does not have. The law, under the user's own request for an
    approximate date read from the conversation: a record of an asked subject, about the asked event
    (it names the asked object, or states the same kind of event: a stay at a named place, a
    relationship's state), with statement time S and an offset it states back from S ("N units ago", or
    a present-perfect state "for N units" of an atelic verb reaching S) supports the offset's duration
    and each claim the offset's window holds AT THE OFFSET'S GRANULARITY, when the clause cites that
    record: it states the same offset AND names S, and names nothing beyond the asked event, the
    record's words, people and time grammar. A telic act's "for N units" ("booked the cabin for two
    weeks", "signed a lease for two years") is the length it arranged, not a state. A follow-up naming
    nobody asks about the conversation's latest named subject. Outside the license the event-bound
    statement-time rule reads the same windows, and nothing else changes.

(C) an interval between a supported date and a cited record time. "<D> (two days before the <D+2>
    conversation)." The law: an interval the clause states equals the difference between two of its
    own supported day dates, at the unit's granularity, when one of them is a date the clause presents
    as a record's time and is a record's statement time; the other is that record time, a bare date, or
    an event date bound to its own event (the checked-operand law). A date only the license's
    approximation supports counts only when a record of the asked subject made at that record time
    states that very day ("on the 6th", "two days ago"). Two bare dates, a date of another action, wrong
    arithmetic, a date no record was stated on, or an approximate date no record states leave the
    interval unsupported.

Every name, place and sentence below is authored for this contract and appears in no benchmark.
"""

from __future__ import annotations

import pytest

from core.model_output_guard import (
    replace_unsupported_past_time_claims,
    stated_past_time_claims,
    unverified_past_time_notice,
)

NOTICE = unverified_past_time_notice("")


def guard(answer, question, evidence):
    return replace_unsupported_past_time_claims(answer, question=question, evidence_texts=evidence)


def derivations(answer, question, evidence, key):
    receipt: dict = {}
    stated_past_time_claims(answer, question=question, evidence_texts=evidence, decision_receipt=receipt)
    return [entry for check in receipt["checks"] for entry in check[key]]


def header(speaker, text, clock, day, iso):
    # A capsule record bound to its speaker under its session header, with its statement suffix.
    return (f'- user said [reported source prefix "{speaker}:"]: Session date: {clock} on {day}\n'
            f"{speaker}: {text} (stated: Session date: {day}; stated: {iso})")


def suffix(speaker, text, clock, day, iso):
    # A capsule fragment whose only statement time is the record suffix ("stated: 4: 05 pm on ...").
    return (f'- user said [reported source prefix "{speaker}:"]: {text} '
            f"(stated: {clock.replace(':', ': ', 1)} on {day}; stated: {iso})")


def plain(speaker, text, iso):
    return f"- user said (stated {iso}): {speaker}: {text}"


APPROX = " An approximate date is fine."


# === (A) a record's own time of day ==============================================================

CHOIR_Q = "When did Odile rehearse with her choir?"
CHOIR_E = [
    header("Odile", "Last night was wonderful, my choir and I rehearsed the whole new programme.",
           "6:55 pm", "12 May, 2024", "2024-05-12"),
    header("Bram", "Lovely. I spent the evening patching my canoe.", "7:02 pm", "12 May, 2024", "2024-05-12"),
    suffix("Odile", "The concert in the old observatory went well, the hall was full.",
           "4:05 pm", "2 June, 2024", "2024-06-02"),
]

RECORD_CLOCK_KEPT = [
    # the reported shape
    "The night before the 6:55 pm conversation on 12 May 2024 — so 11 May 2024.",
    # clean paraphrases: other nouns, a report verb, a gloss, the 24-hour and dotted forms
    "11 May 2024 — the evening before her 6:55 pm message on 12 May 2024.",
    "Odile rehearsed on 11 May 2024; she mentioned it at 6:55 pm on 12 May 2024.",
    "Around 11 May 2024, the day before the session at 6:55 pm on 12 May 2024.",
    "6:55 pm on 12 May 2024, when Odile said the rehearsal had been the night before — so 11 May 2024.",
    "11 May 2024 (per the 18:55 conversation on 12 May 2024).",
    "11 May 2024 — she said so at 6:55 p.m. on 12 May 2024.",
    # sloppy: lower case, no punctuation, casual nouns, yearless record date, run-on
    "night before the 6:55pm convo on may 12 2024 so may 11 2024",
    "may 11 2024 - the 6:55 PM chat on 12 may 2024 says last night",
    "11 may 2024 (she mentioned it at 6:55pm on 12 may)",
    "6:55pm 12 may 2024 when odile said it, so 11 may 2024",
    "the 6:55 pm session on 12 May 2024 says it was the night before so 11 May 2024",
    "11 may 2024 (odile said it 6:55 pm on 12 may 2024)",
    "11 May 2024 (message sent 6:55 pm, 12 May 2024)",
    "Her 12 May 2024 note (6:55 pm) says last night, so 11 May 2024.",
    "11 May 2024 (from the 18:55 conversation on 12 May 2024)",
    # outside any parenthesis the date the record noun is stated with names the record, not the nearest
    "11 May 2024, from the 18:55 conversation on 12 May 2024",
]


@pytest.mark.parametrize("answer", RECORD_CLOCK_KEPT)
def test_a_record_time_of_day_the_answer_cites_as_the_records_time_is_supported(answer):
    assert guard(answer, CHOIR_Q + APPROX, CHOIR_E) == answer
    entries = derivations(answer, CHOIR_Q + APPROX, CHOIR_E, "statement_clock_derivation")
    assert any(entry["rule"] == "statement_clock" for entry in entries), entries


def test_a_record_time_of_day_in_another_sentence_is_bound_through_the_request():
    answer = "Odile rehearsed with her choir on 11 May 2024. She brought it up in her 6:55 pm message the next day."
    assert guard(answer, CHOIR_Q + APPROX, CHOIR_E) == answer
    assert [entry["binding"] for entry in derivations(answer, CHOIR_Q + APPROX, CHOIR_E, "statement_clock_derivation")] == [
        "request_bound_record"]


def test_a_time_in_parentheses_after_its_dated_record_names_that_record():
    # Bram's record is not about the asked rehearsal: only the date the time is given with binds it.
    answer = "Odile rehearsed on 11 May 2024; Bram's 12 May 2024 reply (7:02 pm) mentions it."
    assert guard(answer, CHOIR_Q + APPROX, CHOIR_E) == answer
    assert "7:40" not in guard("Odile rehearsed on 11 May 2024; Bram's 12 May 2024 reply (7:40 pm) mentions it.",
                               CHOIR_Q + APPROX, CHOIR_E)


def test_the_record_suffix_clock_with_a_space_after_its_colon_is_a_record_time():
    question = "When was Odile's concert in the observatory?" + APPROX
    answer = "Around 1 June 2024; she wrote about it at 4:05 pm on 2 June 2024."
    assert guard(answer, question, CHOIR_E) == answer


def test_a_follow_up_without_a_named_subject_keeps_the_cited_record_time():
    # A later turn asks again with no name; the earlier exchange is part of the evidence.
    evidence = [*CHOIR_E, "- user said: When did Odile rehearse with her choir?",
                "- assistant said: She rehearsed the night before her 12 May 2024 message."]
    answer = "The night before the 6:55 pm conversation on 12 May 2024 — so 11 May 2024."
    assert guard(answer, "Roughly when was that rehearsal again?", evidence) == answer


RECORD_CLOCK_WITHDRAWN = [
    # the clock time stands as the event's own time
    "Odile rehearsed with her choir at 6:55 pm on 12 May 2024.",
    # a clock time no record carries
    "The night before the 7:40 pm conversation on 12 May 2024 — so 11 May 2024.",
    # a record's clock stated with a date no record at that time was made on
    "The night before the 6:55 pm conversation on 20 May 2024 — so 19 May 2024.",
    # near miss: glossed with the conversation, but the clock is still the arrival time
    "Odile reached the rehearsal hall at 6:55 pm, the time of the conversation on 12 May 2024.",
    # near miss: the report verb's object is the event itself ("said she rehearsed at <clock>")
    "Odile said she rehearsed at 6:55 pm on 12 May 2024.",
    "odile mentioned the choir rehearsing at 6:55pm 12 may 2024",
]


@pytest.mark.parametrize("answer", RECORD_CLOCK_WITHDRAWN)
def test_a_record_clock_is_never_the_events_time_nor_a_time_no_record_carries(answer):
    delivered = guard(answer, CHOIR_Q + APPROX, CHOIR_E)
    assert delivered != answer and NOTICE in delivered, delivered
    assert not any(token in delivered for token in ("6:55", "7:40"))


def test_another_speakers_record_time_does_not_answer_for_the_asked_subject():
    assert "6:55" not in guard("Bram mentioned it at 6:55 pm on 12 May 2024.", CHOIR_Q + APPROX, CHOIR_E)


def test_without_the_license_a_record_clock_given_as_the_event_time_is_withdrawn():
    assert "6:55" not in guard("Odile rehearsed at 6:55 pm.", "What time did Odile rehearse with her choir?", CHOIR_E)


# --- written forms of one time of day ----------------------------------------------------------

FERRY_Q = "What time did Rasmus take the ferry from the harbour?"


@pytest.mark.parametrize("record,kept,withdrawn", [
    ("I took the ferry from the harbour at 6:55 pm sharp.", "He took the ferry at 18:55.", "He took the ferry at 6:55 am."),
    ("I took the ferry from the harbour at 18:55 sharp.", "He took the ferry at 6:55 pm.", "He took the ferry at 6:55 am."),
    ("I took the ferry from the harbour at 00:40 again.", "He took the ferry at 12:40 a.m.", "He took the ferry at 12:40 pm."),
    ("I took the ferry from the harbour at 9:45 am.", "He took the ferry at 9:45.", "He took the ferry at 9:45 pm."),
    ("I took the ferry from the harbour at 9:45 am.", "He took the ferry at 9:45 a.m.", "He took the ferry at 9:45 p.m."),
])
def test_a_time_of_day_is_one_value_in_its_common_written_forms(record, kept, withdrawn):
    evidence = [plain("Rasmus", record, "2024-04-02")]
    assert guard(kept, FERRY_Q, evidence) == kept
    assert guard(withdrawn, FERRY_Q, evidence) != withdrawn


def test_a_dotted_meridiem_does_not_split_the_time_from_its_date():
    # Bram's record is not about the asked rehearsal: its clock is bound only by the date stated with it.
    answer = "Odile rehearsed on 11 May 2024; Bram replied at 7:02 p.m. on 12 May 2024."
    assert guard(answer, CHOIR_Q + APPROX, CHOIR_E) == answer
    entries = derivations(answer, CHOIR_Q + APPROX, CHOIR_E, "statement_clock_derivation")
    assert [entry["binding"] for entry in entries] == ["stated_date"], entries


def test_a_minute_less_hour_is_never_read_out_of_a_clock_with_a_space_after_its_colon():
    # "6: 05 pm" is 6:05 pm: neither a record nor an answer states a bare "5 pm" with it.
    question = "What time did Ragnhild leave the tram depot?"
    spaced = [plain("Ragnhild", "I left the tram depot at 6: 05 pm, an hour late.", "2024-02-20")]
    assert "5 pm" not in guard("Ragnhild left the tram depot at 5 pm.", question, spaced)
    joined = [plain("Ragnhild", "I left the tram depot at 6:05 pm, an hour late.", "2024-02-20")]
    assert guard("She left the tram depot at 6: 05 pm.", question, joined) == "She left the tram depot at 6: 05 pm."


# --- a record noun that names an event is that event, not the record ---------------------------

QUARTET_Q = "When did Thibaut have his recording session with the quartet?"
QUARTET_E = [
    header("Thibaut", "The recording session with the quartet last night ran until two, I'm exhausted.",
           "10:40 am", "9 June, 2024", "2024-06-09"),
    header("Ottilie", "Get some sleep! I spent the morning repotting basil.", "10:46 am", "9 June, 2024", "2024-06-09"),
]

EVENT_NOUN_CLOCK_WITHDRAWN = [
    # the record's clock given to the asked session itself
    "Thibaut's recording session with the quartet was at 10:40 am on 9 June 2024.",
    "The recording session took place at 10:40 am on 9 June 2024.",
    "He had the 10:40 am session with the quartet on 9 June 2024.",
    "Thibaut's session was at 10:40 am on 9 June 2024.",
    "On 9 June 2024 — the 10:40 am session with the quartet.",
    "He joined the quartet's 10:40 am session on 9 June 2024.",
    "Thibaut recorded with the quartet in the 10:40 am session on 9 June 2024.",
    # sloppy
    "thibauts session w the quartet was at 10:40am on 9 june 2024",
    "the quartet session happened at 10:40 am 9 june 2024",
    # a record noun that is a third party's, or at a place, or on a topic: not this record
    "8 June 2024 — the night before his sister's 10:40 am message on 9 June 2024.",
    "8 June 2024 — the night before his 10:40 am conversation at the café on 9 June 2024.",
    "8 June 2024 — the night before his 10:40 am talk on lichens on 9 June 2024.",
    # a report with somebody outside the conversation
    "Thibaut told his sister at 10:40 am on 9 June 2024 about the session.",
    "At 10:40 am on 9 June 2024 Thibaut chatted with the sound engineer.",
    "Thibaut talked to the quartet at 10:40 am on 9 June 2024.",
]


@pytest.mark.parametrize("answer", EVENT_NOUN_CLOCK_WITHDRAWN)
def test_a_record_clock_given_to_an_event_noun_or_a_third_party_report_is_withdrawn(answer):
    delivered = guard(answer, QUARTET_Q + APPROX, QUARTET_E)
    assert delivered != answer and NOTICE in delivered, delivered
    assert "10:40" not in delivered


RECORD_NOUN_CITED_KEPT = [
    # an event-capable noun the answer cites: relates a time to it, has it report, reports inside it
    "8 June 2024 — the night before the session at 10:40 am on 9 June 2024.",
    "8 June 2024, per the 10:40 session on 9 June 2024.",
    "the 10:40 am chat on 9 June 2024 says it was last night, so 8 June 2024",
    "8 June 2024 — in the 10:40 am chat on 9 June 2024 he said it was the night before.",
    # the conversation's own partner, by name or pronoun
    "8 June 2024 — the night before his 10:40 am conversation with Ottilie on 9 June 2024.",
    "8 June 2024; he told Ottilie at 10:40 am on 9 June 2024 that it ran until two.",
    "8 June 2024 — Thibaut wrote to Ottilie at 10:40 am on 9 June 2024 about it.",
    "8 June 2024 — he told her at 10:40 am on 9 June 2024 that it was the night before.",
    # a timestamp gloss
    "On 8 June 2024 (his 9 June 2024 message, timestamped 10:40 am, says last night).",
    "8 June 2024 (message timestamped 10:40 am, 9 June 2024)",
    # sloppy
    "8 june 2024, nite b4 the 10:40am chat 9 june",
    "8 june 2024 - he told ottilie at 10:40am on 9 june it was last night",
    "8 jun 2024, the nite before his 10:40 AM convo w Ottilie on 9 jun 2024",
]


@pytest.mark.parametrize("answer", RECORD_NOUN_CITED_KEPT)
def test_a_record_noun_or_report_the_answer_cites_keeps_the_records_clock(answer):
    assert guard(answer, QUARTET_Q + APPROX, QUARTET_E) == answer
    entries = derivations(answer, QUARTET_Q + APPROX, QUARTET_E, "statement_clock_derivation")
    assert any(entry["rule"] == "statement_clock" for entry in entries), entries


def test_a_follow_up_keeps_a_cited_record_clock_and_withdraws_an_event_one():
    evidence = [*QUARTET_E, "- user said: When did Thibaut have his recording session with the quartet?"]
    cited = "8 June 2024 — the night before his 10:40 am message on 9 June 2024."
    assert guard(cited, "And roughly when was the recording?", evidence) == cited
    assert "10:40" not in guard("Thibaut's session was at 10:40 am on 9 June 2024.", "And roughly when was the recording?", evidence)


@pytest.mark.parametrize("question,evidence,withdrawn,kept", [
    ("When did Ottilie phone the clockmaker?",
     [header("Ottilie", "I phoned the clockmaker this morning, he is coming on Friday.", "7:15 pm", "2 October, 2024", "2024-10-02")],
     ["Ottilie's call with the clockmaker was at 7:15 pm on 2 October 2024.",
      "On 2 October 2024 — the 7:15 pm call with the clockmaker.",
      "Ottilie told the clockmaker at 7:15 pm on 2 October 2024 that the pedal squeaks."],
     "2 October 2024 — that morning, according to her 7:15 pm message on 2 October 2024."),
    ("When did Halvard give his talk on lichens?",
     [header("Halvard", "My talk on lichens at the library yesterday drew a full room.", "9:05 am", "3 November, 2024", "2024-11-03")],
     ["Halvard gave the lichen talk at 9:05 am on 3 November 2024.",
      "His talk on lichens was held at 9:05 am on 3 November 2024.",
      "halvard gave his talk at 9:05am on 3 nov 2024"],
     "2 November 2024 — the day before his 9:05 am message on 3 November 2024."),
])
def test_a_call_or_talk_given_the_records_clock_is_the_event_not_the_record(question, evidence, withdrawn, kept):
    for answer in withdrawn:
        delivered = guard(answer, question + APPROX, evidence)
        assert delivered != answer and NOTICE in delivered, (answer, delivered)
    assert guard(kept, question + APPROX, evidence) == kept


@pytest.mark.parametrize("answer", [
    "Halvard repainted his canoe during the 9:05 am call on 3 November 2024.",
    "He repainted it in the 9:05 am session on 3 November 2024.",
    "halvard repainted the canoe at the 9:05am chat 3 nov 2024",
])
def test_an_event_capable_noun_the_question_does_not_name_is_still_an_event_unless_cited(answer):
    # "call", "session" and "chat" are not asked about here; uncited, they are the repainting's occasion.
    question = "When did Halvard repaint his canoe?" + APPROX
    evidence = [header("Halvard", "I repainted my canoe yesterday, it is bright red now.", "9:05 am", "3 November, 2024", "2024-11-03")]
    delivered = guard(answer, question, evidence)
    assert delivered != answer and NOTICE in delivered, delivered
    cited = "2 November 2024 — the day before the 9:05 am call on 3 November 2024."
    assert guard(cited, question, evidence) == cited


def test_a_timestamp_gloss_binds_a_clock_to_another_speakers_dated_record():
    # Ottilie's reply is not about the asked session: only the date the timestamp is given with binds it.
    answer = "On 8 June 2024 (Ottilie's 9 June 2024 reply, timestamped 10:46 am, says it ran late)."
    assert guard(answer, QUARTET_Q + APPROX, QUARTET_E) == answer
    assert "10:52" not in guard(answer.replace("10:46", "10:52"), QUARTET_Q + APPROX, QUARTET_E)


def test_a_record_noun_the_question_asks_about_is_the_asked_event():
    question = "When did Maelle have a long conversation with her grandmother?" + APPROX
    evidence = [header("Maelle", "I had a long conversation with my grandmother this afternoon about the old orchard.",
                       "8:30 pm", "5 February, 2025", "2025-02-05")]
    for answer in ("Maelle's conversation was at 8:30 pm on 5 February 2025.",
                   "Their conversation took place at 8:30 pm on 5 February 2025."):
        delivered = guard(answer, question, evidence)
        assert delivered != answer and NOTICE in delivered, (answer, delivered)
    kept = "5 February 2025 — that afternoon, per her 8:30 pm message on 5 February 2025."
    assert guard(kept, question, evidence) == kept


# === (B) a record-stated offset ===================================================================

CHILE_Q = "When did Ylva visit Chile?"
CHILE_E = [
    suffix("Ylva", "Your harbour photos take me back, four years ago I was in Valparaíso for the boat parade.",
           "11:20 am", "3 July, 2024", "2024-07-03"),
    suffix("Kasimir", "Two years ago I sailed all the way up to Tromsø.", "11:24 am", "3 July, 2024", "2024-07-03"),
    plain("Ylva", "I finally joined the choir last spring.", "2024-02-11"),
]

AGO_OFFSET_KEPT = [
    # the reported shape
    "Four years before 3 July 2024, so around 2020.",
    # clean paraphrases
    "Around 2020 — on 3 July 2024 Ylva said she had been in Valparaíso four years earlier.",
    "2020, give or take: four years before her 3 July 2024 message.",
    "In 2020 (Ylva, 3 July 2024: four years ago).",
    "Roughly 2020, i.e. four years prior to 3 July 2024.",
    "Four years earlier than 3 July 2024, so 2020.",
    # sloppy
    "around 2020, 4 yrs before 3 july 2024",
    "4 years b4 jul 3 2024 so 2020-ish",
    "approx 2020 (four years ago as of 3 jul 2024)",
    "2020 four yrs before her july 3 2024 msg",
    "ylva on 3 jul 2024: four years ago, so 2020",
    "abt 2020 (4 years ago as of 3 july 2024)",
    "2020: on 3 July 2024 Ylva recalled being in Valparaíso four years earlier.",
]


@pytest.mark.parametrize("answer", AGO_OFFSET_KEPT)
def test_an_offset_the_subjects_record_states_derives_the_year_from_its_statement_time(answer):
    assert guard(answer, CHILE_Q + APPROX, CHILE_E) == answer
    entries = derivations(answer, CHILE_Q + APPROX, CHILE_E, "statement_time_derivation")
    assert any(entry["rule"] == "request_licensed_cited_offset" and entry["window"] == ["2020-01-01", "2020-12-31"]
               for entry in entries), entries


@pytest.mark.parametrize("question", [
    "when did ylva visit chile? approximate date is fine",
    "Ylva in Chile - roughly when?",
    "when did Ylva go to chile, use the date of the chat",
    "When did Ylva visit Chile? Answer based on when it was said.",
])
def test_sloppy_requests_for_an_approximate_date_keep_the_cited_offset(question):
    answer = "Four years before 3 July 2024, so around 2020."
    assert guard(answer, question, CHILE_E) == answer


AGO_OFFSET_WITHDRAWN = [
    # wrong arithmetic over the record's own offset
    "Four years before 3 July 2024, so around 2018.",
    # an offset no record states
    "Six years before 3 July 2024, so around 2018.",
    # an offset no record states, written with no number the duration check could read
    "A year before 3 July 2024, so around 2020.",
    # another speaker's offset
    "Two years before 3 July 2024, so around 2022.",
    # the record's statement time is not named
    "Four years earlier, so around 2020.",
    # a date that is not the record's statement time
    "Four years before 10 July 2024, so around 2020.",
    # a day picked out of a year offset
    "3 July 2020 — four years before 3 July 2024.",
    # the clause is about another event
    "Ylva's cousin opened a bakery in Cádiz four years before 3 July 2024, so 2020.",
]


@pytest.mark.parametrize("answer", AGO_OFFSET_WITHDRAWN)
def test_an_offset_derivation_the_record_does_not_check_is_withdrawn(answer):
    delivered = guard(answer, CHILE_Q + APPROX, CHILE_E)
    assert delivered != answer and NOTICE in delivered, delivered


# Kasimir, on the same day, states the same offset about himself: only a named subject (or a citation
# that fits one record) decides whose four years the answer cites.
SAME_OFFSET_E = [*CHILE_E, suffix("Kasimir", "Four years ago I was in Valparaíso too, for work.", "11:26 am", "3 July, 2024", "2024-07-03")]


def test_a_follow_up_naming_nobody_asks_about_the_conversations_subject():
    evidence = [*CHILE_E, "- user said: When did Ylva visit Chile?"]
    answer = "Four years before 3 July 2024 — so 2020."
    assert guard(answer, "Roughly when was that trip?", evidence) == answer
    assert [entry["binding"] for entry in derivations(answer, "Roughly when was that trip?", evidence, "statement_time_derivation")
            if entry["rule"] == "request_licensed_cited_offset"] == ["conversation_subject"]


@pytest.mark.parametrize("question,answer", [
    ("roughly when was that?", "Two years before 3 July 2024 — so 2022."),
    ("and approximately when, going by the date of the chat?", "Around 2022, two years before the 3 July 2024 conversation."),
    ("roughly when?", "2022 (two years before 3 July 2024)."),
])
def test_a_follow_up_naming_nobody_never_takes_another_speakers_offset(question, answer):
    # Only Kasimir states two years; the conversation asked about Ylva.
    evidence = [*CHILE_E, "- user said: When did Ylva visit Chile?"]
    delivered = guard(answer, question, evidence)
    assert delivered != answer and NOTICE in delivered, delivered


def test_a_follow_up_with_no_named_subject_in_the_conversation_binds_nothing():
    answer = "Four years before 3 July 2024 — so 2020."
    assert "2020" not in guard(answer, "roughly when was that?", CHILE_E)


def test_a_citation_that_fits_two_speakers_records_needs_a_named_subject():
    answer = "Four years before 3 July 2024 — so 2020."
    assert "2020" not in guard(answer, "Roughly when was that trip?", SAME_OFFSET_E)
    assert guard(answer, "Ylva in Chile - roughly when?", SAME_OFFSET_E) == answer


def test_without_the_license_an_offset_record_not_naming_the_event_lends_nothing():
    # Her 3 July record about visiting Chile makes the cited record time itself supported; the offset
    # record (Valparaíso) never names the asked event, and outside the license nothing binds it.
    evidence = [*CHILE_E, suffix("Ylva", "I want to visit Chile again soon.", "11:22 am", "3 July, 2024", "2024-07-03")]
    answer = "Four years before her 3 July 2024 message, so around 2020."
    receipt: dict = {}
    stated_past_time_claims(answer, question=CHILE_Q, evidence_texts=evidence, decision_receipt=receipt)
    assert sorted(receipt["checks"][0]["unsupported_values"]) == ["d4year", "y2020"], receipt["checks"][0]
    assert "2020" not in guard(answer, CHILE_Q, evidence)
    assert guard(answer, CHILE_Q + APPROX, evidence) == answer


@pytest.mark.parametrize("record", [
    "I haven't been back to Valparaíso for four years.",
    "Maybe four years ago I was in Valparaíso, or was it five?",
])
def test_a_negated_or_uncertain_offset_record_supports_nothing(record):
    evidence = [suffix("Ylva", record, "11:20 am", "3 July, 2024", "2024-07-03")]
    assert "2020" not in guard("Four years before 3 July 2024, so around 2020.", CHILE_Q + APPROX, evidence)


@pytest.mark.parametrize("negated,affirmed", [
    ("Four years ago I had not been to Valparaíso yet.", "Four years ago I had been to Valparaíso already."),
    ("I wasn't in Valparaíso four years ago, that was my brother.", "I was in Valparaíso four years ago, with my brother."),
    ("Four years ago I was never in Valparaíso, only in Lima.", "Four years ago I was in Valparaíso, and in Lima."),
])
def test_a_negated_record_stating_the_offset_is_no_cited_offset(negated, affirmed):
    # The negated record states the same offset at the same statement time and places her at the same
    # place: only its polarity tells it apart from the affirmed one.
    answer = "Four years before 3 July 2024, so around 2020."
    assert guard(answer, CHILE_Q + APPROX, [suffix("Ylva", affirmed, "11:20 am", "3 July, 2024", "2024-07-03")]) == answer
    delivered = guard(answer, CHILE_Q + APPROX, [suffix("Ylva", negated, "11:20 am", "3 July, 2024", "2024-07-03")])
    assert delivered != answer and NOTICE in delivered, delivered


@pytest.mark.parametrize("record", [
    "Four years ago I adopted my greyhound Pernille.",
    "Four years ago I started learning the oboe.",
    "Four years ago I sold my old sloop to a neighbour.",
])
def test_an_offset_record_of_another_event_dates_nothing_asked(record):
    # Her only offset record is about something else: the arithmetic holds, the event does not.
    evidence = [suffix("Ylva", record, "11:20 am", "3 July, 2024", "2024-07-03"),
                suffix("Ylva", "The harbour in Valparaíso is so colourful.", "11:21 am", "3 July, 2024", "2024-07-03")]
    delivered = guard("Four years before 3 July 2024, so around 2020.", CHILE_Q + APPROX, evidence)
    assert "2020" not in delivered and NOTICE in delivered, delivered


def test_an_offset_record_naming_the_asked_object_relates_through_another_verb():
    # "part with" is not "sold": no request-bound rule reads this record; it names the accordion.
    question = "When did Rasmus part with his accordion?" + APPROX
    evidence = [header("Rasmus", "I sold my old accordion two years ago, I still miss it.", "7:44 pm", "15 June, 2024", "2024-06-15")]
    answer = "Two years before 15 June 2024, so around 2022."
    assert guard(answer, question, evidence) == answer
    other = [header("Rasmus", "I sold my old bicycle two years ago, I still miss it.", "7:44 pm", "15 June, 2024", "2024-06-15")]
    assert "2022" not in guard(answer, question, other)


@pytest.mark.parametrize("record", [
    "Four years ago I spent a month in Valparaíso.",
    "Four years ago we travelled to Valparaíso for the regatta.",
    "Four years ago I visited Valparaíso with my aunt.",
    "Four years ago I crewed at the Valparaíso regatta.",
])
def test_an_offset_record_placing_the_subject_at_a_named_place_dates_an_asked_visit(record):
    evidence = [suffix("Ylva", record, "11:20 am", "3 July, 2024", "2024-07-03")]
    answer = "Four years before 3 July 2024, so around 2020."
    assert guard(answer, CHILE_Q + APPROX, evidence) == answer


# --- a present-perfect state reaching the statement time is an offset ---------------------------

COUPLE_Q = "Which year did Teodor and his partner get together?"
COUPLE_E = [header("Teodor", "No ring yet, but we have been a couple for five years and we like it that way.",
                   "8:20 pm", "10 March, 2024", "2024-03-10")]

PERFECT_STATE_KEPT = [
    # the reported shape
    "Around 2019 — on 10 March 2024 Teodor said they'd been a couple for five years.",
    # clean paraphrases
    "2019 (Teodor said on 10 March 2024 that they had been a couple for five years).",
    "Five years before 10 March 2024, so roughly 2019.",
    "Around 2019 — by 10 March 2024 they had been a couple for five years.",
    "Probably 2019: on 10 March 2024 he said five years.",
    "Around 2019 — Teodor said on 10 March 2024 they'd been a couple for five years, so they likely got together about five years before that.",
    # sloppy
    "around 2019 - teodor said 10 march 2024 they'd been a couple 5 yrs",
    "2019-ish (5 years before mar 10 2024)",
    "five yrs before 10 mar 2024 -> 2019",
    "approx 2019 (they'd been a couple 5 years by 10 March 2024)",
    "2019, teodor on 10 march 2024: five years",
]


@pytest.mark.parametrize("answer", PERFECT_STATE_KEPT)
def test_a_state_lasting_up_to_the_statement_dates_its_start_by_its_stated_length(answer):
    assert guard(answer, COUPLE_Q + APPROX, COUPLE_E) == answer
    entries = derivations(answer, COUPLE_Q + APPROX, COUPLE_E, "statement_time_derivation")
    assert any(entry["rule"] == "request_licensed_cited_offset" and entry["offset"] == {"quantity": 5, "unit": "year"}
               for entry in entries), entries


@pytest.mark.parametrize("record", [
    "We had been a couple for five years when we moved to Bergen.",
    "In June we will have been a couple for five years.",
    "We have been to Bergen for five days, and we love it.",
])
def test_a_past_future_or_experiential_perfect_is_no_state_reaching_the_statement(record):
    evidence = [header("Teodor", record, "8:20 pm", "10 March, 2024", "2024-03-10")]
    answer = "Around 2019 — on 10 March 2024 Teodor said they'd been a couple for five years."
    assert "2019" not in guard(answer, COUPLE_Q + APPROX, evidence)


def test_an_experiential_been_to_is_a_past_stay_not_a_state_reaching_the_statement():
    evidence = [header("Teodor", "I have been to Bergen for five weeks, a long time back.",
                       "8:20 pm", "10 March, 2024", "2024-03-10")]
    answer = "Around 4 February 2024 — five weeks before 10 March 2024."
    assert "4 February" not in guard(answer, "When did Teodor arrive in Bergen?" + APPROX, evidence)


def test_a_durative_perfect_does_not_take_a_length_from_a_later_clause():
    # "lived" is a state, but the five weeks belong to the stays in another town after "and" ("but"
    # already splits a record into two units before any window is read).
    evidence = [header("Teodor", "We have lived in Bergen and stayed in Ålesund for five weeks each winter.",
                       "8:20 pm", "10 March, 2024", "2024-03-10")]
    answer = "Around 4 February 2024 — five weeks before 10 March 2024."
    assert "4 February" not in guard(answer, "When did Teodor arrive in Bergen?" + APPROX, evidence)


def test_a_length_after_a_clause_break_is_a_past_stay_not_a_state_reaching_the_statement():
    # "visited ... twice and stayed for five weeks": the five weeks were a stay at some earlier time.
    evidence = [header("Teodor", "We have visited Bergen twice and stayed for five weeks each time.",
                       "8:20 pm", "10 March, 2024", "2024-03-10")]
    answer = "Around 4 February 2024 — five weeks before 10 March 2024."
    assert "4 February" not in guard(answer, "When did Teodor arrive in Bergen?" + APPROX, evidence)


@pytest.mark.parametrize("answer", [
    "Around 2017 — on 10 March 2024 Teodor said they'd been a couple for five years.",
    "10 March 2019 — five years before 10 March 2024.",
])
def test_a_state_offset_checks_only_its_own_arithmetic_at_its_own_granularity(answer):
    assert guard(answer, COUPLE_Q + APPROX, COUPLE_E) != answer


MONTHS_E = [header("Rasmus", "It has been eight months since I quit the orchestra, and I miss it.", "7:43 pm", "15 June, 2024", "2024-06-15"),
            header("Rasmus", "I sold my old accordion four days ago.", "7:44 pm", "15 June, 2024", "2024-06-15")]


@pytest.mark.parametrize("question,kept,withdrawn", [
    # a month offset checks the month, not only its year (the bound record names the asked event)
    ("When did Rasmus quit the orchestra?", "Rasmus quit the orchestra around October 2023.",
     "Rasmus quit the orchestra around December 2023."),
    ("When did Rasmus quit the orchestra?" + APPROX, "Eight months before 15 June 2024 — around October 2023.",
     "Eight months before 15 June 2024 — around December 2023."),
    # a day offset checks the day; the license's nearby-day approximation does not excuse the arithmetic
    ("When did Rasmus part with his accordion?" + APPROX, "11 June 2024, four days before his 15 June 2024 message.",
     "12 June 2024, four days before his 15 June 2024 message."),
])
def test_an_offset_is_checked_at_its_own_granularity(question, kept, withdrawn):
    assert guard(kept, question, MONTHS_E) == kept
    assert guard(withdrawn, question, MONTHS_E) != withdrawn


# --- outside the license: the event-bound rule reads the same windows ---------------------------

CLUB_Q = "Since when has Sunniva run the mandolin club?"


def test_an_event_bound_present_perfect_dates_the_start_without_the_license():
    evidence = [plain("Sunniva", "I have run the mandolin club for six years now and it keeps growing.", "2024-09-14")]
    kept = "Sunniva has run the mandolin club since about 2018."
    assert guard(kept, CLUB_Q, evidence) == kept
    entries = derivations(kept, CLUB_Q, evidence, "statement_time_derivation")
    assert [entry["rule"] for entry in entries] == ["relative_window"], entries
    assert "2016" not in guard("Sunniva has run the mandolin club since about 2016.", CLUB_Q, evidence)


def test_a_present_perfect_state_does_not_undate_an_act_the_record_reports_at_its_statement():
    # The booking is a completed arrangement against "next month", reported at S; the two years date
    # the wish, not the booking (the statement-time rule keeps S).
    question = "When did Marisol book the cabin by the fjord?"
    evidence = [plain("Marisol", "I've wanted it for two years, and I finally booked a cabin by the fjord for next month!",
                      "2024-05-03")]
    answer = "Marisol booked the cabin by the fjord on 3 May 2024, for the following month."
    assert guard(answer, question, evidence) == answer


def test_a_couple_of_units_is_two_and_never_three():
    question = "When did Sunniva move to Bergen?"
    evidence = [plain("Sunniva", "I moved to Bergen a couple of years ago and I love the rain.", "2024-09-14")]
    assert guard("Sunniva moved to Bergen around 2022.", question, evidence) == "Sunniva moved to Bergen around 2022."
    assert "2021" not in guard("Sunniva moved to Bergen around 2021.", question, evidence)


def test_a_records_article_duration_supports_the_same_number():
    question = "How long has Sunniva run the mandolin club?"
    evidence = [plain("Sunniva", "I have run the mandolin club for a year now.", "2024-09-14")]
    assert guard("Sunniva has run the mandolin club for one year.", question, evidence) == (
        "Sunniva has run the mandolin club for one year.")
    assert "two years" not in guard("Sunniva has run the mandolin club for two years.", question, evidence)


def test_a_few_units_has_no_single_value_and_supports_no_year():
    question = "When did Sunniva move to Bergen?"
    evidence = [plain("Sunniva", "I moved to Bergen a few years ago.", "2024-09-14")]
    assert "2021" not in guard("Sunniva moved to Bergen around 2021.", question, evidence)


# --- a telic act's "for N units" is the length it arranged, not a state reaching the statement ---

ARRANGED_LENGTH_WITHDRAWN = [
    # (record, question, answer): the length of what the act arranged read as how long ago it happened
    ("We've booked the ferry to the islands for ten days, I can't wait.",
     "When did Solveig book the ferry to the islands?", "Solveig booked the ferry to the islands around 4 July 2024."),
    ("I have signed up for a bookbinding course for eight weeks.",
     "When did Solveig sign up for the bookbinding course?", "Solveig signed up for the bookbinding course around 19 May 2024."),
    ("I've reserved the boathouse for four days.",
     "When did Solveig reserve the boathouse?", "Solveig reserved the boathouse on 10 July 2024."),
    ("I've scheduled the recital for three weeks from now.",
     "When did Solveig schedule the recital?", "Solveig scheduled the recital around 23 June 2024."),
    ("I have been asked to judge the regatta for two days next month.",
     "When was Solveig asked to judge the regatta?", "Solveig was asked to judge the regatta around 12 July 2024."),
    ("I have volunteered at the regatta for three days next month.",
     "When did Solveig volunteer at the regatta?", "Solveig volunteered at the regatta around 11 July 2024."),
    # under the license, cited as the record's own offset
    ("We've leased the glass studio for three years.",
     "When did Solveig lease the glass studio?" + APPROX, "Three years before 14 July 2024, so around 2021."),
    ("I've been granted a residency in Tallinn for two years.",
     "When was Solveig granted the residency in Tallinn?" + APPROX, "Two years before 14 July 2024, so around 2022."),
    ("I've committed to the herbarium project for five years.",
     "When did Solveig commit to the herbarium project?" + APPROX,
     "Around 2019 — on 14 July 2024 Solveig said she had committed to the herbarium project for five years."),
    ("we've hired a sloop for two years, from the spring",
     "when did solveig hire the sloop? approx date ok", "two years before jul 14 2024 so 2022"),
]


@pytest.mark.parametrize("record,question,answer", ARRANGED_LENGTH_WITHDRAWN)
def test_an_arranged_length_is_no_state_reaching_the_statement(record, question, answer):
    delivered = guard(answer, question, [plain("Solveig", record, "2024-07-14")])
    assert delivered != answer and NOTICE in delivered, delivered


STATE_LENGTH_KEPT = [
    ("I have lived in Tallinn for three years now.", "Since when has Solveig lived in Tallinn?",
     "Solveig has lived in Tallinn since around 2021."),
    ("We've been engaged for two years and we're in no hurry.", "When did Solveig get engaged?" + APPROX,
     "Two years before 14 July 2024, so around 2022."),
    ("I've worked at the herbarium for six years.", "When did Solveig start working at the herbarium?" + APPROX,
     "Around 2018 — on 14 July 2024 Solveig said she had worked at the herbarium for six years."),
    ("I've been playing the mandolin for four years now.", "When did Solveig start playing the mandolin?" + APPROX,
     "four yrs before 14 jul 2024, so 2020-ish"),
]


@pytest.mark.parametrize("record,question,answer", STATE_LENGTH_KEPT)
def test_a_state_length_still_dates_its_start(record, question, answer):
    assert guard(answer, question, [plain("Solveig", record, "2024-07-14")]) == answer


# === (C) an interval between a supported date and a cited record time =============================

GIFT_Q = "When did Liesel give her nephew the telescope?"
GIFT_E = [
    header("Liesel", "I gave my nephew the telescope on the 10th and he loves it.", "9:30 am", "12 May, 2024", "2024-05-12"),
    header("Pavlo", "That's a lovely gift. Our ferry was late again.", "9:41 am", "12 May, 2024", "2024-05-12"),
    plain("Liesel", "The orchard is in full bloom.", "2024-05-20"),
]

INTERVAL_KEPT = [
    # the reported shape
    "10 May 2024 (two days before the 12 May conversation).",
    # clean paraphrases
    "On 10 May 2024, two days before her 12 May 2024 message.",
    "Around 10 May 2024 — two days ahead of the chat on 12 May 2024.",
    "Two days before the 12 May 2024 session: 10 May 2024.",
    "She gave it to him on 10 May 2024, two days before the 12 May conversation.",
    "10 May 2024, which was two days before she wrote about it on 12 May 2024.",
    # sloppy
    "may 10 2024 (2 days b4 the may 12 convo)",
    "10 may 2024 - two days before her 12 may msg",
    "around 10 May 2024 two days before the 12 May chat",
    "2 days before the convo on 12 may 2024 -> 10 may 2024",
    "10th may 2024, 2 days b4 her 12th may message",
]


@pytest.mark.parametrize("answer", INTERVAL_KEPT)
def test_an_interval_between_a_supported_date_and_a_cited_record_time_is_checked_arithmetic(answer):
    assert guard(answer, GIFT_Q + APPROX, GIFT_E) == answer
    entries = derivations(answer, GIFT_Q + APPROX, GIFT_E, "date_interval_derivation")
    assert [entry["days"] for entry in entries] == [2], entries


def test_a_follow_up_keeps_the_interval_to_the_cited_record_time():
    evidence = [*GIFT_E, "- user said: When did Liesel give her nephew the telescope?"]
    answer = "10 May 2024 (two days before the 12 May conversation)."
    assert guard(answer, "Roughly when was that again?", evidence) == answer


INTERVAL_WITHDRAWN = [
    # wrong arithmetic
    "10 May 2024 (three days before the 12 May conversation).",
    # a date no record was stated on, though the date itself is inside the approximation
    "10 May 2024 (four days before the 14 May conversation).",
    # two bare dates: no cited record time
    "2 days, from 10 May 2024 to 12 May 2024.",
]


@pytest.mark.parametrize("answer", INTERVAL_WITHDRAWN)
def test_an_interval_without_a_checked_pair_of_operands_is_withdrawn(answer):
    delivered = guard(answer, GIFT_Q + APPROX, GIFT_E)
    assert delivered != answer and NOTICE in delivered, delivered


def test_a_record_time_operand_must_be_a_records_statement_time():
    # 10 May is exact (the record's "two days ago"); no record was made on 14 May, though the license's
    # approximation covers that date itself.
    question = "When did Liesel deliver the telescope to her nephew?" + APPROX
    evidence = [header("Liesel", "I delivered the telescope to my nephew two days ago, he loves it.", "9:30 am", "12 May, 2024", "2024-05-12")]
    assert "four days" not in guard("10 May 2024 (four days before the 14 May conversation).", question, evidence)
    kept = "10 May 2024 (two days before the 12 May conversation)."
    assert guard(kept, question, evidence) == kept


def test_an_interval_whose_other_operand_sits_in_another_sentence_is_withdrawn():
    answer = "10 May 2024. That was two days before the 12 May conversation."
    assert "two days" not in guard(answer, GIFT_Q + APPROX, GIFT_E)


def test_without_the_license_an_event_operand_must_be_its_own_event():
    question = "When did Liesel deliver the telescope to her nephew?"
    bound = [plain("Liesel", "I delivered the telescope to my nephew two days ago, he loves it.", "2024-05-12")]
    kept = "Liesel delivered the telescope on 10 May 2024, two days before her 12 May 2024 message."
    assert guard(kept, question, bound) == kept
    assert [entry["operand_roles"] for entry in derivations(kept, question, bound, "date_interval_derivation")] == [
        ["event", "record_time"]]
    # The 10 May date is in the sentence's support (a record about the telescope carries it), but it
    # is the wrapping date: as an operand of the delivery it is another action's date.
    other_action = [plain("Liesel", "I wrapped the telescope for my nephew on 10 May.", "2024-05-12"),
                    plain("Liesel", "I delivered the telescope to my nephew on 11 May.", "2024-05-12")]
    question = "When was the telescope delivered to Liesel's nephew?"
    answer = "The telescope was delivered on 10 May 2024, two days before her 12 May 2024 message."
    receipt: dict = {}
    stated_past_time_claims(answer, question=question, evidence_texts=other_action, decision_receipt=receipt)
    assert receipt["checks"][0]["unsupported_values"] == ["d2days"], receipt["checks"][0]
    assert "two days" not in guard(answer, question, other_action)


# --- an operand only the approximation supports must be a day a record at that time states -----

@pytest.mark.parametrize("answer", [
    # self-consistent arithmetic over a date nothing states
    "28 April 2024, two weeks before the 12 May conversation.",
    "1 May 2024 (eleven days before the 12 May conversation).",
    "around 5 may 2024, 1 week b4 her 12 may msg",
])
def test_an_interval_over_an_approximate_date_no_record_states_is_withdrawn(answer):
    delivered = guard(answer, GIFT_Q + APPROX, GIFT_E)
    assert delivered != answer and NOTICE in delivered, delivered


def test_an_approximate_operand_must_be_stated_by_the_asked_subjects_record():
    # Pavlo, not Liesel, names the 8th at that time.
    evidence = [*GIFT_E[:1], header("Pavlo", "Lovely gift! Our ferry broke down on the 8th and is still out.",
                                    "9:41 am", "12 May, 2024", "2024-05-12")]
    answer = "8 May 2024 (four days before the 12 May conversation)."
    assert "four days" not in guard(answer, GIFT_Q + APPROX, evidence)
    assert guard("10 May 2024 (two days before the 12 May conversation).", GIFT_Q + APPROX, evidence) == (
        "10 May 2024 (two days before the 12 May conversation).")


POST_Q = "When did Liesel post the telescope to her nephew?"


@pytest.mark.parametrize("record,iso,answer", [
    # an ordinal day after the statement day is the previous month's in a past report ...
    ("I posted the telescope to my nephew on the 28th, he has it now.", "2024-06-03",
     "28 May 2024 (six days before the 3 June conversation)."),
    # ... and the coming one in a future form
    ("I'll post the telescope to my nephew on the 9th, once it is wrapped.", "2024-06-03",
     "9 June 2024 (six days after the 3 June conversation)."),
])
def test_a_day_named_by_its_ordinal_is_read_against_the_records_statement_time(record, iso, answer):
    evidence = [header("Liesel", record, "4:20 pm", "3 June, 2024", iso)]
    assert guard(answer, POST_Q + APPROX, evidence) == answer
    entries = derivations(answer, POST_Q + APPROX, evidence, "date_interval_derivation")
    assert [entry["approximate_operand_stated_by_record"] for entry in entries] == [True], entries


def test_an_ordinal_counting_a_thing_names_no_day():
    evidence = [header("Liesel", "I left the telescope on the 10th floor of the library for my nephew.",
                       "9:30 am", "12 May, 2024", "2024-05-12")]
    answer = "10 May 2024 (two days before the 12 May conversation)."
    assert "two days" not in guard(answer, GIFT_Q + APPROX, evidence)


def test_a_follow_up_interval_counts_a_day_the_record_states_relatively():
    evidence = [header("Liesel", "I delivered the telescope to my nephew two days ago, he loves it.",
                       "9:30 am", "12 May, 2024", "2024-05-12"),
                "- user said: When did Liesel deliver the telescope to her nephew?"]
    answer = "10 May 2024 (two days before the 12 May conversation)."
    assert guard(answer, "Roughly when was that again?", evidence) == answer
    assert "two days" not in guard("9 May 2024 (three days before the 12 May conversation).", "Roughly when was that again?", evidence)


def test_a_possessed_asked_subject_is_asked_of_its_owner():
    question = "When did Liesel's nephew get the telescope?" + APPROX
    evidence = [header("Liesel", "My nephew got the telescope two days ago, he loves it.", "9:30 am", "12 May, 2024", "2024-05-12")]
    answer = "10 May 2024 (two days before her 12 May 2024 message)."
    assert guard(answer, question, evidence) == answer


def test_an_interval_to_an_events_date_is_not_an_interval_to_a_record_time():
    # The call was the day before the message; the answer dates the call to the message's day.
    question = "When did Ottilie notice the hall clock had stopped?" + APPROX
    evidence = [header("Ottilie", "My call with the clockmaker yesterday sorted it out, the hall clock stopped on the 29th.",
                       "7:15 pm", "2 October, 2024", "2024-10-02")]
    answer = "29 September 2024, three days before her 2 October call with the clockmaker."
    assert "three days" not in guard(answer, question, evidence)
    cited = "29 September 2024, three days before her 2 October message."
    assert guard(cited, question, evidence) == cited
