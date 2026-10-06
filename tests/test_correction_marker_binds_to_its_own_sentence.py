"""A correction marker corrects what its own sentence talks about.

When neither statement carries a value, the correction marker alone decides
whether a newer record withdraws a slot-mate. A conversational record that
narrates several things carries marker WORDS in passing ("growth and
change", "more updates to share", "the bakery is actually good") while the
subject it shares with the slot-mate sits in another sentence. Such a
record is not a correction of that slot-mate (measured on a zero-spend
probe replay: these passing markers withdrew the very records a question
asked about once each speaker's statements formed their own slot).

The law: for a value-empty withdrawal the marker must bear on the
slot-mate: its sentence shares subject, unit or value vocabulary with it,
or names no subject of its own (a bare "Correction." heading), or has a
pronoun subject beside the marker that refers back ("Actually it moved").
Retractions withdraw by reference. Value-bearing corrections are untouched.

All sentences are authored for this contract (a cat, a book club, a
kiln), not copied from any benchmark text.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.temporal_selection import AsOfIntent, TemporalCandidate, apply_temporal_selection

UTC = timezone.utc
NOW = datetime(2026, 9, 1, tzinfo=UTC)


def _ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()


def _verdicts(older: str, newer: str, question: str, *, past: bool = False):
    cands = [
        TemporalCandidate(key="older", body=older, statement_at=_ts("2026-05-02T09:00:00")),
        TemporalCandidate(key="newer", body=newer, statement_at=_ts("2026-05-09T09:00:00")),
    ]
    return apply_temporal_selection(cands, intent=AsOfIntent(), past_only=past,
                                    question=question, now_utc=NOW)


CAT = "My cat Pepper sleeps on the porch swing."
CAT_Q = "Where does Pepper sleep?"

# Passing marker words in a sentence about something else: the older record
# stays. Clean paraphrases and sloppy, user-typed variants of the shape.
PASSING_MARKERS = [
    "Pepper napped on the porch swing all afternoon. Hopefully there will be more updates on the garden soon.",
    "Pepper was on the porch swing again. It is wonderful to see so much growth and change in the neighbourhood.",
    "Pepper curled up on the porch swing today. The new bakery on Elm street is actually really good.",
    "Pepper loves that porch swing. My sister changed jobs last month and seems happy.",
    "Pepper on the porch swing again lol. no updates on the garden yet",
    "porch swing is still peppers spot\nthe neighbours finally changed there fence colour",
    "Pepper and the porch swing, inseparable. Work has been all change this quarter!",
    "Pepper sat on the porch swing; the weather has actually been lovely",
]


@pytest.mark.parametrize("newer", PASSING_MARKERS)
def test_a_marker_in_another_sentence_withdraws_nothing(newer):
    verdicts = _verdicts(CAT, newer, CAT_Q)
    assert verdicts["older"].eligible, (newer, verdicts["older"])
    assert verdicts["older"].superseded_by is None


@pytest.mark.parametrize("newer", PASSING_MARKERS[:3])
def test_a_marker_in_another_sentence_withdraws_nothing_for_a_past_ask(newer):
    verdicts = _verdicts(CAT, newer, "Where did Pepper sleep?", past=True)
    assert verdicts["older"].eligible, (newer, verdicts["older"])


CLUB = "The book club meets in the library."
CLUB_Q = "Where does the book club meet?"

# Controls: real corrections of the slot-mate keep withdrawing it.
CORRECTIONS = [
    "Actually the book club meets at the cafe now.",                     # same sentence
    "We set up the book club last spring. Actually it moved to the cafe.",  # anaphoric subject
    "Correction.\nThe book club meets at the cafe.",                    # bare marker heading
    "Scratch that. The book club is off.",                               # retraction by reference
    "book club update: the cafe from now on",                           # sloppy, same sentence
    "The book club? they changed it to the cafe",                        # pronoun beside marker
    "The book club. That changed: cafe from next week.",                 # demonstrative subject
]


@pytest.mark.parametrize("newer", CORRECTIONS)
def test_a_correction_of_the_slot_mate_still_withdraws_it(newer):
    verdicts = _verdicts(CLUB, newer, CLUB_Q)
    assert not verdicts["older"].eligible, (newer, verdicts["older"])
    assert verdicts["older"].superseded_by == "newer"


def test_a_value_bearing_correction_is_untouched_by_sentence_scope():
    # Values decide value-bearing pairs; the marker sentence law applies
    # only where the marker alone decides.
    verdicts = _verdicts(
        "The kiln fires at 1220 degrees for stoneware.",
        "We cleaned the studio on Sunday. Actually the kiln fires at 1240 degrees for stoneware.",
        "What temperature does the kiln fire stoneware at?")
    assert not verdicts["older"].eligible
    assert verdicts["older"].superseded_by == "newer"


def test_near_miss_marker_sentence_sharing_the_subject_still_withdraws():
    # Reads like passing chatter, but the marker sentence IS about the
    # porch swing: it corrects where Pepper sleeps.
    verdicts = _verdicts(
        CAT, "Nice weather today. Pepper actually sleeps on the window seat, not the porch swing.", CAT_Q)
    assert not verdicts["older"].eligible
