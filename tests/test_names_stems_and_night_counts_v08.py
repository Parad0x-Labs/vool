"""Three small readers: whose name a saved fact gives, which words a memory search treats as one, and which day
ranges are stays.

Measured (the memory and context diagnosis of 2026-11-16, function probes; the fix mission's own cases of 2026-11-18):
any capitalised word after "my <word> <word>" was taken for the name of someone tied to the owner ("my bus leaves
Tuesday", "our last stop is Rotterdam"); a stem of three letters joined "rating" and "rats" and "who is my OT?"
shared nothing with "my OT is Ms Bellamy."; "chapters 5-11 may be out of order" was
shown to the model with "(5-11 may is 6 nights)".

All names, places and numbers are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import tiered_context_loader as loader
from core.agent_runtime import cross_chat_recall as recall
from core.memory import entries
from core.memory.entries import search_relevant_memory
from tests.test_everyday_remember_v08 import _say
from tests.test_memory_forget_resurrection_p0 import _policy, isolated_home

# --- whose name a saved fact gives ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("fact", "name"), [
    ("my aunt Perpetua keeps bees near Athlone.", "perpetua"),            # a relation noun, then the name
    ("my hockey coach Ravindra picks the squad on Fridays.", "ravindra"),  # a word before the relation noun
    ("my landlady is Mrs Okonjo.", "mrs"),                                # relation noun and copula (the title is what follows)
    ("our choir master is called Benedikt.", "benedikt"),                 # "called" after any role
    ("my bandmate Solveig plays the cello.", "solveig"),                  # one noun in apposition, no list needed
    ("my brother Tobiah lives in Quillon", "tobiah"),
    ("our landlord is called Feodora Lask", "feodora"),
])
def test_a_person_of_the_owners_is_named(fact, name):
    assert name in recall._owned_names(fact)


@pytest.mark.parametrize("fact", [
    "my coach leaves Wednesday from bay 3.",          # a weekday
    "our final stop is Trondheim.",                   # a place after "is"
    "my favourite poet is Akhmatova.",                # a taste, not someone of the owner's
    "our next fixture is in Motherwell.",
    "my contract ends in November.",                  # a month
    "my favourite season is Autumn.",
    "our spring outing is to Lindisfarne.",
    "my brother arrives Saturday.",                   # a weekday even after a relation noun
])
def test_a_place_a_day_or_a_taste_is_no_name_of_the_owners(fact):
    assert recall._owned_names(fact) == set()


def test_a_question_that_names_a_saved_place_is_not_about_someone_of_the_owners(isolated_home):
    _say("Remember that our final stop is Trondheim.")
    _say("Remember that my aunt Perpetua keeps bees near Athlone.")
    assert recall.saved_memory_names("how cold does Trondheim get?", session_id="ask", source_context={}) is False
    assert recall.saved_memory_names("what's Perpetua's phone number?", session_id="ask", source_context={}) is True


# --- which words a memory search treats as one ------------------------------------------------------------------


@pytest.mark.parametrize(("one", "other"), [("preference", "prefer"), ("bookings", "booking"), ("tablets", "tablet"), ("walks", "walking")])
def test_forms_of_one_word_still_meet(one, other):
    assert entries._match_stem(one) == entries._match_stem(other)


@pytest.mark.parametrize(("one", "other"), [("rating", "rats"), ("offence", "off"), ("pudding", "pud")])
def test_two_words_do_not_meet_on_three_letters(one, other):
    assert entries._match_stem(one) != entries._match_stem(other)


def test_four_letters_still_meet_which_is_what_keeps_walking_with_walks():
    # The limit of a suffix rule, pinned so nobody reads more into it: "landing" still meets "land" and "evening"
    # still meets "even". What keeps such a row from answering a question is the reader of the rows found (the
    # subject gate and its judgement), not this stem.
    assert entries._match_stem("landing") == entries._match_stem("land")
    assert entries._match_stem("walking") == entries._match_stem("walks")


def test_a_two_letter_word_in_capitals_is_a_word(isolated_home):
    _say("Remember that my OT is Ms Bellamy.")
    found = search_relevant_memory("who is my OT?", access_policy=_policy("ask"), limit=6)
    assert [str(row.get("text")) for row in found] == ["my OT is Ms Bellamy."]


def test_the_seat_preference_still_meets_the_window_seats():
    assert entries._search_tokens("any seat preference?") & entries._search_tokens("i always prefer window seats when i fly")


# --- which day ranges are stays -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("line", "nights"), [
    ("we have the bothy booked 3-7 february.", 4),
    ("the hotel in Tromso is 21-24 september.", 3),
    ("our trip to Arran is 2-9 june.", 7),
    ("the cabin dates changed, its now 13-16 march", 3),
])
def test_a_stay_keeps_its_night_count(line, nights):
    assert loader._with_night_counts(line) == f"{line} ({loader._DAY_RANGE_RE.search(line).group(0).strip()} is {nights} nights)"


@pytest.mark.parametrize("line", [
    "sections 5-11 may be out of order.",
    "floats 30-44 march past the stand first.",
    "ring between 3-5 dec if the part arrives.",
    "lanes 7-9 june and july are closed for resurfacing.",
    "plates 12-15 mar the look of the page.",
])
def test_a_range_that_is_no_stay_gets_no_night_count(line):
    assert loader._with_night_counts(line) == line


def test_a_range_written_as_dates_keeps_its_count_with_no_stay_word():
    assert loader._with_night_counts("the 2nd to 6th of october") == "the 2nd to 6th of october (2nd to 6th of october is 4 nights)"
