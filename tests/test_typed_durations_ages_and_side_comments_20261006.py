"""The past-time check reads operands by type (external review, 2026-10-06).

Three general defects withdrew answers the records support:
- a race or target time written H:MM was read as a clock time, and a record's "2 hours and 30 minutes" never
  matched it, so a correct difference of two such times was withdrawn;
- a year difference between two ages the asker stated ("a 38-year-old ...", "at the age of 30") had no derivation;
- a negation inside a trailing comment clause (", which wasn't too bad") denied the whole record clause.
Each family keeps its falsifiers: wrong arithmetic, an operand the records do not state, a third person's age, a
clock time after "at", a negated main clause, and a comment clause that negates the value itself.
Names, events and numbers are authored for this contract.
"""
from datetime import date

import pytest

from core.model_output_guard import ReferenceClock, stated_past_time_claims

CLOCK = ReferenceClock(date(2024, 6, 1), "request-hash", "clock-hash")


def unsupported(answer, question, evidence):
    return stated_past_time_claims(answer, question=question, evidence_texts=evidence, reference_clock=CLOCK)


# --- H:MM race and target times are durations ------------------------------------------------------------
RACE = [
    "- user said (stated 2024-05-04): My goal for the Lakeshore half was 1 hour and 45 minutes, nothing fancy.",
    "- user said (stated 2024-05-19): I finally ran the Lakeshore half and finished in 1h 52min.",
]
RACE_Q = "How many minutes slower than my goal was I in the Lakeshore half?"


@pytest.mark.parametrize("answer", [
    "7 minutes (goal 1:45, finished 1:52).",
    "You were 7 minutes slower: your goal was 1:45 and you finished in 1:52.",
    "7 minutes slower than your 1:45 goal, with a 1:52 finish time.",
    "7 minutes. Goal 1:45, finish time 1:52.",
    "About 7 minutes over the goal (1:45 goal, ran it in 1:52).",
    # sloppy
    "7 mins slower, goal 1:45 finished 1:52",
    "7 minutes slower goal was 1:45 you finished 1:52",
    "you were 7 minutes off, goal 1:45, finished 1:52.",
    "7 MINUTES (goal 1:45 / finished 1:52)",
    "7 min — goal 1:45, finish time 1:52",
])
def test_a_race_time_difference_is_derived_from_two_stated_durations(answer):
    assert unsupported(answer, RACE_Q, RACE) == ()


@pytest.mark.parametrize("answer", [
    "8 minutes (goal 1:45, finished 1:52).",      # wrong arithmetic
    "7 minutes (goal 1:45, finished 1:53).",      # an operand the records do not state
    "9 minutes (goal 1:43, finished 1:52).",      # wrong operand and wrong result
])
def test_a_wrong_difference_or_unstated_operand_is_still_withdrawn(answer):
    assert unsupported(answer, RACE_Q, RACE)


@pytest.mark.parametrize("answer,question", [
    ("You finished at 1:52.", "When did I finish the Lakeshore half?"),
    ("The start was around 7:30.", "When did the Lakeshore half start?"),
    ("Your call with Ines was at 9:45.", "When was my call with Ines?"),
])
def test_a_time_after_at_or_around_stays_a_clock_time(answer, question):
    # Nothing in the records states these clock times; read as durations they would still be unsupported,
    # but they must be checked as clock times, not converted.
    found = unsupported(answer, question, RACE)
    assert found and all(value.startswith("t") for value in found)


def test_a_compound_record_duration_supports_the_same_duration_written_differently():
    evidence = ["- user said (stated 2024-05-19): The repair at the bike shop took 2 hours and 30 minutes."]
    question = "How long did the repair at the bike shop take?"
    for answer in ("2 hours and 30 minutes.", "2h 30min.", "150 minutes."):
        assert unsupported(answer, question, evidence) == (), answer
    assert unsupported("2 hours and 40 minutes.", question, evidence)


# --- Year differences between the asker's own stated ages -----------------------------------------------
AGES = [
    "- user said (stated 2024-04-02): As a 38-year-old landscape architect at a small studio, I want a calmer job.",
    "- user said (stated 2024-04-09): I got my master's in urban design, which I finished at the age of 30.",
]
# (A possessive object such as "my master's" is read as a person by the question-actor parser, an older quirk that
# this contract does not cover; the asker here names no possessed noun.)
AGE_Q = "How many years older am I now than when I finished my urban design degree?"


@pytest.mark.parametrize("answer", [
    "8 years: you're 38 now and finished your master's at 30.",
    "You are 8 years older (38 now, 30 then).",
    "8 years — 38 today versus 30 when you finished.",
    "8 years older; you finished at 30 and you're 38.",
    "Eight years. You were 30 then and are 38 now.",
    # sloppy
    "8 yrs older (38 vs 30)",
    "8 years you r 38 now finished at 30",
    "8 years, 38 now 30 then",
])
def test_an_age_difference_is_derived_from_two_ages_the_asker_stated(answer):
    assert unsupported(answer, AGE_Q, AGES) == ()


@pytest.mark.parametrize("answer,evidence", [
    ("9 years: you're 38 now and finished at 30.", AGES),                       # wrong arithmetic
    ("7 years: you're 38 now and finished at 31.", AGES),                       # 31 is stated nowhere
    ("3 years: you're 38 and your sister is 41.",
     AGES + ["- user said (stated 2024-04-10): My 41-year-old sister moved to Porto."]),  # a third person's age
    ("8 years.", ["- user said (stated 2024-04-02): As a 38-year-old landscape architect, I want a calmer job."]),
])
def test_an_age_difference_without_two_stated_own_ages_is_withdrawn(answer, evidence):
    assert unsupported(answer, AGE_Q, evidence)


def test_a_question_not_about_age_gets_no_age_derivation():
    # Near miss: two numbers that are ages in the records, but the question asks how long a course took.
    found = unsupported("8 years (38 and 30).", "How long did my urban design course take?", AGES)
    assert "d8year" in found


# --- A comment clause's negation does not deny the clause it comments on -------------------------------
STOOL = "How long did it take me to sand the oak stool?"


@pytest.mark.parametrize("record", [
    "- user said (stated 2024-05-30): I sanded the oak stool last night and it took me 3 hours, which wasn't too bad.",
    "- user said (stated 2024-05-30): Sanding the oak stool took me 3 hours, which was not as bad as I feared.",
    "- user said (stated 2024-05-30): It took me 3 hours to sand the oak stool, which didn't bother me at all.",
    "- user said (stated 2024-05-30): I sanded the oak stool in 3 hours with my neighbour, who didn't mind the dust.",
    "- user said (stated 2024-05-30): the oak stool took me 3 hours to sand, which wasnt terrible",
])
def test_a_negated_comment_clause_leaves_the_stated_duration_supported(record):
    assert unsupported("3 hours.", STOOL, [record]) == ()


@pytest.mark.parametrize("record", [
    "- user said (stated 2024-05-30): I didn't sand the oak stool in 3 hours; it took all weekend.",
    "- user said (stated 2024-05-30): I sanded the oak stool, which didn't take 3 hours at all.",
    "- user said (stated 2024-05-30): I never sanded the oak stool, which would have taken 3 hours.",
])
def test_a_negated_main_clause_or_a_comment_that_negates_the_value_still_withdraws(record):
    assert unsupported("3 hours.", STOOL, [record])

